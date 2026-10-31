"""Mini 上的守岸人 TTS。独立进程，不要 import dock_hub。

Hub 当客户端：POST /v1/speak、POST /v1/stop。安卓不直连本服务。

默认：Qwen3-TTS 1.7B Base 8bit + 守岸人 ref_audio 克隆。
Serena / CustomVoice 用 --no-clone 和对应 --model 切回去。

--play：合成过程中在本机喇叭出声。默认用 Mac Mini 自带扬声器，
跳过 Background Music / NoMachine 虚拟设备。play_only 时给 Hub 的是 202，不把 wav 回给手机。

同轮多次 /v1/speak（边写边念）接到同一条播放队列，不要互相 flush。
只有 /v1/stop 才清空缓冲并打断这一轮合成。
"""

from __future__ import annotations

import argparse
import hmac
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from mlx_audio.audio_io import write as audio_write
from mlx_audio.tts.audio_player import AudioPlayer
from mlx_audio.tts.utils import load_model

from noise import (
    FADE_SEC,
    NOISE_NAMES,
    SAMPLE_RATE,
    clamp_minutes,
    synth_loop,
)
from music import MusicBoard

HERE = Path(__file__).resolve().parent
MODEL_ID = str(HERE / "models" / "Qwen3-TTS-12Hz-1.7B-Base-8bit")
CUSTOMVOICE_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-6bit"
DEFAULT_VOICE = "shorekeeper"
DEFAULT_LANG = "chinese"
DEFAULT_REF_AUDIO = HERE / "voices" / "shorekeeper" / "clone.wav"
MAX_BODY = 16_384
STREAM_INTERVAL = 0.4
_VIRTUAL_AUDIO = re.compile(
    r"background music|nomachine|blackhole|soundflower|loopback|ui sounds",
    re.I,
)
_BUILTIN_MINI = re.compile(r"mac mini\s*扬声器|mac mini speakers|mac mini speaker", re.I)
SPEED_MIN = 0.7
SPEED_MAX = 1.5


def clamp_speed(value: object, default: float = 1.0) -> float:
    try:
        speed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(SPEED_MAX, max(SPEED_MIN, speed))


def stretch_audio(audio: np.ndarray, speed: float) -> np.ndarray:
    """语速。克隆模型的 speed 参数无效，所以在播放前用叠窗把声音拉长或压短，音高不动。"""
    wave = np.asarray(audio, dtype=np.float32).reshape(-1)
    if wave.size < 16 or abs(speed - 1.0) < 0.03:
        return wave
    speed = clamp_speed(speed)
    win = 960
    hop_out = 480
    hop_in = max(1, int(round(hop_out * speed)))
    window = np.hanning(win).astype(np.float32)
    out_len = int(wave.size / speed) + win
    mixed = np.zeros(out_len, dtype=np.float32)
    weight = np.zeros(out_len, dtype=np.float32)
    pos_in = 0
    pos_out = 0
    while pos_in + win <= wave.size and pos_out + win <= out_len:
        frame = wave[pos_in : pos_in + win] * window
        mixed[pos_out : pos_out + win] += frame
        weight[pos_out : pos_out + win] += window
        pos_in += hop_in
        pos_out += hop_out
    if pos_out <= 0:
        return wave
    return mixed[:pos_out] / np.maximum(weight[:pos_out], 1e-6)


