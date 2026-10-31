"""把控制台的「视频背景」转给手机自己起的那个背景服务。

手机在 17920 上跑着一个小页面（BackgroundServer）：列目录、上传、选中、删除。
控制台不复制这套逻辑，只做三件事：

1. **地址**从 Hub 的三端地址簿取（`nodes.android`），端口是手机写死的 17920。
   先试 host（k20.lan）再退 ip：Mac 的默认路由不在 10.14.14 网段时 .lan 解不出来，
   但局域网 IP 永远到得了，所以哪个通就用哪个，并记下来省掉下次的 3 秒超时。
2. **口令**是 Hub Token。跟手机 POST /api/login 换一个会话 cookie，cookie 只活在
   控制台进程里，不进浏览器、不落盘。Token 也不在控制台存着，用的时候向本机 Hub
   要一次（/setup/api/token/reveal 只对本机开放）。
3. **字节原样搬**。视频动辄几十 MB，不在内存里攒完再发，直接从请求体流到手机。

手机没开机 / 没连 Wi-Fi 的时候，这里所有函数都抛 PhoneError，由 server.py 翻成
一句「手机上的岸亭没跑起来」。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from dock_console import hub_proxy

HUB_STATE = "/setup/api/state"
HUB_TOKEN = "/setup/api/token/reveal"

# 手机端 BackgroundServer.PORT，写死在 APK 里
PHONE_PORT = 17920
TIMEOUT = 6.0
UPLOAD_TIMEOUT = 300.0
# 抽预览图得把整个视频搬过来（手机不支持 Range），局域网内几十 MB 也就几秒
MEDIA_TIMEOUT = 120.0
CHUNK = 64 << 10

# 与 BackgroundServer.upload 的上限对齐（VIDEO_MAX_BYTES + 1MB 表单开销）
MAX_UPLOAD = 200 * 1024 * 1024

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_COOKIE_RE = re.compile(r"dock_bg=([^;]+)")


class PhoneError(Exception):
    """手机那边没接上 / 口令不对 / 回了看不懂的东西。"""


# 通了的那条地址记下来：.lan 解不出来时不必每点一次卡 3 秒
_GOOD_ADDRESS: list[str] | None = None
_SESSIONS: dict[str, str] = {}


def known_address() -> str | None:
    return _GOOD_ADDRESS[0] if _GOOD_ADDRESS else None


def reset() -> None:
    """控制台进程内缓存全清（测试与「连不上重试」用）。"""
    global _GOOD_ADDRESS
    _GOOD_ADDRESS = None
    _SESSIONS.clear()


def _hub_json(path: str) -> Any:
    status, _ctype, body = hub_proxy.forward("GET", path)
    if status != 200:
        raise PhoneError("先启动 Hub：拿不到三端地址")
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise PhoneError("Hub 回的不是 JSON") from exc


def candidates() -> list[str]:
    """地址簿里安卓那台的候选地址，按「上次通了的那条」优先。"""
    state = _hub_json(HUB_STATE)
    nodes = ((state.get("config") or {}) if isinstance(state, dict) else {}).get("nodes") or {}
    node = nodes.get("android") or {}
    host = str(node.get("host") or "").strip()
    ip = str(node.get("ip") or "").strip()
    ordered = [item for item in (host, ip) if item]
    if not ordered:
        raise PhoneError("三端地址簿里没写安卓的地址")
    if _GOOD_ADDRESS and _GOOD_ADDRESS[0] in ordered:
        ordered.remove(_GOOD_ADDRESS[0])
        ordered.insert(0, _GOOD_ADDRESS[0])
    return ordered


def hub_token() -> str:
    """向本机 Hub 要一次 Hub Token（不落盘、不回浏览器）。"""
    status, _ctype, body = hub_proxy.forward(
        "POST", HUB_TOKEN, content_type="application/json", body=b"{}"
    )
    if status != 200:
        raise PhoneError("拿不到 Hub Token，先把 Hub 启动起来")
    try:
        token = str(json.loads(body.decode("utf-8", errors="replace")).get("token") or "")
    except ValueError as exc:
        raise PhoneError("Hub 回的 Token 看不懂") from exc
    if not token:
        raise PhoneError("hub.yaml 里的 token 是空的")
    return token


def _login(host: str) -> str:
    payload = json.dumps({"token": hub_token()}).encode("utf-8")
    request = urllib.request.Request(
        f"http://{host}:{PHONE_PORT}/api/login",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            cookie = response.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 401:
            raise PhoneError("手机不认这个 Hub Token，检查两边是不是同一份") from exc
        raise PhoneError(f"手机回了 {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PhoneError(f"连不上手机（{exc}）") from exc
    match = _COOKIE_RE.search(cookie)
    if not match:
        raise PhoneError("手机没有发会话 cookie")
    return match.group(1)


def _session(host: str, *, refresh: bool = False) -> str:
    key = f"{host}:{PHONE_PORT}"
    if refresh or key not in _SESSIONS:
        _SESSIONS[key] = _login(host)
    return _SESSIONS[key]


def _call(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    host: str | None = None,
    not_found: str | None = None,
) -> dict[str, Any]:
    """带会话打一次手机接口；401 就重登一次再试。

    not_found：手机回 404 时的替身文案——旧 APK 没有新接口，报「版本太旧」
    比报「没有这个地址」更像一句人话。
    """
    if host is None:
        host = _connect()
    for attempt in range(2):
        cookie = _session(host, refresh=attempt > 0)
        request = urllib.request.Request(
            f"http://{host}:{PHONE_PORT}{path}",
            data=body or None,
            method=method,
            headers={
                "Accept": "application/json",
                "Cookie": f"dock_bg={cookie}",
                **({"Content-Type": "application/json"} if body else {}),
            },
        )
        try:
            with _OPENER.open(request, timeout=TIMEOUT) as response:
                raw = response.read(1 << 20).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read(1 << 16).decode("utf-8", errors="replace")
            exc.close()
            if exc.code == 401 and attempt == 0:
                continue  # 会话过期的唯一可能：手机重启过
            if exc.code == 404 and not_found:
                raise PhoneError(not_found) from exc
            raise PhoneError(_error_text(detail, exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _forget(host)
            raise PhoneError(f"连不上手机（{exc}）") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise PhoneError("手机回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise PhoneError("手机回的不是对象")
        return data
    raise PhoneError("手机一直不认这个会话")  # pragma: no cover - 循环里必返回


def _error_text(detail: str, code: int) -> str:
    try:
        payload = json.loads(detail)
        if isinstance(payload, dict):
            message = str(payload.get("error") or payload.get("message") or "")
            if message:
                return message
    except ValueError:
        pass
    return f"手机回了 {code}"


def _forget(host: str) -> None:
    global _GOOD_ADDRESS
    _SESSIONS.pop(f"{host}:{PHONE_PORT}", None)
    if _GOOD_ADDRESS and _GOOD_ADDRESS[0] == host:
        _GOOD_ADDRESS = None


def _connect() -> str:
    """挑一个能连上的地址：先 host 后 ip，通了的记住。"""
    global _GOOD_ADDRESS
    errors: list[str] = []
    for host in candidates():
        try:
            _session(host)
        except PhoneError as exc:
            errors.append(f"{host}: {exc}")
            continue
        _GOOD_ADDRESS = [host]
        return host
    raise PhoneError("；".join(errors) or "手机上没有开背景服务")


def list_backgrounds() -> dict[str, Any]:
    data = _call("GET", "/api/backgrounds")
    items = data.get("items") or []
    for item in items:
        # 抽过帧的顺手带上时长 / 分辨率；没抽过的不为它现拉一遍视频
        if item.get("kind") == "video":
            meta = cached_meta(str(item.get("id") or ""), int(item.get("bytes") or 0))
            item.update(meta)
    return {
        "ok": True,
        "current": data.get("current"),
        "items": items,
        "address": _GOOD_ADDRESS[0] if _GOOD_ADDRESS else None,
    }


def select(ident: str) -> dict[str, Any]:
    data = _call("POST", f"/api/backgrounds/{quote(ident)}/select", body=b"{}")
    return {"ok": True, **data}


def clear() -> dict[str, Any]:
    data = _call("POST", "/api/backgrounds/clear", body=b"{}")
    return {"ok": True, **data}


def get_layout() -> dict[str, Any]:
    """手机主屏布局现状：画布、槽位目录、默认布局、用户已存的那份。

    404 说明手机上还是旧 APK（没有这个接口），提示去更新。
    """
    data = _call("GET", "/api/layout", not_found="手机上的岸亭版本太旧，更新 APK 后才能读布局")
    return {"ok": True, **data}


def delete(ident: str) -> dict[str, Any]:
    data = _call("DELETE", f"/api/backgrounds/{quote(ident)}")
    return {"ok": True, **data}


# ---------------------------------------------------------------- 预览图
# 手机不支持 Range 请求，抽帧只能把整个文件搬过来。视频在局域网内就几十 MB，
# 搬一次按 (id, 字节数) 缓存成 JPEG，之后列表页刷多少遍都走本地缓存。
THUMB_WIDTH = 480

_CACHE_DIR: Path | None = None


def cache_dir() -> Path:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = Path.home() / ".cache" / "dock-console" / "backgrounds"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def set_cache_dir(path: Path) -> None:
    """测试换目录用。"""
    global _CACHE_DIR
    _CACHE_DIR = path
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    # launchd 起的进程 PATH 往往没有 /opt/homebrew/bin，手动补两条常见位置
    for fixed in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if os.path.exists(fixed):
            return fixed
    return None


def _thumb_key(ident: str, size: int) -> str:
    return hashlib.sha1(f"{ident}\n{size}".encode("utf-8")).hexdigest()[:20]


def _download_media(ident: str, dest: Path) -> None:
    path = f"/api/backgrounds/{quote(ident)}/media"
    host = _connect()
    for attempt in range(2):
        cookie = _session(host, refresh=attempt > 0)
        request = urllib.request.Request(
            f"http://{host}:{PHONE_PORT}{path}",
            headers={"Cookie": f"dock_bg={cookie}", "Accept": "*/*"},
        )
        try:
            with _OPENER.open(request, timeout=MEDIA_TIMEOUT) as response:
                with dest.open("wb") as out:
                    while True:
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
            return
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 401 and attempt == 0:
                continue  # 会话过期：手机重启过
            raise PhoneError(f"拿不到视频文件（{exc.code}）") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _forget(host)
            raise PhoneError(f"连不上手机（{exc}）") from exc
    raise PhoneError("手机一直不认这个会话")  # pragma: no cover - 循环里必返回


def _probe(source: Path) -> dict[str, Any]:
    """ffprobe 出时长与分辨率，拿不到就空着，不影响预览图本身。"""
    ffprobe = _tool("ffprobe")
    if ffprobe is None:
        return {}
    try:
        done = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(source),
            ],
            capture_output=True,
            timeout=30,
        )
        data = json.loads(done.stdout.decode("utf-8", errors="replace") or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    meta: dict[str, Any] = {}
    try:
        meta["duration"] = round(float(data["format"]["duration"]), 1)
    except (KeyError, TypeError, ValueError):
        pass
    for stream in data.get("streams") or []:
        if str(stream.get("codec_type")) == "video":
            try:
                meta["width"] = int(stream["width"])
                meta["height"] = int(stream["height"])
            except (KeyError, TypeError, ValueError):
                pass
            break
    return meta


def _extract_frame(source: Path, dest: Path) -> bool:
    ffmpeg = _tool("ffmpeg")
    if ffmpeg is None:
        raise PhoneError("Mac 上没装 ffmpeg，出不了预览图（brew install ffmpeg）")
    for seek in ("1.5", "0"):
        temp = dest.with_suffix(".tmp.jpg")
        try:
            done = subprocess.run(
                [
                    ffmpeg, "-nostdin", "-v", "error", "-ss", seek,
                    "-i", str(source), "-frames:v", "1",
                    "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "4",
                    "-y", str(temp),
                ],
                capture_output=True,
                timeout=60,
            )
        except subprocess.SubprocessError:
            continue
        if done.returncode == 0 and temp.exists() and temp.stat().st_size > 0:
            temp.replace(dest)
            return True
        temp.unlink(missing_ok=True)
    return False


_IMAGE_TYPES = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def thumbnail(ident: str, *, kind: str, size: int) -> dict[str, Any]:
    """返回 {"bytes", "content_type"}：视频抽帧，图片原样。失败抛 PhoneError。"""
    key = _thumb_key(ident, size)
    jpg = cache_dir() / f"{key}.jpg"
    meta_path = cache_dir() / f"{key}.json"
    if not jpg.exists():
        source = cache_dir() / f"{key}.src"
        try:
            _download_media(ident, source)
            if kind == "image":
                source.replace(jpg)
            else:
                if not _extract_frame(source, jpg):
                    raise PhoneError("抽不出预览帧，这个视频的编码可能不支持")
                meta_path.write_text(json.dumps(_probe(source)), encoding="utf-8")
        finally:
            source.unlink(missing_ok=True)
    content_type = "image/jpeg" if kind == "video" else _IMAGE_TYPES.get(Path(ident).suffix.lower(), "image/jpeg")
    return {"bytes": jpg.read_bytes(), "content_type": content_type}


def cached_meta(ident: str, size: int) -> dict[str, Any]:
    """列表页顺手带上已缓存的时长 / 分辨率，没有就不加，不为它去拉视频。"""
    meta_path = cache_dir() / f"{_thumb_key(ident, size)}.json"
    if not meta_path.exists():
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def upload(
    read_body: Callable[[int], bytes],
    content_type: str,
    length: int,
) -> dict[str, Any]:
    """把浏览器的 multipart 请求体原样流给手机，不在内存里攒。"""
    if length <= 0:
        raise PhoneError("没有收到文件")
    if length > MAX_UPLOAD:
        raise PhoneError("视频不能超过 200MB")
    if "multipart/form-data" not in content_type.lower():
        raise PhoneError("上传要 multipart/form-data")
    host = _connect()
    cookie = _session(host)
    connection = http.client.HTTPConnection(host, PHONE_PORT, timeout=UPLOAD_TIMEOUT)
    try:
        connection.putrequest("POST", "/api/backgrounds", skip_accept_encoding=True)
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(length))
        connection.putheader("Cookie", f"dock_bg={cookie}")
        connection.putheader("Connection", "close")
        connection.endheaders()
        left = length
        while left > 0:
            chunk = read_body(min(CHUNK, left))
            if not chunk:
                raise PhoneError("上传中断：文件没读完")
            connection.send(chunk)
            left -= len(chunk)
        response = connection.getresponse()
        raw = response.read(1 << 20)
    except (http.client.HTTPException, OSError) as exc:
        _forget(host)
        raise PhoneError(f"传到一半断了（{exc}）") from exc
    finally:
        connection.close()
    text = raw.decode("utf-8", errors="replace")
    if response.status >= 400:
        raise PhoneError(_error_text(text, response.status))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PhoneError("手机回的不是 JSON") from exc
    return {"ok": True, **(data if isinstance(data, dict) else {})}
