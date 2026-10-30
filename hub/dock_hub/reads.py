"""朗读任务：排队、工作线程、磁盘元数据。

从 companion.py 搬出来的一段。这里只管「一篇稿子怎么念完」：排队、逐块推进、
合成好的 wav 落到 reads/ 目录、内存里留一份任务表给朗读页看。
出声、打断、字幕状态仍归 Companion——它们通过 host 上的几个回调接进来，
所以这个文件不需要知道 TTS 地址、锁和序号怎么维护。

host 要提供（tests/test_reads.py 里有个 FakeHost 示范最小实现）：

    stop_playback()                    叫停喇叭当前这句
    cue_sentence(text, ident, seq)     边合成边念一句；False 表示已被打断
    synthesize(text, ident)            只合成、不播，返回 wav；None 表示失败
    next_seq()                         开一轮新的朗读，顺带作废旧的序号
    set_speaking(flag)                 主屏字幕的「她正在说」
    keep_clip(ident, wav)              短时缓存的 wav（重播走这里）
    drop_clip(ident)                   删任务时顺手清缓存
    note(turn, stage, **fields)        打点（实现里就是 _latency）
"""

from __future__ import annotations

import json
import threading
import time
import wave
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Protocol

from dock_hub.config import ID_RE
from dock_hub.errors import HubError
from dock_hub.speech import estimate_read_seconds, join_wavs, wav_duration_sec

MAX_READ_JOBS = 24
"""内存里最多留这么多个任务；再多就从最旧的丢（磁盘上的 wav 还在，会从目录里捞回来）。"""


class ReadHost(Protocol):
    def stop_playback(self) -> None: ...

    def cue_sentence(self, text: str, ident: str, seq: int) -> bool: ...

    def synthesize(self, text: str, ident: str) -> bytes | None: ...

    def next_seq(self) -> int: ...

    def still_current(self, seq: int) -> bool: ...

    def set_speaking(self, speaking: bool) -> None: ...

    def keep_clip(self, ident: str, wav: bytes) -> None: ...

    def drop_clip(self, ident: str) -> None: ...

    def note(self, turn: str, stage: str, **fields: Any) -> None: ...


