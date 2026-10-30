"""F29 守岸人日记：每天傍晚写一段今天，可回看。

设计取向（features.md F29）：
- 一天一段，写在 hub.yaml 同目录的 `companion-diary.jsonl`，一行一天；
- 材料只来自她已经有的东西：台账（在桌/朗读/说了几句）、今天是什么日子、
  今天的壁纸句子、以及当天他自己说过的话的片段——不新采任何数据；
- 正文由 LLM 用她的口吻写，写不出（大脑不在）就退到一句干巴巴的事实——
  日记缺一天可以，但缺的那天不该是因为偷懒；
- 可回看也可删（/diary 页）。和记忆一样，她写的东西必须可见可撤销。

规矩：两句话以内、不报菜名、不煽情。数据为空的日子不写——
空日记是在提醒「你没用我」，没意思（口径同周报）。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

KEEP_DAYS = 180
MAX_TEXT = 400
SAID_LIMIT = 6
SAID_MAX_CHARS = 40
DUE_WINDOW = timedelta(hours=2)
"""到点后两小时内都还值得补写，再晚这一天就跳过——丢弃，不补发。"""

_AT_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

_WEEKDAY = ("", "周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass
class DayMaterial:
    """一天的材料。渲染进提示词，也为兜底文案服务。"""

    day: str
    weekday: str
    desk_min: int | None = None
    read_min: int | None = None
    chats: int | None = None
    boots: int | None = None
    last_at: str | None = None
    occasions: list[str] = field(default_factory=list)
    wallpaper: str | None = None
    said: list[str] = field(default_factory=list)

    def worth_writing(self) -> bool:
        """数据为空（机器没开过、一句没说）就不写，口径同周报。"""
        return any(
            value
            for value in (self.desk_min, self.read_min, self.chats, self.boots)
        ) or bool(self.said)


def build_material(
    *,
    now: datetime,
    ledger_row: dict[str, Any] | None = None,
    occasions: list[str] | None = None,
    wallpaper: str | None = None,
    said: list[str] | None = None,
) -> DayMaterial:
    """把各处来的原料收成一天的材料。纯拼装，不落盘。"""
    row = ledger_row or {}
    picked: list[str] = []
    seen: set[str] = set()
    for line in said or []:
        text = (line or "").strip().replace("\n", " ")
        if not (1 < len(text) <= SAID_MAX_CHARS) or text in seen:
            continue
        seen.add(text)
        picked.append(text)
        if len(picked) >= SAID_LIMIT:
            break
    return DayMaterial(
        day=now.astimezone().date().isoformat(),
        weekday=_WEEKDAY[now.astimezone().isoweekday()],
        desk_min=round(int(row["desk_sec"]) / 60) if row.get("desk_sec") else None,
        read_min=round(int(row["read_sec"]) / 60) if row.get("read_sec") else None,
        chats=int(row["chats"]) if row.get("chats") else None,
        boots=int(row["boots"]) if row.get("boots") else None,
        last_at=str(row.get("last_at") or "") or None,
        occasions=[str(item) for item in (occasions or []) if str(item).strip()],
        wallpaper=(wallpaper or "").strip() or None,
        said=picked,
    )


def render_material(m: DayMaterial) -> str:
    """材料渲染成提示词里的大白话。数字她看得懂就行，不用漂亮。"""
    lines = [f"日期：{m.day} {m.weekday}"]
    if m.desk_min:
        lines.append(f"在桌：约 {m.desk_min} 分钟")
    if m.read_min:
        lines.append(f"朗读：{m.read_min} 分钟")
    if m.chats:
        lines.append(f"说了 {m.chats} 句话")
    if m.boots:
        lines.append(f"开了 {m.boots} 次机")
    if m.last_at:
        lines.append(f"最晚忙到 {m.last_at}")
    if m.occasions:
        lines.append("今天特别：" + "、".join(m.occasions))
    if m.wallpaper:
        lines.append(f"今天桌面上她留的那句：「{m.wallpaper}」")
    if m.said:
        lines.append("今天他说过的几句话（片段）：")
        lines.extend(f"- {line}" for line in m.said)
    return "\n".join(lines)


DIARY_PROMPT = """你在给自己的日记补今天这一段。你是守岸人：慢、短、轻、静，偏诗意，不卖萌，不煽情，不用感叹号，不喊口号。
两句话以内，最多六十字。材料给了什么写什么，数字不许改，材料里没有的事不许编。
不称呼开头，不署名，不写日期。像收工前自言自语一句，不是汇报。"""


def diary_messages(material: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": DIARY_PROMPT},
        {"role": "user", "content": material},
    ]


def fallback_text(m: DayMaterial) -> str:
    """大脑不在场时的兜底：一句干巴巴的事实。没什么可说就空着，别硬凑。"""
    parts: list[str] = []
    if m.desk_min:
        hours, minutes = m.desk_min // 60, m.desk_min % 60
        if hours:
            parts.append(f"今天桌前坐了 {hours} 小时" + (f" {minutes} 分" if minutes else ""))
        else:
            parts.append(f"今天桌前坐了 {minutes} 分钟")
    if m.read_min:
        parts.append(f"读了 {m.read_min} 分钟书")
    if m.chats:
        parts.append(f"聊了 {m.chats} 句")
    if not parts:
        return ""
    return "，".join(parts) + "。"


def clean_entry(raw: str) -> str:
    """她写回来的正文清洗：去思考块残留、引号包裹、换行，截到上限。"""
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE)
    text = text.strip().strip("\"'「」『』“”‘’")
    text = re.sub(r"\s*\n+\s*", " ", text).strip()
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1] + "…"
    return text


def diary_due(now: datetime, at: str, done_day: str | None) -> str | None:
    """今天该写了吗。返回该写的那天（ISO），不该写返回 None。

    到点前不算；到点后两小时内都算（Hub 起晚了也能补上）；写过就算完。
    """
    if done_day == now.astimezone().date().isoformat():
        return None
    match = _AT_RE.match(at or "")
    if not match:
        return None
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        hours=int(match.group(1)), minutes=int(match.group(2))
    )
    if now < start or now - start >= DUE_WINDOW:
        return None
    return now.astimezone().date().isoformat()


class DiaryStore:
    """companion-diary.jsonl 的读写。线程安全（写作线程与页面都会碰）。"""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            entry = self._parse(line)
            if entry is not None:
                self._entries[entry["date"]] = entry
        self._prune()

    @staticmethod
    def _parse(line: str) -> dict[str, Any] | None:
        line = line.strip()
        if not line:
            return None
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            return None
        day = str(row.get("date") or "")
        text = str(row.get("text") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not text:
            return None
        return {
            "date": day,
            "text": text[:MAX_TEXT],
            "source": str(row.get("source") or "llm"),
            "written_at": str(row.get("written_at") or ""),
        }

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(self._entries[day], ensure_ascii=False, separators=(",", ":"))
            for day in sorted(self._entries)
        ]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def _prune(self) -> None:
        if not self._entries:
            return
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        for day in [day for day in self._entries if day < cutoff]:
            self._entries.pop(day, None)

    def write(self, day: str, text: str, source: str, written_at: str) -> None:
        text = text.strip()[:MAX_TEXT]
        if not text:
            return
        with self._lock:
            self._entries[day] = {
                "date": day,
                "text": text,
                "source": source,
                "written_at": written_at,
            }
            self._prune()
            self._save()

    def get(self, day: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(day)
            return dict(entry) if entry else None

    def list(self, limit: int = 200, day: str = "") -> list[dict[str, Any]]:
        """新→旧。day 给了就只看那天；limit 折回 1..200。"""
        with self._lock:
            items = [dict(value) for key, value in sorted(self._entries.items(), reverse=True)]
        if day:
            items = [item for item in items if item["date"] == day]
        return items[: max(1, min(int(limit), 200))]

    def delete(self, day: str) -> bool:
        with self._lock:
            if day not in self._entries:
                return False
            self._entries.pop(day)
            self._save()
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class DiaryWriter:
    """到点写日记的人。每半分钟被 tick 一次，写没写过自己记着。

    材料与成文都从外面注入：material() 给一天的材料，compose() 拿材料换
    正文（大脑不在就退兜底）。空材料不写，但当天也算写过了——别每半分钟
    重算一遍空账。
    """

    def __init__(
        self,
        store: DiaryStore,
        material: Callable[[], DayMaterial | None],
        compose: Callable[[DayMaterial], str],
    ) -> None:
        self.store = store
        self._material = material
        self._compose = compose
        self.enabled = True
        self.at = "21:30"
        self._done_day: str | None = None

    def configure(self, enabled: bool, at: str) -> None:
        self.enabled = enabled
        self.at = at

    def tick(self, now: datetime | None = None) -> str | None:
        """该写就写，返回写下的正文；不写返回 None（带原因可打日志）。"""
        if not self.enabled:
            return None
        now = now or datetime.now().astimezone()
        day = diary_due(now, self.at, self._done_day)
        if day is None:
            return None
        self._done_day = day  # 无论写成什么样，今天这一趟到此为止
        m = self._material()
        if m is None or not m.worth_writing():
            return None
        try:
            text = clean_entry(self._compose(m))
        except Exception:  # noqa: BLE001 - 大脑挂了不连累兜底
            text = ""
        source = "llm"
        if not text:
            text = fallback_text(m)
            source = "log"
        if not text:
            return None
        self.store.write(day, text, source, now.isoformat(timespec="seconds"))
        return text
