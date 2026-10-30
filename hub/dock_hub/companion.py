"""桌面伴侣：Hub 把 K20 的一句话转给配置的 LLM，再按句 cue Mini TTS。

LLM 可以是本机/局域网 Ollama，或 OpenAI 兼容网关（如阿里云 MaaS）。
默认 tts.deliver=false：stream 读模型，第一句就能开口；chat HTTP 仍等全文给字幕。
对话不在调用时现拉米家：Hub 把已缓存的书桌状态塞进提示词，对方没问不要提。
"""

from __future__ import annotations

import io
import json
import re
import threading
import time
import uuid
import wave
import urllib.error
import urllib.request
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dock_hub.actions import apply_device_skills, launch_apps, set_temperature
from dock_hub.chat_log import ChatLog
from dock_hub.config import CompanionConfig, ID_RE
from dock_hub.errors import HubError
<<<<<<< HEAD
from dock_hub.reads import ReadJobs, job_public

# 「听懂话」那一段搬去了 intents.py（纯函数：原话 + 技能表 → 设备动作）。
# 这里把所有名字原样再导出一遍，外面照旧 from dock_hub.companion import ...，
# 免得为了搬家去改测试和调用方。
from dock_hub.intents import (
    ack_ledstrip,
    infer_media,
    infer_volume,
    parse_media_action,
    parse_timer,
    parse_timer_action,
    parse_volume_action,
    constrain_light_actions,
    fold_game_asr,
    infer_brightness,
    infer_control,
    infer_temp,
    is_teach_attempt,
    looks_like_control,
    looks_like_query,
    normalize_phrase,
    parse_action_lines,
    parse_bri_actions,
    parse_cn_int,
    parse_teach,
    parse_temp_actions,
    query_kind,
    resolve_skill,
    skill_nicknames,
    skill_prompt,
)

# 「怎么说」搬去了 persona.py（催稿文案、情绪、mood.yaml 读写）。
# 同样把名字再导出一次，外面照旧 from dock_hub.companion import speak_temp。
from dock_hub.llm import (
    DEFAULT_PERSONA,
    _clean_reply,
    _join,
    _openai_delta_content,
    _openai_message_content,
    _ollama_chunk,
    _openrouter,
    build_messages,
    desk_facts_prompt,
    facts_from_snapshot,
    openai_mode,
)
from dock_hub.speech import (
    chunk_read_text,
    clip_announce,
    cut_sentences,
    estimate_read_seconds,
    join_wavs,
    pack_read_chunks,
    spoken_chars,
    wav_duration_sec,
)
from dock_hub.persona import (
    ack_after_skills,
    allow_ask,
    appraise_user,
    cn_clock,
    cn_num,
    decay_mood,
    default_mood,
    enforce_ask_budget,
    load_mood,
    mood_prompt,
    next_self,
    period_label,
    recent_prompt,
    save_mood,
    speak_gpu,
    speak_last,
    speak_light,
    speak_media,
    speak_memory,
    speak_mini,
    speak_pc,
    speak_pc_temp,
    speak_temp,
    speak_time,
    speak_timer,
    unsolicited_block_reason,
)
from dock_hub.metrics import METRICS

MAX_CLIPS = 8
MAX_READ_CHARS = 8000
READ_TTS_TIMEOUT_SEC = 90.0
=======
from dock_hub.tts import Tts

MAX_CLIPS = 8
MAX_READ_CHARS = 8000
MAX_READ_JOBS = 24
READ_CHARS_PER_SEC = 4.0
>>>>>>> refactor/companion-tts


PRESENCE_SEC = 90


@dataclass
class CompanionReply:
    text: str
    audio_id: str | None = None