def job_public(job: dict[str, Any]) -> dict[str, Any]:
    """任务的外形：把内部字段（_text / _chunks）挡在外面。"""
    return {
        "id": job.get("id"),
        "job_id": job.get("id"),
        "mode": job.get("mode"),
        "status": job.get("status"),
        "chars": job.get("chars"),
        "estimate_sec": job.get("estimate_sec"),
        "chunks": job.get("chunks"),
        "done": job.get("done"),
        "preview": job.get("preview"),
        "audio_id": job.get("audio_id"),
        "duration_sec": job.get("duration_sec"),
        "bytes": job.get("bytes"),
        "filename": job.get("filename"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "format": "audio/wav",
    }


def preview_of(text: str) -> str:
    preview = "".join(text.split())
    if len(preview) > 36:
        preview = preview[:35] + "…"
    return preview


class ReadJobs:
    def __init__(self, host: ReadHost) -> None:
        self.host = host
        self.dir: Path | None = None
        self.jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._cancel: set[str] = set()

    def set_dir(self, path: str | Path | None) -> None:
        self.dir = Path(path) if path else None

    # ---- 对外 ---------------------------------------------------------------

    def submit(
        self,
        ident: str,
        *,
        mode: str,
        text: str,
        chars: int,
        estimate: int,
        pieces: list[str],
    ) -> dict[str, Any]:
        job = self._remember(
            ident,
            mode=mode,
            text=text,
            chars=chars,
            estimate=estimate,
            chunks=len(pieces),
            status="queued",
            done=0,
            pieces=pieces,
        )
        self._ensure_worker()
        with self._cv:
            self._queue.append(ident)
            self._cv.notify()
        return job

    def list(self) -> dict[str, Any]:
        self._merge_disk()
        with self._lock:
            items = [job_public(job) for job in self.jobs.values()]
        items.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
        return {"items": items}

    def cancel_all(self) -> None:
        """开始新一轮朗读 / 故事之前，把排队和正在跑的统统作废。"""
        with self._lock:
            for ident, job in self.jobs.items():
                if job.get("status") in {"queued", "running"}:
                    job["status"] = "cancelled"
                    job["error"] = None
                    job.pop("_chunks", None)
                    self._cancel.add(ident)
            self._queue.clear()

    def cancel(self, ident: str) -> dict[str, Any]:
        running = False
        with self._lock:
            job = self.jobs.get(ident)
            if job is None:
                raise HubError("not_found", "没有这个任务")
            if job.get("status") in {"done", "error", "cancelled"}:
                return job_public(job)
            job["status"] = "cancelled"
            job["error"] = None
            job.pop("_chunks", None)
            self._cancel.add(ident)
            try:
                self._queue.remove(ident)
            except ValueError:
                running = True
        if running:
            self.host.next_seq()
            self.host.set_speaking(False)
            self.host.stop_playback()
        with self._lock:
            job = self.jobs.get(ident) or job
        return job_public(job)

    def delete(self, ident: str) -> dict[str, Any]:
        with self._lock:
            job = self.jobs.get(ident)
        if job and job.get("status") in {"queued", "running"}:
            self.cancel(ident)
        with self._lock:
            self.jobs.pop(ident, None)
            self._cancel.discard(ident)
        self.host.drop_clip(ident)
        if self.dir and ID_RE.match(ident):
            for suffix in (".wav", ".json"):
                path = self.dir / f"{ident}{suffix}"
                if path.is_file():
                    path.unlink()
        return {"ok": True, "id": ident}

    def text_of(self, ident: str) -> str:
        with self._lock:
            job = self.jobs.get(ident)
            text = str((job or {}).get("_text") or "")
        if text:
            return text
        self._merge_disk()
        with self._lock:
            job = self.jobs.get(ident)
            return str((job or {}).get("_text") or "")

    def stored(self, ident: str) -> bytes | None:
        """读完的 wav 落过盘，进程重启后还能下载。"""
        if self.dir is None or not ID_RE.match(ident):
            return None
        path = self.dir / f"{ident}.wav"
        return path.read_bytes() if path.is_file() else None

    # ---- 内部 ---------------------------------------------------------------

    def _remember(
        self,
        ident: str,
        *,
        mode: str,
        text: str,
        chars: int,
        estimate: int,
        chunks: int,
        status: str,
        done: int,
        pieces: list[str] | None = None,
    ) -> dict[str, Any]:
        job: dict[str, Any] = {
            "id": ident,
            "mode": mode,
            "status": status,
            "chars": chars,
            "estimate_sec": estimate,
            "chunks": chunks,
            "done": done,
            "preview": preview_of(text),
            "audio_id": None,
            "duration_sec": None,
            "bytes": None,
            "filename": None,
            "error": None,
            "created_at": int(time.time()),
            "_text": text,
        }
        if pieces is not None:
            job["_chunks"] = list(pieces)
        with self._lock:
            self.jobs[ident] = job
            while len(self.jobs) > MAX_READ_JOBS:
                self.jobs.popitem(last=False)
        return job

    def _ensure_worker(self) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            return
        thread = threading.Thread(target=self._loop, name="read-jobs", daemon=True)
        self._worker = thread
        thread.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                ident = self._queue.popleft()
            try:
                self._run(ident)
            except Exception as exc:  # noqa: BLE001
                self.host.note(ident, "read_job_crash")
                with self._lock:
                    job = self.jobs.get(ident)
                    if job is not None:
                        job["status"] = "error"
                        job["error"] = str(exc)[:120]

    def _run(self, ident: str) -> None:
        with self._lock:
            if ident in self._cancel:
                job = self.jobs.get(ident)
                if job is not None and job.get("status") in {"queued", "running"}:
                    job["status"] = "cancelled"
                    job.pop("_chunks", None)
                return
            job = self.jobs.get(ident)
            if job is None or job.get("status") == "cancelled":
                return
            chunks = list(job.get("_chunks") or [])
            mode = str(job.get("mode") or "file")
            job["status"] = "running"
            job["done"] = 0
        self.host.note(ident, "read_job_run", mode=mode, chunks=len(chunks))
        seq = self.host.next_seq()
        self.host.set_speaking(True)
        try:
            if mode == "play":
                self.host.stop_playback()
            parts: list[bytes] = []
            for i, sent in enumerate(chunks, start=1):
                self._raise_if_stopped(ident, seq)
                if mode == "play":
                    if not self.host.cue_sentence(sent, ident, seq):
                        raise HubError("companion_unavailable", "被打断了")
                else:
                    wav = self.host.synthesize(sent, ident)
                    if not wav:
                        raise HubError("companion_unavailable", "合成失败")
                    parts.append(wav)
                with self._lock:
                    current = self.jobs.get(ident)
                    if current is not None:
                        current["done"] = i
                self.host.note(ident, "read_chunk", i=i, n=len(chunks), chars=len(sent), mode=mode)
            if mode == "play":
                with self._lock:
                    current = self.jobs.get(ident)
                    if current is not None:
                        current["status"] = "done"
                        current["done"] = len(chunks)
                        current.pop("_chunks", None)
                # sec 给周报台账（F27）：边念边出声的没有 wav 可量，按字数估
                self.host.note(
                    ident,
                    "read_job_done",
                    mode="play",
                    sec=estimate_read_seconds("".join(chunks)),
                )
                return
            joined = join_wavs(parts)
            self.host.keep_clip(ident, joined)
            self._write_wav(ident, joined)
            with self._lock:
                current = self.jobs.get(ident)
                if current is not None:
                    current["status"] = "done"
                    current["done"] = len(chunks)
                    current["audio_id"] = ident
                    current["duration_sec"] = round(wav_duration_sec(joined), 3)
                    current["bytes"] = len(joined)
                    current["filename"] = f"shorekeeper-{ident}.wav"
                    current.pop("_chunks", None)
                    self._write_meta(current)
            self.host.note(
                ident,
                "read_job_done",
                bytes=len(joined),
                sec=round(wav_duration_sec(joined), 3),
            )
        except (HubError, ValueError, wave.Error) as exc:
            interrupted = isinstance(exc, HubError) and exc.message == "被打断了"
            message = exc.message if isinstance(exc, HubError) else "合成失败"
            with self._lock:
                current = self.jobs.get(ident)
                if current is not None:
                    current["status"] = "cancelled" if interrupted else "error"
                    current["error"] = None if interrupted else message
                    current.pop("_chunks", None)
            self.host.note(ident, "read_job_error" if not interrupted else "read_job_cancel")
        finally:
            self.host.set_speaking(False)

    def _raise_if_stopped(self, ident: str, seq: int) -> None:
        with self._lock:
            cancelled = ident in self._cancel
        if cancelled or not self.host.still_current(seq):
            raise HubError("companion_unavailable", "被打断了")

    def _merge_disk(self) -> None:
        if self.dir is None or not self.dir.is_dir():
            return
        wavs = sorted(self.dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        with self._lock:
            for path in wavs:
                ident = path.stem
                if not ID_RE.match(ident) or ident in self.jobs:
                    continue
                if len(self.jobs) >= MAX_READ_JOBS:
                    break
                meta: dict[str, Any] = {}
                sidecar = path.with_suffix(".json")
                if sidecar.is_file():
                    try:
                        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        loaded = {}
                    if isinstance(loaded, dict):
                        meta = loaded
                self.jobs[ident] = {
                    "id": ident,
                    "mode": "file",
                    "status": "done",
                    "chars": meta.get("chars"),
                    "estimate_sec": meta.get("estimate_sec"),
                    "chunks": meta.get("chunks"),
                    "done": meta.get("chunks") or meta.get("done"),
                    "preview": meta.get("preview") or ident,
                    "audio_id": ident,
                    "duration_sec": meta.get("duration_sec"),
                    "bytes": meta.get("bytes") or path.stat().st_size,
                    "filename": meta.get("filename") or f"shorekeeper-{ident}.wav",
                    "error": None,
                    "created_at": meta.get("created_at") or int(path.stat().st_mtime),
                    "_text": meta.get("text") or "",
                }

    def _write_wav(self, ident: str, wav: bytes) -> None:
        if self.dir is None or not ID_RE.match(ident):
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{ident}.wav").write_bytes(wav)

    def _write_meta(self, job: dict[str, Any]) -> None:
        if self.dir is None:
            return
        ident = str(job.get("id") or "")
        if not ident or not ID_RE.match(ident):
            return
        payload = {
            "id": ident,
            "mode": "file",
            "status": "done",
            "chars": job.get("chars"),
            "estimate_sec": job.get("estimate_sec"),
            "chunks": job.get("chunks"),
            "done": job.get("done"),
            "preview": job.get("preview"),
            "duration_sec": job.get("duration_sec"),
            "bytes": job.get("bytes"),
            "filename": job.get("filename"),
            "created_at": job.get("created_at"),
            "text": job.get("_text") or "",
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{ident}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
