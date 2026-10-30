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

from dock_hub.actions import apply_device_skills, call_skill, launch_apps, set_temperature
from dock_hub.chat_log import ChatLog
from dock_hub.clarify import PendingClarify, clarify_for, take_answer
from dock_hub.config import CompanionConfig, ID_RE
from dock_hub.errors import HubError
from dock_hub import logs
from dock_hub.memory import (
    MemoryStore,
    parse_forget as parse_memory_forget,
    parse_teach as parse_memory_teach,
)
from dock_hub.notify import parse_relay
from dock_hub.reads import ReadJobs, job_public

# 「听懂话」那一段搬去了 intents.py（纯函数：原话 + 技能表 → 设备动作）。
# 这里把所有名字原样再导出一遍，外面照旧 from dock_hub.companion import ...，
# 免得为了搬家去改测试和调用方。
from dock_hub.intents import (
    ack_ledstrip,
    infer_media,
    infer_volume,
    parse_media_action,
    parse_leave_action,
    parse_leaving,
    parse_stopwatch_action,
    parse_stopwatch_start,
    parse_stopwatch_stop,
    parse_timer,
    parse_timer_action,
    parse_volume_action,
    constrain_light_actions,
    fold_game_asr,
    infer_brightness,
    infer_control,
    infer_music,
    infer_noise,
    infer_power,
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
    schedule_day,
    skill_nicknames,
    skill_prompt,
)

# 「怎么说」搬去了 persona.py（催稿文案、情绪、mood.yaml 读写）。
# 同样把名字再导出一次，外面照旧 from dock_hub.companion import speak_temp。
from dock_hub.diary import clean_entry, diary_messages
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
    speak_schedule,
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

from dock_hub.timers import TimerBoard, speak_stopwatch_result
from dock_hub.tts import Tts

MAX_CLIPS = 8
MAX_READ_CHARS = 8000

# 白噪音没放成时的统一口径（_handle_desk 里用它判断 ok）
_NOISE_FAIL_SPOKEN = ("这次没动成。", "这台 Mini 还不能放白噪音。")

# ---- F21 电源动作：问出口的那句、做成的短确认、等多久作废 -------------------

POWER_TTL_SEC = 60.0
"""确认悬着多久作废。比 F3 的两分钟紧——这是要动真格的动作。"""