class Companion:
    def __init__(self, config: CompanionConfig | None) -> None:
        self.config = config or CompanionConfig(enabled=False)
        self.tts = Tts.from_config(self.config, log=_latency)
        self._ready = False
        self._voice = False
        self._ready_at = 0.0
        self._voice_at = 0.0
        self._speaking = False
        self._seq = 0
        self._lock = threading.Lock()
        self._clips: OrderedDict[str, bytes] = OrderedDict()
        self._skills: list[dict[str, str]] = []
        self._run_skill = None
        self._aliases: dict[str, dict[str, Any]] = {}
        self._aliases_path: Path | None = None
        self._mood_path: Path | None = None
        self._mood: dict[str, Any] = default_mood()
        self._world: dict[str, Any] = {}
        self._chat_log: ChatLog | None = None
        self._recent: deque[tuple[str, str]] = deque(maxlen=5)
        self._timer: threading.Timer | None = None
        self._extra_llm_headers: dict[str, str] = {}
        self._k20_seen_at = 0.0
        self._story_books = None
        self.reads = ReadJobs(self)

    def configure(self, config: CompanionConfig | None) -> None:
        self.config = config or CompanionConfig(enabled=False)
        self.tts = Tts.from_config(self.config, log=_latency)
        self._ready = False
        self._voice = False
        self._ready_at = 0.0
        self._voice_at = 0.0
        with self._lock:
            self._speaking = False
            self._seq += 1
            self._clips.clear()
        self._cancel_timer()
        self._recent.clear()

    def set_skills(
        self,
        skills: list[dict[str, str]],
        run_skill,
        aliases_path: str | Path | None = None,
        world: dict[str, Any] | None = None,
    ) -> None:
        self._skills = list(skills)
        self._run_skill = run_skill
        self._aliases_path = Path(aliases_path) if aliases_path else None
        self._aliases = load_aliases(self._aliases_path)
        self._world = dict(world or {})
        self._mood_path = (
            self._aliases_path.with_name("companion-mood.yaml") if self._aliases_path else None
        )
        self._mood = load_mood(self._mood_path)

    def set_chat_log(self, log: ChatLog | None) -> None:
        self._chat_log = log

    def set_reads_dir(self, path: str | Path | None) -> None:
        self.reads.set_dir(path)

    def note_presence(self) -> None:
        """K20 刚打过认证过的 snapshot / chat：人还在这条链路上。"""
        self._k20_seen_at = time.monotonic()

    def k20_connected(self, now_mono: float | None = None) -> bool:
        seen = self._k20_seen_at
        if seen <= 0:
            return False
        return (now_mono if now_mono is not None else time.monotonic()) - seen < PRESENCE_SEC

    def snapshot(self) -> dict[str, Any] | None:
        if not self.config.enabled:
            return None
        with self._lock:
            speaking = self._speaking
        body: dict[str, Any] = {
            "ready": self.ready(),
            "voice": self.voice(),
            "speaking": speaking,
        }
        return body

    def ready(self) -> bool:
        if not self.config.enabled:
            return False
        now = time.monotonic()
        if now - self._ready_at < 5:
            return self._ready
        self._ready = self._ping_llm()
        self._ready_at = now
        return self._ready

    def chat(self, body: dict[str, Any], facts: str) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 必须是对象")
        heard = str(body.get("text") or "").strip()
        if not heard:
            raise HubError("bad_request", "text 不能为空")
        text = fold_game_asr(heard)
        turn = _turn_id(body)
        started = time.monotonic()
        _latency(turn, "chat_recv", chars=len(heard))
        with self._lock:
            self._seq += 1
            seq = self._seq
        self.tts.stop()
        taught = self._remember_skill(text)
        if taught or is_teach_attempt(text):
            spoken = taught or "这个只能绑已经配好的灯或启动项，不能自己加程序。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard
            )
        control = looks_like_control(text)
        mood_extra = self._prepare_mood(text, control=control)
        asked = query_kind(text)
        if asked:
            spoken = self._answer_query(asked, text)
            self._commit_reply(spoken)
            _latency(turn, "query", kind=asked)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard
            )
        desk = self._handle_desk(text, turn)
        if desk:
            spoken, skills = desk
            self._commit_reply(spoken)
            _latency(turn, "desk", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, skills=skills
            )
        # 泰缇斯 / Hermes 已移除：不再做「简单还是复杂」的路由判定，
        # 所有对话直接交给伴侣模型（顺带省掉每次一条分类请求）。
        catalog = skill_prompt(self._skills)
        memory = recent_prompt(self._recent)
        desk_facts = desk_facts_prompt(_call_world(self._world.get("desk")))
        extra = "\n".join(
            part for part in (catalog, mood_extra, memory, facts, desk_facts) if part.strip()
        )
        messages = build_messages(text, extra, self.config.persona)
        llm_at = time.monotonic()
        cue_as_we_go = self.tts.configured and not self.tts.deliver
        try:
            if cue_as_we_go:
                raw = self._stream_and_cue(messages, turn, seq)
            else:
                raw = self._complete(messages)
        except HubError as exc:
            _latency(turn, "ollama_error", ms=_ms(llm_at))
            self._record_turn(turn, heard, "", _ms(started), [], exc.message)
            raise
        spoken = _clean_reply(raw)
        spoken = enforce_ask_budget(spoken, bool(self._mood.get("allow_ask")))
        applied, skills = self._apply_device_skills(text, raw, turn)
        spoken = ack_after_skills(spoken, skills)
        spoken = ack_ledstrip(text, spoken, skills)
        filled = False
        if not spoken and applied:
            spoken = "好。"
            filled = True
        if not spoken:
            _latency(turn, "ollama_empty", ms=_ms(llm_at))
            self._record_turn(turn, heard, "", _ms(started), skills, "大脑没有说出话")
            raise HubError("companion_unavailable", "大脑没有说出话")
        _latency(turn, "ollama_done", ms=_ms(llm_at), chars=len(spoken))
        self._commit_reply(spoken)
        return self._finish_spoken(
            spoken, turn, seq, started, filled=filled, user=heard, skills=skills
        )

    def _finish_spoken(
        self,
        spoken: str,
        turn: str,
        seq: int,
        started: float,
        *,
        filled: bool = False,
        cue_llm: bool = True,
        user: str = "",
        skills: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._ready = True
        self._ready_at = time.monotonic()
        audio_id = None
        cue_as_we_go = self.tts.configured and not self.tts.deliver
        if not cue_as_we_go:
            with self._lock:
                if seq != self._seq:
                    _latency(turn, "barge_in", ms=_ms(started))
                    self._record_turn(turn, user, spoken, _ms(started), skills or [])
                    return {"text": spoken, "audio_id": None}
            audio_id = self._maybe_speak(spoken, turn)
        else:
            with self._lock:
                barged = seq != self._seq
            if barged:
                _latency(turn, "barge_in", ms=_ms(started))
            elif filled or not cue_llm:
                self._cue_sentence(spoken, turn, seq)
        _latency(turn, "chat_total", ms=_ms(started), audio=1 if audio_id else 0)
        self._record_turn(turn, user, spoken, _ms(started), skills or [])
        self._remember(user, spoken)
        return {"text": spoken, "audio_id": audio_id}

    def _record_turn(
        self,
        turn: str,
        user: str,
        reply: str,
        ms: int,
        skills: list[dict[str, Any]],
        error: str | None = None,
    ) -> None:
        if not self._chat_log:
            return
        self._chat_log.append(
            turn=turn, user=user, reply=reply, ms=ms, skills=skills, error=error
        )

    def set_story_books(self, books: Any) -> None:
        self._story_books = books

    def begin_story_audio(self) -> None:
        self.reads.cancel_all()
        with self._lock:
            self._seq += 1
            self._speaking = True
        self.tts.stop()

    def cue_story_sentence(self, text: str) -> bool:
        if not self.config.enabled or not self.tts.configured:
            return False
        with self._lock:
            seq = self._seq
        return bool(self._cue_sentence(text, "story", seq))

    def stop_story_audio(self) -> None:
        with self._lock:
            self._seq += 1
            self._speaking = False
        self.tts.stop()

    def stop(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if self._story_books is not None:
            self._story_books.pause()
        with self._lock:
            self._seq += 1
            self._speaking = False
<<<<<<< HEAD
        self.reads.cancel_all()
        self._stop_tts()
=======
        self._cancel_running_reads()
        self.tts.stop()
>>>>>>> refactor/companion-tts
        _latency("-", "stop")
        return {"ok": True}

    def announce(self, body: dict[str, Any]) -> dict[str, Any]:
        """微信等侧路：让守岸人说一句，不走调度、不调泰缇斯。"""
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 必须是对象")
        text = clip_announce(str(body.get("text") or ""))
        if not text:
            raise HubError("bad_request", "text 不能为空")
        turn = _turn_id(body)
        started = time.monotonic()
        skipped = self._unsolicited_block()
        if skipped:
            _latency(turn, "announce_skip", reason=skipped, chars=len(text))
            self._commit_reply(text)
            self._record_turn(turn, "（微信）", text, _ms(started), [])
            return {"text": text, "audio_id": None}
        _latency(turn, "announce_recv", chars=len(text))
        with self._lock:
            self._seq += 1
            seq = self._seq
        self.tts.stop()
        self._commit_reply(text)
        return self._finish_spoken(
            text, turn, seq, started, cue_llm=False, user="（微信）"
        )

    # ---- 朗读任务的回调 ------------------------------------------------------
    # reads.ReadJobs 干活，出声和状态都在这一侧；下面几个方法就是两边之间的缝。

    def stop_playback(self) -> None:
        self._stop_tts()

    def cue_sentence(self, text: str, ident: str, seq: int) -> bool:
        return self._cue_sentence(text, ident, seq)

    def synthesize(self, text: str, ident: str) -> bytes | None:
        return self._speak(text, ident, play_only=False, play=False)

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def still_current(self, seq: int) -> bool:
        with self._lock:
            return seq == self._seq

    def set_speaking(self, speaking: bool) -> None:
        with self._lock:
            self._speaking = speaking

    def keep_clip(self, ident: str, wav: bytes) -> None:
        with self._lock:
            self._clips[ident] = wav
            while len(self._clips) > MAX_CLIPS:
                self._clips.popitem(last=False)

    def drop_clip(self, ident: str) -> None:
        with self._lock:
            self._clips.pop(ident, None)

    def note(self, turn: str, stage: str, **fields: Any) -> None:
        _latency(turn, stage, **fields)

    def read(self, body: dict[str, Any]) -> dict[str, Any]:
        """Hub 朗读页：预估时长后，书桌播放或生成可下载 wav。"""
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 必须是对象")
        text = str(body.get("text") or "").strip()
        if not text:
            raise HubError("bad_request", "text 不能为空")
        if len(text) > MAX_READ_CHARS:
            raise HubError("bad_request", "一次最多八千字")
        mode = str(body.get("mode") or "play").strip()
        if mode not in {"play", "file"}:
            raise HubError("bad_request", "mode 只能是 play 或 file")
        if not self.tts.configured:
            raise HubError("companion_unavailable", "还没接上嘴巴")
        if self._story_books is not None:
            self._story_books.pause()
        chars = spoken_chars(text)
        estimate = estimate_read_seconds(text)
        turn = _turn_id(body)
        raw_chunks = chunk_read_text(text)
        chunks = pack_read_chunks(raw_chunks)
        if not chunks:
            raise HubError("bad_request", "text 不能为空")
        _latency(turn, "read_start", mode=mode, chars=chars, chunks=len(chunks), raw=len(raw_chunks))
        job = self.reads.submit(
            turn,
            mode=mode,
            text=text,
            chars=chars,
            estimate=estimate,
            pieces=chunks,
        )
        out = job_public(job)
        out["text"] = text
        return out

    def read_jobs(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        return self.reads.list()

    def read_job_action(self, ident: str, action: str) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not ident or not ID_RE.match(ident):
            raise HubError("not_found", "没有这个任务")
        action = (action or "").strip()
        if action == "cancel":
            return self.reads.cancel(ident)
        if action == "delete":
            return self.reads.delete(ident)
        if action == "replay":
            return self._replay_read_job(ident)
        raise HubError("bad_request", "action 只能是 cancel、delete 或 replay")

<<<<<<< HEAD
=======
    def _remember_read_job(
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
        preview = "".join(text.split())
        if len(preview) > 36:
            preview = preview[:35] + "…"
        job: dict[str, Any] = {
            "id": ident,
            "mode": mode,
            "status": status,
            "chars": chars,
            "estimate_sec": estimate,
            "chunks": chunks,
            "done": done,
            "preview": preview,
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
        with self._read_lock:
            self._read_jobs[ident] = job
            while len(self._read_jobs) > MAX_READ_JOBS:
                self._read_jobs.popitem(last=False)
        return job

    def _ensure_read_worker(self) -> None:
        worker = self._read_worker
        if worker is not None and worker.is_alive():
            return
        thread = threading.Thread(target=self._read_loop, name="read-jobs", daemon=True)
        self._read_worker = thread
        thread.start()

    def _read_loop(self) -> None:
        while True:
            with self._read_cv:
                while not self._read_queue:
                    self._read_cv.wait()
                ident = self._read_queue.popleft()
            try:
                self._run_read_job(ident)
            except Exception as exc:  # noqa: BLE001
                _latency(ident, "read_job_crash")
                with self._read_lock:
                    job = self._read_jobs.get(ident)
                    if job is not None:
                        job["status"] = "error"
                        job["error"] = str(exc)[:120]

    def _run_read_job(self, ident: str) -> None:
        with self._read_lock:
            if ident in self._read_cancel:
                job = self._read_jobs.get(ident)
                if job is not None and job.get("status") in {"queued", "running"}:
                    job["status"] = "cancelled"
                    job.pop("_chunks", None)
                return
            job = self._read_jobs.get(ident)
            if job is None or job.get("status") == "cancelled":
                return
            chunks = list(job.get("_chunks") or [])
            mode = str(job.get("mode") or "file")
            job["status"] = "running"
            job["done"] = 0
        _latency(ident, "read_job_run", mode=mode, chunks=len(chunks))
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._speaking = True
        try:
            if mode == "play":
                self.tts.stop()
            parts: list[bytes] = []
            for i, sent in enumerate(chunks, start=1):
                self._raise_if_read_stopped(ident, seq)
                if mode == "play":
                    if not self._cue_sentence(sent, ident, seq):
                        raise HubError("companion_unavailable", "被打断了")
                else:
                    wav = self.tts.speak(sent, ident, play_only=False, play=False)
                    if not wav:
                        raise HubError("companion_unavailable", "合成失败")
                    parts.append(wav)
                with self._read_lock:
                    current = self._read_jobs.get(ident)
                    if current is not None:
                        current["done"] = i
                _latency(ident, "read_chunk", i=i, n=len(chunks), chars=len(sent), mode=mode)
            if mode == "play":
                with self._read_lock:
                    current = self._read_jobs.get(ident)
                    if current is not None:
                        current["status"] = "done"
                        current["done"] = len(chunks)
                        current.pop("_chunks", None)
                _latency(ident, "read_job_done", mode="play")
                return
            joined = join_wavs(parts)
            self._store_read(ident, joined)
            with self._read_lock:
                current = self._read_jobs.get(ident)
                if current is not None:
                    current["status"] = "done"
                    current["done"] = len(chunks)
                    current["audio_id"] = ident
                    current["duration_sec"] = round(wav_duration_sec(joined), 3)
                    current["bytes"] = len(joined)
                    current["filename"] = f"shorekeeper-{ident}.wav"
                    current.pop("_chunks", None)
                    self._write_read_meta(current)
            _latency(ident, "read_job_done", bytes=len(joined))
        except (HubError, ValueError, wave.Error) as exc:
            interrupted = isinstance(exc, HubError) and exc.message == "被打断了"
            message = exc.message if isinstance(exc, HubError) else "合成失败"
            with self._read_lock:
                current = self._read_jobs.get(ident)
                if current is not None:
                    current["status"] = "cancelled" if interrupted else "error"
                    current["error"] = None if interrupted else message
                    current.pop("_chunks", None)
            _latency(ident, "read_job_error" if not interrupted else "read_job_cancel")
        finally:
            with self._lock:
                self._speaking = False

    def _raise_if_read_stopped(self, ident: str, seq: int) -> None:
        with self._read_lock:
            cancelled = ident in self._read_cancel
        with self._lock:
            barged = seq != self._seq
        if cancelled or barged:
            raise HubError("companion_unavailable", "被打断了")

    def _cancel_running_reads(self) -> None:
        with self._read_lock:
            for ident, job in self._read_jobs.items():
                if job.get("status") in {"queued", "running"}:
                    job["status"] = "cancelled"
                    job["error"] = None
                    job.pop("_chunks", None)
                    self._read_cancel.add(ident)
            self._read_queue.clear()

    def _cancel_read_job(self, ident: str) -> dict[str, Any]:
        running = False
        with self._read_lock:
            job = self._read_jobs.get(ident)
            if job is None:
                raise HubError("not_found", "没有这个任务")
            if job.get("status") in {"done", "error", "cancelled"}:
                return _read_job_public(job)
            job["status"] = "cancelled"
            job["error"] = None
            job.pop("_chunks", None)
            self._read_cancel.add(ident)
            try:
                self._read_queue.remove(ident)
            except ValueError:
                running = True
        if running:
            with self._lock:
                self._seq += 1
                self._speaking = False
            self.tts.stop()
        with self._read_lock:
            job = self._read_jobs.get(ident) or job
        return _read_job_public(job)

    def _delete_read_job(self, ident: str) -> dict[str, Any]:
        with self._read_lock:
            job = self._read_jobs.get(ident)
        if job and job.get("status") in {"queued", "running"}:
            self._cancel_read_job(ident)
        with self._read_lock:
            self._read_jobs.pop(ident, None)
            self._read_cancel.discard(ident)
        with self._lock:
            self._clips.pop(ident, None)
        if self._reads_dir and ID_RE.match(ident):
            for suffix in (".wav", ".json"):
                path = self._reads_dir / f"{ident}{suffix}"
                if path.is_file():
                    path.unlink()
        return {"ok": True, "id": ident}

>>>>>>> refactor/companion-tts
    def _replay_read_job(self, ident: str) -> dict[str, Any]:
        text = self.reads.text_of(ident)
        if not text:
            raise HubError("not_found", "这篇稿已经不在了")
        return self.read({"text": text, "mode": "play"})

    def audio(self, ident: str) -> bytes:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not ident or not ID_RE.match(ident):
            raise HubError("not_found", "没有这段声音")
        with self._lock:
            wav = self._clips.get(ident)
        if not wav:
            wav = self.reads.stored(ident)
        if not wav:
            raise HubError("not_found", "没有这段声音")
        _latency(ident, "audio_get", bytes=len(wav))
        return wav

    def voice(self) -> bool:
        if not self.config.enabled or not self.tts.configured:
            return False
        now = time.monotonic()
        if now - self._voice_at < 5:
            return self._voice
        self._voice = self.tts.ping()
        self._voice_at = now
        return self._voice

    def _remember_skill(self, user_text: str) -> str | None:
        spec = parse_teach(user_text, self._skills)
        if not spec:
            return None
        self._aliases[spec["phrase"]] = {"id": spec["id"], "on": spec["on"]}
        save_aliases(self._aliases_path, self._aliases)
        verb = "开" if spec["on"] else "关"
        _latency("-", "skill_taught", phrase=spec["phrase"], id=spec["id"], on=int(spec["on"]))
        return f"好，以后说{spec['phrase']}我就{verb}{spec['name']}。"

    def _prepare_mood(self, text: str, *, control: bool) -> str:
        now = datetime.now().astimezone()
        mood = decay_mood(dict(self._mood), now)
        user = appraise_user(text)
        mood["user"] = user
        mood["self"] = next_self(str(mood.get("self") or "quiet"), user, now.hour)
        mood["updated_at"] = now.isoformat(timespec="seconds")
        mood["allow_ask"] = allow_ask(mood, control)
        mood["last_user_at"] = now.isoformat(timespec="seconds")
        self._mood = mood
        save_mood(self._mood_path, mood)
        return mood_prompt(mood, now.hour)

    def _commit_reply(self, spoken: str) -> None:
        self._mood["last_ask"] = bool(re.search(r"[？?]", spoken or ""))
        save_mood(self._mood_path, self._mood)

    def _answer_query(self, kind: str, text: str) -> str:
        self_tag = str(self._mood.get("self") or "quiet")
        if kind == "time":
            return speak_time(datetime.now().astimezone(), self_tag, text)
        if kind == "temp":
            return speak_temp(_call_world(self._world.get("temperature")), text)
        if kind == "pc":
            return speak_pc(_call_world(self._world.get("pc")), text)
        if kind == "mini":
            return speak_mini(_call_world(self._world.get("desk")), text)
        if kind == "light":
            getter = self._world.get("device")
            return speak_light(text, self._skills, getter if callable(getter) else None)
        if kind == "media":
            return speak_media(_call_world(self._world.get("media")))
        if kind == "last":
            return speak_last(self._recent)
        return "我不太清楚。"

    def _handle_desk(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        seconds = parse_timer(text)
        if seconds is not None:
            spoken = self._arm_timer(seconds)
            return spoken, [{"id": "timer", "sec": seconds, "ok": True}]
        media_act = infer_media(text)
        if media_act:
            spoken = self._run_media(media_act, turn)
            ok = spoken not in {"这次没动成。", "播放控制关着。", "这台电脑还不能控播放。"}
            return spoken, [{"id": "media", "action": media_act, "ok": ok}]
        volume_act = infer_volume(text)
        if volume_act:
            spoken = self._run_volume(volume_act, turn)
            ok = spoken not in {"这次没动成。", "音量我还调不了。"}
            return spoken, [{"id": "volume", "action": volume_act, "ok": ok}]
        launched = self._run_launch(text, turn)
        if launched:
            return launched
        climate = self._run_temp(text, turn)
        if climate:
            return climate
        return None

    def _run_launch(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        return launch_apps(
            text=text,
            turn=turn,
            skills=self._skills,
            aliases=self._aliases,
            run_skill=self._run_skill,
            note=_latency,
        )

    def _run_temp(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        return set_temperature(
            text=text,
            turn=turn,
            skills=self._skills,
            run_skill=self._run_skill,
            note=_latency,
        )

    def _run_media(self, action: str, turn: str) -> str:
        fn = self._world.get("media_command")
        if not callable(fn):
            return "这台电脑还不能控播放。"
        try:
            snap = fn(action)
        except HubError as exc:
            _latency(turn, "skill_error", id="media", code=exc.code)
            if exc.code == "not_found":
                return "播放控制关着。"
            return "这次没动成。"
        _latency(turn, "skill_ok", id="media", action=action)
        if action == "toggle" and isinstance(snap, dict) and snap.get("playing") is False:
            return "好，先停着。"
        if action == "next":
            return "好，下一首。"
        if action == "previous":
            return "好，上一首。"
        return "好。"

    def _run_volume(self, action: str, turn: str) -> str:
        fn = self._world.get("volume")
        if not callable(fn):
            return "音量我还调不了。"
        try:
            fn(action)
        except HubError as exc:
            _latency(turn, "skill_error", id="volume", code=exc.code)
            return "这次没动成。"
        _latency(turn, "skill_ok", id="volume", action=action)
        if action == "mute":
            return "好，静音。"
        if action == "down":
            return "好，小声一点。"
        return "好，大声一点。"

    def _arm_timer(self, seconds: int) -> str:
        self._cancel_timer()
        timer = threading.Timer(seconds, self._timer_fire)
        timer.daemon = True
        with self._lock:
            self._timer = timer
        timer.start()
        _latency("-", "timer_set", sec=seconds)
        return speak_timer(seconds)

    def _timer_fire(self) -> None:
        with self._lock:
            self._timer = None
            self._seq += 1
            seq = self._seq
        spoken = "到时间了。"
        turn = uuid.uuid4().hex[:12]
        self._cue_sentence(spoken, turn, seq)
        self._remember("（计时）", spoken)
        _latency(turn, "timer_fire")

    def _cancel_timer(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()

    def _desk_flags(self) -> tuple[bool, bool]:
        raw_game = _call_world(self._world.get("game"))
        if "game" not in self._world:
            game = False
        else:
            game = True if raw_game is None else bool(raw_game)
        return self.k20_connected(), game

    def _unsolicited_block(self) -> str | None:
        connected, game = self._desk_flags()
        return unsolicited_block_reason(connected=connected, game=game)

    def _remember(self, user: str, reply: str) -> None:
        spoken = (reply or "").strip()
        heard = (user or "").strip()
        if not spoken or not heard:
            return
        self._recent.append((heard, spoken))

    def _apply_device_skills(
        self, user_text: str, raw_reply: str, turn: str
    ) -> tuple[bool, list[dict[str, Any]]]:
        applied, events = apply_device_skills(
            user_text=user_text,
            raw_reply=raw_reply,
            turn=turn,
            skills=self._skills,
            aliases=self._aliases,
            run_skill=self._run_skill,
            world=self._world,
            note=_latency,
        )
        desk_ok = self._apply_desk_actions(user_text, raw_reply, turn, events)
        return applied or desk_ok, events

    def _apply_desk_actions(
        self,
        user_text: str,
        raw_reply: str,
        turn: str,
        events: list[dict[str, Any]],
    ) -> bool:
        if not looks_like_control(user_text):
            return False
        applied = False
        media_act = parse_media_action(raw_reply)
        if media_act:
            spoken = self._run_media(media_act, turn)
            ok = spoken not in {"这次没动成。", "播放控制关着。", "这台电脑还不能控播放。"}
            events.append({"id": "media", "action": media_act, "ok": ok})
            applied = applied or ok
        volume_act = parse_volume_action(raw_reply)
        if volume_act:
            spoken = self._run_volume(volume_act, turn)
            ok = spoken != "这次没动成。" and spoken != "音量我还调不了。"
            events.append({"id": "volume", "action": volume_act, "ok": ok})
            applied = applied or ok
        seconds = parse_timer_action(raw_reply)
        if seconds:
            self._arm_timer(seconds)
            events.append({"id": "timer", "sec": seconds, "ok": True})
            applied = True
        return applied

    def _maybe_speak(self, spoken: str, turn: str) -> str | None:
        if not self.tts.configured:
            return None
        with self._lock:
            self._speaking = True
        try:
            wav = self.tts.speak(spoken, turn)
        finally:
            with self._lock:
                self._speaking = False
        if not self.tts.deliver or not wav:
            return None
        self.keep_clip(turn, wav)
        return turn

    def _complete_endpoint(
        self,
        messages: list[dict[str, str]],
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_sec: float,
        num_predict: int,
        temperature: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        saved = (
            self.config.llm_base_url,
            self.config.llm_model,
            self.config.llm_api_key,
            self.config.timeout_sec,
            self.config.num_predict,
        )
        saved_headers = self._extra_llm_headers
        self.config.llm_base_url = base_url
        self.config.llm_model = model
        self.config.llm_api_key = api_key
        self.config.timeout_sec = timeout_sec
        self.config.num_predict = num_predict
        self._extra_llm_headers = extra_headers or {}
        try:
            return self._complete(messages, temperature=temperature)
        finally:
            (
                self.config.llm_base_url,
                self.config.llm_model,
                self.config.llm_api_key,
                self.config.timeout_sec,
                self.config.num_predict,
            ) = saved
            self._extra_llm_headers = saved_headers

    def _ping_llm(self) -> bool:
        openai = openai_mode(self.config)
        url = _join(self.config.llm_base_url, "/models" if openai else "/api/tags")
        timeout = 2.0 if openai else 0.4
        try:
            req = urllib.request.Request(url, method="GET", headers=self._llm_headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _complete(self, messages: list[dict[str, str]], *, temperature: float | None = None) -> str:
        body = self._chat_response(messages, stream=False, temperature=temperature)
        if openai_mode(self.config):
            return _openai_message_content(body)
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, dict):
            raise HubError("companion_unavailable", "大脑返回无法解析")
        return str(message.get("content") or "")

    def _stream_and_cue(self, messages: list[dict[str, str]], turn: str, seq: int) -> str:
        openai = openai_mode(self.config)
        path = "/chat/completions" if openai else "/api/chat"
        accept = "text/event-stream" if openai else "application/x-ndjson"
        url = _join(self.config.llm_base_url, path)
        req = urllib.request.Request(
            url,
            data=self._chat_payload(messages, stream=True),
            method="POST",
            headers=self._llm_headers(accept=accept),
        )
        pieces: list[str] = []
        buf = ""
        first = True
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_sec) as resp:
                if not (200 <= resp.status < 300):
                    raise HubError("companion_unavailable", "大脑拒绝请求")
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if openai and line == "[DONE]":
                        break
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if not openai and obj.get("done"):
                        break
                    chunk = _openai_delta_content(obj) if openai else _ollama_chunk(obj)
                    if not chunk:
                        continue
                    pieces.append(chunk)
                    buf += chunk
                    ready, buf = cut_sentences(buf)
                    allow = bool(self._mood.get("allow_ask"))
                    for sent in ready:
                        spoken = _clean_reply(sent)
                        if spoken and not allow and re.search(r"[？?]", spoken):
                            continue
                        if spoken and self._cue_sentence(spoken, turn, seq) and first:
                            _latency(turn, "tts_first_sentence", chars=len(spoken))
                            first = False
        except TimeoutError as exc:
            raise HubError("companion_unavailable", "大脑响应超时") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise HubError("companion_unavailable", f"大脑拒绝请求：{exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise HubError("companion_unavailable", "连不上大脑") from exc
        tail = _clean_reply(buf)
        if tail and not (not bool(self._mood.get("allow_ask")) and re.search(r"[？?]", tail)):
            if tail and self._cue_sentence(tail, turn, seq) and first:
                _latency(turn, "tts_first_sentence", chars=len(tail))
        return "".join(pieces)

    def _cue_sentence(self, spoken: str, turn: str, seq: int) -> bool:
        with self._lock:
            if seq != self._seq:
                return False
        self.tts.speak(spoken, turn)
        return True

    def _llm_headers(self, accept: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if accept:
            headers["Content-Type"] = "application/json"
            headers["Accept"] = accept
        if self.config.llm_api_key:
            headers["Authorization"] = f"Bearer {self.config.llm_api_key}"
        extra = getattr(self, "_extra_llm_headers", None) or {}
        for key, value in extra.items():
            if key and value:
                headers[key] = value
        return headers

    def _chat_payload(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        temperature: float | None = None,
    ) -> bytes:
        temp = 0.7 if temperature is None else temperature
        if openai_mode(self.config):
            payload: dict[str, Any] = {
                "model": self.config.llm_model,
                "messages": messages,
                "stream": stream,
                "max_tokens": self.config.num_predict,
                "temperature": temp,
            }
            if "hermes" not in (self.config.llm_model or "").lower():
                payload["enable_thinking"] = False
            # openrouter/free 会抽到必须推理的模型。effort=none 会被拒绝，
            # 推理正文又进不了 content，守岸人就会当成没说话。minimal 把话留在正文。
            if _openrouter(self.config.llm_base_url):
                payload["reasoning"] = {"effort": "minimal"}
            return json.dumps(payload).encode("utf-8")
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "stream": stream,
            "think": False,
            "options": {
                "num_ctx": self.config.num_ctx,
                "num_predict": self.config.num_predict,
                "temperature": temp,
                "presence_penalty": 0.0,
            },
        }
        return json.dumps(payload).encode("utf-8")

    def _chat_response(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        path = "/chat/completions" if openai_mode(self.config) else "/api/chat"
        url = _join(self.config.llm_base_url, path)
        req = urllib.request.Request(
            url,
            data=self._chat_payload(messages, stream=stream, temperature=temperature),
            method="POST",
            headers=self._llm_headers(accept="application/json"),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
        except TimeoutError as exc:
            raise HubError("companion_unavailable", "大脑响应超时") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise HubError("companion_unavailable", f"大脑拒绝请求：{exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise HubError("companion_unavailable", "连不上大脑") from exc
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HubError("companion_unavailable", "大脑返回无法解析") from exc
        if not isinstance(body, dict):
            raise HubError("companion_unavailable", "大脑返回无法解析")
        return body




def _call_world(fn: Any) -> Any:
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def load_aliases(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    aliases: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        phrase = normalize_phrase(str(key))
        if not phrase:
            continue
        if isinstance(value, str):
            ident = value.strip()
            if ident:
                aliases[phrase] = {"id": ident, "on": True}
            continue
        if isinstance(value, dict) and value.get("id"):
            aliases[phrase] = {"id": str(value["id"]), "on": bool(value.get("on", True))}
    return aliases


def save_aliases(path: Path | None, aliases: dict[str, dict[str, Any]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(aliases, allow_unicode=True, sort_keys=True)
    path.write_text(body, encoding="utf-8")


def _turn_id(body: dict[str, Any]) -> str:
    raw = str(body.get("turn_id") or "").strip()
    if raw and ID_RE.match(raw) and len(raw) <= 64:
        return raw
    return uuid.uuid4().hex[:12]


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _latency(turn: str, stage: str, **fields: Any) -> None:
    """一行日志 + 一个计数。日志给人翻，计数给 /metrics 看趋势。"""
    _count_stage(turn, stage, fields)
    stamp = time.strftime("%H:%M:%S")
    parts = [f"latency {stamp} turn={turn} stage={stage}"]
    parts.extend(f"{key}={value}" for key, value in fields.items())
    print(" ".join(parts), flush=True)
def _count_stage(turn: str, stage: str, fields: dict[str, Any]) -> None:
    """把打点翻成指标。

    只挑「看趋势时真的会问」的几个：一轮对话、一轮的耗时、首句延迟、
    TTS 失败、大脑报错。turn 是随机 id，绝不能进 label——那会把基数打爆。
    """
    if stage == "chat_recv":
        METRICS.note_turn(turn)
        return
    if stage == "tts_first_sentence":
        first = METRICS.first_sentence(turn)
        if first is not None:
            METRICS.observe("hub_companion_first_sentence_ms", round(first, 1))
        return
    if stage == "chat_total":
        METRICS.inc("hub_companion_chat_total")
        ms = fields.get("ms")
        if isinstance(ms, (int, float)):
            METRICS.observe("hub_companion_chat_ms", float(ms))
        return
    if stage in {"tts_error", "tts_empty"}:
        METRICS.inc("hub_companion_tts_fail_total", reason=stage)
        return
    if stage == "ollama_error":
        METRICS.inc("hub_companion_llm_error_total")
