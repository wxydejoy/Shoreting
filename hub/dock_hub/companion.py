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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dock_hub.chat_log import ChatLog
from dock_hub.config import CompanionConfig, ID_RE
from dock_hub.errors import HubError

MAX_CLIPS = 8
MAX_READ_CHARS = 8000
MAX_READ_JOBS = 24
READ_CHARS_PER_SEC = 4.0
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
_ACTION_PARSE = re.compile(r"^\s*ACTION:\s*(\S+?)\.(on|off|run)\s*$", re.IGNORECASE | re.MULTILINE)
_ACTION_BRI = re.compile(
    r"^\s*ACTION:\s*(\S+?)\.(?:bri|brightness)\.(\d{1,3})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ACTION_TEMP = re.compile(
    r"^\s*ACTION:\s*(\S+?)\.(?:temp|temperature)\.(\d{1,2})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CONTROL_HINT = re.compile(
    r"(打开|关掉|关闭|关上|开启|开一下|关一下|开灯|关灯|开空调|关空调|启动|"
    r"无畏契约|valorant|打瓦|开黑|打游戏|玩游戏|启动游戏|开游戏|wegame|"
    r"抖音|亮一点|调亮|暗一点|调暗|调到|调成|设成|设为|设定|再亮|再暗|灯带|台灯|挂灯|"
    r"暂停|下一首|上一首|切歌|继续播放|继续放|静音|音量|声音大|声音小|"
    r"叫我|提醒我|喊我)"
)
_QUERY_TIME = re.compile(r"(几点|几号|星期几|周几|礼拜几|什么日子|现在.*时间)")
_QUERY_TEMP = re.compile(
    r"(多少度|几度|室温|潮不潮|湿度|屋里.*(?:度|温|热|潮|湿)|室内.*(?:度|温|热|潮|湿))"
)
_QUERY_PC = re.compile(r"(热|忙|卡|占用|cpu|内存|显卡)", re.IGNORECASE)
_QUERY_LIGHT = re.compile(r"(开了没|亮着吗|开着吗|关了没|亮着没有)")
_QUERY_MEDIA = re.compile(r"(在播什么|放的什么|在放什么|现在放的|播放的是什么|放什么歌)")
_QUERY_LAST = re.compile(r"(刚才那句|刚才你说|刚才说了什么|上一句|你刚说)")
_QUERY_MINI = re.compile(r"(mac\s*mini|mini|麦金塔|迷你|咪妮|麦克迷)", re.IGNORECASE)
_MEDIA_NEXT = re.compile(r"(下一首|切歌|换一首|下一曲)")
_MEDIA_PREV = re.compile(r"(上一首|上一曲)")
_MEDIA_PAUSE = re.compile(r"(暂停|停下|别放了)")
_MEDIA_RESUME = re.compile(r"(继续放|继续播放|接着放)")
_VOLUME_UP = re.compile(r"(声音大|音量大|大声点|大声一)")
_VOLUME_DOWN = re.compile(r"(声音小|音量小|小声点|小声一)")
_VOLUME_UNMUTE = re.compile(r"(取消静音|打开声音)")
_VOLUME_MUTE = re.compile(r"(静音)")
_ACTION_MEDIA = re.compile(
    r"^\s*ACTION:\s*media\.(toggle|next|previous)\s*$", re.IGNORECASE | re.MULTILINE
)
_ACTION_VOLUME = re.compile(
    r"^\s*ACTION:\s*volume\.(up|down|mute)\s*$", re.IGNORECASE | re.MULTILINE
)
_ACTION_TIMER = re.compile(
    r"^\s*ACTION:\s*timer\.(\d{1,3})\s*$", re.IGNORECASE | re.MULTILINE
)
_RECENT_TURNS = 4
_RECENT_CLIP = 72
PRESENCE_SEC = 90
_BRIGHT_UP = re.compile(r"(亮一点|调亮|再亮|亮一些)")
_BRIGHT_DOWN = re.compile(r"(暗一点|调暗|再暗|暗一些|暗一点)")
_BRIGHT_SET = re.compile(
    r"(?:调到|亮度(?:调到|设为|设成)?)\s*([0-9]{1,3}|[零〇一二两三四五六七八九十百]+)"
)
_TEMP_SET = re.compile(
    r"(?:调到|调成|设成|设为|设定|调至)?\s*([0-9]{2}|[零〇一二两三四五六七八九十]+)\s*度"
)
_TEMP_DEGREE = re.compile(r"(?:\d|[零〇一二两三四五六七八九十]+)\s*度")
_USER_TIRED = re.compile(r"(累|困|乏|不想动|好累|疲惫)")
_USER_FRUSTRATED = re.compile(r"(烦|气死|吵|够了|烦死)")
_USER_BRIGHT = re.compile(r"(开心|谢谢|哈哈|好呀|不错)")
_USER_GREET = re.compile(r"^(小岸|岸宝|晚上好|早上好|中午好|下午好|晚安|嗨|你好)[。！？.?]*$")
_TURN_ON = re.compile(r"(打开|开启|开一下|开灯|开空调|启动)")
_TURN_OFF = re.compile(r"(关掉|关闭|关上|关一下|关灯|关空调)")
_TEACH = re.compile(
    r"(?:帮我)?(?:创建|新建|加一个)(?:一个)?技能[，,：:]?"
    r"(?:以后)?(?:我说|我喊)?[「『\"“]?(?P<phrase>.{2,16}?)[」』\"”]?"
    r"就(?:帮我)?(?:去)?(?:打开|启动|开启|关掉|关闭|关上|开)(?P<target>.+)"
)
_TEACH_ALT = re.compile(
    r"以后(?:我说|我喊)(?P<phrase>.{2,16}?)就(?:帮我)?(?:打开|启动|开启|关掉|关闭|关上|开)(?P<target>.+)"
)
_TEACH_GAME = re.compile(
    r"(?P<phrase>打游戏|玩游戏|开黑).{0,16}?就是.{0,12}?"
    r"(?P<target>打瓦|无畏契约|valorant|无畏|瓦)",
    re.IGNORECASE,
)
_ASR_CONTRACT = re.compile(r"五[位味微]契约|无[谓位味]契约")
_ASR_LEDSTRIP = re.compile(r"(?<![台挂])(?:灯带|灯袋|灯戴|登带|凳带)")
_ASR_LEDSTRIP_ASR = re.compile(r"(打开|关掉|关闭|关上|开一下|关一下|开启).{0,6}当代")
_ASR_LAMP = re.compile(r"(?:台灯|台登|太灯|抬灯)")
_ASR_HIT_ME = re.compile(r"(?<![别不])打我(?=[啊呀啦哈。！？.?]|$)")
_ASR_WANT_HIT = re.compile(r"(?:我要|我想)打我")
_PUNCT = re.compile(r"[。！？，,、.\s「」『』\"“”]+")
_FORBIDDEN_PHRASE = frozenset({"技能", "创建技能", "打开", "启动", "关掉", "关闭", "开启", "关上"})


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
        spoken = _ack_after_skills(spoken, skills)
        spoken = _ack_ledstrip(text, spoken, skills)
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


