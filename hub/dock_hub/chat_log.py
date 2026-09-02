"""守岸人对话原文。jsonl 写在 hub.yaml 同目录，不进 git。"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_TEXT = 4000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RETURN = 500


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
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self._trim_unlocked()

    def list(self, *, limit: int = 200, query: str = "") -> list[dict[str, Any]]:
        if self.path is None or not self.path.is_file():
            return []
        cap = min(max(1, int(limit)), MAX_RETURN)
        needle = query.strip().lower()
        with self._lock:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
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
            if needle and needle not in f"{obj.get('user') or ''} {obj.get('reply') or ''}".lower():
                continue
            items.append(obj)
        return items[-cap:]

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


def chats_page_html() -> bytes:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "chats.html"  # type: ignore[attr-defined]
        if bundled.is_file():
            return bundled.read_bytes()
    return (Path(__file__).resolve().parent / "assets" / "chats.html").read_bytes()
