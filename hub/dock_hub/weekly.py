"""F27 桌面周报：这周你在桌前多久、读了多少、最晚到几点、说了多少句、开了几次机。

陪伴需要被看见，否则半年后你会忘记它一直在。数据不新采——对话、朗读、在桌、
开关机本来就有边沿事件，这里只是把它们按天记进一份 jsonl（hub.yaml 同目录，
`companion-stats.jsonl`），一行一天，周日晚上由守岸人自己总结一句。

五个数字对应 features.md F27 的五张卡：在桌时长、朗读时长、最晚一次、
说了多少句话、开了多少次机。
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# 在桌时长按「在场边沿」积分；进程崩了最多丢一个区间，所以这里给单区间
# 设上限：状态卡死（比如安卓断连没发离座）最多虚记两小时，不会记出一整天。
DESK_INTERVAL_CAP_SEC = 2 * 3600
KEEP_DAYS = 90


def _day_key(now: datetime) -> str:
    return now.astimezone().date().isoformat()


def _clock(now: datetime) -> str:
    return now.astimezone().strftime("%H:%M")


def load_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    """读历史台账：{date: row}。坏行跳过，不抛——统计坏了不该连累 Hub 启动。"""
    rows: dict[str, dict[str, Any]] = {}
    if path is None or not path.is_file():
        return rows
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("date"), str):
            rows[row["date"]] = row
    return rows


def week_window(today: date) -> list[date]:
    """最近 7 天（含今天），旧→新。周报说「这周」按滚动 7 天算，不按自然周切。"""
    return [today - timedelta(days=offset) for offset in range(6, -1, -1)]


def summarize(rows: dict[str, dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    """聚成周报：7 天逐日 + 合计 + 和上一个 7 天的差。

    纯函数：rows 随便来（测试喂假数据），落不落盘不归它管。
    """
    today = today or date.today()
    days = week_window(today)
    prev_days = [days[0] - timedelta(days=offset) for offset in range(7, 0, -1)]

    def collect(keys: list[date]) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        fields = ("desk_sec", "read_sec", "chats", "boots")
        total = {name: 0 for name in fields}
        rows_out: list[dict[str, Any]] = []
        latest: str | None = None
        for key in keys:
            row = rows.get(key.isoformat()) or {}
            for name in fields:
                value = row.get(name)
                total[name] += int(value) if isinstance(value, (int, float)) else 0
            last_at = str(row.get("last_at") or "") or None
            if last_at:
                latest = max(latest, last_at) if latest else last_at
            rows_out.append(
                {
                    "date": key.isoformat(),
                    "weekday": key.isoweekday(),
                    "desk_sec": int(row.get("desk_sec") or 0),
                    "read_sec": int(row.get("read_sec") or 0),
                    "chats": int(row.get("chats") or 0),
                    "boots": int(row.get("boots") or 0),
                    "last_at": last_at,
                }
            )
        return total, rows_out, latest

    total, rows_out, latest = collect(days)
    prev_total, _prev_rows, _ = collect(prev_days)
    delta = {name: total[name] - prev_total[name] for name in total}
    return {
        "days": rows_out,
        "total": total,
        "delta": delta,
        "latest_at": latest,
    }


def weekly_line(summary: dict[str, Any]) -> str:
    """周日晚上她说的一句。克制，报数，不许煽情。"""
    total = summary.get("total") or {}
    delta = summary.get("delta") or {}
    desk_min = round(int(total.get("desk_sec") or 0) / 60)
    desk_hours = desk_min / 60.0
    chats = int(total.get("chats") or 0)
    read_min = round(int(total.get("read_sec") or 0) / 60)
    latest = str(summary.get("latest_at") or "")
    if desk_min <= 0 and chats <= 0 and read_min <= 0:
        # 一周没开过机就不念了——数据为空的周报是在提醒「你没用我」，没意思
        return ""
    parts: list[str] = []
    if desk_min > 0:
        if desk_hours >= 1:
            parts.append(f"这周你在桌前坐了 {desk_hours:.0f} 个小时")
        else:
            parts.append(f"这周你在桌前坐了 {desk_min} 分钟")
        prev = int(delta.get("desk_sec") or 0)
        if abs(prev) >= 30 * 60:
            word = "多" if prev > 0 else "少"
            parts.append(f"比上周{word} {abs(prev) // 3600} 个小时" if abs(prev) >= 3600 else f"比上周{word} {abs(prev) // 60} 分钟")
    if read_min > 0:
        parts.append(f"读了 {read_min} 分钟书")
    if chats > 0:
        parts.append(f"我们说了 {chats} 句话")
    if latest:
        parts.append(f"最晚一次到 {latest}")
    if not parts:
        return ""
    text = "，".join(parts) + "。"
    return text


class DailyLedger:
    """companion-stats.jsonl 的记账人。线程安全（对话线程/后台循环都会写）。"""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows = load_rows(self.path)
        self._desk_since: datetime | None = None
        # 老台账在加载时就剪一次，别等「新建今天行」才想起来
        self._prune(datetime.now().astimezone())

    # ---- 记账 -----------------------------------------------------------

    def note_chat(self, now: datetime | None = None) -> None:
        now = now or datetime.now().astimezone()
        with self._lock:
            row = self._row_for(now)
            row["chats"] = int(row.get("chats") or 0) + 1
            self._touch(row, now)

    def note_read(self, seconds: float, now: datetime | None = None) -> None:
        if seconds <= 0:
            return
        now = now or datetime.now().astimezone()
        with self._lock:
            row = self._row_for(now)
            row["read_sec"] = int(row.get("read_sec") or 0) + int(seconds)
            self._touch(row, now)

    def note_boot(self, now: datetime | None = None) -> None:
        now = now or datetime.now().astimezone()
        with self._lock:
            row = self._row_for(now)
            row["boots"] = int(row.get("boots") or 0) + 1
            self._touch(row, now)

    def note_desk(self, present: bool, now: datetime | None = None) -> None:
        """在桌边沿：坐下开区间，离座结算。误报由 tick 的上限兜住。"""
        now = now or datetime.now().astimezone()
        with self._lock:
            if present:
                if self._desk_since is None:
                    self._desk_since = now
                self._touch(self._row_for(now), now)
                return
            self._settle_desk(now)

    def tick(self, now: datetime | None = None) -> None:
        """后台循环每分钟调一次：在桌区间到上限就结算重开，防状态卡死虚记。"""
        now = now or datetime.now().astimezone()
        with self._lock:
            since = self._desk_since
            if since is None:
                return
            if (now - since).total_seconds() >= DESK_INTERVAL_CAP_SEC:
                # 结算上限这一段（cap=True 恰好记 CAP 秒），人可能还在，重开一段
                self._settle_desk(now)
                self._desk_since = now

    # ---- 读 -------------------------------------------------------------

    def summary(self, today: date | None = None) -> dict[str, Any]:
        """周报聚合。只读：当前开着的在桌区间虚拟计入，不动台账。"""
        today = today or date.today()
        with self._lock:
            rows = {key: dict(value) for key, value in self._rows.items()}
            since = self._desk_since
            if since is not None:
                elapsed = min(
                    max((datetime.now().astimezone() - since).total_seconds(), 0.0),
                    DESK_INTERVAL_CAP_SEC,
                )
                key = today.isoformat()
                row = rows.setdefault(key, {"date": key})
                row["desk_sec"] = int(row.get("desk_sec") or 0) + int(elapsed)
        return summarize(rows, today)

    # ---- 内部 -----------------------------------------------------------

    def _row_for(self, now: datetime) -> dict[str, Any]:
        key = _day_key(now)
        row = self._rows.get(key)
        if row is None:
            row = {"date": key}
            self._rows[key] = row
            self._prune(now)
        return row

    def _touch(self, row: dict[str, Any], now: datetime) -> None:
        clock = _clock(now)
        row["last_at"] = clock
        first = str(row.get("first_at") or "")
        if not first or clock < first:
            row["first_at"] = clock
        self._save()

    def _settle_desk(self, now: datetime) -> None:
        since = self._desk_since
        if since is None:
            return
        elapsed = min(max((now - since).total_seconds(), 0.0), DESK_INTERVAL_CAP_SEC)
        if elapsed <= 0:
            return
        row = self._row_for(now)
        row["desk_sec"] = int(row.get("desk_sec") or 0) + int(elapsed)
        self._touch(row, now)
        self._desk_since = None

    def _prune(self, now: datetime) -> None:
        cutoff = (now.astimezone().date() - timedelta(days=KEEP_DAYS)).isoformat()
        for key in [key for key in self._rows if key < cutoff]:
            self._rows.pop(key, None)

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(self._rows[key], ensure_ascii=False, separators=(",", ":"))
            for key in sorted(self._rows)
        ]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
