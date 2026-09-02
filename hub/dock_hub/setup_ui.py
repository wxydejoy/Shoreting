"""Local-only web setup wizard for hub.yaml (beginner friendly)."""

from __future__ import annotations

import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from dock_hub.config import (
    default_config_path,
    hub_config_to_raw,
    save_config_raw,
)
from dock_hub.errors import HubError
from dock_hub.mijia_bridge import clear_mijia_auth
from dock_hub.service import DockHub

SETUP_FLAG = Path.home() / ".config" / "dock-hub" / ".setup-opened"

_mijia_login_lock = threading.Lock()
_mijia_login_state: dict[str, Any] = {
    "phase": "idle",
    "qr_url": None,
    "message": None,
}


def setup_asset_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "setup.html"  # type: ignore[attr-defined]
        if p.is_file():
            return p
    return Path(__file__).resolve().parent / "assets" / "setup.html"


def setup_page_html() -> bytes:
    return setup_asset_path().read_bytes()


def is_local_client(address: tuple[str, int] | str) -> bool:
    host = address[0] if isinstance(address, tuple) else str(address)
    return host in {"127.0.0.1", "::1", "localhost"}


def open_setup_browser(hub: DockHub, *, force: bool = False) -> None:
    if not force and SETUP_FLAG.is_file():
        return
    SETUP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    SETUP_FLAG.write_text("1", encoding="utf-8")
    url = f"http://127.0.0.1:{hub.config.port}/setup"
    try:
        webbrowser.open(url)
    except Exception as exc:
        print(f"无法打开配置向导：{exc}", flush=True)
        print(f"请手动打开 {url}", flush=True)


def mijia_login_status() -> dict[str, Any]:
    with _mijia_login_lock:
        return {
            "phase": _mijia_login_state["phase"],
            "qr_url": _mijia_login_state["qr_url"],
            "message": _mijia_login_state["message"],
        }


def _set_mijia_login(**kwargs: Any) -> None:
    with _mijia_login_lock:
        _mijia_login_state.update(kwargs)


def start_mijia_login(hub: DockHub, *, force: bool = True) -> dict[str, Any]:
    with _mijia_login_lock:
        if _mijia_login_state["phase"] == "qr":
            return mijia_login_status()
        _mijia_login_state.update(phase="starting", qr_url=None, message="正在获取二维码…")

    def work() -> None:
        try:
            from mijiaAPI import mijiaAPI

            if force:
                clear_mijia_auth()
                with hub._mijia_lock:  # noqa: SLF001
                    hub.session = None

            client = mijiaAPI()
            login_data = client._get_qr_login_data()  # noqa: SLF001
            if login_data.get("refreshed"):
                hub.login()
                _set_mijia_login(phase="done", qr_url=None, message="登录成功")
                return

            login_url = str(login_data.get("loginUrl") or "")
            qr_url = str(login_data.get("qr") or login_url)
            if not qr_url:
                raise RuntimeError("米家未返回二维码链接")

            _set_mijia_login(phase="qr", qr_url=qr_url, message="请用米家 App 扫码")
            client._complete_qr_login(login_data)  # noqa: SLF001
            hub.login()
            _set_mijia_login(phase="done", qr_url=None, message="登录成功")
        except Exception as exc:
            _set_mijia_login(phase="error", qr_url=None, message=str(exc))
            print(f"米家登录失败：{exc}", flush=True)

    threading.Thread(target=work, name="setup-mijia-login", daemon=True).start()
    return mijia_login_status()


def fetch_mijia_devices(hub: DockHub) -> tuple[list[dict[str, Any]], str | None]:
    """List mijia devices for the setup wizard (never opens QR login)."""
    try:
        from mijiaAPI import mijiaAPI

        api = None
        if hub.session is not None and hub.session.available():
            api = hub.session.api
        else:
            client = mijiaAPI()
            if client.available:
                api = client
        if api is None:
            return [], "请先在本页扫码登录米家"
        devices = [
            {
                "name": str(d.get("name", "")),
                "model": str(d.get("model", "")),
                "online": d.get("isOnline", d.get("is_online")),
            }
            for d in api.get_devices_list()
        ]
        if not devices:
            return [], "米家账号里没有设备，或接口返回为空"
        return devices, None
    except Exception as exc:
        return [], f"拉取设备失败：{exc}"


