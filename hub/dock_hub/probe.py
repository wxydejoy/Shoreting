"""Windows 探针：上报 CPU，并按 Hub 转发的 id 启动本机程序。"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from dock_hub.config import DEFAULT_PROBE_HUB_URL, HubConfig, PcConfig
from dock_hub.errors import HubError
from dock_hub.launch import launch, running_image_names
from dock_hub.pc import PcSampler
from dock_hub.reporter import post_telemetry
from dock_hub.setup_ui import is_local_client

MAX_BODY = 64 * 1024


def parse_listen(raw: str) -> tuple[str, int]:
    text = (raw or "0.0.0.0:17892").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    host, sep, port_s = text.rpartition(":")
    if not sep:
        return text or "0.0.0.0", 17892
    return (host or "0.0.0.0"), int(port_s)


OPEN_PAGES = {
    "/v1/open-setup": "/setup",
    "/v1/open-chats": "/chats",
    "/v1/open-read": "/read",
}


def mint_claim_url(config: HubConfig, next_path: str = "/setup") -> str:
    base = hub_lan_base(config)
    url = base + "/v1/login-ticket"
    raw = json.dumps({"next": next_path}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {probe_token(config)}",
    }
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
    except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        raise HubError("offline", "无法自动登录 Mini Hub") from exc
    path = str(body.get("path") or "")
    if not path.startswith("/chats/claim?"):
        raise HubError("offline", "无法自动登录 Mini Hub")
    return base + path


def probe_token(config: HubConfig) -> str:
    return (config.probe.token or config.token).strip()


def make_probe_handler(config: HubConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            if os.environ.get("DOCK_HUB_ACCESS_LOG"):
                print(f"{self.address_string()} {fmt % args}")

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if path == "/health":
                    self._json(
                        200,
                        {
                            "ok": True,
                            "service": "dock-probe",
                            "protocol": 1,
                            "name": "windows",
                        },
                    )
                    return
                if path in OPEN_PAGES:
                    if not is_local_client(self.client_address):
                        raise HubError("forbidden", "只能本机打开配置页")
                    nxt = OPEN_PAGES[path]
                    try:
                        target = mint_claim_url(config, nxt)
                    except HubError:
                        target = hub_lan_base(config) + "/chats?next=" + nxt
                    self._redirect(target)
                    return
                raise HubError("not_found", "未知接口")
            except HubError as exc:
                self._json(exc.status, exc.body())

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if path != "/v1/launch":
                    raise HubError("not_found", "未知接口")
                self._require_auth()
                body = self._read_json()
                self._json(200, handle_launch(config, body))
            except HubError as exc:
                self._json(exc.status, exc.body())

        def _require_auth(self) -> None:
            header = self.headers.get("Authorization") or self.headers.get("authorization") or ""
            expected = f"Bearer {probe_token(config)}"
            if not hmac.compare_digest(header.strip(), expected):
                raise HubError("unauthorized", "Token 不正确")

        def _read_json(self) -> dict[str, Any]:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError as exc:
                raise HubError("bad_request", "JSON 无效") from exc
            if length < 0 or length > MAX_BODY:
                raise HubError("bad_request", "请求体过大")
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HubError("bad_request", "JSON 无效") from exc
            if not isinstance(payload, dict):
                raise HubError("bad_request", "JSON 无效")
            return payload

        def _json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    return Handler


def handle_launch(config: HubConfig, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("run") is not True:
        raise HubError("bad_request", "启动项请发送 {\"run\": true}")
    ident = str(body.get("id") or "").strip()
    if not ident:
        raise HubError("bad_request", "缺少设备 id")
    cfg = config.device(ident)
    if not cfg.is_action:
        raise HubError("unsupported", f"{cfg.name} 不是启动项")
    launch(cfg)
    return {"ok": True, "id": cfg.id}


def idle_seconds() -> float | None:
    """键鼠空闲了多少秒（Windows）。拿不到（非 Windows / API 失败）就 None，
    Hub 侧的久坐提醒会安静跳过，不会瞎猜。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)
    except Exception:
        return None


def _windows_sample(sampler: PcSampler) -> dict[str, Any]:
    pc = sampler.snapshot() or {"online": True}
    idle = idle_seconds()
    sample: dict[str, Any] = {
        "pc": pc,
        "running": sorted(running_image_names()),
    }
    if idle is not None:
        sample["idle_sec"] = round(idle, 1)
    return sample


def _report_loop(config: HubConfig, sampler: PcSampler, stop: threading.Event) -> None:
    from dock_hub.config import MiniLinkConfig

    mini = MiniLinkConfig(
        enabled=True,
        base_url=(config.probe.hub_url or "").rstrip("/"),
        token=probe_token(config),
        pc_ms=max(400, (config.pc.sample_ms if config.pc else 1000)),
    )
    while not stop.wait(max(0.4, mini.pc_ms / 1000.0)):
        try:
            post_telemetry(mini, _windows_sample(sampler))
        except Exception:
            continue


def hub_lan_base(config: HubConfig) -> str:
    return (config.probe.hub_url or DEFAULT_PROBE_HUB_URL).rstrip("/")


def hub_lan_pages(config: HubConfig) -> dict[str, str]:
    base = hub_lan_base(config)
    return {
        "setup": f"{base}/setup",
        "setup_login": f"{base}/chats?next=/setup",
        "chats": f"{base}/chats",
        "read": f"{base}/read",
        "health": f"{base}/health",
    }


class ProbeRuntime:
    def __init__(self, server: ThreadingHTTPServer, stop: threading.Event, sampler: PcSampler) -> None:
        self.server = server
        self.stop_event = stop
        self.sampler = sampler
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.server.serve_forever, name="dock-probe-http", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        try:
            self.sampler.stop()
        except Exception:
            pass
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass


def _hide_windows_console() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def start_probe(config: HubConfig) -> ProbeRuntime:
    host, port = parse_listen(config.probe.listen)
    pc_cfg = config.pc or PcConfig(enabled=True)
    sampler = PcSampler(
        enabled=True,
        sample_ms=pc_cfg.sample_ms,
        cpu_temp=pc_cfg.cpu_temp,
        gpu=pc_cfg.gpu,
    )
    sampler.start()
    stop = threading.Event()
    reporter = threading.Thread(
        target=_report_loop,
        args=(config, sampler, stop),
        name="dock-probe-report",
        daemon=True,
    )
    reporter.start()
    handler = make_probe_handler(config)
    server = ThreadingHTTPServer((host, port), handler)
    bound = server.server_address[1]
    print(f"岸亭探针  http://{host}:{bound}", flush=True)
    print(f"上报 Hub  {config.probe.hub_url}/v1/telemetry", flush=True)
    if os.name == "nt":
        print("Windows 防火墙请放行入站 TCP 17892：", flush=True)
        print(
            '  netsh advfirewall firewall add rule name="Shoreting Probe" '
            "dir=in action=allow protocol=TCP localport=17892",
            flush=True,
        )
    return ProbeRuntime(server, stop, sampler)


def serve_probe(config: HubConfig) -> int:
    _hide_windows_console()
    runtime = start_probe(config)
    try:
        runtime.server.serve_forever()
    except KeyboardInterrupt:
        print("\n探针已停止", flush=True)
    finally:
        runtime.shutdown()
    return 0