def skill_prompt(skills: list[dict[str, str]]) -> str:
    toggles = [item for item in skills if item.get("kind", "toggle") not in {"run", "sensor"}]
    launches = [item for item in skills if item.get("kind") == "run"]
    dimmable = [item for item in toggles if item.get("brightness")]
    lines: list[str] = []
    if toggles:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in toggles)
        lines.append(f"可开关（未告知当前开没开）：{names}。")
        lines.append("对方明确要开关时，口头短应一声，并单独一行写 ACTION: <id>.on 或 ACTION: <id>.off。")
    if dimmable:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in dimmable)
        lines.append(f"可调亮度：{names}。对方要调亮暗时，ACTION: <id>.bri.<1-100>。")
    climate = [item for item in toggles if item.get("temp")]
    if climate:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in climate)
        lines.append(f"可调温度：{names}。对方要调空调温度时，ACTION: <id>.temp.<16-30>。")
    if launches:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in launches)
        lines.append(f"可启动：{names}。")
        lines.append("对方明确要启动时，口头短应一声，并单独一行写 ACTION: <id>.run。")
    if skills:
        lines.append("闲聊不要写 ACTION。对方要把口令绑到上述设备时，口头答应即可，不要编造新程序。")
    lines.append(
        "暂停/下一首：ACTION: media.toggle 或 ACTION: media.next。"
        "音量：ACTION: volume.up / volume.down / volume.mute。"
        "倒计时：ACTION: timer.<分钟>。"
    )
    return "\n".join(lines)


def parse_action_lines(raw: str) -> list[tuple[str, bool]]:
    found: list[tuple[str, bool]] = []
    for match in _ACTION_PARSE.finditer(raw):
        found.append((match.group(1), match.group(2).lower() != "off"))
    return found


def parse_bri_actions(raw: str) -> list[tuple[str, dict[str, int]]]:
    found: list[tuple[str, dict[str, int]]] = []
    for match in _ACTION_BRI.finditer(raw):
        value = int(match.group(2))
        if 1 <= value <= 100:
            found.append((match.group(1), {"value": value}))
    return found


def parse_temp_actions(raw: str) -> list[tuple[str, dict[str, int]]]:
    found: list[tuple[str, dict[str, int]]] = []
    for match in _ACTION_TEMP.finditer(raw):
        value = int(match.group(2))
        if 16 <= value <= 30:
            found.append((match.group(1), {"value": value}))
    return found


def fold_game_asr(text: str) -> str:
    """把打瓦 / 无畏契约 / 灯带台灯的常见口误折回技能能认的词。"""
    out = (text or "").strip()
    if not out:
        return out
    out = _ASR_CONTRACT.sub("无畏契约", out)
    out = out.replace("微企业", "WeGame")
    out = _ASR_WANT_HIT.sub("我要打瓦", out)
    out = _ASR_HIT_ME.sub("打瓦", out)
    if _ASR_LEDSTRIP_ASR.search(out):
        out = _ASR_LEDSTRIP_ASR.sub(lambda m: m.group(0).replace("当代", "灯带"), out)
    if _ASR_LEDSTRIP.search(out) and "台灯" not in out:
        out = _ASR_LEDSTRIP.sub("灯带", out)
    elif _ASR_LAMP.search(out) and "灯带" not in out:
        out = _ASR_LAMP.sub("台灯", out)
    return out


