"""伴侣的公共缝：打点、轮次 id、称呼、别名文件、取消令牌。

从 companion.py 搬出来的模块级小件——聊天流（companion_flow）、
设备动作（companion_desk）、大脑管线（companion_llm）两边都要用，
放在这儿谁都能 import，谁也不用反过来依赖谁。
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from dock_hub.config import ID_RE
from dock_hub.intents import normalize_phrase
from dock_hub.metrics import METRICS


class _TurnCancelled(Exception):
    """这一轮已经被下一轮顶掉。别再写字幕、别再进记忆。"""


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


def _clean_speaker(raw: Any) -> str:
    """声纹认出来的人（F5）：只留一个短称呼，别把识别结果整段带进来。"""
    name = re.sub(r"\s+", "", str(raw or ""))
    name = re.sub(r"^[，。！？!?,.、·:：\-—]+|[，。！？!?,.、·:：\-—]+$", "", name)
    return name[:12]


def speaker_prompt(name: str) -> str:
    """F5 声纹：知道是谁在说话就带一句进提示词——称呼自然用上，别句句报名字。"""
    if not name:
        return ""
    return f"现在跟你说话的人：{name}。称呼可以自然用上，但别每句都提。"


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
