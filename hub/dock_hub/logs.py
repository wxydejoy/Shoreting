"""吞掉异常的地方至少留一行痕迹。

这些服务没有 logging 体系（输出直接进 launchd 日志 / 控制台窗口），所以这里
只用 print(stderr)。同一处 60 秒内只打一次：轮询路径（米家刷新、桌面 reporter）
每几秒就会失败一次，不节流会把日志刷满，反而看不见别的东西。

用法：
    except Exception as exc:
        logs.swallowed("刷新米家设备缓存", exc)
"""

from __future__ import annotations

import sys
import threading
import time

_QUIET_SEC = 60.0
_lock = threading.Lock()
_last: dict[str, float] = {}


def swallowed(context: str, exc: BaseException) -> None:
    """记一次「这里出错但还能继续」。context 写能定位到代码的中文短语。"""
    now = time.monotonic()
    with _lock:
        if now - _last.get(context, 0.0) < _QUIET_SEC:
            return
        _last[context] = now
    detail = f"{type(exc).__name__}: {exc}".strip()
    print(f"[swallowed] {context}：{detail}", file=sys.stderr, flush=True)


def reset() -> None:
    """测试用：清掉节流记录。"""
    with _lock:
        _last.clear()