_POWER_ASK = {
    "shutdown": "真的要关电脑吗？确认我就关了。",
    "sleep": "让电脑睡吗？确认我就动手。",
    "lock": "锁屏吗？确认就锁。",
}
_POWER_ACK = {"shutdown": "好，五秒后关机。", "sleep": "好，睡了。", "lock": "锁了。"}
_POWER_LABEL = {"shutdown": "关机", "sleep": "睡眠", "lock": "锁屏"}
_POWER_CONFIRM_RE = re.compile(r"确认|确定|动手吧|就这么办")
# 「好 / 对 / 嗯」这种一个字的答应，只有整句就是它的时候才算——
# 「今天好累」里的「好」不能变成「好，动手」。
_POWER_YES_RE = re.compile(r"^(?:好的?|好呀|行|嗯+|对呀?|是的?|要)[吧呀啊。.!！]?$")
_POWER_CANCEL_RE = re.compile(r"算了|不用|取消|别弄|不弄|先不|不关|不锁|不睡")


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
        self.timers = TimerBoard()
        self._extra_llm_headers: dict[str, str] = {}
        self._k20_seen_at = 0.0
        self._story_books = None
        self.reads = ReadJobs(self)
        self._memory: MemoryStore | None = None
        # F27：朗读任务念完的秒数往这儿送（service 接到周报台账）；None = 没接
        self.on_read_done = None
        # F3 追问澄清：问出去还没被认领的那句
        self._pending_clarify: PendingClarify | None = None
        # F21 电源动作：问过「真的要吗」、等「确认」的那一下（动作, 问的时刻）
        self._pending_power: tuple[str, float] | None = None

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

    def set_memory(self, store: MemoryStore | None) -> None:
        """F28 长期记忆：放进来说明这条通道开着。"""
        self._memory = store

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
            "timer": self.timers.snapshot(),
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

    def chat(self, body: dict[str, Any], facts: str = "") -> dict[str, Any]:
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
        pending = self._pop_clarify()
        power_pending = self._pop_power()
        taught = self._remember_skill(text)
        if taught or is_teach_attempt(text):
            spoken = taught or "这个只能绑已经配好的灯或启动项，不能自己加程序。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard
            )
        # F28：「记住，……」当场落盘并短确认，不绕模型——记忆这事确定性比文采重要
        memo = parse_memory_teach(text)
        if memo and self._memory is not None:
            key = self._memory.teach(memo)
            spoken = "记住了。" if key else "这条太长，你拆成短一点的说。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            _latency(turn, "memory_teach", ok=1 if key else 0)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard
            )
        memo_forget = parse_memory_forget(text)
        if memo_forget is not None and self._memory is not None:
            gone = self._memory.forget(memo_forget)
            spoken = f"忘掉了，{gone}。" if gone else "这个我本来就没记住。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            _latency(turn, "memory_forget", ok=1 if gone else 0)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard
            )
        control = looks_like_control(text)
        mood_extra = self._prepare_mood(text, control=control)
        if pending is not None:
            # F3：上一问有了下文就直接办；换话题了就当没问过，正常往下走
            answered = self._take_pending_answer(text, pending, turn)
            if answered is not None:
                spoken, skills = answered
                self._commit_reply(spoken)
                _latency(turn, "clarify", chars=len(spoken))
                return self._finish_spoken(
                    spoken, turn, seq, started, cue_llm=False, user=heard, skills=skills
                )
        asked = query_kind(text)
        if asked:
            spoken = self._answer_query(asked, text)
            self._commit_reply(spoken)
            _latency(turn, "query", kind=asked)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard
            )
        scene = self._handle_scene(text, turn)
        if scene:
            spoken, events = scene
            self._commit_reply(spoken)
            _latency(turn, "scene", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, skills=events
            )
        leaving = self._handle_leaving(text, turn)
        if leaving:
            spoken, events = leaving
            self._commit_reply(spoken)
            _latency(turn, "leave", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, skills=events
            )
        desk = self._handle_desk(text, turn)
        if desk:
            spoken, skills = desk
            self._commit_reply(spoken)
            _latency(turn, "desk", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, skills=skills
            )
        relay = self._handle_relay(text, turn)
        if relay:
            spoken, skills = relay
            self._commit_reply(spoken)
            _latency(turn, "relay", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, skills=skills
            )
        # 泰缇斯 / Hermes 已移除：不再做「简单还是复杂」的路由判定，
        # 所有对话直接交给伴侣模型（顺带省掉每次一条分类请求）。
        # F21：电源动作走两步确认，必须排在澄清前面——「关电脑」不能被
        # 反问成「要关哪个？」，也不能落到模型手里。
        power = self._handle_power(text, turn, power_pending)
        if power is not None:
            spoken, events = power
            self._commit_reply(spoken)
            _latency(turn, "power", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, skills=events
            )
        # F3：像开关指令却解不出设备的话，先问一句，别把「帮我关了」扔给模型瞎猜。
        question = (
            clarify_for(text, self._skills, self._aliases)
            if self._run_skill is not None and self._skills
            else None
        )
        if question is not None:
            self._pending_clarify = question
            self._commit_reply(question.asked)
            _latency(turn, "clarify_ask", chars=len(question.asked))
            return self._finish_spoken(
                question.asked, turn, seq, started, cue_llm=False, user=heard
            )
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
        self._pending_clarify = None
        self._pending_power = None
        with self._lock:
            self._seq += 1
            self._speaking = False
        self.reads.cancel_all()
        self.tts.stop()
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

    def speak_up(self, text: str) -> bool:
        """主动开口：到点说一句。被三道闸拦下（手机不在、在打游戏、伴侣没开）
        就返回 False——不算配额，下个到点的时刻还会再试。"""
        if not self.config.enabled:
            return False
        spoken = clip_announce(str(text or ""))
        if not spoken:
            return False
        turn = _turn_id({"text": spoken})
        started = time.monotonic()
        skipped = self._unsolicited_block()
        if skipped:
            _latency(turn, "announce_skip", reason=skipped, source="proactive")
            return False
        _latency(turn, "announce_recv", chars=len(spoken), source="proactive")
        with self._lock:
            self._seq += 1
            seq = self._seq
        self._stop_tts()
        self._commit_reply(spoken)
        self._finish_spoken(spoken, turn, seq, started, cue_llm=False, user="（定时）")
        return True

    def diary_compose(self, material: str) -> str:
        """F29 日记：拿材料换一段她口吻的正文。一次性问完，不流式、不进对话史。

        大脑不在场或没开伴侣就抛错，由调用方退到兜底文案——日记缺一天的
        理由可以是「大脑不在」，不能是「她没试」。
        """
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        text = self._complete(diary_messages(str(material or "")), temperature=0.8)
        cleaned = clean_entry(text)
        if not cleaned:
            raise HubError("companion_unavailable", "日记没写出来")
        return cleaned

    # ---- 朗读任务的回调 ------------------------------------------------------
    # reads.ReadJobs 干活，出声和状态都在这一侧；下面几个方法就是两边之间的缝。

    def stop_playback(self) -> None:
        self.tts.stop()

    def cue_sentence(self, text: str, ident: str, seq: int) -> bool:
        return self._cue_sentence(text, ident, seq)

    def synthesize(self, text: str, ident: str) -> bytes | None:
        return self.tts.speak(text, ident, play_only=False, play=False)

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
        if stage == "read_job_done" and self.on_read_done is not None:
            seconds = fields.get("sec")
            try:
                seconds = float(seconds) if seconds is not None else 0.0
                if seconds > 0:
                    self.on_read_done(seconds)
            except Exception as exc:  # noqa: BLE001 - 台账坏了不该连累朗读
                logs.swallowed("周报朗读记账", exc)

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
        if kind == "schedule":
            # F13 日程：日历拿不到就空手回，她照实说，不编
            getter = self._world.get("calendar")
            events: Any = None
            if callable(getter):
                try:
                    events = getter(schedule_day(text))
                except Exception:  # noqa: BLE001 - 日历挂了当没配
                    events = None
            return speak_schedule(events, schedule_day(text))
        if kind == "last":
            return speak_last(self._recent)
        return "我不太清楚。"

    def _handle_scene(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「我要睡了」这类口令直接跑预置场景，不用绕模型。"""
        scenes = self._world.get("scenes") or []
        run = self._world.get("scene")
        if not scenes or not callable(run):
            return None
        needle = normalize_phrase(text)
        if not needle:
            return None
        hits: list[tuple[int, dict[str, Any]]] = []
        for item in scenes:
            for phrase in item.get("phrases") or []:
                key = normalize_phrase(str(phrase))
                if key and key in needle:
                    hits.append((len(key), item))
                    break
        if not hits:
            return None
        hits.sort(key=lambda row: row[0], reverse=True)
        item = hits[0][1]
        ident = str(item.get("id") or "")
        name = str(item.get("name") or ident)
        try:
            result = run(ident, say=False)
        except HubError as exc:
            _latency(turn, "scene_error", id=ident, code=exc.code)
            return (
                "这次没动成。",
                [{"id": ident, "name": name, "scene": True, "ok": False, "code": exc.code}],
            )
        ok = bool(result.get("ok"))
        _latency(turn, "scene_ok", id=ident, ok=1 if ok else 0)
        spoken = str(item.get("say") or "").strip() or f"好，{name}。"
        return spoken, [{"id": ident, "name": name, "scene": True, "ok": ok}]

    def _handle_relay(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「跟她说我晚点回」（F33 反向 / F4 语音留言）：递到手机，她只确认一句。

        失败不重试——递话是即发即忘的，用户会自己再说一次。
        """
        content = parse_relay(text)
        if content is None:
            return None
        post = self._world.get("notify")
        if not callable(post):
            return None
        try:
            post(content)
        except Exception as exc:  # noqa: BLE001 - 递话失败不是聊天失败
            _latency(turn, "relay_error", err=type(exc).__name__)
            return "这会儿没递到，等会儿再说一遍。", [{"id": "relay", "ok": False}]
        _latency(turn, "relay_ok", chars=len(content))
        return "写下来了，手机上会响一下。", [{"id": "relay", "ok": True}]

    def _handle_leaving(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「我出门了」（F18）：能关的全关，报告没关掉的。没接 Hub 就不认。"""
        if not parse_leaving(text) and not parse_leave_action(text):
            return None
        leave = self._world.get("leave")
        if not callable(leave):
            return None
        try:
            result = leave()
        except HubError as exc:
            _latency(turn, "leave_error", code=exc.code)
            return "这次没动成。", [{"id": "leave", "leave": True, "ok": False, "code": exc.code}]
        spoken = str(result.get("spoken") or "都关好了，路上小心。")
        _latency(turn, "leave_ok", ok=1 if result.get("ok") else 0, left=len(result.get("left") or []))
        return spoken, [{"id": "leave", "leave": True, "ok": bool(result.get("ok"))}]

    def _handle_desk(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        act = parse_stopwatch_action(text)
        if act is None:
            if parse_stopwatch_start(text):
                act = "start"
            elif parse_stopwatch_stop(text):
                act = "stop"
        if act == "start":
            self.timers.start_stopwatch()
            _latency(turn, "stopwatch_start")
            return "好，开始计时。", [{"id": "stopwatch", "action": "start", "ok": True}]
        if act == "stop":
            elapsed = self.timers.stop_stopwatch()
            if elapsed is None:
                return "还没开始记呢。", [{"id": "stopwatch", "action": "stop", "ok": False}]
            _latency(turn, "stopwatch_stop", sec=elapsed)
            return speak_stopwatch_result(elapsed), [
                {"id": "stopwatch", "action": "stop", "sec": elapsed, "ok": True}
            ]
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
        noise_act = infer_noise(text)
        if noise_act:
            spoken = self._run_noise(noise_act, turn)
            ok = spoken not in _NOISE_FAIL_SPOKEN
            return spoken, [{"id": "noise", **noise_act, "ok": ok}]
        music_act = infer_music(text)
        if music_act:
            spoken = self._run_music(music_act, turn)
            ok = spoken not in _NOISE_FAIL_SPOKEN
            return spoken, [{"id": "music", **music_act, "ok": ok}]
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

    # ---- F3 追问澄清 ---------------------------------------------------------

    def _pop_clarify(self) -> PendingClarify | None:
        """取走上一次追问。过期的当没问过。"""
        pending = self._pending_clarify
        self._pending_clarify = None
        if pending is None or pending.expired():
            return None
        return pending

    def _take_pending_answer(
        self, text: str, pending: PendingClarify, turn: str
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """上一问有了下文就直接办。None = 换话题了，正常流程接着走。"""
        answer = take_answer(text, pending, self._skills, self._aliases)
        if answer is None:
            return None
        if answer.kind == "run":
            events = self._run_clarified(str(answer.ident), pending.on, turn)
            spoken = "好。" if events[-1].get("ok") else "这次没动成。"
            return spoken, events
        if answer.kind == "cancel":
            return "好。", []
        return "没听清。", []

    def _run_clarified(self, ident: str, on: bool, turn: str) -> list[dict[str, Any]]:
        """追问认领后的那一按，事件形状跟 apply_device_skills 一致。"""
        name = next((item["name"] for item in self._skills if item["id"] == ident), ident)
        try:
            call_skill(self._run_skill, ident, on)
        except HubError as exc:
            _latency(turn, "skill_error", id=ident, code=exc.code)
            return [{"id": ident, "name": name, "on": on, "ok": False, "code": exc.code}]
        _latency(turn, "skill_ok", id=ident, on=int(on))
        return [{"id": ident, "name": name, "on": on, "ok": True}]

    # ---- F21 电源动作：危险，必须两步 ---------------------------------------

    def _pop_power(self) -> str | None:
        """取走挂着的确认。过期的当没问过——「确认」不能隔一夜还算数。"""
        pending = self._pending_power
        self._pending_power = None
        if pending is None or time.monotonic() - pending[1] > POWER_TTL_SEC:
            return None
        return pending[0]

    def _handle_power(
        self, text: str, turn: str, pending: str | None
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """「关电脑 / 锁屏 / 让电脑睡会儿」（F21）：先问后做，听见「确认」才动手。

        两步是死规矩（规格 5.2：危险动作单独说、单独确认）。问出去之后，
        同一个动作再说一遍也算数（有人就这么答）；换动作就改问新的；
        说别的去了当没问过，正常流程接着走。
        """
        action = infer_power(text)
        if pending is None:
            if action is None or not callable(self._world.get("power")):
                return None
            self._pending_power = (action, time.monotonic())
            _latency(turn, "power_ask", action=action)
            return _POWER_ASK[action], [
                {"id": "power", "action": action, "confirm": True, "ok": False}
            ]
        if action == pending:
            return self._fire_power(action, turn)
        if action is not None:
            self._pending_power = (action, time.monotonic())
            _latency(turn, "power_ask", action=action)
            return _POWER_ASK[action], [
                {"id": "power", "action": action, "confirm": True, "ok": False}
            ]
        if _POWER_CANCEL_RE.search(text):
            return "好，不动了。", [{"id": "power", "action": pending, "ok": False}]
        if _POWER_CONFIRM_RE.search(text) or _POWER_YES_RE.fullmatch(text.strip()):
            return self._fire_power(pending, turn)
        return None

    def _fire_power(self, action: str, turn: str) -> tuple[str, list[dict[str, Any]]]:
        self._pending_power = None
        power = self._world.get("power")
        if not callable(power):
            return "这边还没接上电脑。", [{"id": "power", "action": action, "ok": False}]
        try:
            power(action)
        except HubError as exc:
            _latency(turn, "power_error", action=action, code=exc.code)
            return f"{_POWER_LABEL[action]}没动成。", [
                {"id": "power", "action": action, "ok": False, "code": exc.code}
            ]
        _latency(turn, "power_ok", action=action)
        return _POWER_ACK[action], [{"id": "power", "action": action, "ok": True}]

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

    def _run_noise(self, act: dict[str, Any], turn: str) -> str:
        """F31 白噪音：放/停。到点她自己淡出，不用再喊一声。"""
        fn = self._world.get("noise")
        if not callable(fn):
            return "这台 Mini 还不能放白噪音。"
        try:
            result = fn(act)
        except HubError as exc:
            _latency(turn, "skill_error", id="noise", code=exc.code)
            return _NOISE_FAIL_SPOKEN[0]
        except Exception:  # noqa: BLE001 - Mini 连不上也算没动成
            _latency(turn, "skill_error", id="noise", code="offline")
            return _NOISE_FAIL_SPOKEN[0]
        if not isinstance(result, dict) or not result.get("ok"):
            return _NOISE_FAIL_SPOKEN[0]
        if act.get("action") == "off":
            _latency(turn, "skill_ok", id="noise", action="off")
            return "关掉了。"
        labels = {"rain": "雨声", "waves": "海浪", "fan": "风扇", "brown": "白噪音"}
        label = labels.get(str(result.get("name") or ""), "白噪音")
        minutes = result.get("minutes") or 30
        _latency(turn, "skill_ok", id="noise", name=result.get("name"), minutes=minutes)
        return f"{label}放上了，{minutes} 分钟后停。"

    def _run_music(self, act: dict[str, Any], turn: str) -> str:
        """F32 本地电台：放/停。歌在 Mini 那边的 music/ 目录里，到点自己淡出。"""
        fn = self._world.get("music")
        if not callable(fn):
            return "这台 Mini 还不能放音乐。"
        try:
            result = fn(act)
        except HubError as exc:
            _latency(turn, "skill_error", id="music", code=exc.code)
            return "这次没动成。"
        except Exception:  # noqa: BLE001 - Mini 连不上也算没动成
            _latency(turn, "skill_error", id="music", code="offline")
            return "这次没动成。"
        if not isinstance(result, dict):
            return "这次没动成。"
        if not result.get("ok"):
            if result.get("error") == "empty":
                return "这台 Mini 还没有能放的音乐，往 music 目录里放几首。"
            return "这次没动成。"
        if act.get("action") == "off":
            _latency(turn, "skill_ok", id="music", action="off")
            return "停了。"
        minutes = result.get("minutes") or 60
        _latency(turn, "skill_ok", id="music", minutes=minutes)
        return f"放歌了，{minutes} 分钟后停。"

    def _arm_timer(self, seconds: int) -> str:
        self._cancel_timer()
        timer = threading.Timer(seconds, self._timer_fire)
        timer.daemon = True
        with self._lock:
            self._timer = timer
        timer.start()
        self.timers.arm(seconds)
        _latency("-", "timer_set", sec=seconds)
        return speak_timer(seconds)

    def _timer_fire(self) -> None:
        with self._lock:
            self._timer = None
            self._seq += 1
            seq = self._seq
        self.timers.clear()
        spoken = "到时间了。"
        turn = uuid.uuid4().hex[:12]
        self._cue_sentence(spoken, turn, seq)
        self._remember("（计时）", spoken)
        _latency(turn, "timer_fire")

    def _cancel_timer(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
        self.timers.clear()
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
        if self._memory is not None and not looks_like_control(heard):
            # F28：闲聊里捞候选事实。失败不影响本轮对话，留一行痕迹就好
            try:
                learned = self._memory.note_turn(heard)
            except Exception as exc:  # noqa: BLE001 - 记忆坏了不该连累说话
                logs.swallowed("长期记忆抽取", exc)
                return
            if learned:
                _latency("-", "memory_learn", n=len(learned))

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
        if parse_leave_action(raw_reply):
            leave = self._world.get("leave")
            if callable(leave):
                try:
                    result = leave()
                except HubError as exc:
                    events.append(
                        {"id": "leave", "leave": True, "ok": False, "code": exc.code}
                    )
                else:
                    events.append({"id": "leave", "leave": True, "ok": bool(result.get("ok"))})
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
