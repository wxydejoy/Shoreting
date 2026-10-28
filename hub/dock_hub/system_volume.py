"""这台电脑的输出音量。Mac 用系统音量；不是把波形乘一个系数。"""

from __future__ import annotations

import subprocess
import sys

SPEED_MIN = 0.7
SPEED_MAX = 1.5


def clamp_volume(value: object) -> int:
    try:
        level = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("音量无效") from exc
    if level < 0 or level > 100:
        raise ValueError("音量在 0 到 100")
    return level


def clamp_speed(value: object) -> float:
    try:
        speed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("语速无效") from exc
    if speed < SPEED_MIN or speed > SPEED_MAX:
        raise ValueError("语速在 0.7 到 1.5 之间")
    return round(speed, 2)


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
