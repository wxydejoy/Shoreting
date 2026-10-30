"""F31 白噪音：睡前不放朗读时，放点声音。

只依赖 numpy，不碰声卡——出声由 server.py 的 NoiseBoard 接。四种声音全靠合成，
不放音频文件：这玩意儿的本质是「有点响、不抓耳」，不是高保真。

循环接缝用等功率交叉淡化抹掉：人耳对雨声里的「咔」一声比音色敏感得多。
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 24000
NOISE_NAMES = ("rain", "waves", "fan", "brown")
DEFAULT_MINUTES = 30
FADE_SEC = 45.0
"""到点前的淡出时长。突然安静比声音本身更容易把人惊着。"""
MAX_MINUTES = 180
LOOP_XFADE_SEC = 1.0


def clamp_minutes(value: object, default: int = DEFAULT_MINUTES) -> int:
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_MINUTES, minutes))


def _brown(seconds: float, rng: np.random.Generator, rate: int = SAMPLE_RATE) -> np.ndarray:
    n = int(seconds * rate)
    white = rng.standard_normal(n).astype(np.float32)
    brown = np.cumsum(white)
    brown -= np.linspace(brown[0], brown[-1], n)  # 去掉累计的直流浪头
    brown /= max(1e-9, np.max(np.abs(brown)))
    return brown.astype(np.float32)


def _lowpass(audio: np.ndarray, alpha: float) -> np.ndarray:
    """一阶 IIR 低通。alpha 越小越闷。"""
    out = np.empty_like(audio)
    acc = 0.0
    for index, sample in enumerate(audio):
        acc += alpha * (sample - acc)
        out[index] = acc
    return out


def _rain(rng: np.random.Generator, seconds: float, rate: int) -> np.ndarray:
    """雨 = 一层闷掉的底（屋顶那片）+ 稀疏的高频滴答（真的雨点）。"""
    n = int(seconds * rate)
    base = _brown(seconds, rng, rate) * 0.75
    droplets = np.zeros(n, dtype=np.float32)
    drops = rng.integers(0, n, size=max(1, int(seconds * 26)))
    for start in drops:
        length = min(60, n - int(start))
        if length <= 0:
            continue
        decay = np.exp(-np.arange(length) / 9.0).astype(np.float32)
        droplets[start : start + length] += decay * rng.uniform(0.4, 1.0)
    click = _lowpass(droplets, 0.55)
    return base + click * 0.5


def _waves(rng: np.random.Generator, seconds: float, rate: int) -> np.ndarray:
    """浪 = 宽带沙沙声乘一个 0.08Hz 的慢起慢落，浪头偶尔带点高频。"""
    n = int(seconds * rate)
    base = _brown(seconds, rng, rate) * 0.6 + rng.standard_normal(n).astype(np.float32) * 0.4
    t = np.arange(n, dtype=np.float32) / rate
    swell = 0.55 + 0.45 * np.sin(2 * np.pi * 0.08 * t + float(rng.uniform(0, 6.28)))
    swell *= 0.85 + 0.15 * np.sin(2 * np.pi * 0.021 * t)
    return base * swell.astype(np.float32)


def _fan(rng: np.random.Generator, seconds: float, rate: int) -> np.ndarray:
    """风扇 = 闷掉的白噪音 + 极轻的桨叶周期声。"""
    n = int(seconds * rate)
    base = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n, dtype=np.float32) / rate
    blade = 0.06 * np.sin(2 * np.pi * 22.0 * t)
    return _lowpass(base, 0.12) + blade


_SYNTH = {
    "rain": _rain,
    "waves": _waves,
    "fan": _fan,
    "brown": lambda rng, seconds, rate: _brown(seconds, rng, rate) * 0.9,
}


def synth_loop(name: str, seconds: float = 8.0, rate: int = SAMPLE_RATE, seed: int = 7) -> np.ndarray:
    """生成一段无缝循环的噪音（float32 mono，峰值 ≤ 0.5）。

    纯函数：不碰声卡、不落盘。seconds 控制循环体长度，8 秒对雨声足够长，
    长到听不出循环。
    """
    if name not in _SYNTH:
        raise ValueError(f"没有这种声音：{name}")
    rng = np.random.default_rng(seed)
    audio = _SYNTH[name](rng, seconds, rate)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio / peak * 0.5
    return _make_loopable(audio, rate)


def _make_loopable(audio: np.ndarray, rate: int) -> np.ndarray:
    """尾巴交叉淡化进开头，循环点听不出来。"""
    fade = int(LOOP_XFADE_SEC * rate)
    if fade <= 0 or fade * 3 >= len(audio):
        return audio
    head = audio[:fade].copy()
    tail = audio[-fade:].copy()
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    # 尾巴淡出、开头淡入，重叠相加，然后砍掉接缝两侧多余的段
    audio = audio[:-fade]
    audio[:fade] = tail * (1.0 - ramp) + head * ramp
    return np.clip(audio, -1.0, 1.0)


def fade_out(audio: np.ndarray, seconds: float, rate: int = SAMPLE_RATE) -> np.ndarray:
    """整段尾部线性淡出。淡出曲线在最后一秒内走到 0。"""
    fade = min(int(seconds * rate), len(audio))
    if fade <= 0:
        return audio
    ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32)
    out = audio.copy()
    out[-fade:] *= ramp
    return out
