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

from dock_hub.chat_log import ChatLog
from dock_hub.config import CompanionConfig, ID_RE
from dock_hub.errors import HubError

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

MAX_CLIPS = 8
MAX_READ_CHARS = 8000
MAX_READ_JOBS = 24
READ_TTS_TIMEOUT_SEC = 90.0

DEFAULT_PERSONA = """你是守岸人，正在漂泊者的书桌上值班。锚点：静、守、留白。说话慢、短、轻，偏诗意，不卖萌，不喊口号，不讲游戏剧情。
每次只回 1～3 句。可以叫对方「漂泊者」。
问候、闲聊、常识用你自己的话答。书桌状态已写在提示里：对方问占用、温度、灯、哪台机器时，直接用那些数字，不要说不知道。
需要查天气、新闻、资料、放歌或分析时，不要装不知道——那种题 Hub 会交给深度系统。
自己做不到的事不要说「不懂」；只有书桌状态里真没有的数字才说不知道。
对方没问就不要报室内温湿度或电脑占用，也不要声称已经开灯或开了程序。
电脑默认指 Windows；没点名 Mini / Mac 时不要提那边的占用或温度。
对方明确要开关设备时：口头短应一声，并单独一行写 ACTION: <id>.on 或 ACTION: <id>.off。
台灯是米家书桌灯（lamp）；灯带是桌上那条灯条（desk-led）；挂灯是顶上那盏，不要和灯带当成同一个。
对方说灯带就 ACTION: desk-led.on 或 desk-led.off；说台灯才 ACTION: lamp.on 或 lamp.off。
对方要调灯的亮度时：ACTION: <id>.bri.<1-100>。
对方要调空调温度时：ACTION: <id>.temp.<16-30>。
对方要打游戏、打瓦、开黑、启动无畏契约或启动电脑上的程序时：短应，并 ACTION: <id>.run。不要问想玩哪一款，不要讲剧情或抽卡。
闲聊不要写 ACTION。
对方叹气、说累、打招呼时，先应人，不要当成开关指令。
关灯或开程序时不要提问。上一轮已经问过就不要再问。
对方要暂停、下一首或问在播什么时：短应；暂停写 ACTION: media.toggle，下一首写 ACTION: media.next。
对方要调电脑音量时：ACTION: volume.up 或 volume.down 或 volume.mute。
对方要倒计时时：ACTION: timer.<1-120分钟>。
对方要记住一句口令时（例如「以后我说开黑就启动无畏契约」「记住打游戏就是打瓦」），口头答应即可，不要编造新程序。"""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ACTION_RE = re.compile(r"^\s*ACTION:\s*\S+\s*$", re.MULTILINE)
PRESENCE_SEC = 90


@dataclass
class CompanionReply:
    text: str
    audio_id: str | None = None


