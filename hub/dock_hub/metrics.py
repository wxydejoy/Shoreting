"""几个真正有人看的数字，按 Prometheus 的文本格式吐出来。

为什么要有：现在只有一行行 `latency ...` 打在 stdout 里，出了「今天她怎么变慢了」
这种问题，只能翻日志数。伴侣体验里唯一真正重要的指标是**首句延迟**——从收到
一句话到第一句出声，用户的容忍度大概在 1.5 秒，超了就明显「卡」。所以这里把
它做成直方图，另配几个计数。

刻意做得小：没有依赖、没有后台线程、没有 push。进程内计数，`/metrics` 现读现渲染。
重启归零，这是可以接受的——需要长期趋势时应该上真正的时间序列库，到时候
把这些名字照搬过去即可。
"""

from __future__ import annotations

import threading
import time
from typing import Iterable

# 首句延迟的桶（毫秒）。伴侣场景下 300ms 是「几乎立刻」，3000ms 往上基本
# 等于用户已经在怀疑是不是没听见。
LATENCY_BUCKETS_MS: tuple[float, ...] = (300, 600, 1000, 1500, 2500, 4000, 8000)

_MAX_TRACKED_TURNS = 64


class Registry:
    """线程安全的计数器 + 直方图。

    Counter：只增不减，用来数「发生了几次」。
    Histogram：观察值落进桶里，同时记 sum/count，够算平均与分位。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], list[float]
        ] = {}
        self._turn_started: dict[str, float] = {}

    # --- 写入 -------------------------------------------------------------

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            buckets = self._histograms.setdefault(key, [0.0] * (len(LATENCY_BUCKETS_MS) + 2))
            buckets[-2] += value  # sum
            buckets[-1] += 1.0  # count
            for index, edge in enumerate(LATENCY_BUCKETS_MS):
                if value <= edge:
                    buckets[index] += 1.0

    def note_turn(self, turn: str) -> None:
        """记下这一轮开始的时间，用来算首句延迟。"""
        now = time.monotonic()
        with self._lock:
            if len(self._turn_started) >= _MAX_TRACKED_TURNS:
                # 丢最旧的，别让异常路径把这张表撑爆
                oldest = min(self._turn_started, key=self._turn_started.get)
                self._turn_started.pop(oldest, None)
            self._turn_started[turn] = now

    def first_sentence(self, turn: str) -> float | None:
        """这一轮的首句延迟（毫秒）。没记过开始时间就返回 None。"""
        with self._lock:
            started = self._turn_started.pop(turn, None)
        if started is None:
            return None
        return (time.monotonic() - started) * 1000.0

    # --- 读取 -------------------------------------------------------------

    def counter(self, name: str, **labels: str) -> float:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            return self._counters.get(key, 0.0)

    def histogram(self, name: str, **labels: str) -> dict[str, float]:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            buckets = list(self._histograms.get(key) or [])
        if not buckets:
            return {"count": 0.0, "sum": 0.0}
        out = {f"le_{int(edge)}": buckets[i] for i, edge in enumerate(LATENCY_BUCKETS_MS)}
        out["count"] = buckets[-1]
        out["sum"] = buckets[-2]
        return out

    def render(self, gauges: dict[str, float] | None = None) -> str:
        """Prometheus 文本格式。gauge 是「读的时候才知道」的值（比如登录状态）。"""
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            histograms = dict(self._histograms)

        by_name: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = {}
        for (name, labels), value in counters.items():
            by_name.setdefault(name, []).append((labels, value))
        for name, rows in sorted(by_name.items()):
            lines.append(f"# TYPE {name} counter")
            for labels, value in sorted(rows):
                lines.append(f"{name}{_fmt_labels(labels)} {_fmt_num(value)}")

        hist_by_name: dict[str, list[tuple[tuple[tuple[str, str], ...], list[float]]]] = {}
        for (name, labels), buckets in histograms.items():
            hist_by_name.setdefault(name, []).append((labels, buckets))
        for name, rows in sorted(hist_by_name.items()):
            lines.append(f"# TYPE {name} histogram")
            for labels, buckets in sorted(rows):
                for index, edge in enumerate(LATENCY_BUCKETS_MS):
                    lines.append(
                        f"{name}_bucket{_fmt_labels(labels, le=str(int(edge)))} {_fmt_num(buckets[index])}"
                    )
                lines.append(f"{name}_bucket{_fmt_labels(labels, le='+Inf')} {_fmt_num(buckets[-1])}")
                lines.append(f"{name}_sum{_fmt_labels(labels)} {_fmt_num(buckets[-2])}")
                lines.append(f"{name}_count{_fmt_labels(labels)} {_fmt_num(buckets[-1])}")

        for name, value in sorted((gauges or {}).items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {_fmt_num(value)}")

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """测试用：把计数清空。"""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._turn_started.clear()


def _fmt_labels(labels: Iterable[tuple[str, str]], **extra: str) -> str:
    pairs = list(labels) + list(extra.items())
    if not pairs:
        return ""
    body = ",".join(f'{key}="{_escape(value)}"' for key, value in pairs)
    return "{" + body + "}"


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _fmt_num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}"


METRICS = Registry()
