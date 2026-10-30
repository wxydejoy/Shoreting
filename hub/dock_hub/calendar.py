"""日程接入（F13）：问「今天有什么事」，答日历。

订阅一份 ICS（iCloud / Google 日历的「秘密地址」都行），纯标准库解析：
展开折叠行、挑出 VEVENT，重复规则只认 FREQ=DAILY/WEEKLY/MONTHLY/YEARLY
加 INTERVAL/COUNT/UNTIL/BYDAY——真实日历里九成的重复都在这四种里。

规矩：
- 拉取 30 分钟一次，失败记仇 15 分钟（口径同天气），不能把日历源敲穿；
- 展开从 DTSTART 一格一格走，步数封顶——写错规则的日历不能把 Hub 拖死；
- 只回答某一天有哪些事，不进快照：主屏只回答三个问题（规格 5.3）；
- 拿不到数据就空着，早间简报自己兜底说「今天没安排」，不编。
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
import urllib.request
from typing import Callable
from zoneinfo import ZoneInfo

FETCH_TIMEOUT_SEC = 10.0
CACHE_TTL_SEC = 30 * 60.0
"""日历一天变不了几次，半小时拉一回足够新。"""
FAIL_BACKOFF_SEC = 15 * 60.0
"""拉失败后的记仇期，期间直接用旧账，不去敲源。"""
MAX_STEPS = 20000
"""重复展开的步数上限：一天一格走几十年也走不完这个数。"""
MAX_EVENTS = 50
"""单日最多报 50 条，防一份病态日历把回复撑爆。"""


# ---- ICS 文本解析 -----------------------------------------------------------


def unfold(text: str) -> list[str]:
    """RFC 5545 折叠行：以空格/Tab 开头的行接回上一行。"""
    out: list[str] = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            continue
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split_prop(line: str) -> tuple[str, dict[str, str], str] | None:
    """`DTSTART;TZID=Asia/Shanghai:20261031T140000` → (名字, 参数, 值)。"""
    head, sep, value = line.partition(":")
    if not sep:
        return None
    parts = head.split(";")
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        params[key.upper()] = val.strip().strip('"')
    return parts[0].upper(), params, value.strip()


def _parse_when(params: dict[str, str], value: str) -> dt.datetime | dt.date | None:
    """DTSTART/DTEND/UNTIL 的写法：纯日期、UTC、带 TZID 或本地浮动。"""
    value = (value or "").strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return dt.datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not match:
        return None
    try:
        naive = dt.datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if match.group(3) == "Z":
        return naive.replace(tzinfo=dt.timezone.utc)
    tzid = params.get("TZID")
    if tzid:
        try:
            return naive.replace(tzinfo=ZoneInfo(tzid))
        except Exception:  # noqa: BLE001 - 认不得的时区当本地时间
            return naive.astimezone()
    return naive.astimezone()


def _local_date(item: dt.datetime | dt.date) -> dt.date:
    """哪一天：datetime 折到本地时区再看日期，date 原样。"""
    if isinstance(item, dt.datetime):
        return item.astimezone().date()
    return item


def _local_hhmm(item: dt.datetime | dt.date) -> str:
    if isinstance(item, dt.datetime):
        return item.astimezone().strftime("%H:%M")
    return ""


class Event:
    """一条日程的原始记录：起止、标题、重复规则、例外日期。"""

    __slots__ = ("start", "end", "title", "rrule", "exdates")

    def __init__(
        self,
        start: dt.datetime | dt.date,
        end: dt.datetime | dt.date | None,
        title: str,
        rrule: dict[str, str] | None,
        exdates: set[str],
    ) -> None:
        self.start = start
        self.end = end
        self.title = title
        self.rrule = rrule
        self.exdates = exdates

    def spans(self, day: dt.date) -> bool:
        """这天有没有它：跨天的事（出差、假期）中间每天也算。"""
        start_day = _local_date(self.start)
        if day < start_day:
            return False
        if self.end is None:
            return day == start_day
        end_day = _local_date(self.end)
        if isinstance(self.start, dt.datetime) or isinstance(self.end, dt.datetime):
            # 带钟点的跨天事：结束那天的钟点前都算
            return day <= end_day
        # 全天事件 DTEND 是排他端点：明天结束的事今天算、明天不算
        return day < end_day


def parse_ics(text: str) -> list[Event]:
    """一份 ICS 文本换成一列 Event。看不懂的条目跳过，不整体失败。"""
    events: list[Event] = []
    stack: list[str] = []
    props: dict[str, tuple[dict[str, str], str]] = {}
    exdates: set[str] = set()

    def flush() -> None:
        nonlocal props, exdates
        start_raw = props.get("DTSTART")
        if start_raw is not None:
            start = _parse_when(*start_raw)
            if start is not None:
                end = None
                if "DTEND" in props:
                    end = _parse_when(*props["DTEND"])
                title = str(props.get("SUMMARY", ({}, ""))[1] or "").strip()
                rrule_raw = props.get("RRULE")
                rrule = parse_rrule(rrule_raw[1]) if rrule_raw else None
                events.append(Event(start, end, title, rrule, set(exdates)))
        props = {}
        exdates = set()

    for line in unfold(text):
        parsed = _split_prop(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN":
            stack.append(value.upper())
            if stack[-1] == "VEVENT":
                props, exdates = {}, set()
            continue
        if name == "END":
            block = value.upper()
            if stack and stack[-1] == block:
                stack.pop()
            if block == "VEVENT":
                flush()
            continue
        if not stack or stack[-1] != "VEVENT":
            continue  # 日历本体和 VALARM 里的一切都不看
        if name == "EXDATE":
            for piece in re.split(r"[,;]", value):
                when = _parse_when(params, piece)
                if when is not None:
                    exdates.add(_local_date(when).isoformat())
        elif name in ("DTSTART", "DTEND", "SUMMARY", "RRULE"):
            props[name] = (params, value)
    return events


def parse_rrule(value: str) -> dict[str, str]:
    """`FREQ=WEEKLY;BYDAY=MO,WE` → dict。没有 FREQ 的不算规则。"""
    rule: dict[str, str] = {}
    for part in (value or "").strip().split(";"):
        key, _, val = part.partition("=")
        if key and val:
            rule[key.strip().upper()] = val.strip().upper()
    return rule if rule.get("FREQ") else {}


# ---- 重复展开 ---------------------------------------------------------------


_WEEKDAY_NUM = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _weeks_apart(anchor: dt.date, day: dt.date) -> int:
    a_week = anchor - dt.timedelta(days=anchor.weekday())
    d_week = day - dt.timedelta(days=day.weekday())
    return (d_week - a_week).days // 7


def _month_days(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.date(year, month, 1)).days


def _shift_month(day: dt.date, months: int) -> dt.date:
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return dt.date(year, month, min(day.day, _month_days(year, month)))


def _occurrences(event: Event, first: dt.date, last: dt.date) -> list[dt.date]:
    """从 DTSTART 起一格一格走，开始那天落在 [first, last] 的都算。

    步数封顶 MAX_STEPS；COUNT 数到就停，UNTIL 过了就停。
    """
    start_day = _local_date(event.start)
    if not event.rrule:
        return [start_day] if first <= start_day <= last else []

    rule = event.rrule
    freq = rule["FREQ"]
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return []
    try:
        interval = max(1, int(rule.get("INTERVAL", "1")))
    except ValueError:
        interval = 1
    count: int | None = None
    if "COUNT" in rule:
        try:
            count = max(1, int(rule["COUNT"]))
        except ValueError:
            count = None
    until_day: dt.date | None = None
    if "UNTIL" in rule:
        until = _parse_when({}, rule["UNTIL"])
        if until is not None:
            until_day = _local_date(until)
    byday = [
        _WEEKDAY_NUM[piece.strip()]
        for piece in rule.get("BYDAY", "").split(",")
        if piece.strip() in _WEEKDAY_NUM
    ]

    out: list[dt.date] = []
    made = 0
    cursor = start_day
    steps = 0
    while cursor <= last and steps < MAX_STEPS:
        steps += 1
        if until_day is not None and cursor > until_day:
            break
        if freq == "DAILY":
            hit = True
        elif freq == "WEEKLY":
            if byday:
                hit = cursor.weekday() in byday and _weeks_apart(start_day, cursor) % interval == 0
            else:
                hit = _weeks_apart(start_day, cursor) % interval == 0
        elif freq == "MONTHLY":
            safe_day = min(start_day.day, _month_days(cursor.year, cursor.month))
            hit = cursor.day == safe_day and (
                (cursor.year - start_day.year) * 12 + (cursor.month - start_day.month)
            ) % interval == 0
        else:  # YEARLY
            safe_day = min(start_day.day, _month_days(cursor.year, cursor.month))
            hit = (
                cursor.month == start_day.month
                and cursor.day == safe_day
                and (cursor.year - start_day.year) % interval == 0
            )
        if hit:
            made += 1
            if count is not None and made > count:
                break
            if first <= cursor <= last and cursor.isoformat() not in event.exdates:
                out.append(cursor)
        # 下一格：周规则按天走才能捞到 BYDAY 的每一天，月/年规则跳整格
        if freq == "DAILY":
            cursor += dt.timedelta(days=interval)
        elif freq == "WEEKLY":
            cursor += dt.timedelta(days=1)
        elif freq == "MONTHLY":
            cursor = _shift_month(cursor, interval)
        else:
            year = cursor.year + interval
            cursor = dt.date(year, cursor.month, min(cursor.day, _month_days(year, cursor.month)))
    return out


def day_events(events: list[Event], day: dt.date) -> list[dict[str, str]]:
    """某一天的日程：全天的事排最前，剩下的按钟点。上限 MAX_EVENTS。

    命中两条路：重复展开算到今天，或者不重复但今天落在起止之间（跨天）。
    """
    out: list[dict[str, str]] = []
    for event in events:
        title = event.title.strip()
        if not title:
            continue  # 没标题的事没法说出口，不占名额
        start_day = _local_date(event.start)
        if not event.rrule and start_day > day:
            continue  # 还没来的单次事项，跟今天无关
        starts = _occurrences(event, day, day)
        if not starts and not event.spans(day):
            continue
        clock = _local_hhmm(event.start) if starts else ""
        out.append({"title": title, "start": clock})
        if len(out) >= MAX_EVENTS:
            break
    # 全天的没有钟点排最前，剩下的按钟点走
    out.sort(key=lambda item: (item["start"] != "", item["start"], item["title"]))
    return out


# ---- 拉取与缓存 -------------------------------------------------------------


def fetch_ics(url: str) -> str:
    """webcal:// 当 https:// 用；只拿文本，不碰别的协议。"""
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    req = urllib.request.Request(
        url, headers={"Accept": "text/calendar, text/*;q=0.9, */*;q=0.8"}
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
        return resp.read().decode("utf-8", errors="replace")


class CalendarFeed:
    """一份日历订阅：拉、存、按天答。线程安全（语音和早间同时问）。"""

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[str], str] | None = None,
        clock: Callable[[], float] | None = None,
        ttl: float = CACHE_TTL_SEC,
        backoff: float = FAIL_BACKOFF_SEC,
    ) -> None:
        self.url = (url or "").strip()
        self._fetch = fetch or fetch_ics
        self._clock = clock or time.monotonic
        self._ttl = ttl
        self._backoff = backoff
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._ok_at = -1e18
        self._fail_at = -1e18

    def _refresh(self, now: float) -> None:
        """到点就重新拉；刚失败过就先忍着，用旧账。"""
        if now - self._ok_at < self._ttl:
            return
        if now - self._fail_at < self._backoff:
            return
        try:
            self._events = parse_ics(self._fetch(self.url))
            self._ok_at = now
            self._fail_at = -1e18
        except Exception:  # noqa: BLE001 - 源挂了不吭声，记仇一会儿再来
            self._fail_at = now

    def events(self, day: dt.date) -> list[dict[str, str]]:
        if not self.url:
            return []
        with self._lock:
            self._refresh(self._clock())
            return day_events(self._events, day)


_FEEDS: dict[str, CalendarFeed] = {}
_FEEDS_LOCK = threading.Lock()


def feed_for(url: str) -> CalendarFeed:
    """同一份订阅共享一个 Feed——语音和早间各问各的，账只拉一份。"""
    with _FEEDS_LOCK:
        got = _FEEDS.get(url)
        if got is None:
            got = CalendarFeed(url)
            _FEEDS[url] = got
        return got
