"""把一段文字切成能念的块，再把念出来的 wav 拼回去。

朗读这件事最影响听感的两处都在这里：句子怎么断、块多大。守岸人克隆参考大约
37 字 / 9.5 秒，同一段里句子一短一长，短的会被拖慢、长的会被赶到 5 字/秒，
所以 pack_read_chunks 会把过短的碎片按逗号拼回参考长度。

和 companion.py 的关系：那边是主流程（一轮对话、TTS 队列、线程），这边只用
标准库处理字符串和 wav 字节，不起线程不发请求，验起来不用等模型。
"""

from __future__ import annotations

import io
import re
import wave

# 朗读语速的经验值：按它估时长给界面显示（不是 TTS 的真实语速）
READ_CHARS_PER_SEC = 4.0

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


PUNCT_ONLY = re.compile(r"^[\s。！？!?，,、．.；;：:\"“”‘’「」『』（）()…—\-·]+$")
CLAUSE_END = set("，、；：")


def _clauses(piece: str) -> list[str]:
    """在逗号处断开，避免一整句长短差太大时模型忽慢忽快。"""
    parts: list[str] = []
    start = 0
    for i, ch in enumerate(piece):
        if ch in CLAUSE_END:
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
        if not text or PUNCT_ONLY.match(text):
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



SENTENCE_END = set("。！？")


def cut_sentences(buf: str) -> tuple[list[str], str]:
    ready: list[str] = []
    start = 0
    for i, ch in enumerate(buf):
        if ch in SENTENCE_END:
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
