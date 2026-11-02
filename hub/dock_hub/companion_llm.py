"""伴侣的大脑管线：OpenAI 兼容 / Ollama 双模的 HTTP、流式、按句 cue。

从 companion.py 搬出来的那一坨（原 1508–1726 行段）。只跟
self.config / self._extra_llm_headers / self._mood / self._seq 打交道，
不动设备、不动记忆——那些在 companion_desk / companion_flow 里。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from dock_hub.errors import HubError
from dock_hub.llm import (
    _clean_reply,
    _join,
    _openai_delta_content,
    _openai_message_content,
    _ollama_chunk,
    _openrouter,
    openai_mode,
)
from dock_hub.speech import cut_sentences

from dock_hub.companion_support import _TurnCancelled, _latency

# 上游限流与抖动的重试节奏：0.5s、1.5s 各再来一趟。429 的 Retry-After 说了
# 要等多久就照它（封顶 3s）——一次一次性限流不该让整句话丢掉，
# 用户听到的只会是「她没说话」。
LLM_RETRY_DELAYS = (0.5, 1.5)
LLM_RETRY_AFTER_CAP = 3.0
# 这几类状态码是「再来一次可能就好了」：限流、过载、网关抖动
_RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def retriable_error(exc: BaseException) -> bool:
    return isinstance(exc, HubError) and bool(getattr(exc, "retriable", False))


def retry_delay(exc: BaseException, fallback: float) -> float:
    headers = getattr(exc, "headers", None) or {}
    for key, value in headers.items():
        if str(key).lower() != "retry-after":
            continue
        try:
            return min(LLM_RETRY_AFTER_CAP, max(0.0, float(value)))
        except (TypeError, ValueError):
            break
    return fallback


def with_retry(attempt, *, delays: tuple[float, ...] = LLM_RETRY_DELAYS, sleep=time.sleep):
    """跑一次 attempt()；只有可重试的 HubError 才按 delays 再来几趟。

    超时、连不上、4xx 一律直接抛：前两个是人已经等了一轮，后者再试也没用。
    """
    remaining = list(delays)
    while True:
        try:
            return attempt()
        except HubError as exc:
            if not retriable_error(exc) or not remaining:
                raise
            sleep(retry_delay(exc, remaining.pop(0)))


def _llm_http_error(exc: urllib.error.HTTPError) -> HubError:
    try:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
    except Exception:  # noqa: BLE001 - 读不到正文不影响报错
        detail = ""
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    message = f"大脑拒绝请求：{exc.code} {detail}".strip() if detail else f"大脑拒绝请求：{exc.code}"
    return HubError(
        "companion_unavailable",
        message,
        headers=headers,
        retriable=exc.code in _RETRIABLE_STATUS,
    )


class LlmMixin:
    """混合进 Companion：大脑请求的底层通道。"""

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

    def _stream_and_cue(
        self,
        messages: list[dict[str, str]],
        turn: str,
        seq: int,
        cancel: threading.Event | None = None,
    ) -> str:
        # 已经念出去的句子不能重来（会重复念），所以只有在还没开口时才允许重试
        cued = [0]
        remaining = list(LLM_RETRY_DELAYS)
        while True:
            try:
                return self._stream_once(messages, turn, seq, cancel, cued)
            except HubError as exc:
                if cued[0] or not retriable_error(exc) or not remaining:
                    raise
                _latency(turn, "llm_retry", detail=exc.message[:60])
                time.sleep(retry_delay(exc, remaining.pop(0)))

    def _stream_once(
        self,
        messages: list[dict[str, str]],
        turn: str,
        seq: int,
        cancel: threading.Event | None,
        cued: list[int],
    ) -> str:
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
                    if cancel is not None and cancel.is_set():
                        raise _TurnCancelled()
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
                        if spoken and self._cue_sentence(spoken, turn, seq):
                            cued[0] += 1
                            if first:
                                _latency(turn, "tts_first_sentence", chars=len(spoken))
                                first = False
        except TimeoutError as exc:
            raise HubError("companion_unavailable", "大脑响应超时") from exc
        except urllib.error.HTTPError as exc:
            raise _llm_http_error(exc) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise HubError("companion_unavailable", "连不上大脑") from exc
        tail = _clean_reply(buf)
        if tail and not (not bool(self._mood.get("allow_ask")) and re.search(r"[？?]", tail)):
            if self._cue_sentence(tail, turn, seq):
                cued[0] += 1
                if first:
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
        return with_retry(
            lambda: self._chat_response_once(messages, stream=stream, temperature=temperature)
        )

    def _chat_response_once(
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
            raise _llm_http_error(exc) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise HubError("companion_unavailable", "连不上大脑") from exc
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HubError("companion_unavailable", "大脑返回无法解析") from exc
        if not isinstance(body, dict):
            raise HubError("companion_unavailable", "大脑返回无法解析")
        return body