def read_output_volume() -> int | None:
    if sys.platform != "darwin":
        return None
    try:
        raw = subprocess.check_output(
            ["osascript", "-e", "output volume of (get volume settings)"],
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return max(0, min(100, int(raw.strip())))
    except ValueError:
        return None


def write_output_volume(level: int) -> None:
    level = max(0, min(100, int(level)))
    if sys.platform != "darwin":
        raise OSError("这台系统还不能直接设音量")
    subprocess.check_call(
        ["osascript", "-e", f"set volume output volume {level} without output muted"],
        timeout=3,
    )


def pick_output_device(sd) -> int | None:
    """Prefer Mac Mini built-in speakers. Skip Background Music / NoMachine."""
    devices = list(sd.query_devices())
    default = sd.default.device
    default_out = default[1] if isinstance(default, (list, tuple)) else default
    builtin: list[int] = []
    other: list[int] = []
    for i, item in enumerate(devices):
        if int(item.get("max_output_channels") or 0) < 1:
            continue
        name = str(item.get("name") or "")
        if _VIRTUAL_AUDIO.search(name):
            continue
        if _BUILTIN_MINI.search(name):
            builtin.append(i)
            continue
        other.append(i)
    if builtin:
        return builtin[0]
    if isinstance(default_out, int) and 0 <= default_out < len(devices):
        name = str(devices[default_out].get("name") or "")
        if not _VIRTUAL_AUDIO.search(name):
            return default_out
    if other:
        return other[0]
    return None


class _LocalSpeaker(AudioPlayer):
    """尽快开口。同轮逐句接到缓冲里；只有 /v1/stop 才打断。"""

    min_buffer_seconds = 0.25

    def start_stream(self):
        import sounddevice as sd

        device = pick_output_device(sd)
        name = ""
        if device is not None:
            try:
                name = str(sd.query_devices(device).get("name") or "")
            except Exception:
                name = str(device)
        print(f"tts play device={device} {name}", flush=True)
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            callback=self.callback,
            blocksize=self.buffer_size,
            device=device,
        )
        self.stream.start()
        self.playing = True
        self.drain_event.clear()

    def flush(self):
        with self.buffer_lock:
            self.audio_buffer.clear()
        self.stop_stream()


