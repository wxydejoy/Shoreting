"""静态资源读取：进程内缓存，按 mtime/size 失效。"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], bytes]] = {}


def read_asset(path: Path) -> bytes:
    """读打包进程序或源码目录的静态文件；没改过就直接给上次的字节。"""

    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        with _lock:
            _cache.pop(key, None)
        return path.read_bytes()
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    data = path.read_bytes()
    with _lock:
        _cache[key] = (stamp, data)
    return data


def etag_for(data: bytes) -> str:
    """弱校验值：内容变则变，够用来回 304。"""

    return '"' + hashlib.sha1(data).hexdigest()[:16] + '"'


def clear_cache() -> None:
    with _lock:
        _cache.clear()
