"""F32 电台/播客：一句「放点音乐」，Mini 喇叭放本地音乐目录里的歌。

与 NoiseBoard（F31 白噪音）同一套骨架：自己的输出流、起播淡入、到点统一
淡出，音量压在 0.32——她说话时音乐当底，不该盖过人声。差别在声源：

- 白噪音靠合成，音乐靠本地文件（`music/` 目录，`--music-dir` 换地方）；
- 「电台」的感觉靠洗牌：目录里的歌随机排序放完一轮再洗一次，不停就接着放；
- 目录为空或没这个目录不算错得离谱——回 `{"ok": False, "error": "empty"}`，
  守岸人会照实说「这台 Mini 还没有能放的音乐」，不假装放上了。

解码不挑库：soundfile 有就用它（wav/flac/ogg），没有退 torchaudio（mp3 等）。
解码不了的歌跳过，不连累整张歌单。
"""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path

import numpy as np

MUSIC_EXTS = (".mp3", ".flac", ".ogg", ".oga", ".wav", ".m4a", ".aac", ".opus")
DEFAULT_MINUTES = 60
"""「放点音乐」没说放多久：默认一小时后自己淡出，比白噪音（30 分钟）长，
干活时音乐会一直陪着，但也不该放到天亮。"""
MAX_MINUTES = 240
FADE_SEC = 45.0
FADE_IN_SEC = 2.0
GAIN = 0.32


def clamp_minutes(value: object, default: int = DEFAULT_MINUTES) -> int:
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_MINUTES, minutes))


def list_tracks(directory: Path | None) -> list[Path]:
    """目录里的音频文件，按名字排序。没目录、没文件都算空。"""
    if directory is None or not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in MUSIC_EXTS
    )


def load_track(path: Path) -> tuple[np.ndarray, int]:
    """读一首歌：float32 mono + 采样率。解码不了抛 LookupError，调用方跳过。"""
    try:
        import soundfile as sf

        audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
        return audio.mean(axis=1).astype(np.float32), int(rate)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - 坏文件换下一首
        raise LookupError(str(exc)) from exc
    try:
        import torchaudio

        (audio, rate) = torchaudio.load(str(path))
        mono = audio.mean(dim=0).numpy().astype(np.float32)
        return mono, int(rate)
    except Exception as exc:  # noqa: BLE001
        raise LookupError(str(exc)) from exc


class MusicBoard:
    """本地音乐电台。与 NoiseBoard 同一套规矩，互抢时由 server 仲裁。"""

    BLOCK_SEC = 2.0

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._deadline = 0.0
        self._minutes = 0
        self._track: str | None = None

    def start(self, minutes: int | None = None) -> dict:
        minutes = clamp_minutes(minutes)
        tracks = list_tracks(self.directory)
        if not tracks:
            # 世界的状态，不是请求的错：200 带 ok=False，让她照实说
            return {"ok": False, "error": "empty", "playing": False}
        with self._lock:
            self._minutes = minutes
            self._deadline = time.monotonic() + minutes * 60
            self._stop.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="music", daemon=True
                )
                self._thread.start()
        print(
            f"tts music start tracks={len(tracks)} minutes={minutes} dir={self.directory}",
            flush=True,
        )
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            self._deadline = 0.0
            self._minutes = 0
            self._track = None
        return self.status()

    def status(self) -> dict:
        with self._lock:
            if not self._minutes or self._deadline <= 0:
                return {"playing": False, "track": None, "seconds_left": 0, "minutes": 0}
            left = max(0.0, self._deadline - time.monotonic())
            return {
                "playing": True,
                "track": self._track,
                "seconds_left": int(left),
                "minutes": self._minutes,
            }

    def _loop(self) -> None:
        try:
            import sounddevice as sd

            played: set[str] = set()
            stream = None
            try:
                while not self._stop.is_set() and not self._expired():
                    if stream is None:
                        stream = self._open_stream()
                    track = self._next_track(played)
                    if track is None:
                        # 一轮放完：重新洗牌接着放，这才是电台
                        played.clear()
                        continue
                    try:
                        audio, rate = load_track(track)
                    except LookupError:
                        continue
                    if self._stop.is_set() or self._expired():
                        break
                    if stream.samplerate != rate:
                        stream.stop()
                        stream.close()
                        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32")
                        stream.start()
                    with self._lock:
                        self._track = track.stem
                    print(f"tts music track={track.stem}", flush=True)
                    self._play_file(stream, audio, rate)
            finally:
                if stream is not None:
                    stream.stop()
                    stream.close()
        except Exception as exc:  # noqa: BLE001 - 声卡忙/目录没了：音乐闭嘴就行
            print(f"tts music error {exc}", flush=True)
        finally:
            self.stop()

    def _next_track(self, played: set[str]) -> Path | None:
        tracks = list_tracks(self.directory)
        rest = [path for path in tracks if path.name not in played]
        if not rest:
            return None
        pick = random.choice(rest)
        played.add(pick.name)
        return pick

    def _open_stream(self):
        import sounddevice as sd

        stream = sd.OutputStream(samplerate=24000, channels=1, dtype="float32")
        stream.start()
        return stream

    def _play_file(self, stream, audio: np.ndarray, rate: int) -> None:
        block = int(self.BLOCK_SEC * rate)
        position = 0
        while position < len(audio):
            if self._stop.is_set() or self._expired():
                return
            chunk = audio[position : position + block]
            position += len(chunk)
            stream.write(self._shape(chunk).reshape(-1, 1))

    def _expired(self) -> bool:
        return self._deadline > 0 and time.monotonic() >= self._deadline

    def _shape(self, chunk: np.ndarray) -> np.ndarray:
        """淡入 + 到点前淡出，音量整体压到 0.32——音乐当底，人声在上面。"""
        with self._lock:
            total_left = max(0.0, self._deadline - time.monotonic())
        elapsed_total = self._minutes * 60
        gain = GAIN
        if elapsed_total > 0:
            played = elapsed_total - total_left
            if played < FADE_IN_SEC:
                gain *= max(0.0, played / FADE_IN_SEC)
        if total_left < FADE_SEC:
            gain *= max(0.0, total_left / FADE_SEC)
        return (chunk * gain).astype(np.float32)