def looks_like_control(text: str) -> bool:
    return bool(_CONTROL_HINT.search(fold_game_asr(text)))


def looks_like_query(text: str) -> bool:
    return query_kind(text) is not None


def query_kind(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _QUERY_TIME.search(stripped):
        return "time"
    if _QUERY_LAST.search(stripped):
        return "last"
    if _QUERY_MEDIA.search(stripped):
        return "media"
    if re.search(r"(内存|运存)", stripped, re.IGNORECASE):
        return "pc"
    if re.search(r"(电脑|cpu|处理器|显卡|gpu)", stripped, re.IGNORECASE) and re.search(
        r"(温度|多少度|几度)", stripped
    ):
        return "pc"
    if _QUERY_MINI.search(stripped) and re.search(
        r"(温度|多少度|几度|占用|内存|热|忙)", stripped
    ):
        return "mini"
    if re.search(r"(显卡|gpu)", stripped, re.IGNORECASE):
        return "pc"
    if "电脑" in stripped and _QUERY_PC.search(stripped):
        return "pc"
    if _QUERY_TEMP.search(stripped):
        return "temp"
    if _QUERY_LIGHT.search(stripped) and ("灯" in stripped or "台灯" in stripped):
        return "light"
    return None


def is_teach_attempt(text: str) -> bool:
    folded = fold_game_asr(text)
    if "以后我说" in folded or "以后我喊" in folded:
        return True
    if ("技能" in folded) and any(word in folded for word in ("创建", "新建", "加一个")):
        return True
    if _TEACH_GAME.search(folded):
        return True
    return False


def skill_nicknames(item: dict[str, str]) -> list[str]:
    names = [item["id"], item["name"]]
    if "无畏契约" in item["name"] or item["id"] == "wegame":
        names.extend(
            [
                "无畏契约",
                "无畏",
                "valorant",
                "打瓦",
                "瓦",
                "打游戏",
                "玩游戏",
                "开黑",
                "启动游戏",
                "开游戏",
                "五位契约",
                "WeGame",
                "wegame",
            ]
        )
    if "抖音" in item["name"] or item["id"] == "douyin":
        names.extend(["抖音", "tiktok", "刷抖音"])
    if "灯带" in item["name"] or item["id"] in {"desk-led", "ledstrip", "ws2812"}:
        names.extend(["灯带", "氛围灯", "LED灯带"])
    return [name for name in names if name]


def _is_ledstrip(item: dict[str, str]) -> bool:
    if item.get("kind") == "run":
        return False
    name = str(item.get("name") or "")
    ident = str(item.get("id") or "")
    return "灯带" in name or ident in {"desk-led", "ledstrip", "ws2812"}


def constrain_light_actions(
    text: str,
    wanted: list[tuple[Any, Any]],
    skills: list[dict[str, str]],
) -> list[tuple[Any, Any]]:
    """User said 灯带: don't operate 挂灯 / 台灯 even if the model wrote ACTION: 挂灯.off."""
    if "灯带" not in (text or "") or not wanted:
        if "台灯" in (text or "") and "灯带" not in (text or "") and wanted:
            lamp_ids = {
                item["id"]
                for item in skills
                if item.get("id") == "lamp" or item.get("name") == "台灯"
            }
            kept_lamp: list[tuple[Any, Any]] = []
            for token, payload in wanted:
                ident = resolve_skill(str(token), skills)
                if ident and ident in lamp_ids:
                    kept_lamp.append((token, payload))
            return kept_lamp
        return wanted
    led_ids = {item["id"] for item in skills if _is_ledstrip(item)}
    kept: list[tuple[Any, Any]] = []
    for token, payload in wanted:
        ident = resolve_skill(str(token), skills)
        if ident and ident in led_ids:
            kept.append((token, payload))
    return kept


def _ack_ledstrip(text: str, spoken: str, events: list[dict[str, Any]]) -> str:
    if "灯带" not in (text or ""):
        return spoken
    ok = any(
        item.get("ok")
        and (
            "灯带" in str(item.get("name") or "")
            or str(item.get("id") or "") in {"desk-led", "ledstrip", "ws2812"}
        )
        for item in events
    )
    if ok:
        return spoken
    return "灯带还没接到 Hub。"


def resolve_skill(token: str, skills: list[dict[str, str]]) -> str | None:
    needle = token.strip().lower()
    if not needle:
        return None
    for item in skills:
        if item["id"].lower() == needle or item["name"].lower() == needle:
            return item["id"]
        for nick in skill_nicknames(item):
            if nick.lower() == needle:
                return item["id"]
    for item in skills:
        for nick in skill_nicknames(item):
            if len(nick) >= 2 and nick.lower() in needle:
                return item["id"]
    return None


def infer_control(
    text: str,
    skills: list[dict[str, str]],
    aliases: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[str, bool]]:
    text = fold_game_asr(text)
    hits: list[tuple[str, bool]] = []
    for phrase, spec in (aliases or {}).items():
        if phrase and phrase in text:
            ident = spec.get("id") if isinstance(spec, dict) else spec
            on = spec.get("on", True) if isinstance(spec, dict) else True
            if ident:
                hits.append((str(ident), bool(on)))
    if hits:
        return hits
    if not looks_like_control(text):
        return []
    want_on = bool(_TURN_ON.search(text))
    want_off = bool(_TURN_OFF.search(text))
    if want_on == want_off:
        if "关" in text and "开" not in text:
            want_on, want_off = False, True
        elif "开" in text and "关" not in text:
            want_on, want_off = True, False
        elif any(
            nick in text
            for item in skills
            if item.get("kind") == "run"
            for nick in skill_nicknames(item)
            if len(nick) >= 2
        ):
            want_on, want_off = True, False
        else:
            return []
    on = want_on
    if "灯带" in text:
        leds = [item for item in skills if _is_ledstrip(item)]
        if leds:
            return [(leds[0]["id"], on)]
        return []
    if "台灯" in text:
        lamps = [
            item
            for item in skills
            if item.get("kind") != "run"
            and (item.get("id") == "lamp" or item.get("name") == "台灯")
        ]
        if lamps:
            return [(lamps[0]["id"], on)]
    for item in skills:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            if item.get("kind") == "run":
                if want_off:
                    continue
                hits.append((item["id"], True))
            else:
                hits.append((item["id"], on))
    if hits:
        return hits
    if re.search(r"[开关].*灯|灯.*[开关]|开灯|关灯", text):
        lamps = [
            item
            for item in skills
            if item.get("kind") != "run"
            and (item.get("id") == "lamp" or item.get("name") == "台灯")
        ]
        if lamps:
            return [(lamps[0]["id"], on)]
        lights = [
            item
            for item in skills
            if "灯" in item.get("name", "")
            and not _is_ledstrip(item)
            and item.get("name") != "挂灯"
        ]
        if lights:
            return [(lights[0]["id"], on)]
    if re.search(r"空调", text):
        acs = [item for item in skills if "空调" in item["name"]]
        if acs:
            return [(acs[0]["id"], on)]
    return []


def _dimmable(skills: list[dict[str, str]]) -> list[dict[str, str]]:
    lit = [item for item in skills if item.get("brightness")]
    if lit:
        return lit
    return [item for item in skills if "灯" in item.get("name", "")]


def infer_brightness(
    text: str, skills: list[dict[str, str]]
) -> list[tuple[str, dict[str, int]]]:
    if not skills:
        return []
    if _TEMP_DEGREE.search(text) and "亮度" not in text:
        return []
    delta = 0
    value: int | None = None
    match = _BRIGHT_SET.search(text)
    if match:
        parsed = parse_cn_int(match.group(1))
        if parsed is not None and 1 <= parsed <= 100:
            value = parsed
    elif _BRIGHT_UP.search(text):
        delta = 15
    elif _BRIGHT_DOWN.search(text):
        delta = -15
    else:
        return []
    lights = _dimmable(skills)
    if not lights:
        return []
    chosen = lights[0]
    for item in lights:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            chosen = item
            break
    spec: dict[str, int] = {"value": value} if value is not None else {"delta": delta}
    return [(chosen["id"], spec)]


def infer_temp(
    text: str, skills: list[dict[str, str]]
) -> list[tuple[str, dict[str, int]]]:
    climate = [item for item in skills if item.get("temp")]
    if not climate:
        return []
    match = _TEMP_SET.search(text)
    if not match:
        return []
    parsed = parse_cn_int(match.group(1))
    if parsed is None:
        return []
    chosen = climate[0]
    for item in climate:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            chosen = item
            break
    else:
        if "空调" not in text and len(climate) != 1:
            return []
    lo = int(chosen.get("temp_min") or 16)
    hi = int(chosen.get("temp_max") or 30)
    if parsed < lo or parsed > hi:
        parsed = max(lo, min(hi, parsed))
    return [(chosen["id"], {"value": parsed})]


def parse_cn_int(raw: str) -> int | None:
    text = (raw or "").strip()
    digits = re.search(r"(\d{1,3})", text)
    if digits:
        return int(digits.group(1))
    if "百" in text:
        return 100
    digits_map = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = 1 if not left else digits_map.get(left)
        ones = 0 if not right else digits_map.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(text) == 1:
        return digits_map.get(text)
    return None


_CN_SMALL = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
_WEEKDAYS = "一二三四五六日"


def cn_num(n: int) -> str:
    n = max(0, int(n))
    if n <= 10:
        return _CN_SMALL[n]
    if n < 20:
        return "十" + ("" if n == 10 else _CN_SMALL[n - 10])
    tens, ones = divmod(n, 10)
    if n >= 100:
        return str(n)
    return _CN_SMALL[tens] + "十" + ("" if ones == 0 else _CN_SMALL[ones])


def cn_clock(hour: int, minute: int) -> str:
    clock = cn_num(hour) + "点"
    if minute == 0:
        return clock
    if minute < 10:
        return clock + "零" + _CN_SMALL[minute]
    return clock + cn_num(minute)


def period_label(hour: int) -> str:
    if 5 <= hour < 11:
        return "清晨"
    if 11 <= hour < 17:
        return "午后"
    if 17 <= hour < 23:
        return "夜里"
    return "深夜"


def appraise_user(text: str) -> str:
    stripped = (text or "").strip()
    if _USER_GREET.match(stripped):
        return "greeting"
    if _USER_TIRED.search(stripped):
        return "tired"
    if _USER_FRUSTRATED.search(stripped):
        return "frustrated"
    if _USER_BRIGHT.search(stripped):
        return "bright"
    return "calm"


def next_self(prev: str, user: str, hour: int) -> str:
    late = hour >= 23 or hour < 5
    if user == "tired" or (late and user != "bright"):
        target = "concerned"
    elif user == "frustrated":
        target = "distant"
    elif user == "bright":
        target = "warm"
    elif user == "greeting" and not late:
        target = "warm"
    elif late:
        target = "weary"
    else:
        target = "quiet"
    if prev == "concerned" and target in {"quiet", "warm"} and user != "bright":
        return "concerned"
    return target


def allow_ask(mood: dict[str, Any], control: bool) -> bool:
    if control or mood.get("last_ask"):
        return False
    user = str(mood.get("user") or "calm")
    if user in {"greeting", "frustrated"}:
        return False
    return True


def decay_mood(mood: dict[str, Any], now: datetime) -> dict[str, Any]:
    raw = mood.get("updated_at")
    if not raw:
        return mood
    try:
        then = datetime.fromisoformat(str(raw))
    except ValueError:
        return mood
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    if now - then < timedelta(hours=1):
        return mood
    out = dict(mood)
    out["self"] = "quiet"
    out["user"] = "calm"
    out["last_ask"] = False
    return out


def unsolicited_block_reason(
    *,
    connected: bool,
    game: bool,
) -> str | None:
    if not connected:
        return "disconnected"
    if game:
        return "game"
    return None


def default_mood() -> dict[str, Any]:
    return {"user": "calm", "self": "quiet", "last_ask": False, "allow_ask": False}


_USER_CN = {
    "calm": "平静",
    "tired": "疲倦",
    "frustrated": "烦",
    "bright": "还轻快",
    "greeting": "在打招呼",
}
_SELF_CN = {
    "quiet": "安静",
    "warm": "温",
    "concerned": "担心",
    "weary": "乏",
    "distant": "远一点",
}


def mood_prompt(mood: dict[str, Any], hour: int) -> str:
    rules: list[str] = []
    user = str(mood.get("user") or "calm")
    self_tag = str(mood.get("self") or "quiet")
    if user == "tired":
        rules.append("先应人，短一些。")
    if user == "frustrated":
        rules.append("少说话，先办事，不要讲道理。")
    if self_tag == "weary":
        rules.append("更短，少堆诗意。")
    if mood.get("allow_ask"):
        rules.append("可以轻轻问一句。")
    else:
        rules.append("不要提问。")
    return (
        f"此刻：{period_label(hour)}。"
        f"漂泊者：{_USER_CN.get(user, '平静')}。"
        f"你：{_SELF_CN.get(self_tag, '安静')}。\n"
        f"策略：{''.join(rules)}"
    )


def speak_time(now: datetime, self_tag: str, text: str = "") -> str:
    asked = text or ""
    if re.search(r"(星期几|周几|礼拜几)", asked):
        return f"今天星期{_WEEKDAYS[now.weekday()]}。"
    if re.search(r"(几号|什么日子)", asked) and not re.search(r"几点", asked):
        return f"今天{cn_num(now.month)}月{cn_num(now.day)}号。"
    clock = cn_clock(now.hour, now.minute)
    late = now.hour >= 23 or now.hour < 5
    if self_tag == "concerned" or late:
        return f"已经{clock}了。"
    return f"现在是{clock}。"


def speak_temp(payload: Any, text: str) -> str:
    if not isinstance(payload, dict) or payload.get("celsius") is None:
        return "我这边还没有室温。"
    celsius = payload.get("celsius")
    try:
        value = float(celsius)
    except (TypeError, ValueError):
        return "我这边还没有室温。"
    spoken = f"屋里{value:g}度。"
    if "潮" in text or "湿" in text:
        humidity = payload.get("humidity")
        if humidity is None:
            return spoken + "湿度我还不清楚。"
        try:
            humid = float(humidity)
        except (TypeError, ValueError):
            return spoken
        if humid >= 70:
            return spoken + "有一点潮。"
        return spoken + "还不潮。"
    return spoken


def _pc_num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def speak_memory(payload: dict[str, Any]) -> str:
    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
    pct = _pc_num(memory.get("percent"))
    used = _pc_num(memory.get("used_gb"))
    if pct is None:
        return "内存我还没读到。"
    if used is not None:
        return f"内存{int(pct)}%，用了{used:g}G。"
    return f"内存{int(pct)}%。"


def speak_gpu(payload: dict[str, Any]) -> str:
    gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
    pct = _pc_num(gpu.get("percent"))
    if pct is None:
        return "显卡占用我还没读到。"
    name = str(gpu.get("name") or "").strip()
    if name:
        return f"{name}百分之{int(pct)}。"
    return f"显卡百分之{int(pct)}。"


def speak_pc_temp(payload: dict[str, Any]) -> str:
    cpu = payload.get("cpu") if isinstance(payload.get("cpu"), dict) else {}
    gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
    cpu_t = _pc_num(cpu.get("temp_celsius"))
    gpu_t = _pc_num(gpu.get("temp_celsius"))
    bits: list[str] = []
    if cpu_t is not None:
        bits.append(f"CPU {int(cpu_t)}度")
    if gpu_t is not None:
        bits.append(f"显卡 {int(gpu_t)}度")
    if not bits:
        return "温度我还没读到。"
    return "电脑现在" + "，".join(bits) + "。"


def speak_mini(desk: Any, text: str = "") -> str:
    mini = desk.get("mini") if isinstance(desk, dict) else None
    pc = mini.get("pc") if isinstance(mini, dict) else None
    if not isinstance(pc, dict) or not pc:
        return "Mini 那边还没报到。"
    payload = dict(pc)
    if "online" not in payload:
        payload["online"] = True
    asked = text or ""
    if re.search(r"(温度|多少度|几度)", asked):
        line = speak_pc_temp(payload)
        return line.replace("电脑现在", "Mini 现在", 1) if line.startswith("电脑") else line
    if re.search(r"(内存|运存)", asked):
        line = speak_memory(payload)
        return "Mini " + line if not line.startswith("Mini") else line
    spoken = speak_pc(payload, asked)
    return spoken.replace("电脑", "Mini", 1)


def speak_pc(payload: Any, text: str = "") -> str:
    if not isinstance(payload, dict) or not payload.get("online"):
        return "电脑那边还没连上。"
    asked = text or ""
    if re.search(r"(内存|运存)", asked):
        return speak_memory(payload)
    if re.search(r"(温度|多少度|几度)", asked):
        return speak_pc_temp(payload)
    if re.search(r"(显卡|gpu)", asked, re.IGNORECASE):
        return speak_gpu(payload)
    cpu = payload.get("cpu") if isinstance(payload.get("cpu"), dict) else {}
    load = _pc_num(cpu.get("percent") if isinstance(cpu, dict) else None)
    if load is None:
        cpu_line = "电脑在，占用我还没读到。"
    elif load < 40:
        cpu_line = "电脑还不热。"
    elif load < 75:
        cpu_line = f"电脑有一点忙，CPU {int(load)}%。"
    else:
        cpu_line = f"电脑挺忙的，CPU {int(load)}%。"
    if re.search(r"占用", asked) and not re.search(r"(热|忙|cpu)", asked, re.IGNORECASE):
        mem = speak_memory(payload)
        if mem != "内存我还没读到。":
            return cpu_line.rstrip("。") + "，" + mem
    return cpu_line


def speak_light(text: str, skills: list[dict[str, str]], getter) -> str:
    lights = [item for item in skills if "灯" in item.get("name", "")] or [
        item for item in skills if item.get("kind", "toggle") != "run"
    ]
    chosen = None
    for item in lights:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            chosen = item
            break
    if chosen is None and lights:
        chosen = lights[0]
    if chosen is None:
        return "我不确定灯现在开没开。"
    state = getter(chosen["id"]) if callable(getter) else None
    if not isinstance(state, dict) or state.get("on") is None:
        return "我不确定灯现在开没开。"
    name = chosen.get("name") or "灯"
    if state.get("on"):
        bri = state.get("brightness")
        if isinstance(bri, int):
            return f"{name}亮着，亮度{bri}。"
        return f"{name}亮着。"
    return f"{name}关着。"


def speak_media(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "播放控制关着。"
    title = str(payload.get("title") or "").strip()
    artist = str(payload.get("artist") or "").strip()
    if payload.get("playing"):
        if title and artist:
            return f"在放{title}，{artist}。"
        if title:
            return f"在放{title}。"
        return "电脑在放东西。"
    if title:
        return f"停着，刚才是{title}。"
    return "现在没在放。"


def speak_last(turns: deque[tuple[str, str]] | list[tuple[str, str]]) -> str:
    if not turns:
        return "我还没记住上一句。"
    _user, reply = list(turns)[-1]
    text = (reply or "").strip()
    if not text:
        return "我还没记住上一句。"
    if len(text) > _RECENT_CLIP:
        text = text[: _RECENT_CLIP - 1] + "…"
    return f"上一句是：{text}"


def speak_timer(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"好，{cn_num(hours)}小时后叫你。"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"好，{cn_num(minutes)}分钟后叫你。"
    return f"好，{seconds}秒后叫你。"


def recent_prompt(turns: deque[tuple[str, str]] | list[tuple[str, str]]) -> str:
    items = list(turns)[-_RECENT_TURNS:]
    if not items:
        return ""
    lines = ["最近对话（只作上下文，不要复述）："]
    for user, reply in items:
        lines.append(f"漂泊者：{_clip_turn(user)}")
        lines.append(f"你：{_clip_turn(reply)}")
    return "\n".join(lines)


def _clip_turn(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if len(value) <= _RECENT_CLIP:
        return value
    return value[: _RECENT_CLIP - 1] + "…"


def infer_media(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _MEDIA_NEXT.search(stripped):
        return "next"
    if _MEDIA_PREV.search(stripped):
        return "previous"
    if _MEDIA_RESUME.search(stripped):
        return "toggle"
    if _MEDIA_PAUSE.search(stripped) and "灯" not in stripped:
        return "toggle"
    return parse_media_action(stripped)


def infer_volume(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _VOLUME_UNMUTE.search(stripped):
        return "up"
    if _VOLUME_MUTE.search(stripped):
        return "mute"
    if _VOLUME_UP.search(stripped):
        return "up"
    if _VOLUME_DOWN.search(stripped):
        return "down"
    return parse_volume_action(stripped)


def parse_media_action(raw: str) -> str | None:
    match = _ACTION_MEDIA.search(raw or "")
    if not match:
        return None
    return match.group(1).lower()


def parse_volume_action(raw: str) -> str | None:
    match = _ACTION_VOLUME.search(raw or "")
    if not match:
        return None
    return match.group(1).lower()


def parse_timer_action(raw: str) -> int | None:
    match = _ACTION_TIMER.search(raw or "")
    if not match:
        return None
    minutes = int(match.group(1))
    if 1 <= minutes <= 120:
        return minutes * 60
    return None


def parse_timer(text: str) -> int | None:
    action = parse_timer_action(text)
    if action is not None:
        return action
    stripped = (text or "").strip()
    if not re.search(r"(叫我|提醒我|喊我|叫一声)", stripped):
        return None
    if re.search(r"半(?:个)?小时", stripped):
        return 30 * 60
    match = re.search(
        r"(?:再)?(?:过)?(?P<num>\d{1,3}|[零〇一二两三四五六七八九十]+)"
        r"(?P<unit>秒钟|秒|分钟|分|小时|钟头)后",
        stripped,
    )
    if not match:
        if re.search(r"(一会儿|过一会).*(叫我|提醒我)", stripped):
            return 5 * 60
        return None
    number = parse_cn_int(match.group("num"))
    if number is None or number < 1:
        return None
    unit = match.group("unit")
    if unit in {"秒", "秒钟"}:
        seconds = number
    elif unit in {"小时", "钟头"}:
        seconds = number * 3600
    else:
        seconds = number * 60
    if seconds < 10 or seconds > 2 * 3600:
        return None
    return seconds


def _call_world(fn: Any) -> Any:
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def enforce_ask_budget(spoken: str, allow: bool) -> str:
    """Hub 执行问句额度：本轮不允许问时，丢掉带问号的句子。"""
    text = (spoken or "").strip()
    if allow or not text or not re.search(r"[？?]", text):
        return text
    pieces = re.split(r"(?<=[。！？!?])", text)
    kept = [piece for piece in pieces if piece.strip() and not re.search(r"[？?]", piece)]
    cleaned = "".join(kept).strip()
    return cleaned or "嗯。"


def _ack_after_skills(spoken: str, events: list[dict[str, Any]]) -> str:
    if not events:
        return spoken
    ok = any(item.get("ok") for item in events)
    failed = any(item.get("ok") is False for item in events)
    if failed and not ok:
        stripped = (spoken or "").strip()
        if not stripped or stripped in {"好。", "好", "嗯。", "嗯"} or stripped.startswith("好"):
            return "这次没动成。"
    return spoken


def load_mood(path: Path | None) -> dict[str, Any]:
    mood = default_mood()
    if path is None or not path.is_file():
        return mood
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return mood
    if not isinstance(raw, dict):
        return mood
    if raw.get("user") in _USER_CN:
        mood["user"] = raw["user"]
    if raw.get("self") in _SELF_CN:
        mood["self"] = raw["self"]
    mood["last_ask"] = bool(raw.get("last_ask"))
    if raw.get("updated_at"):
        mood["updated_at"] = str(raw["updated_at"])
    if raw.get("last_user_at"):
        mood["last_user_at"] = str(raw["last_user_at"])
    return mood


def save_mood(path: Path | None, mood: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "user": mood.get("user") or "calm",
        "self": mood.get("self") or "quiet",
        "last_ask": bool(mood.get("last_ask")),
    }
    if mood.get("updated_at"):
        body["updated_at"] = mood["updated_at"]
    if mood.get("last_user_at"):
        body["last_user_at"] = mood["last_user_at"]
    path.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=True), encoding="utf-8")


def parse_teach(text: str, skills: list[dict[str, str]]) -> dict[str, Any] | None:
    folded = fold_game_asr(text)
    match = _TEACH.search(folded) if "技能" in folded else None
    if match is None:
        match = _TEACH_ALT.search(folded)
    if match is None:
        match = _TEACH_GAME.search(folded)
    if match is None:
        return None
    phrase = _normalize_phrase(match.group("phrase"))
    target = _normalize_phrase(match.group("target"))
    if not phrase or phrase in _FORBIDDEN_PHRASE or len(phrase) < 2:
        return None
    ident = resolve_skill(target, skills)
    if not ident:
        return None
    name = next((item["name"] for item in skills if item["id"] == ident), ident)
    on = not bool(_TURN_OFF.search(match.group(0)))
    return {"phrase": phrase, "id": ident, "name": name, "on": on}


def _normalize_phrase(raw: str) -> str:
    return _PUNCT.sub("", (raw or "").strip())


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
        phrase = _normalize_phrase(str(key))
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


def clip_announce(text: str, *, max_sentences: int = 3) -> str:
    """微信→书桌只说短的：最多几句。"""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    ready, rest = cut_sentences(stripped)
    parts = ready[:max_sentences]
    if parts:
        return "".join(parts).strip()
    leftover = (rest or stripped).strip()
    if len(leftover) <= 160:
        return leftover
    return leftover[:159] + "…"


def spoken_chars(text: str) -> int:
    return len("".join((text or "").split()))


def estimate_read_seconds(text: str) -> int:
    n = spoken_chars(text)
    if n <= 0:
        return 0
    return max(1, int(round(n / READ_CHARS_PER_SEC)))


def chunk_read_text(text: str, limit: int = 120) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    ready, rest = cut_sentences(text)
    chunks = list(ready)
    leftover = (rest or "").strip()
    if leftover:
        chunks.append(leftover)
    if not chunks:
        chunks = [text]
    out: list[str] = []
    for piece in chunks:
        if len(piece) <= limit:
            out.append(piece)
            continue
        for i in range(0, len(piece), limit):
            out.append(piece[i : i + limit])
    return out


_PUNCT_ONLY = re.compile(r"^[\s。！？!?，,、．.；;：:\"“”‘’「」『』（）()…—\-·]+$")
_CLAUSE_END = set("，、；：")


def _clauses(piece: str) -> list[str]:
    """在逗号处断开，避免一整句长短差太大时模型忽慢忽快。"""
    parts: list[str] = []
    start = 0
    for i, ch in enumerate(piece):
        if ch in _CLAUSE_END:
            parts.append(piece[start : i + 1])
            start = i + 1
    if start < len(piece):
        parts.append(piece[start:])
    return [part for part in parts if part.strip()]


def pack_read_chunks(pieces: list[str], *, target: int = 36, limit: int = 52) -> list[str]:
    """把过短的碎片合成一段再送 TTS。

    守岸人克隆参考约 37 字 / 9.5 秒。同一段里句子一短一长时，短的会被拖慢、
    长的会被赶到约 5 字/秒。先按逗号拆开，再拼回参考长度，语速才稳。
    """
    flat: list[str] = []
    for piece in pieces:
        text = (piece or "").strip()
        if not text or _PUNCT_ONLY.match(text):
            continue
        flat.extend(_clauses(text))
    packed: list[str] = []
    buf = ""
    floor = max(8, target // 2)

    def take(buf: str) -> str:
        while len(buf) > limit:
            tail = len(buf) - target
            cut = target if tail >= floor else max(target, len(buf) // 2)
            cut = min(cut, limit, len(buf) - 1)
            packed.append(buf[:cut])
            buf = buf[cut:]
        return buf

    for text in flat:
        if not buf:
            buf = text
            continue
        if len(buf) < target and len(buf) + len(text) <= limit:
            buf += text
            continue
        buf = take(buf)
        if buf and len(buf) < target and len(buf) + len(text) <= limit:
            buf += text
            continue
        if buf:
            packed.append(buf)
        buf = text
    if not buf:
        return packed
    buf = take(buf)
    if packed and len(buf) < floor and len(packed[-1]) + len(buf) <= limit:
        packed[-1] += buf
    elif buf:
        packed.append(buf)
    return packed


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


def join_wavs(parts: list[bytes]) -> bytes:
    if not parts:
        raise ValueError("没有音频")
    if len(parts) == 1:
        return parts[0]
    frames: list[bytes] = []
    layout: tuple[int, int, int] | None = None
    for raw in parts:
        with wave.open(io.BytesIO(raw), "rb") as src:
            current = (src.getnchannels(), src.getsampwidth(), src.getframerate())
            if layout is None:
                layout = current
            elif current != layout:
                raise ValueError("wav 格式不一致")
            frames.append(src.readframes(src.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        assert layout is not None
        dst.setnchannels(layout[0])
        dst.setsampwidth(layout[1])
        dst.setframerate(layout[2])
        dst.writeframes(b"".join(frames))
    return out.getvalue()


def wav_duration_sec(raw: bytes) -> float:
    with wave.open(io.BytesIO(raw), "rb") as src:
        rate = src.getframerate()
        if rate <= 0:
            return 0.0
        return src.getnframes() / float(rate)


def _chunk_for_voice(text: str, limit: int = 120) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    ready, _ = cut_sentences(text)
    if ready:
        return ready
    return [text[:limit] + "…"]


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


_SENTENCE_END = set("。！？")


def cut_sentences(buf: str) -> tuple[list[str], str]:
    ready: list[str] = []
    start = 0
    for i, ch in enumerate(buf):
        if ch in _SENTENCE_END:
            piece = buf[start : i + 1].strip()
            if piece:
                ready.append(piece)
            start = i + 1
        elif ch == "\n":
            piece = buf[start:i].strip()
            if piece:
                ready.append(piece)
            start = i + 1
    return ready, buf[start:]


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
