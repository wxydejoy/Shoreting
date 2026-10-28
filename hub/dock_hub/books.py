"""睡前朗读：书架、拆章、书签、播放与定时。"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from dock_hub.companion import (
    _PUNCT_ONLY,
    chunk_read_text,
    estimate_read_seconds,
    pack_read_chunks,
)
from dock_hub.errors import HubError

TIMER_CHOICES = frozenset({0, 15, 30, 45, 60})
MAX_BOOK_BYTES = 32 * 1024 * 1024
_DEFAULT_PROGRESS = {"current_id": None, "timer_min": 30, "books": {}}


def _log(stage: str, **fields: Any) -> None:
    stamp = time.strftime("%H:%M:%S")
    parts = [f"latency {stamp} turn=story stage={stage}"]
    for key, value in fields.items():
        text = str(value).replace(" ", "_").replace("\n", "")
        parts.append(f"{key}={text}")
    print(" ".join(parts), flush=True)


def safe_book_id(name: str) -> str:
    text = str(name or "")
    if (
        not text
        or "/" in text
        or "\\" in text
        or text != Path(text).name
        or not text.endswith(".txt")
        or text == "progress.json"
        or ".." in text
    ):
        raise HubError("bad_request", "文件名不合法")
    return text


def load_progress(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"current_id": None, "timer_min": 30, "books": {}}
    if not isinstance(raw, dict):
        return {"current_id": None, "timer_min": 30, "books": {}}
    current = raw.get("current_id")
    if current is not None:
        current = str(current)
    try:
        timer = int(raw.get("timer_min", 30))
    except (TypeError, ValueError):
        timer = 30
    if timer not in TIMER_CHOICES:
        timer = 30
    books: dict[str, dict[str, int]] = {}
    incoming = raw.get("books")
    if isinstance(incoming, dict):
        for key, item in incoming.items():
            if not isinstance(item, dict):
                continue
            try:
                offset = int(item.get("offset", 0))
                updated = int(item.get("updated_at", 0))
            except (TypeError, ValueError):
                continue
            books[str(key)] = {"offset": offset, "updated_at": updated}
    return {"current_id": current, "timer_min": timer, "books": books}


@dataclass
class Chapter:
    index: int
    title: str
    volume: str
    start: int
    end: int
    text: str


@dataclass
class Document:
    encoding: str
    text: str
    chapters: list[Chapter]
    problem_label: str


def next_slice(text: str, offset: int, limit: int | None = None) -> tuple[int, str] | None:
    bound = len(text) if limit is None else min(len(text), max(0, limit))
    if offset >= bound:
        return None
    start = offset
    while start < bound and _PUNCT_ONLY.match(text[start]):
        start += 1
    if start >= bound:
        return None
    window = text[start:bound]
    packed = pack_read_chunks(chunk_read_text(window))
    if packed and window.startswith(packed[0]) and not _PUNCT_ONLY.match(packed[0]):
        return start, packed[0]
    for candidate in chunk_read_text(window):
        piece = candidate.strip()
        if piece and window.startswith(piece) and not _PUNCT_ONLY.match(piece):
            return start, piece
    return start, window


def split_document(raw: bytes, fallback_title: str, split_fn: Callable | None = None) -> Document:
    from zh_chapter_splitter import clean_text, decode_bytes, split_chapters_with_report

    text, encoding = decode_bytes(raw)
    cleaned = clean_text(text)
    if raw and not cleaned.strip():
        raise HubError("bad_request", "这本书读不出")
    report: dict[str, Any] = {}
    found: list[Any] | None
    try:
        fn = split_fn or split_chapters_with_report
        found, report = fn(cleaned)
        if not isinstance(report, dict):
            report = {}
    except Exception:
        found = None
        report = {}
    rows = [item for item in (found or []) if isinstance(item, dict)]
    if not rows:
        rows = [{"title": fallback_title, "content": cleaned, "volume_title": ""}]
    chapters: list[Chapter] = []
    cursor = 0
    for item in rows:
        content = str(item.get("content") or "")
        title = str(item.get("title") or "").strip() or fallback_title
        volume = str(item.get("volume_title") or "")
        chapters.append(
            Chapter(
                index=len(chapters),
                title=title,
                volume=volume,
                start=cursor,
                end=cursor + len(content),
                text=content,
            )
        )
        cursor += len(content)
    if len(chapters) == 1:
        lines = {line.strip() for line in cleaned.splitlines() if line.strip()}
        if chapters[0].title not in lines:
            chapters[0] = replace(chapters[0], title=fallback_title)
    return Document(
        encoding=encoding,
        text="".join(ch.text for ch in chapters),
        chapters=chapters,
        problem_label=str(report.get("problem_label") or ""),
    )


class _RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> int:
        return int(time.time())

    def wait(self, seconds: float, interrupt: threading.Event, deadline: float | None) -> str:
        end = time.monotonic() + max(0.0, seconds)
        while True:
            if interrupt.is_set():
                return "stopped"
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return "deadline"
            if now >= end:
                return "elapsed"
            interrupt.wait(min(0.05, max(0.0, end - now)))


class _NullSpeaker:
    def cue(self, text: str) -> bool:
        return True

    def stop(self) -> None:
        return None

    def cancel_short_reads(self) -> None:
        return None


class Books:
    def __init__(
        self,
        root: Path,
        speaker: Any = None,
        clock: Any = None,
        split_fn: Callable | None = None,
    ) -> None:
        self.root = Path(root)
        self.speaker = speaker or _NullSpeaker()
        self.clock = clock or _RealClock()
        self.split_fn = split_fn
        self._cache: dict[str, tuple[tuple[Any, ...], Document]] = {}
        self._lock = threading.RLock()
        self._interrupt = threading.Event()
        self._thread: threading.Thread | None = None
        self._playing = False
        self._book_id: str | None = None
        self._sentence = ""
        self._sentence_start = 0
        self._wait_seconds = 0
        self._stopped_by: str | None = None
        self._error: str | None = None
        self._deadline: float | None = None
        self._frozen: float | None = None
        self._jump: tuple[str, int] | None = None
        self._logged_at: tuple[str, int] | None = None
        self._ahead: tuple[str, int, str] | None = None

    def _progress_path(self) -> Path:
        return self.root / "progress.json"

    def _read_progress(self) -> dict[str, Any]:
        return load_progress(self._progress_path())

    def _write_progress(self, progress: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        on_disk = {path.name for path in self.root.glob("*.txt") if path.is_file()}
        books = {
            key: value
            for key, value in (progress.get("books") or {}).items()
            if key in on_disk
        }
        payload = {
            "current_id": progress.get("current_id"),
            "timer_min": progress.get("timer_min", 30),
            "books": books,
        }
        self._progress_path().write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_books(self) -> dict[str, Any]:
        progress = self._read_progress()
        items = []
        if self.root.is_dir():
            for path in sorted(self.root.glob("*.txt")):
                if not path.is_file():
                    continue
                stat = path.stat()
                items.append(
                    {
                        "id": path.name,
                        "title": path.name[: -len(".txt")],
                        "bytes": stat.st_size,
                        "mtime": int(stat.st_mtime),
                    }
                )
        current = progress.get("current_id")
        if current is not None and not any(item["id"] == current for item in items):
            current = None
        return {"items": items, "current_id": current, "timer_min": progress["timer_min"]}

    def save_upload(self, filename: str, data: bytes) -> dict[str, Any]:
        if len(data) > MAX_BOOK_BYTES:
            raise HubError("bad_request", "文件超过 32MB")
        book_id = safe_book_id(filename)
        self.root.mkdir(parents=True, exist_ok=True)
        target = (self.root / book_id).resolve()
        if self.root.resolve() not in target.parents:
            raise HubError("bad_request", "文件名不合法")
        target.write_bytes(data)
        self._cache.pop(book_id, None)
        progress = self._read_progress()
        progress["current_id"] = book_id
        self._write_progress(progress)
        _log("book_save", id=book_id, bytes=len(data))
        return {"id": book_id, "bytes": len(data)}

    def _file(self, book_id: str) -> tuple[str, Path]:
        book_id = safe_book_id(book_id)
        path = (self.root / book_id).resolve()
        root = self.root.resolve()
        if root != path.parent:
            raise HubError("bad_request", "文件名不合法")
        if not path.is_file():
            raise HubError("not_found", "没有这本书")
        return book_id, path

    def _document(self, book_id: str) -> Document:
        book_id, path = self._file(book_id)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        cached = self._cache.get(book_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        doc = split_document(path.read_bytes(), path.name[: -len(".txt")], self.split_fn)
        self._cache[book_id] = (key, doc)
        return doc

    def _bookmark(self, book_id: str, chars: int, *, persist: bool) -> int:
        progress = self._read_progress()
        item = (progress.get("books") or {}).get(book_id) or {}
        try:
            offset = int(item.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        if offset < 0:
            offset = 0
        if offset > chars:
            offset = chars
            if persist:
                books = dict(progress.get("books") or {})
                books[book_id] = {"offset": offset, "updated_at": int(item.get("updated_at") or 0)}
                progress["books"] = books
                self._write_progress(progress)
        return offset

    def open_book(self, book_id: str) -> dict[str, Any]:
        started = time.monotonic()
        book_id, _path = self._file(book_id)
        doc = self._document(book_id)
        bookmark = self._bookmark(book_id, len(doc.text), persist=True)
        _log(
            "book_open",
            id=book_id,
            chapters=len(doc.chapters),
            chars=len(doc.text),
            ms=max(0, int((time.monotonic() - started) * 1000)),
        )
        return {
            "id": book_id,
            "title": book_id[: -len(".txt")],
            "encoding": doc.encoding,
            "chars": len(doc.text),
            "bookmark": bookmark,
            "chapters": [
                {
                    "index": ch.index,
                    "title": ch.title,
                    "volume": ch.volume,
                    "start": ch.start,
                    "end": ch.end,
                }
                for ch in doc.chapters
            ],
            "problem_label": doc.problem_label,
        }

    def chapter(self, book_id: str, index: int) -> dict[str, Any]:
        book_id, _path = self._file(book_id)
        doc = self._document(book_id)
        if index < 0 or index >= len(doc.chapters):
            raise HubError("not_found", "没有这一章")
        ch = doc.chapters[index]
        return {
            "index": ch.index,
            "title": ch.title,
            "text": ch.text,
            "start": ch.start,
            "end": ch.end,
        }

    def set_root(self, root: Path) -> None:
        with self._lock:
            self.root = Path(root)
            self._cache.clear()

    def _persist_offset(self, book_id: str, offset: int) -> None:
        progress = self._read_progress()
        books = dict(progress.get("books") or {})
        books[book_id] = {"offset": offset, "updated_at": self.clock.wall()}
        progress["books"] = books
        progress["current_id"] = book_id
        self._write_progress(progress)

    def _chapter_at(self, doc: Document, offset: int) -> Chapter | None:
        if not doc.chapters:
            return None
        if offset >= len(doc.text):
            return doc.chapters[-1]
        for ch in doc.chapters:
            if ch.start <= offset < ch.end or (ch.end == ch.start and offset == ch.start):
                return ch
        return doc.chapters[-1]

    def _remaining_sec(self) -> int | None:
        if self._deadline is not None:
            return max(0, math.ceil(self._deadline - self.clock.monotonic()))
        if self._frozen is not None:
            return max(0, math.ceil(self._frozen))
        return None

    def story(self) -> dict[str, Any]:
        with self._lock:
            return self._story_locked()

    def _story_locked(self) -> dict[str, Any]:
        progress = self._read_progress()
        book_id = self._book_id or progress.get("current_id")
        doc: Document | None = None
        if book_id:
            try:
                doc = self._document(book_id)
            except HubError:
                doc = None
                book_id = None
        chars = len(doc.text) if doc else 0
        if self._playing and self._book_id:
            offset = self._sentence_start
        elif book_id and doc is not None:
            offset = self._bookmark(book_id, chars, persist=False)
        else:
            offset = 0
        chapter = self._chapter_at(doc, offset) if doc else None
        return {
            "playing": self._playing,
            "book_id": book_id,
            "offset": offset,
            "chapter": chapter.index if chapter else None,
            "chapter_title": chapter.title if chapter else None,
            "sentence": self._sentence or None,
            "sentence_start": self._sentence_start if self._sentence else None,
            "chars": chars,
            "chapters": len(doc.chapters) if doc else 0,
            "timer_min": progress["timer_min"],
            "timer_remaining_sec": self._remaining_sec(),
            "stopped_by": self._stopped_by,
            "error": self._error,
        }

    def _chapter_end(self, doc: Document, offset: int) -> int:
        for chapter in doc.chapters:
            if offset < chapter.end:
                return chapter.end
        return len(doc.text)

    def _next_readable(self, doc: Document, offset: int) -> tuple[int, str] | None:
        offset = max(0, offset)
        while offset < len(doc.text):
            nxt = next_slice(doc.text, offset, self._chapter_end(doc, offset))
            if nxt is not None:
                return nxt
            later = next((chapter.start for chapter in doc.chapters if chapter.start > offset), None)
            if later is None:
                return None
            offset = later
        return None

    def _voice_rate(self) -> float:
        getter = getattr(self, "voice_speed", None)
        if not callable(getter):
            return 1.0
        try:
            speed = float(getter())
        except (TypeError, ValueError):
            return 1.0
        if speed <= 0:
            return 1.0
        return min(2.0, max(0.5, speed))

    def _show_sentence(self, book_id: str, start: int, piece: str) -> None:
        doc = self._document(book_id)
        self._book_id = book_id
        self._sentence_start = start
        self._sentence = piece
        self._wait_seconds = estimate_read_seconds(piece) / self._voice_rate()
        self._persist_offset(book_id, start)
        chapter = self._chapter_at(doc, start)
        mark = (book_id, chapter.index if chapter else -1)
        if mark != self._logged_at:
            self._logged_at = mark
            _log(
                "story_chapter",
                id=book_id,
                chapter=chapter.index + 1 if chapter else 0,
                title=(chapter.title if chapter else "")[:40],
            )

    def _prepare(self, book_id: str, offset: int) -> str | None:
        doc = self._document(book_id)
        nxt = self._next_readable(doc, offset)
        if nxt is None:
            return None
        start, piece = nxt
        self._show_sentence(book_id, start, piece)
        return piece

    def _prefetch_locked(self) -> None:
        """这一句还在播时，把下一句先送进喇叭队列，避免每句开头空等合成。"""
        book_id = self._book_id or ""
        doc = self._document(book_id)
        nxt = self._next_readable(doc, self._sentence_start + len(self._sentence))
        if nxt is None:
            self._ahead = None
            return
        start, piece = nxt
        if not self.speaker.cue(piece):
            self._ahead = None
            return
        self._ahead = (book_id, start, piece)
        _log("story_prefetch", id=book_id, offset=start, chars=len(piece))

    def _finish_end(self, book_id: str) -> None:
        doc = self._document(book_id)
        self._persist_offset(book_id, len(doc.text))
        self._playing = False
        self._stopped_by = "end"
        _log("story_end", id=book_id, chars=len(doc.text))
        self._error = None
        self._sentence = ""
        self._deadline = None
        self._frozen = None
        self._ahead = None

    def _finish_error(self) -> None:
        self._playing = False
        self._ahead = None
        self._stopped_by = "error"
        self._error = "还没接上嘴巴"
        _log("story_error", id=self._book_id or "-", offset=self._sentence_start)

    def _finish_timer(self) -> None:
        self._playing = False
        self._stopped_by = "timer"
        self._error = None
        self._deadline = None
        self._frozen = None
        self._ahead = None
        _log("story_timer_done", id=self._book_id or "-", offset=self._sentence_start)
        self.speaker.stop()

    def _cue_prepared(self, piece: str) -> bool:
        if self.speaker.cue(piece):
            return True
        self._finish_error()
        return False

    def play(self, book_id: str, offset: int | None = None) -> dict[str, Any]:
        book_id, _path = self._file(book_id)
        doc = self._document(book_id)
        chars = len(doc.text)
        if offset is None:
            offset = self._bookmark(book_id, chars, persist=False)
        elif offset < 0:
            offset = 0
        elif offset > chars:
            offset = chars
        if offset >= chars:
            offset = 0
        if self._next_readable(doc, offset) is None:
            raise HubError("bad_request", "没有可以读的字")
        self.speaker.cancel_short_reads()
        with self._lock:
            if self._playing:
                self._jump = (book_id, offset)
                self._interrupt.set()
                self.speaker.stop()
                _log("story_jump", id=book_id, offset=offset)
                return self._story_locked()
            piece = self._prepare(book_id, offset)
            if piece is None:
                raise HubError("bad_request", "没有可以读的字")
            if not self._cue_prepared(piece):
                return self._story_locked()
            _log("story_play", id=book_id, offset=self._sentence_start, chars=len(piece))
            self._playing = True
            self._stopped_by = None
            self._error = None
            self._interrupt.clear()
            self._arm_start()
            self._prefetch_locked()
            self._thread = threading.Thread(target=self._loop, name="bedtime", daemon=True)
            self._thread.start()
            return self._story_locked()

    def _arm_start(self) -> None:
        progress = self._read_progress()
        minutes = int(progress["timer_min"])
        if self._frozen is not None:
            self._deadline = self.clock.monotonic() + self._frozen
            self._frozen = None
        elif minutes > 0 and self._deadline is None:
            self._deadline = self.clock.monotonic() + minutes * 60

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if not self._playing:
                return self._story_locked()
            paused_id = self._book_id or "-"
            paused_at = self._sentence_start
            self._stopped_by = "user"
            self._playing = False
            _log("story_pause", id=paused_id, offset=paused_at)
            self._jump = None
            self._ahead = None
            if self._deadline is not None:
                self._frozen = max(0.0, self._deadline - self.clock.monotonic())
                self._deadline = None
            thread = self._thread
            self._interrupt.set()
        self.speaker.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        return self.story()

    def bookmark(self, book_id: str, offset: int) -> dict[str, Any]:
        book_id, _path = self._file(book_id)
        with self._lock:
            if self._playing:
                raise HubError("bad_request", "正在读，用播放接口跳转")
            doc = self._document(book_id)
            chars = len(doc.text)
            if offset < 0:
                offset = 0
            if offset > chars:
                offset = chars
            self._sentence = ""
            self._sentence_start = offset
            self._book_id = book_id
            self._stopped_by = None
            self._error = None
            self._persist_offset(book_id, offset)
            _log("story_bookmark", id=book_id, offset=offset)
            return self._story_locked()

    def set_timer(self, minutes: int) -> dict[str, Any]:
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            minutes = -1
        if minutes not in TIMER_CHOICES:
            raise HubError("bad_request", "定时只能是关、15、30、45 或 60 分钟")
        with self._lock:
            progress = self._read_progress()
            progress["timer_min"] = minutes
            self._write_progress(progress)
            _log("story_timer", minutes=minutes)
            if minutes == 0:
                self._deadline = None
                self._frozen = None
            elif self._playing:
                self._deadline = self.clock.monotonic() + minutes * 60
                self._frozen = None
            elif self._frozen is not None or self._stopped_by == "user":
                self._frozen = float(minutes * 60)
            return self._story_locked()

    def _loop(self) -> None:
        while True:
            seconds = self._wait_seconds
            deadline = self._deadline
            why = self.clock.wait(seconds, self._interrupt, deadline)
            with self._lock:
                jump = self._jump
                self._jump = None
                if jump is not None:
                    self._interrupt.clear()
                    self._ahead = None
                    book_id, offset = jump
                    piece = self._prepare(book_id, offset)
                    if piece is None:
                        self._finish_end(book_id)
                        return
                    if not self._cue_prepared(piece):
                        return
                    self._prefetch_locked()
                    continue
                if why == "stopped" or not self._playing:
                    self._playing = False
                    self._ahead = None
                    return
                if why == "deadline" or (
                    self._deadline is not None and self.clock.monotonic() >= self._deadline
                ):
                    self._finish_timer()
                    return
                book_id = self._book_id or ""
                if self._deadline is not None and self.clock.monotonic() >= self._deadline:
                    self._finish_timer()
                    return
                ahead = self._ahead
                self._ahead = None
                if ahead is None or ahead[0] != book_id:
                    cursor = self._sentence_start + len(self._sentence)
                    piece = self._prepare(book_id, cursor)
                    if piece is None:
                        self._finish_end(book_id)
                        return
                    if not self._cue_prepared(piece):
                        return
                else:
                    _, start, piece = ahead
                    self._show_sentence(book_id, start, piece)
                self._prefetch_locked()
