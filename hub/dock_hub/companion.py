"""桌面伴侣（门面）：Hub 把 K20 的一句话转给配置的 LLM，再按句 cue Mini TTS。

LLM 可以是本机/局域网 Ollama，或 OpenAI 兼容网关（如阿里云 MaaS）。
默认 tts.deliver=false：stream 读模型，第一句就能开口；chat HTTP 仍等全文给字幕。
对话不在调用时现拉米家：Hub 把已缓存的书桌状态塞进提示词，对方没问不要提。

实现分在四个文件，这里只剩门面：
- companion_flow.py  一轮对话的流程（chat）、心情记忆、朗读与主动开口
- companion_desk.py  设备动作与口头技能（场景/递话/离家/访客/番茄钟/电源…）
- companion_llm.py   大脑管线（OpenAI 兼容 / Ollama 双模、流式、按句 cue）
- companion_support.py  公共缝：打点、轮次 id、称呼、别名文件
纯函数的老住户在 intents.py（听懂话）、persona.py（怎么说）、
speech.py（怎么念）、llm.py（怎么问）、actions.py（怎么动手）。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dock_hub.chat_log import ChatLog
from dock_hub.clarify import PendingClarify
from dock_hub.config import CompanionConfig
from dock_hub.errors import HubError  # noqa: F401  再导出：外面照旧从这里拿
from dock_hub.memory import MemoryStore
from dock_hub.reads import ReadJobs
from dock_hub.timers import TimerBoard
from dock_hub.tts import Tts

from dock_hub.companion_desk import (
    DeskMixin,
    POWER_TTL_SEC,  # noqa: F401  测试从这里 import
    SLOW_DIM_SEC,  # noqa: F401
    SLOW_DIM_STEPS,  # noqa: F401
)
from dock_hub.companion_flow import (
    ChatFlowMixin,
    MAX_CLIPS,  # noqa: F401
    MAX_READ_CHARS,  # noqa: F401
)

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

from dock_hub.companion_support import (
    _TurnCancelled,  # noqa: F401
    _call_world,  # noqa: F401
    _clean_speaker,  # noqa: F401  测试从这里 import
    _latency,  # noqa: F401
    _ms,  # noqa: F401
    _turn_id,  # noqa: F401
    load_aliases,  # noqa: F401
    save_aliases,  # noqa: F401
    speaker_prompt,  # noqa: F401  测试从这里 import
)
from dock_hub.companion_llm import LlmMixin

# LLM / TTS 探活结果的有效期。过期后由后台线程刷新，读的人永远只拿缓存，
# 免得 3s 一次的 /v1/snapshot 被一个卡住的探活拖住。
PROBE_SEC = 5.0

# K20 最近一次说话算「在场」的有效期（秒）。
PRESENCE_SEC = 90


@dataclass
class CompanionReply:
    text: str
    audio_id: str | None = None


class Companion(ChatFlowMixin, DeskMixin, LlmMixin):
    """门面：持有状态（配置、心情、别名、计时器、朗读队列），
    行为在三个 Mixin 里——flow 管一轮话，desk 管动手，llm 管大脑。"""

    def __init__(self, config: CompanionConfig | None) -> None:
        self.config = config or CompanionConfig(enabled=False)
        self.tts = Tts.from_config(self.config, log=_latency)
        self._ready = False
        self._voice = False
        self._ready_at = 0.0
        self._voice_at = 0.0
        self._probe_at = 0.0
        self._probing = False
        self._speaking = False
        self._seq = 0
        self._lock = threading.Lock()
        # 在飞那一轮的取消令牌：新一轮进来就 set，旧的流式循环立刻收手
        self._turn_cancel = threading.Event()
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
        self._dim_gen = 0
        self._dim_thread: threading.Thread | None = None
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
        # F26 访客模式：真就一句话——现在该不该把对话藏起来（不落盘、不记事）
        self._guest_hidden: Callable[[], bool] = lambda: False

    def set_guest_gate(self, gate: Callable[[], bool] | None) -> None:
        """F26：传进来一个「现在是不是访客模式」的问法。None = 永远不是。"""
        self._guest_hidden = gate or (lambda: False)

    def configure(self, config: CompanionConfig | None) -> None:
        self.config = config or CompanionConfig(enabled=False)
        self.tts = Tts.from_config(self.config, log=_latency)
        self._ready = False
        self._voice = False
        self._ready_at = 0.0
        self._voice_at = 0.0
        self._probe_at = 0.0
        with self._lock:
            self._speaking = False
            self._seq += 1
            self._dim_gen += 1
            self._clips.clear()
            # 换配置后要重新探活：冷启动那条同步路径会再走一次
            self._probing = False
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

    def _probe_once(self) -> None:
        try:
            ready = self._ping_llm()
            voice = self.tts.ping() if self.config.tts_base_url else False
        finally:
            with self._lock:
                self._probing = False
        self._ready = ready
        self._voice = voice
        self._ready_at = time.monotonic()
        self._voice_at = self._ready_at
        self._probe_at = self._ready_at

    def _refresh_probes(self) -> None:
        """把「探活」从读路径上摘出去。

        `/v1/snapshot` 是 3s 一次的轮询，而 ready()/voice() 原来会在过期时
        同步 ping 一次 LLM（openai 模式超时 2s）和 TTS。大脑卡住的时候，主屏
        每个轮询都要陪着等；这里改成：冷启动同步探一次（要如实报状态），
        之后过期就丢给后台线程，读的人只拿上一次的结果。
        """
        if not self.config.enabled:
            return
        if time.monotonic() - self._probe_at < PROBE_SEC:
            return
        with self._lock:
            if self._probing:
                return
            self._probing = True
        if self._probe_at <= 0:
            self._probe_once()
            return
        threading.Thread(target=self._probe_once, name="companion-probe", daemon=True).start()

    def ready(self) -> bool:
        if not self.config.enabled:
            return False
        self._refresh_probes()
        return self._ready

    def _begin_turn(self) -> tuple[int, threading.Event]:
        """开新一轮，顺便把还在想的那一轮顶掉。

        安卓重试、连着说两句话都会让两条 chat 并行：旧的那条不但继续烧
        模型额度，回复回来还会抢字幕、抢记忆。seq 只能挡住「已经生成完」的
        那一步，流式途中的句子仍在往外 cue，所以额外给每轮发一个取消令牌。
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._turn_cancel.set()
            cancel = threading.Event()
            self._turn_cancel = cancel
        return seq, cancel