class Engine:
    def __init__(
        self,
        model_id: str,
        voice: str,
        ref_audio: str | None,
        ref_text: str | None,
        play: bool,
    ) -> None:
        self.model_id = model_id
        self.voice = voice
        self.ref_audio = ref_audio
        self.ref_text = ref_text
        self.play = play
        self.model = None
        self.error: str | None = None
        self.lock = threading.Lock()
        self._play_lock = threading.Lock()
        self._gate = threading.Lock()
        self._gen = 0
        self.speaker = _LocalSpeaker(sample_rate=24000) if play else None
        self.speed = 1.0

    def clone(self) -> bool:
        return bool(self.ref_audio and self.ref_text)

    @staticmethod
    def _wire_headroom() -> None:
        """把权重钉在物理内存里。

        机器内存一紧，MLX 的权重 (~2GiB) 会被换出磁盘；下一次开口前先把权重从
        swap 换回来，首字延迟忽快忽慢（实测同一句话 3.9s ~ 9.4s 抖动）。加载之前
        抬高 wired 上限，之后新分配的 buffer 一律不换出。
        """
        try:
            import mlx.core as mx

            set_wired = getattr(mx, "set_wired_limit", None) or mx.metal.set_wired_limit
            info = mx.device_info() if hasattr(mx, "device_info") else mx.metal.device_info()
            recommended = int(info.get("max_recommended_working_set_size") or 0)
            cap = min(recommended // 2, 4 << 30)
            if cap <= 0:
                print("wired 不可用，权重不常驻", flush=True)
                return
            set_wired(cap)
            print(f"wired 上限 {cap / 2**30:.1f}GiB（权重加载后不换出）", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"wired 设置失败，权重不常驻：{exc}", flush=True)

    def load(self) -> None:
        try:
            print(f"加载 {self.model_id} …", flush=True)
            self._wire_headroom()
            self.model = load_model(self.model_id)
            try:
                import mlx.core as mx

                get_active = getattr(mx, "get_active_memory", None) or mx.metal.get_active_memory
                print(f"权重 {get_active() / 2**30:.2f}GiB 已加载", flush=True)
            except Exception:  # noqa: BLE001
                pass
            if self.clone():
                print(
                    f"就绪。克隆 {self.voice}  ref={self.ref_audio}",
                    flush=True,
                )
                self._warmup()
                return
            speakers = []
            if hasattr(self.model, "get_supported_speakers"):
                speakers = list(self.model.get_supported_speakers() or [])
            print(f"就绪。音色：{', '.join(speakers) or self.voice}", flush=True)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            print(f"加载失败：{exc}", flush=True)

    def _warmup(self) -> None:
        if self.model is None or not self.clone():
            return
        t0 = time.monotonic()
        list(
            self.model.generate(
                text="嗯。",
                lang_code=DEFAULT_LANG,
                verbose=False,
                stream=False,
                ref_audio=self.ref_audio,
                ref_text=self.ref_text,
            )
        )
        ms = max(0, int((time.monotonic() - t0) * 1000))
        print(f"克隆预热 ms={ms}", flush=True)

    def ready(self) -> bool:
        return self.model is not None and self.error is None

    def stop(self) -> None:
        with self._play_lock:
            with self._gate:
                self._gen += 1
            if self.speaker is not None:
                self.speaker.flush()

    def speak_token(self) -> int:
        with self._gate:
            return self._gen

    def speak(
        self,
        text: str,
        voice: str | None,
        language: str | None,
        expected_gen: int | None = None,
        allow_play: bool | None = None,
        speed: float | None = None,
    ) -> tuple[bytes, int]:
        if self.model is None:
            raise RuntimeError(self.error or "模型还没就绪")
        spoken = text.strip()
        if not spoken:
            raise ValueError("text 不能为空")
        lang = (language or DEFAULT_LANG).strip() or DEFAULT_LANG
        rate = self.speed if speed is None else clamp_speed(speed, self.speed)
        playing = bool(self.play) if allow_play is None else bool(self.play and allow_play)
        use_stream = playing
        kwargs: dict = {
            "text": spoken,
            "lang_code": lang,
            "verbose": False,
            "stream": use_stream,
        }
        if use_stream:
            kwargs["streaming_interval"] = STREAM_INTERVAL
        if self.clone():
            # ICL 要求 voice=None。Hub 仍可能发 Serena，这里丢掉。
            kwargs["ref_audio"] = self.ref_audio
            kwargs["ref_text"] = self.ref_text
        else:
            kwargs["voice"] = (voice or self.voice).strip() or self.voice
        chunks: list[np.ndarray] = []
        sample_rate = 24000
        t0 = time.monotonic()
        first_ms = 0
        with self.lock:
            with self._play_lock:
                with self._gate:
                    if expected_gen is not None and self._gen != expected_gen:
                        raise InterruptedError("stopped")
                    my = self._gen
            for result in self.model.generate(**kwargs):
                sample_rate = int(getattr(result, "sample_rate", sample_rate) or sample_rate)
                audio = stretch_audio(np.asarray(result.audio, dtype=np.float32), rate)
                if not chunks:
                    first_ms = max(0, int((time.monotonic() - t0) * 1000))
                chunks.append(audio)
                with self._play_lock:
                    with self._gate:
                        if self._gen != my:
                            if self.speaker is not None:
                                self.speaker.flush()
                            raise InterruptedError("stopped")
                    if playing and self.speaker is not None:
                        self.speaker.queue_audio(audio)
        if not chunks:
            raise RuntimeError("模型没有发出声音")
        wav = np.concatenate(chunks, axis=0) if len(chunks) > 1 else chunks[0]
        buf = io.BytesIO()
        audio_write(buf, wav, sample_rate, format="wav")
        return buf.getvalue(), first_ms


class NoiseBoard:
    """白噪音播放（F31）。与 TTS 各开各的输出流；到点前统一淡出。

    音量不和 TTS 抢：她说话时 TTS 流在上面，雨声当底。突然大声比小声吓人，
    所以起播也有 2 秒淡入。
    """

    FADE_IN_SEC = 2.0
    BLOCK_SEC = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._name: str | None = None
        self._deadline = 0.0
        self._minutes = 0

    def start(self, name: str, minutes: int) -> dict:
        minutes = clamp_minutes(minutes)
        with self._lock:
            if name == "off":
                self.stop()
                return self.status()
            if name not in NOISE_NAMES:
                raise ValueError(f"没有这种声音：{name}")
            self._name = name
            self._minutes = minutes
            self._deadline = time.monotonic() + minutes * 60
            self._stop.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="noise", daemon=True
                )
                self._thread.start()
            print(f"tts noise start={name} minutes={minutes}", flush=True)
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            self._name = None
            self._minutes = 0
            self._deadline = 0.0
            return self.status()

    def status(self) -> dict:
        with self._lock:
            if not self._name:
                return {"playing": False, "name": None, "seconds_left": 0, "minutes": 0}
            left = max(0.0, self._deadline - time.monotonic())
            return {
                "playing": True,
                "name": self._name,
                "seconds_left": int(left),
                "minutes": self._minutes,
            }

    def _loop(self) -> None:
        try:
            import sounddevice as sd

            with self._lock:
                name = self._name
            if not name:
                return
            loop = synth_loop(name)
            block = int(self.BLOCK_SEC * SAMPLE_RATE)
            position = 0
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32"
            )
            stream.start()
            try:
                while not self._stop.is_set() and not self._expired():
                    chunk = loop[position : position + block]
                    position = (position + len(chunk)) % len(loop)
                    if len(chunk) < block:
                        chunk = np.concatenate([chunk, loop[: block - len(chunk)]])
                        position = block - len(chunk)
                    chunk = self._shape(chunk)
                    stream.write(chunk.reshape(-1, 1))
            finally:
                stream.stop()
                stream.close()
        except Exception as exc:  # noqa: BLE001 - 声卡忙/设备没了：白噪音闭嘴就行
            print(f"tts noise error {exc}", flush=True)
        finally:
            self.stop()

    def _expired(self) -> bool:
        return self._deadline > 0 and time.monotonic() >= self._deadline

    def _shape(self, chunk: np.ndarray) -> np.ndarray:
        """淡入 + 到点前淡出，音量整体压到 0.32——底噪不该盖过她说话。"""
        gain = 0.32
        with self._lock:
            total_left = max(0.0, self._deadline - time.monotonic())
        elapsed_total = self._minutes * 60
        if elapsed_total > 0:
            played = elapsed_total - total_left
            if played < self.FADE_IN_SEC:
                gain *= max(0.0, played / self.FADE_IN_SEC)
        if total_left < FADE_SEC:
            gain *= max(0.0, total_left / FADE_SEC)
        return (chunk * gain).astype(np.float32)


