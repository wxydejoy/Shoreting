"""Read and write the Windows probe address in hub.yaml."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_PROBE_PORT = 17892
_PROBE_HEAD = re.compile(r"^probe:\s*(#.*)?$")
_URL_LINE = re.compile(r"^(\s+)url:\s*(.*?)\s*$")


def normalize_windows_address(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("请填写 Windows 的地址")
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    host = parsed.hostname
    if not host or parsed.username or parsed.password:
        raise ValueError("地址看不懂")
    if any(ch.isspace() for ch in host):
        raise ValueError("地址看不懂")
    port = parsed.port or DEFAULT_PROBE_PORT
    if port < 1 or port > 65535:
        raise ValueError("端口不对")
    return f"http://{host}:{port}"


def read_probe_url(text: str) -> str | None:
    in_probe = False
    for line in text.splitlines():
        if _PROBE_HEAD.match(line):
            in_probe = True
            continue
        if not in_probe:
            continue
        stripped = line.strip()
        if stripped and not line[:1].isspace() and not stripped.startswith("#"):
            return None
        match = _URL_LINE.match(line)
        if match:
            return _unquote(match.group(2).split("#", 1)[0].strip()) or None
    return None


def display_address(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return url
    if parsed.port in (None, DEFAULT_PROBE_PORT):
        return host
    return f"{host}:{parsed.port}"


def windows_online(url: str, timeout: float = 0.6) -> bool:
    import json
    import urllib.request

    try:
        request = urllib.request.Request(url.rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(4096).decode())
    except Exception:
        return False
    return isinstance(payload, dict) and bool(payload.get("ok"))


def read_probe_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return read_probe_url(text)


def write_probe_url(text: str, url: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    ends_with_newline = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()
    in_probe = False
    replaced = False
    saw_probe = False
    out: list[str] = []
    for line in lines:
        if _PROBE_HEAD.match(line):
            in_probe = True
            saw_probe = True
            out.append(line)
            continue
        if in_probe:
            stripped = line.strip()
            if stripped and not line[:1].isspace() and not stripped.startswith("#"):
                if not replaced:
                    out.append(f"  url: {url}")
                    replaced = True
                in_probe = False
            else:
                match = _URL_LINE.match(line)
                if match and not replaced:
                    out.append(f"{match.group(1)}url: {url}")
                    replaced = True
                    continue
        out.append(line)
    if saw_probe and in_probe and not replaced:
        out.append(f"  url: {url}")
        replaced = True
    if not replaced:
        if out and out[-1] != "":
            pass
        out.append("probe:")
        out.append(f"  url: {url}")
    body = newline.join(out)
    if ends_with_newline or not text:
        body += newline
    return body


def write_probe_file(path: Path, url: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    original = path.read_text(encoding="utf-8")
    path.write_text(write_probe_url(original, url), encoding="utf-8")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