class Companion:
    def __init__(self, config: CompanionConfig | None) -> None:
        self.config = config or CompanionConfig(enabled=False)
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
        self._reads_dir: Path | None = None
        self._story_books = None
        self._read_jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._read_queue: deque[str] = deque()
        self._read_lock = threading.Lock()
        self._read_cv = threading.Condition(self._read_lock)
        self._read_worker: threading.Thread | None = None
        self._read_cancel: set[str] = set()

    def configure(self, config: CompanionConfig | None) -> None:
        self.config = config or CompanionConfig(enabled=False)
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
        self._reads_dir = Path(path) if path else None

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
        self._stop_tts()
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
        cue_as_we_go = bool(self.config.tts_base_url) and not self.config.tts_deliver
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
        cue_as_we_go = bool(self.config.tts_base_url) and not self.config.tts_deliver
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
        self._cancel_running_reads()
        with self._lock:
            self._seq += 1
            self._speaking = True
        self._stop_tts()

    def cue_story_sentence(self, text: str) -> bool:
        if not self.config.enabled or not self.config.tts_base_url:
            return False
        with self._lock:
            seq = self._seq
        return bool(self._cue_sentence(text, "story", seq))

    def stop_story_audio(self) -> None:
        with self._lock:
            self._seq += 1
            self._speaking = False
        self._stop_tts()

    def stop(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if self._story_books is not None:
            self._story_books.pause()
        with self._lock:
            self._seq += 1
            self._speaking = False
        self._cancel_running_reads()
        self._stop_tts()
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
        self._stop_tts()
        self._commit_reply(text)
        return self._finish_spoken(
            text, turn, seq, started, cue_llm=False, user="（微信）"
        )

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
        if not self.config.tts_base_url:
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
        job = self._remember_read_job(
            turn,
            mode=mode,
            text=text,
            chars=chars,
            estimate=estimate,
            chunks=len(chunks),
            status="queued",
            done=0,
            pieces=chunks,
        )
        self._ensure_read_worker()
        with self._read_cv:
            self._read_queue.append(turn)
            self._read_cv.notify()
        out = _read_job_public(job)
        out["text"] = text
        return out

    def read_jobs(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        self._merge_disk_reads()
        with self._read_lock:
            items = [_read_job_public(job) for job in self._read_jobs.values()]
        items.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
        return {"items": items}

    def read_job_action(self, ident: str, action: str) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not ident or not ID_RE.match(ident):
            raise HubError("not_found", "没有这个任务")
        action = (action or "").strip()
        if action == "cancel":
            return self._cancel_read_job(ident)
        if action == "delete":
            return self._delete_read_job(ident)
        if action == "replay":
            return self._replay_read_job(ident)
        raise HubError("bad_request", "action 只能是 cancel、delete 或 replay")

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
                self._stop_tts()
            parts: list[bytes] = []
            for i, sent in enumerate(chunks, start=1):
                self._raise_if_read_stopped(ident, seq)
                if mode == "play":
                    if not self._cue_sentence(sent, ident, seq):
                        raise HubError("companion_unavailable", "被打断了")
                else:
                    wav = self._speak(sent, ident, play_only=False, play=False)
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
            self._stop_tts()
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

    def _replay_read_job(self, ident: str) -> dict[str, Any]:
        with self._read_lock:
            job = self._read_jobs.get(ident)
            text = str((job or {}).get("_text") or "")
        if not text:
            self._merge_disk_reads()
            with self._read_lock:
                job = self._read_jobs.get(ident)
                text = str((job or {}).get("_text") or "")
        if not text:
            raise HubError("not_found", "这篇稿已经不在了")
        return self.read({"text": text, "mode": "play"})

    def _merge_disk_reads(self) -> None:
        if self._reads_dir is None or not self._reads_dir.is_dir():
            return
        wavs = sorted(self._reads_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        with self._read_lock:
            for path in wavs:
                ident = path.stem
                if not ID_RE.match(ident) or ident in self._read_jobs:
                    continue
                if len(self._read_jobs) >= MAX_READ_JOBS:
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
                self._read_jobs[ident] = {
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

    def _write_read_meta(self, job: dict[str, Any]) -> None:
        if self._reads_dir is None:
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
        self._reads_dir.mkdir(parents=True, exist_ok=True)
        (self._reads_dir / f"{ident}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def audio(self, ident: str) -> bytes:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not ident or not ID_RE.match(ident):
            raise HubError("not_found", "没有这段声音")
        with self._lock:
            wav = self._clips.get(ident)
        if not wav and self._reads_dir and ID_RE.match(ident):
            stored = self._reads_dir / f"{ident}.wav"
            if stored.is_file():
                wav = stored.read_bytes()
        if not wav:
            raise HubError("not_found", "没有这段声音")
        _latency(ident, "audio_get", bytes=len(wav))
        return wav

    def voice(self) -> bool:
        if not self.config.enabled or not self.config.tts_base_url:
            return False
        now = time.monotonic()
        if now - self._voice_at < 5:
            return self._voice
        self._voice = self._ping_tts()
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
        if not self._skills or self._run_skill is None:
            return None
        wanted = infer_control(text, self._skills, self._aliases)
        for token, on in wanted:
            if not on:
                continue
            ident = resolve_skill(token, self._skills)
            if not ident:
                continue
            item = next((s for s in self._skills if s["id"] == ident), None)
            if not item or item.get("kind") != "run":
                continue
            name = item.get("name") or ident
            try:
                self._call_run_skill(ident, True)
            except HubError as exc:
                _latency(turn, "skill_error", id=ident, code=exc.code)
                return (
                    "这次没动成。",
                    [{"id": ident, "name": name, "on": True, "ok": False, "code": exc.code}],
                )
            _latency(turn, "skill_ok", id=ident, on=1)
            return f"好，开{name}。", [{"id": ident, "name": name, "on": True, "ok": True}]
        return None

    def _run_temp(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        wanted = infer_temp(text, self._skills)
        if not wanted or self._run_skill is None:
            return None
        events: list[dict[str, Any]] = []
        applied = False
        spoken = ""
        for token, spec in wanted:
            ident = resolve_skill(token, self._skills)
            if not ident:
                continue
            item = next((row for row in self._skills if row["id"] == ident), None)
            if not item:
                continue
            name = item.get("name") or ident
            prop_id = str(item.get("temp_id") or "temp")
            value = int(spec["value"])
            try:
                self._call_run_skill(ident, True, props={prop_id: value})
            except HubError as exc:
                _latency(turn, "skill_error", id=ident, code=exc.code)
                return (
                    "这次没动成。",
                    [
                        {
                            "id": ident,
                            "name": name,
                            "on": True,
                            "temp": value,
                            "ok": False,
                            "code": exc.code,
                        }
                    ],
                )
            applied = True
            _latency(turn, "skill_ok", id=ident, on=1, temp=value)
            events.append(
                {"id": ident, "name": name, "on": True, "temp": value, "ok": True}
            )
            spoken = f"好，{name}{cn_num(value)}度。"
        if not applied:
            return None
        return spoken, events

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

    def _call_run_skill(
        self,
        ident: str,
        on: bool,
        brightness: int | None = None,
        props: dict[str, Any] | None = None,
    ) -> None:
        fn = self._run_skill
        if fn is None:
            return
        kwargs: dict[str, Any] = {}
        if brightness is not None:
            kwargs["brightness"] = brightness
        if props:
            kwargs["props"] = props
        try:
            fn(ident, on, **kwargs)
        except TypeError:
            fn(ident, on)

    def _apply_device_skills(
        self, user_text: str, raw_reply: str, turn: str
    ) -> tuple[bool, list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        if not self._skills or self._run_skill is None:
            desk_ok = self._apply_desk_actions(user_text, raw_reply, turn, events)
            return desk_ok, events
        wanted = parse_action_lines(raw_reply)
        bri_wanted = parse_bri_actions(raw_reply)
        temp_wanted = parse_temp_actions(raw_reply)
        if (wanted or bri_wanted or temp_wanted) and not looks_like_control(user_text):
            wanted = []
            bri_wanted = []
            temp_wanted = []
        wanted = constrain_light_actions(user_text, wanted, self._skills)
        bri_wanted = constrain_light_actions(user_text, bri_wanted, self._skills)
        if not wanted:
            wanted = infer_control(user_text, self._skills, self._aliases)
        if not bri_wanted:
            bri_wanted = infer_brightness(user_text, self._skills)
        if not temp_wanted:
            temp_wanted = infer_temp(user_text, self._skills)
        applied = False
        for token, on in wanted:
            ident = resolve_skill(token, self._skills)
            if not ident:
                continue
            name = next((item["name"] for item in self._skills if item["id"] == ident), ident)
            try:
                self._call_run_skill(ident, on)
            except HubError as exc:
                _latency(turn, "skill_error", id=ident, code=exc.code)
                events.append(
                    {"id": ident, "name": name, "on": on, "ok": False, "code": exc.code}
                )
                continue
            applied = True
            _latency(turn, "skill_ok", id=ident, on=int(on))
            events.append({"id": ident, "name": name, "on": on, "ok": True})
        getter = self._world.get("device")
        for token, spec in bri_wanted:
            ident = resolve_skill(token, self._skills)
            if not ident:
                continue
            name = next((item["name"] for item in self._skills if item["id"] == ident), ident)
            target = spec.get("value")
            if target is None:
                current = None
                if callable(getter):
                    state = getter(ident)
                    if isinstance(state, dict):
                        current = state.get("brightness")
                base = int(current) if isinstance(current, int) else 50
                target = max(1, min(100, base + int(spec.get("delta") or 0)))
            target = max(1, min(100, int(target)))
            try:
                self._call_run_skill(ident, True, brightness=target)
            except HubError as exc:
                _latency(turn, "skill_error", id=ident, code=exc.code)
                events.append(
                    {
                        "id": ident,
                        "name": name,
                        "on": True,
                        "brightness": target,
                        "ok": False,
                        "code": exc.code,
                    }
                )
                continue
            applied = True
            _latency(turn, "skill_ok", id=ident, on=1, bri=target)
            events.append(
                {"id": ident, "name": name, "on": True, "brightness": target, "ok": True}
            )
        for token, spec in temp_wanted:
            ident = resolve_skill(token, self._skills)
            if not ident:
                continue
            item = next((row for row in self._skills if row["id"] == ident), None)
            name = (item or {}).get("name") or ident
            prop_id = str((item or {}).get("temp_id") or "temp")
            value = int(spec["value"])
            try:
                self._call_run_skill(ident, True, props={prop_id: value})
            except HubError as exc:
                _latency(turn, "skill_error", id=ident, code=exc.code)
                events.append(
                    {
                        "id": ident,
                        "name": name,
                        "on": True,
                        "temp": value,
                        "ok": False,
                        "code": exc.code,
                    }
                )
                continue
            applied = True
            _latency(turn, "skill_ok", id=ident, on=1, temp=value)
            events.append(
                {"id": ident, "name": name, "on": True, "temp": value, "ok": True}
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
        if not self.config.tts_base_url:
            return None
        with self._lock:
            self._speaking = True
        try:
            wav = self._speak(spoken, turn)
        finally:
            with self._lock:
                self._speaking = False
        if not self.config.tts_deliver or not wav:
            return None
        ident = turn
        with self._lock:
            self._clips[ident] = wav
            while len(self._clips) > MAX_CLIPS:
                self._clips.popitem(last=False)
        return ident

    def _store_read(self, ident: str, wav: bytes) -> None:
        with self._lock:
            self._clips[ident] = wav
            while len(self._clips) > MAX_CLIPS:
                self._clips.popitem(last=False)
        if self._reads_dir is None:
            return
        self._reads_dir.mkdir(parents=True, exist_ok=True)
        (self._reads_dir / f"{ident}.wav").write_bytes(wav)

    def _stop_tts(self) -> None:
        if not self.config.tts_base_url:
            return
        url = _join(self.config.tts_base_url, "/v1/stop")
        req = urllib.request.Request(
            url,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                resp.read()
        except (TimeoutError, urllib.error.URLError, OSError):
            return

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
        self._speak(spoken, turn)
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

    def _ping_tts(self) -> bool:
        url = _join(self.config.tts_base_url or "", "/health")
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=0.4) as resp:
                if not (200 <= resp.status < 300):
                    return False
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return False
        return isinstance(body, dict) and bool(body.get("ready"))

    def _speak(
        self,
        text: str,
        turn: str,
        *,
        play_only: bool | None = None,
        play: bool | None = None,
    ) -> bytes | None:
        url = _join(self.config.tts_base_url or "", "/v1/speak")
        if play_only is None:
            play_only = not self.config.tts_deliver
        body: dict[str, Any] = {
            "text": text,
            "voice": "shorekeeper",
            "language": "chinese",
            "turn_id": turn,
            "speed": round(float(getattr(self.config, "voice_speed", 1.0) or 1.0), 2),
        }
        if play_only:
            body["play_only"] = True
        if play is False:
            body["play"] = False
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        accept = "application/json" if play_only else "audio/wav"
        if play_only:
            timeout = 3.0
        elif play is False:
            timeout = READ_TTS_TIMEOUT_SEC
        else:
            timeout = self.config.tts_timeout_sec
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": accept},
        )
        tts_at = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except (TimeoutError, urllib.error.URLError, OSError):
            _latency(turn, "tts_error", ms=_ms(tts_at))
            return None
        if play_only:
            _latency(turn, "tts_cued", ms=_ms(tts_at), bytes=len(raw))
            return None
        if not raw:
            _latency(turn, "tts_empty", ms=_ms(tts_at))
            return None
        _latency(turn, "tts_done", ms=_ms(tts_at), bytes=len(raw))
        return raw




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


def _read_job_public(job: dict[str, Any]) -> dict[str, Any]:
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


def build_messages(text: str, facts: str, persona: str | None = None) -> list[dict[str, str]]:
    """用户消息只有这句话。默认不附带 snapshot / 米家。"""
    system = (persona or DEFAULT_PERSONA).strip()
    facts = facts.strip()
    if facts:
        system = f"{system}\n\n{facts}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def facts_from_snapshot(snap: dict[str, Any]) -> str:
    """只抽本机 PC。对话路径不要调用，也不要读米家灯/温度。"""
    lines = []
    pc = snap.get("pc")
    if isinstance(pc, dict) and pc.get("online"):
        cpu = pc.get("cpu") or {}
        gpu = pc.get("gpu") or {}
        mem = pc.get("memory") or {}
        bits = []
        if cpu.get("percent") is not None:
            bits.append(f"CPU {cpu['percent']}%")
        if mem.get("percent") is not None:
            bits.append(f"内存 {mem['percent']}%")
        if isinstance(gpu, dict) and gpu.get("percent") is not None:
            bits.append(f"GPU {gpu['percent']}%")
        if bits:
            lines.append("电脑：" + "，".join(bits) + "。")
    return "\n".join(lines)


def desk_facts_prompt(desk: Any) -> str:
    """已落盘/内存里的书桌状态。调用时塞进提示词，禁止再去现拉米家。"""
    if not isinstance(desk, dict) or not desk:
        return ""
    win = desk.get("windows") if isinstance(desk.get("windows"), dict) else {}
    mini = desk.get("mini") if isinstance(desk.get("mini"), dict) else {}
    lines = [
        "机器与设备（仅你自己看。对方没问灯、温度、占用、内存、显卡、某台机器时，禁止提起下面任何一项。"
        "问了就用下面的数字直接答，不要说不知道。"
        "「电脑」默认指 Windows；没点名 Mini / Mac 不要提。）"
    ]
    win_pc = win.get("pc") if isinstance(win.get("pc"), dict) else {}
    win_line = _pc_fact_line(win_pc)
    if win_line:
        lines.append("Windows：" + win_line)
    mini_pc = mini.get("pc") if isinstance(mini.get("pc"), dict) else {}
    mini_line = _pc_fact_line(mini_pc)
    if mini_line:
        lines.append("Mac Mini：" + mini_line)
    temp = win.get("temperature")
    if isinstance(temp, dict) and temp.get("celsius") is not None:
        bits = [f"{temp['celsius']}°C"]
        if temp.get("humidity") is not None:
            bits.append(f"湿度 {temp['humidity']}%")
        lines.append("室温：" + " ".join(bits))
    media = win.get("media")
    if isinstance(media, dict):
        if media.get("playing"):
            title = str(media.get("title") or media.get("app") or "").strip()
            lines.append("媒体：在播" + (f" {title}" if title else ""))
        elif media.get("playing") is False:
            lines.append("媒体：未在播")
    devices = win.get("devices")
    if isinstance(devices, list):
        for item in devices:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            dtype = str(item.get("type") or "")
            if dtype == "action":
                lines.append(f"{name}：{'在跑' if item.get('on') else '未在跑'}")
                continue
            status = "开" if item.get("on") else "关"
            if item.get("brightness") is not None and item.get("on"):
                status += f" 亮度{item['brightness']}"
            if item.get("online") is False:
                status += " 离线"
            lines.append(f"{name}：{status}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _pc_fact_line(pc: dict[str, Any]) -> str:
    if not pc.get("online"):
        return "离线" if pc else ""
    bits: list[str] = []
    cpu = pc.get("cpu") if isinstance(pc.get("cpu"), dict) else {}
    mem = pc.get("memory") if isinstance(pc.get("memory"), dict) else {}
    gpu = pc.get("gpu") if isinstance(pc.get("gpu"), dict) else {}
    cpu_bits: list[str] = []
    if cpu.get("percent") is not None:
        cpu_bits.append(f"{cpu['percent']}%")
    if cpu.get("temp_celsius") is not None:
        cpu_bits.append(f"{cpu['temp_celsius']}°C")
    if cpu_bits:
        bits.append("CPU " + " ".join(cpu_bits))
    if mem.get("percent") is not None:
        bits.append(f"内存 {mem['percent']}%")
    gpu_bits: list[str] = []
    if gpu.get("percent") is not None:
        gpu_bits.append(f"{gpu['percent']}%")
    if gpu.get("temp_celsius") is not None:
        gpu_bits.append(f"{gpu['temp_celsius']}°C")
    if gpu_bits:
        bits.append("GPU " + " ".join(gpu_bits))
    return "，".join(bits)


def _clean_reply(raw: str) -> str:
    text = _THINK_RE.sub("", raw)
    text = _ACTION_RE.sub("", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def _turn_id(body: dict[str, Any]) -> str:
    raw = str(body.get("turn_id") or "").strip()
    if raw and ID_RE.match(raw) and len(raw) <= 64:
        return raw
    return uuid.uuid4().hex[:12]


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _latency(turn: str, stage: str, **fields: Any) -> None:
    stamp = time.strftime("%H:%M:%S")
    parts = [f"latency {stamp} turn={turn} stage={stage}"]
    parts.extend(f"{key}={value}" for key, value in fields.items())
    print(" ".join(parts), flush=True)


def _openrouter(base_url: str | None) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def openai_mode(config: CompanionConfig) -> bool:
    if config.llm_api_key:
        return True
    parsed = urlparse(config.llm_base_url or "")
    path = (parsed.path or "").rstrip("/")
    url = (config.llm_base_url or "").lower()
    if "compatible-mode" in url:
        return True
    if "/v1" in path:
        return True
    return False


def _ollama_chunk(obj: dict[str, Any]) -> str:
    message = obj.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return ""


def _openai_delta_content(obj: dict[str, Any]) -> str:
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    return str(delta.get("content") or "")


def _openai_message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise HubError("companion_unavailable", "大脑返回无法解析")
    first = choices[0]
    if not isinstance(first, dict):
        raise HubError("companion_unavailable", "大脑返回无法解析")
    message = first.get("message")
    if not isinstance(message, dict):
        raise HubError("companion_unavailable", "大脑返回无法解析")
    return str(message.get("content") or "")


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")