def _ref_text_from_lab(audio_path: Path) -> str:
    lab = audio_path.with_suffix(".lab")
    if not lab.is_file():
        return ""
    return lab.read_text(encoding="utf-8").strip()


def _resolve_refs(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if args.no_clone:
        return None, None
    raw = (args.ref_audio or "").strip()
    if not raw:
        return None, None
    audio = Path(raw).expanduser()
    if not audio.is_file():
        raise SystemExit(f"参考音频不存在：{audio}")
    text = (args.ref_text or "").strip() or _ref_text_from_lab(audio)
    if not text:
        raise SystemExit("克隆需要 --ref-text 或同目录 .lab")
    return str(audio), text


def make_handler(
    engine: Engine,
    noise: NoiseBoard,
    token: str = "",
    music: "MusicBoard | None" = None,
):
    secret = (token or "").strip()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            print(f"tts {self.address_string()} {fmt % args}", flush=True)

        def _authorized(self) -> bool:
            # 没设 token 时只允许本机回环访问（main 里会拒绝非回环绑定）
            if not secret:
                return True
            header = self.headers.get("Authorization") or self.headers.get("authorization") or ""
            value = header.strip()
            if not value.lower().startswith("bearer "):
                return False
            got = value[7:].strip()
            if not got:
                return False
            try:
                return hmac.compare_digest(got.encode("utf-8"), secret.encode("utf-8"))
            except (UnicodeError, TypeError):
                return False

        def _deny(self) -> None:
            self._json(401, {"error": {"code": "unauthorized", "message": "Token 不正确"}})

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._deny()
                return
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/health":
                self._json(
                    200,
                    {
                        "ok": True,
                        "service": "helm-companion-tts",
                        "ready": engine.ready(),
                        "model": engine.model_id,
                        "voice": engine.voice,
                        "clone": engine.clone(),
                        "play": engine.play,
                        "speed": engine.speed,
                        "ref_audio": engine.ref_audio,
                        "error": engine.error,
                    },
                )
                return
            if path == "/v1/settings":
                self._json(200, {"ok": True, "speed": engine.speed, "volume": read_output_volume()})
                return
            if path == "/v1/noise":
                self._json(200, {"ok": True, **noise.status()})
                return
            if path == "/v1/music":
                if music is None:
                    self._json(404, {"error": {"code": "not_found", "message": "未开启音乐"}})
                    return
                self._json(200, {"ok": True, **music.status()})
                return
            self._json(404, {"error": {"code": "not_found", "message": "未知接口"}})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._deny()
                return
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                body = self._read_json()
                if path == "/v1/stop":
                    engine.stop()
                    print("tts latency turn=- stage=stopped", flush=True)
                    self._json(200, {"ok": True, "stopped": True})
                    return
                if path == "/v1/settings":
                    if "speed" in body:
                        engine.speed = clamp_speed(body.get("speed"))
                    if body.get("volume") is not None:
                        write_output_volume(int(body.get("volume")))
                    self._json(200, {"ok": True, "speed": engine.speed, "volume": read_output_volume()})
                    return
                if path == "/v1/noise":
                    # F31：白噪音。name=off 或缺省就是停；到点自动淡出
                    name = str(body.get("name") or "off").strip().lower()
                    minutes = clamp_minutes(body.get("minutes"))
                    music.stop()
                    state = noise.start(name, minutes)
                    self._json(200, {"ok": True, **state})
                    return
                if path == "/v1/music":
                    # F32：本地电台。minutes 省缺默认一小时；到点自动淡出。
                    # 和白噪音共用喇叭，谁后到谁说了算。
                    if music is None:
                        self._json(404, {"error": {"code": "not_found", "message": "未开启音乐"}})
                        return
                    action = str(body.get("action") or "on").strip().lower()
                    if action == "off":
                        state = music.stop()
                        print("tts music stop", flush=True)
                        self._json(200, {"ok": True, **state})
                        return
                    minutes = clamp_minutes(body.get("minutes"))
                    noise.stop()
                    state = music.start(minutes)
                    if not state.get("ok"):
                        # 目录空的也算收下了：ok=False 让她照实说，不报 500
                        self._json(200, state)
                        return
                    self._json(200, {"ok": True, **state})
                    return
                if path != "/v1/speak":
                    self._json(404, {"error": {"code": "not_found", "message": "未知接口"}})
                    return
                if not engine.ready():
                    self._json(
                        503,
                        {"error": {"code": "not_ready", "message": engine.error or "模型加载中"}},
                    )
                    return
                text = str(body.get("text") or "")
                voice = body.get("voice")
                language = body.get("language")
                turn = str(body.get("turn_id") or "").strip() or "-"
                play_only = bool(body.get("play_only"))
                play_value = body["play"] if "play" in body else None
                allow_play = True if play_only else (None if play_value is None else bool(play_value))
                speed = clamp_speed(body.get("speed"), engine.speed) if "speed" in body else None
                if speed is not None:
                    engine.speed = speed
                if play_only:
                    spoken = text.strip()
                    if not spoken:
                        raise ValueError("text 不能为空")
                    threading.Thread(
                        target=_play_only,
                        args=(
                            engine,
                            spoken,
                            str(voice) if voice else None,
                            str(language) if language else None,
                            turn,
                            engine.speak_token(),
                            allow_play,
                            speed,
                        ),
                        name=f"tts-play-{turn}",
                        daemon=True,
                    ).start()
                    self._json(202, {"ok": True, "play": engine.play, "turn_id": turn})
                    return
                t0 = time.monotonic()
                wav, first_ms = engine.speak(
                    text,
                    str(voice) if voice else None,
                    str(language) if language else None,
                    allow_play=allow_play,
                    speed=speed,
                )
            except ValueError as exc:
                self._json(400, {"error": {"code": "bad_request", "message": str(exc)}})
                return
            except Exception as exc:  # noqa: BLE001
                self._json(502, {"error": {"code": "tts_error", "message": str(exc)}})
                return
            ms = max(0, int((time.monotonic() - t0) * 1000))
            print(
                f"tts latency turn={turn} stage=speak_done first_ms={first_ms} "
                f"ms={ms} bytes={len(wav)} chars={len(text.strip())} play={int(engine.play)}",
                flush=True,
            )
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-First-Audio-Ms", str(first_ms))
            self.end_headers()
            self.wfile.write(wav)

        def _read_json(self) -> dict:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError as exc:
                raise ValueError("JSON 无效") from exc
            if length < 0 or length > MAX_BODY:
                raise ValueError("请求体过大")
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON 必须是对象")
            return data

        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _play_only(
    engine: Engine,
    text: str,
    voice: str | None,
    language: str | None,
    turn: str,
    expected_gen: int,
    allow_play: bool | None = True,
    speed: float | None = None,
) -> None:
    t0 = time.monotonic()
    try:
        wav, first_ms = engine.speak(
            text,
            voice,
            language,
            expected_gen=expected_gen,
            allow_play=allow_play,
            speed=speed,
        )
    except InterruptedError:
        ms = max(0, int((time.monotonic() - t0) * 1000))
        print(f"tts latency turn={turn} stage=play_stopped ms={ms}", flush=True)
        return
    except Exception as exc:  # noqa: BLE001
        print(f"tts play_only turn={turn} error={exc}", flush=True)
        return
    ms = max(0, int((time.monotonic() - t0) * 1000))
    print(
        f"tts latency turn={turn} stage=play_only first_ms={first_ms} "
        f"ms={ms} bytes={len(wav)} chars={len(text)} play={int(engine.play)}",
        flush=True,
    )


def _retry_load(engine: "Engine", *, first_delay: float = 30.0, max_delay: float = 900.0) -> None:
    """加载失败后的退避重试：30s 起，翻倍到 15 分钟封顶。"""
    delay = first_delay
    while not engine.ready():
        time.sleep(delay)
        print(f"重试加载模型（上次失败：{engine.error}）", flush=True)
        engine.error = None
        engine.load()
        if engine.ready():
            print("模型重试成功，开始接活", flush=True)
            return
        delay = min(max_delay, delay * 2)


def _loopback(host: str) -> bool:
    return (host or "").strip().lower() in {"127.0.0.1", "::1", "localhost"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Shoreting companion TTS")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="默认只听本机；要给局域网用必须同时设置 --token",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HELM_TTS_TOKEN", ""),
        help="Bearer 口令；监听非回环地址时必填",
    )
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--ref-audio", default=str(DEFAULT_REF_AUDIO))
    parser.add_argument(
        "--ref-text",
        default="",
        help="参考音频文案；空则读同名 .lab",
    )
    parser.add_argument(
        "--no-clone",
        action="store_true",
        help=f"关掉克隆。Serena 例：--no-clone --model {CUSTOMVOICE_MODEL_ID} --voice Serena",
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="合成时在本机播放。默认走 Mac Mini 自带扬声器",
    )
    parser.add_argument(
        "--music-dir",
        default=str(HERE / "music"),
        help="F32 本地电台的目录；往里丢 mp3/flac/ogg 就能被「放点音乐」点到",
    )
    parser.add_argument(
        "--exit-on-load-error",
        action="store_true",
        help="模型加载失败就退出（调试用）。默认留在线上重试，别让 KeepAlive 每 10s 重读 2GB",
    )
    args = parser.parse_args()

    token = (args.token or "").strip()
    if not _loopback(args.host) and not token:
        raise SystemExit(
            f"拒绝在 {args.host} 上无口令监听：局域网内任何人都能让 Mini 说话、改音量。"
            "请设 --token（或 HELM_TTS_TOKEN），或改回 --host 127.0.0.1。"
        )

    ref_audio, ref_text = _resolve_refs(args)
    engine = Engine(args.model, args.voice, ref_audio, ref_text, args.play)
    engine.load()
    if not engine.ready():
        if args.exit_on_load_error:
            raise SystemExit(1)
        # KeepAlive 会立刻把进程拉起来，于是变成每 10 秒重读一遍 2GB 模型、
        # 每次都失败、CPU 和磁盘全占着。这里留在线上：/health 报 ready:false
        # 和具体错误，后台按退避重试，Hub 那边也能看出来是没就绪而不是连不上。
        print(f"模型没加载成功（{engine.error}）：服务先留在线上，后台重试", flush=True)
        threading.Thread(target=_retry_load, args=(engine,), name="tts-reload", daemon=True).start()

    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(engine, NoiseBoard(), token, MusicBoard(args.music_dir)),
    )
    extra = "  本机播放" if args.play else ""
    auth = "  Bearer" if token else "  本机免口令"
    print(f"TTS 听 {args.host}:{args.port}  POST /v1/speak /v1/stop{extra}{auth}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("停止 TTS", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