def build_state(hub: DockHub) -> dict[str, Any]:
    from dock_hub.server import lan_ips

    status, message = hub._mijia_status()  # noqa: SLF001
    devices, devices_error = fetch_mijia_devices(hub)
    if devices_error and status != "ok":
        message = message or devices_error

    return {
        "config": hub_config_to_raw(hub.config),
        "lan_ips": lan_ips(),
        "mijia": status,
        "mijia_message": message,
        "mijia_devices": devices,
        "mijia_devices_error": devices_error,
        "mijia_login": mijia_login_status(),
        "config_path": str(hub.config.path or default_config_path()),
    }


def apply_save(hub: DockHub, payload: dict[str, Any]) -> dict[str, Any]:
    path = hub.config.path or default_config_path()
    raw = _payload_to_raw(payload, hub.config)
    port_changed = int(raw.get("port") or hub.config.port) != hub.config.port
    cfg = save_config_raw(raw, path)
    hub.reload_config(cfg)
    note = "已自动保存"
    if port_changed:
        note += "。端口已改，请退出 Hub 后重新启动"
    return {"ok": True, "message": note, "port_changed": port_changed}


def _payload_to_raw(payload: dict[str, Any], current: Any) -> dict[str, Any]:
    name = str(payload.get("name") or current.name).strip()
    token = str(payload.get("token") or current.token).strip()
    port = int(payload.get("port") or current.port)
    raw: dict[str, Any] = {
        "name": name,
        "host": current.host,
        "port": port,
        "token": token,
        "pc": payload.get("pc") if payload.get("pc") is not None else hub_config_to_raw(current).get("pc"),
        "devices": payload.get("devices") or [],
    }
    dumped = hub_config_to_raw(current)
    if "temperature" in payload:
        temp = payload.get("temperature")
        if temp:
            raw["temperature"] = temp
    elif current.temperature:
        raw["temperature"] = dumped["temperature"]
    if dumped.get("companion") is not None:
        raw["companion"] = dumped["companion"]
    if dumped.get("mini") is not None:
        raw["mini"] = dumped["mini"]
    return raw


def handle_setup_get(hub: DockHub, path: str, handler: Any) -> bool:
    """Return True if request handled."""
    if path == "/setup":
        handler._html(200, setup_page_html(), "text/html; charset=utf-8")  # noqa: SLF001
        return True
    if path == "/setup/api/state":
        handler._json(200, build_state(hub))  # noqa: SLF001
        return True
    if path == "/setup/api/mijia-login/status":
        handler._json(200, mijia_login_status())  # noqa: SLF001
        return True
    return False


def handle_setup_post(hub: DockHub, path: str, body: dict[str, Any], handler: Any) -> bool:
    if path == "/setup/api/save":
        try:
            result = apply_save(hub, body)
        except (ValueError, TypeError) as exc:
            handler._json(400, {"ok": False, "message": str(exc)})  # noqa: SLF001
            return True
        handler._json(200, result)  # noqa: SLF001
        return True
    if path in {"/setup/api/relogin", "/setup/api/mijia-login/start"}:
        force = bool(body.get("force", True))
        handler._json(200, start_mijia_login(hub, force=force))  # noqa: SLF001
        return True
    if path == "/setup/api/token":
        handler._json(200, {"token": secrets.token_urlsafe(32)})  # noqa: SLF001
        return True
    return False


def ensure_local(handler: Any) -> None:
    if not is_local_client(handler.client_address):
        raise HubError("forbidden", "配置向导只允许本机访问")
