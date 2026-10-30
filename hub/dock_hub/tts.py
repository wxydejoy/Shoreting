"""嘴巴：把一句话交给 TTS 服务，或者让它立刻闭嘴。

三个动作，一个类：探活（`/health` 里 ready 没 ready）、合成（`/v1/speak`）、
叫停（`/v1/stop`）。以前这三个方法散在 Companion 里，夹在朗读任务和人设之间，
现在挪出来单独放，理由是它们只依赖配置里的四个值（地址、是否回传音频、超时、
语速），和「守岸人是谁」没有关系。

`/v1/speak` 有两种用法，差别在响应体：

- `play_only`（默认，当 `tts_deliver` 关时）：TTS 自己播，Hub 只收 JSON 回执，
  超时给 3 秒 —— 这条路上 Hub 不等声音放完；
- 否则：TTS 把整段 wav 回传，Hub 拿去缓存或存盘，超时按配置（默认 20 秒）。

朗读长文时用第三种：`play=False`，只要 wav 不要播，超时给到 90 秒 —— 一篇文章
合成三十块，任何一块都不该按「播一句」的时限掐掉。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# 只要回执的那条路：Hub 不等声音，只等 TTS 收下
CUE_TIMEOUT_SEC = 3.0
# 长文整段合成：一块一句话，但要在几十块里挑最长的那块
READ_TIMEOUT_SEC = 90.0
# 叫停是「顺手」的事，慢一点就算了，不拖累调用方
STOP_TIMEOUT_SEC = 0.8

Log = Callable[..., None]


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


class Tts:
    def __init__(
        self,
        base_url: str | None,
        *,
        deliver: bool = False,
        timeout_sec: float = 20.0,
        speed: float = 1.0,
        log: Log | None = None,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.deliver = bool(deliver)
        self.timeout_sec = float(timeout_sec)
        self.speed = round(float(speed or 1.0), 2)
        # 打点回调：真的用了才传（Companion 传 _latency），单独测时不传
        self.log = log

    @classmethod
    def from_config(cls, config: Any, *, log: Log | None = None) -> Tts:
        return cls(
            getattr(config, "tts_base_url", None),
            deliver=bool(getattr(config, "tts_deliver", False)),
            timeout_sec=float(getattr(config, "tts_timeout_sec", 20.0) or 20.0),
            speed=float(getattr(config, "voice_speed", 1.0) or 1.0),
            log=log,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _note(self, turn: str, stage: str, **fields: Any) -> None:
        if self.log is not None:
            self.log(turn, stage, **fields)

    def ping(self) -> bool:
        """TTS 在不在、模型加载完没有。0.4 秒就放弃：这是探活，不是干活。"""
        if not self.configured:
            return False
        try:
            req = urllib.request.Request(join_url(self.base_url, "/health"), method="GET")
            with urllib.request.urlopen(req, timeout=0.4) as resp:
                if not (200 <= resp.status < 300):
                    return False
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return False
        return isinstance(body, dict) and bool(body.get("ready"))

    def stop(self) -> None:
        """让 TTS 立刻停。没接嘴巴就什么都不做，失败也不报。"""
        if not self.configured:
            return
        req = urllib.request.Request(
            join_url(self.base_url, "/v1/stop"),
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=STOP_TIMEOUT_SEC) as resp:
                resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return

    def speak(
        self,
        text: str,
        turn: str,
        *,
        play_only: bool | None = None,
        play: bool | None = None,
    ) -> bytes | None:
        """合成一句话。返回 wav 字节；只要回执或失败时返回 None。"""
        if not self.configured:
            return None
        if play_only is None:
            play_only = not self.deliver
        body: dict[str, Any] = {
            "text": text,
            "voice": "shorekeeper",
            "language": "chinese",
            "turn_id": turn,
            "speed": self.speed,
        }
        if play_only:
            body["play_only"] = True
        if play is False:
            body["play"] = False
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        accept = "application/json" if play_only else "audio/wav"
        if play_only:
            timeout = CUE_TIMEOUT_SEC
        elif play is False:
            timeout = READ_TIMEOUT_SEC
        else:
            timeout = self.timeout_sec
        req = urllib.request.Request(
            join_url(self.base_url, "/v1/speak"),
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": accept},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            self._note(turn, "tts_error", ms=_ms(started))
            return None
        if play_only:
            self._note(turn, "tts_cued", ms=_ms(started), bytes=len(raw))
            return None
        if not raw:
            self._note(turn, "tts_empty", ms=_ms(started))
            return None
        self._note(turn, "tts_done", ms=_ms(started), bytes=len(raw))
        return raw


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
