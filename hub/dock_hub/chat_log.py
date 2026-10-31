"""守岸人对话原文。jsonl 写在 hub.yaml 同目录，不进 git。"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from dock_hub.assets import read_asset

MAX_TEXT = 4000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RETURN = 500
TAIL_BLOCK = 64 * 1024


def chat_log_path(config_path: Path | None) -> Path | None:
    if config_path is None:
        return None
    return config_path.with_name("companion-chats.jsonl")


class ChatLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(
        self,
        *,
        turn: str,
        user: str,
        reply: str,
        ms: int = 0,
        skills: list[dict[str, Any]] | None = None,
        error: str | None = None,
        speaker: str = "",
    ) -> None:
        if self.path is None:
            return
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "turn": str(turn or ""),
            "user": _clip(user),
            "reply": _clip(reply),
            "ms": max(0, int(ms)),
            "skills": list(skills or []),
            "error": _clip(error) if error else None,
            # F5 声纹：谁在说话（安卓认出来的），没认出来就不占这个字段
            "speaker": _clip(speaker) if speaker else None,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self._trim_unlocked()

    def list(self, *, limit: int = 200, query: str = "", day: str = "") -> list[dict[str, Any]]:
        if self.path is None or not self.path.is_file():
            return []
        cap = min(max(1, int(limit)), MAX_RETURN)
        needle = query.strip().lower()
        day = day.strip()[:10]
        with self._lock:
            raw = (
                self.path.read_text(encoding="utf-8", errors="replace")
                if needle
                else self._tail_text(cap)
            )
        items: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if day and str(obj.get("ts") or "")[:10] != day:
                continue
            if needle and needle not in f"{obj.get('user') or ''} {obj.get('reply') or ''}".lower():
                continue
            items.append(obj)
        return items[-cap:]

    def _tail_text(self, cap: int) -> str:
        """只看文件尾部：记录攒到 8MB 也不必为最后 200 条把全文读进来。"""

        assert self.path is not None
        size = self.path.stat().st_size
        if size <= TAIL_BLOCK:
            return self.path.read_text(encoding="utf-8", errors="replace")
        chunks: list[bytes] = []
        pos = size
        breaks = 0
        with self.path.open("rb") as handle:
            while pos > 0:
                step = min(TAIL_BLOCK, pos)
                pos -= step
                handle.seek(pos)
                block = handle.read(step)
                chunks.append(block)
                breaks += block.count(b"\n")
                # 末行可能没换行，多要一行兜底
                if breaks > cap:
                    break
        raw = b"".join(reversed(chunks))
        if pos > 0:
            cut = raw.find(b"\n")
            if cut >= 0:
                raw = raw[cut + 1 :]
        return raw.decode("utf-8", errors="replace")

    def days(self) -> list[str]:
        """记过话的日子（YYYY-MM-DD，旧→新）。时间线一天一档，翻旧账用。"""
        if self.path is None or not self.path.is_file():
            return []
        with self._lock:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        seen: set[str] = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                ts = str(obj.get("ts") or "")
                if len(ts) >= 10:
                    seen.add(ts[:10])
        return sorted(seen)

    def _trim_unlocked(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        if self.path.stat().st_size <= MAX_FILE_BYTES:
            return
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        keep = lines[len(lines) // 5 :]
        self.path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def _clip(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) <= MAX_TEXT:
        return text
    return text[: MAX_TEXT - 1] + "…"


def asset_page_html(name: str) -> bytes:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
        if bundled.is_file():
            return read_asset(bundled)
    return read_asset(Path(__file__).resolve().parent / "assets" / name)


def chats_page_html() -> bytes:
    """F30 时间线版对话页：独立小页（gate + ‹ › 翻天），不再走 app.html 三栏。"""
    return asset_page_html("chats.html")


def read_page_html() -> bytes:
    return asset_page_html("app.html")


def week_page_html() -> bytes:
    """F27 桌面周报页（独立小页，不走 app.html 那套三栏）。"""
    return asset_page_html("week.html")


def diary_page_html() -> bytes:
    """F29 守岸人日记页（独立小页，一天一段，可删）。"""
    return asset_page_html("diary.html")
