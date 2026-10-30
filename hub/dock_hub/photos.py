"""回忆相框（F37）：Hub 侧只管把照片递出去，不管什么时候显示。

照片目录默认在配置文件旁边的 ``photos/``——放进去就生效，不用改配置；
``photos: false`` 关掉，``photos: /path`` 换地方。安卓在没有数据可看的
时刻（电脑不在线的白天）轮播，夜里还是防烧屏的飘字钟，不抢它的活。

安全边界：文件名只认 basename，路径穿越（``..``、子目录）一律拒绝；
扩展名白名单，目录里混进来的别的文件不看。
"""

from __future__ import annotations

from pathlib import Path

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}

_MIMES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".gif": "image/gif",
}


def list_photos(directory: Path | None) -> list[str]:
    """按文件名排序的照片 id 列表。目录没开/不存在/读不了都算空。"""
    if directory is None:
        return []
    try:
        return sorted(
            item.name
            for item in directory.iterdir()
            if item.is_file() and item.suffix.lower() in PHOTO_EXTS
        )
    except OSError:
        return []


def resolve_photos_dir(explicit: Path | None, off: bool, config_path: Path | None) -> Path | None:
    """图库在哪：false 一票否决，显式路径其次，没配就看 hub.yaml 旁边的 photos/。

    旁目录是活检测——Hub 起来之后才建 photos/ 也算数，放进去就生效，不用改配置。
    """
    if off:
        return None
    if explicit is not None:
        return explicit
    if config_path is None:
        return None
    sibling = config_path.parent / "photos"
    return sibling if sibling.is_dir() else None


def photo_bytes(directory: Path | None, ident: str) -> tuple[bytes, str]:
    """读一张照片，返回 (bytes, mime)。不存在的、带路径的都拒。"""
    if directory is None:
        raise ValueError("相册没开")
    safe = Path(ident or "").name
    if not safe or safe in {".", ".."} or safe != ident:
        raise ValueError("照片 id 不对")
    path = directory / safe
    mime = mime_for(path.name)
    if mime is None or not path.is_file():
        raise ValueError("没有这张照片")
    return path.read_bytes(), mime


def mime_for(name: str) -> str | None:
    return _MIMES.get(Path(name).suffix.lower())
