"""Local-only web setup wizard for hub.yaml (beginner friendly)."""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from dock_hub.assets import read_asset
from dock_hub.config import (
    DEFAULT_PROBE_PORT,
    NODE_KEYS,
    MiniLinkConfig,
    PhoneConfig,
    ProbeConfig,
    default_config_path,
    device_to_raw,
    hub_config_to_raw,
    phone_to_raw,
    save_config_raw,
)
from dock_hub.errors import HubError
from dock_hub.mijia_bridge import clear_mijia_auth, list_device_props
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
    return Path(__file__).resolve().parent / "assets" / "app.html"


def setup_page_html() -> bytes:
    return read_asset(setup_asset_path())


def is_local_client(address: tuple[str, int] | str) -> bool:
    host = address[0] if isinstance(address, tuple) else str(address)
    if host.startswith("::ffff:"):
        host = host[7:]
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


def fetch_mijia_props(hub: DockHub, name: str) -> tuple[list[dict[str, Any]], str | None]:
    ident = (name or "").strip()
    if not ident:
        return [], "缺少设备名"
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
        return list_device_props(api, ident), None
    except Exception as exc:
        return [], f"拉取属性失败：{exc}"


def mask_secret(value: str, keep: int = 4) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "••••"
    return f"••••{text[-keep:]}"


def build_state(hub: DockHub) -> dict[str, Any]:
    from dock_hub.server import lan_ips

    status, message = hub._mijia_status()  # noqa: SLF001
    devices, devices_error = fetch_mijia_devices(hub)
    if devices_error and status != "ok":
        message = message or devices_error

    # Token 是安卓那边的口令，原文不进浏览器：页面只显示末 4 位，
    # 用户点「复制」时才走 /setup/api/token/reveal 取一次。
    config_raw = hub_config_to_raw(hub.config, mask_secrets=True)
    config_raw["token"] = ""
    # 探针口令、Mini 口令、朗读服务口令同理：只告诉页面「配过没有」，
    # 表单留空 = 保持原值（见 _clean_probe / _clean_mini / _clean_tts）。
    secrets = {
        "probe": bool(hub.config.probe.token),
        "mini": bool(hub.config.mini and hub.config.mini.token),
        "tts": bool(getattr(hub.config.companion, "tts_token", None)),
    }
    probe_raw = config_raw.get("probe")
    if isinstance(probe_raw, dict):
        probe_raw.pop("token", None)
    mini_raw = config_raw.get("mini")
    if isinstance(mini_raw, dict):
        mini_raw.pop("token", None)
    companion_raw = config_raw.get("companion")
    if isinstance(companion_raw, dict) and isinstance(companion_raw.get("tts"), dict):
        companion_raw["tts"].pop("token", None)
    # 手机端外观/电源/天气：yaml 里 rev==0 时整段不写，但控制台表单要读全量，
    # 所以这里无条件塞一份完整值（默认值也照样给）。
    config_raw["phone"] = phone_to_raw(hub.config.phone)
    return {
        "config": config_raw,
        "secrets": secrets,
        "token_masked": mask_secret(hub.config.token),
        "lan_ips": lan_ips(),
        "mijia": status,
        "mijia_message": message,
        "mijia_devices": devices,
        "mijia_devices_error": devices_error,
        "mijia_login": mijia_login_status(),
        "config_path": str(hub.config.path or default_config_path()),
        "llm": current_llm(hub),
        "voice": current_voice(hub),
    }


# ---------------------------------------------------------------- 大模型配置辅助
LLM_TIMEOUT_MODELS = 12.0
LLM_TIMEOUT_CHAT = 25.0


def _llm_base(base_url: str) -> str:
    """与 companion.py 的 _join 保持一致：不改写用户填的路径。

    之前这里会自动补 /v1，导致「测试通过」但正式调用用的是另一个地址。
    地址是否带 /v1 由用户按服务商要求填写（预设都已带好）。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("请先填写 API 地址")
    return base


def _llm_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def current_llm(hub: DockHub) -> dict[str, Any]:
    """当前 hub.yaml 里生效的大模型配置（不回显完整 key，只给长度提示）。"""
    comp = getattr(hub.config, "companion", None)
    if comp is None:
        return {"base_url": "", "model": "", "has_key": False}
    key = getattr(comp, "llm_api_key", None) or ""
    return {
        "base_url": getattr(comp, "llm_base_url", "") or "",
        "model": getattr(comp, "llm_model", "") or "",
        "has_key": bool(key),
        "key_hint": (key[:8] + "…" + key[-4:]) if len(key) > 14 else ("已配置" if key else ""),
    }


def current_voice(hub: DockHub) -> dict[str, Any]:
    from dock_hub.system_volume import read_output_volume

    comp = getattr(hub.config, "companion", None)
    saved = getattr(comp, "voice_volume", None) if comp is not None else None
    live = read_output_volume()
    speed = float(getattr(comp, "voice_speed", 1.0) or 1.0) if comp is not None else 1.0
    return {
        "volume": int(saved) if saved is not None else live,
        "speed": speed,
        "saved": saved is not None,
    }


def apply_voice(hub: DockHub, body: dict[str, Any]) -> dict[str, Any]:
    """写入 hub.yaml 的 companion.voice，并立刻改系统音量、通知朗读服务语速。"""
    from dock_hub.system_volume import clamp_speed, clamp_volume, write_output_volume

    comp = getattr(hub.config, "companion", None)
    if comp is None or not comp.enabled:
        raise ValueError("先在配置里开启桌面伴侣")
    volume = clamp_volume(body.get("volume"))
    speed = clamp_speed(body.get("speed"))
    comp.voice_volume = volume
    comp.voice_speed = speed
    _write_voice_yaml(hub, volume, speed)
    note = "已生效"
    try:
        write_output_volume(volume)
    except (OSError, subprocess.SubprocessError) as exc:
        note = f"音量没改成：{exc}"
    pushed = _push_voice_to_tts(comp.tts_base_url, volume, speed, getattr(comp, "tts_token", None))
    if pushed:
        note = pushed if note == "已生效" else f"{note}；{pushed}"
    return {"ok": True, "message": note, "volume": volume, "speed": speed}


def _write_voice_yaml(hub: DockHub, volume: int, speed: float) -> None:
    path = hub.config.path or default_config_path()
    raw: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    companion = raw.get("companion")
    if not isinstance(companion, dict):
        companion = {}
        raw["companion"] = companion
    companion["voice"] = {"volume": int(volume), "speed": round(float(speed), 2)}
    save_config_raw(raw, path)


def _push_voice_to_tts(
    base_url: str | None, volume: int, speed: float, token: str | None = None
) -> str | None:
    if not base_url:
        return None
    url = base_url.rstrip("/") + "/v1/settings"
    payload = json.dumps({"volume": volume, "speed": speed}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if (token or "").strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 404:
                return "系统音量已改。语速要等朗读服务更新后才生效"
            return None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "系统音量已改。语速要等朗读服务更新后才生效"
        return "朗读服务没接上语速"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "系统音量已改。朗读服务没开，语速先记着"


def _resolve_llm(hub: DockHub, body: dict[str, Any]) -> tuple[str, str, str | None]:
    """表单没填的字段回落到当前生效配置，方便直接点「拉取/测试」。"""
    now = current_llm(hub)
    base = str(body.get("base_url") or "").strip() or now["base_url"]
    model = str(body.get("model") or "").strip() or now["model"]
    key_in = str(body.get("api_key") or "").strip()
    if key_in:
        key = key_in
    else:
        comp = getattr(hub.config, "companion", None)
        key = getattr(comp, "llm_api_key", None) if comp else None
    return base, model, key


def fetch_llm_models(base_url: str, api_key: str | None) -> tuple[list[str], str | None]:
    import json
    import urllib.error
    import urllib.request

    try:
        url = _llm_base(base_url) + "/models"
    except ValueError as exc:
        return [], str(exc)
    req = urllib.request.Request(url, headers=_llm_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_MODELS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return [], f"{exc.code} {detail}"
    except Exception as exc:  # noqa: BLE001
        return [], f"连不上：{exc}"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return [], "返回不是 JSON"

    items: Any = None
    if isinstance(body, dict):
        items = body.get("data") or body.get("models") or body.get("result")
    elif isinstance(body, list):
        items = body
    if isinstance(items, dict):
        items = list(items.keys())
    ids: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            val = item.get("id") or item.get("name") or item.get("model")
            if val:
                ids.append(str(val))
        elif isinstance(item, str):
            ids.append(item)
    return sorted(set(ids)), None


def test_llm_model(base_url: str, model: str, api_key: str | None) -> dict[str, Any]:
    import json
    import time
    import urllib.error
    import urllib.request

    if not (model or "").strip():
        return {"ok": False, "message": "请先填写模型名"}
    try:
        url = _llm_base(base_url) + "/chat/completions"
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}

    # 与 Hub 正式调用保持一致的 payload：推理型模型若不禁用 thinking，
    # 少量 max_tokens 会被推理内容吃光，content 返回空 -> 误判为失败。
    payload: dict[str, Any] = {
        "model": model.strip(),
        "messages": [{"role": "user", "content": "只回复两个字：可用"}],
        "stream": False,
        "max_tokens": 256,
        "temperature": 0.7,
    }
    if "hermes" not in model.strip().lower():
        payload["enable_thinking"] = False
    headers = _llm_headers(api_key)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_CHAT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "message": f"{exc.code} {detail}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"连不上：{exc}"}
    ms = int((time.monotonic() - started) * 1000)

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "message": "返回不是 JSON"}
    sample = ""
    reasoning = ""
    finish = ""
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                finish = str(first.get("finish_reason") or "")
                msg = first.get("message")
                if isinstance(msg, dict):
                    sample = str(msg.get("content") or "")
                    reasoning = str(
                        msg.get("reasoning_content") or msg.get("reasoning") or ""
                    )
                if not sample:
                    sample = str(first.get("text") or "")
    sample = sample.strip()
    reasoning = reasoning.strip()
    if sample:
        return {"ok": True, "message": f"{ms} ms · {url}", "sample": sample[:120]}
    if reasoning:
        return {
            "ok": True,
            "message": f"{ms} ms（只回了推理内容，正式对话已用 enable_thinking=false）",
            "sample": reasoning[:120],
        }
    return {
        "ok": False,
        "message": f"{ms} ms，回复为空（finish_reason={finish or '?'}）：{raw[:160]}",
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


def apply_phone(hub: DockHub, body: dict[str, Any]) -> dict[str, Any]:
    """把手机端外观/电源/天气写进 hub.yaml，并立刻通知长轮询。

    手机不监听端口，Hub 推不动它；这条链路是「Hub 存着，手机来问」。写完
    note_change() 一下，挂着 /v1/snapshot?wait= 的那台会马上醒过来拿新 rev。
    """
    payload = body.get("phone") if isinstance(body.get("phone"), dict) else body
    current = hub.config
    phone_raw = _clean_phone(payload, current)
    path = current.path or default_config_path()
    raw = hub_config_to_raw(current)
    raw["phone"] = phone_raw
    cfg = save_config_raw(raw, path)
    hub.reload_config(cfg)
    hub.note_change()
    if cfg.phone.layout.rev != current.phone.layout.rev:
        note = f"已保存，等手机下次刷新就套用（布局第 {cfg.phone.layout.rev} 版）"
    else:
        note = f"已保存，等手机下次刷新就套用（第 {cfg.phone.rev} 版）"
    return {
        "ok": True,
        "message": note,
        "phone": phone_to_raw(cfg.phone),
    }


# ---------------------------------------------------------------- 表单字段清洗
# 三端配置页能改的段落比以前多：Windows 的探针、Mini 的监控地址、三端地址簿。
# 这些都是直接写进 yaml 的，所以每个字段都在这里过一遍，宁可报错也不写进半截 ip。

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,250}[A-Za-z0-9])?$")


def _clean_hostname(value: Any, field: str = "主机名") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _HOST_RE.match(text) or ".." in text:
        raise ValueError(f"{field}只能写主机名或 IP：{text}")
    return text


def _clean_port(value: Any, field: str = "端口", *, allow_zero: bool = False) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        if allow_zero:
            return 0
        raise ValueError(f"{field}不能为空")
    if not text.isdigit():
        raise ValueError(f"{field}要是数字")
    port = int(text)
    if port < (0 if allow_zero else 1) or port > 65535:
        raise ValueError(f"{field}超出范围：{port}")
    return port


def _clean_http(value: Any, default_port: int, field: str = "地址") -> str:
    """统一成 http://host:port。没写协议补 http，没写端口补默认端口。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field}看不懂：{text}")
    if parsed.username or parsed.password:
        raise ValueError(f"{field}不要带用户名密码")
    host = _clean_hostname(parsed.hostname, field)
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise ValueError(f"{field}端口不对：{text}") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{field}端口超出范围：{port}")
    return f"{parsed.scheme}://{host}:{port}"


def _clean_listen(value: Any, field: str = "监听地址") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    host, _, port = text.rpartition(":")
    if not host:
        raise ValueError(f"{field}要写成 host:port：{text}")
    return f"{_clean_hostname(host, field)}:{_clean_port(port, field, allow_zero=True)}"


def _clean_nodes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("nodes 必须是对象")
    out: dict[str, Any] = {}
    for key in NODE_KEYS:
        entry = value.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"nodes.{key} 必须是对象")
        clean: dict[str, Any] = {}
        if "host" in entry:
            clean["host"] = _clean_hostname(entry.get("host"), f"{key} 的主机名")
        if "ip" in entry:
            clean["ip"] = _clean_hostname(entry.get("ip"), f"{key} 的 IP")
        if "port" in entry:
            port = _clean_port(entry.get("port"), f"{key} 的端口", allow_zero=True)
            # 端口留空 = 这个角色没有端口（安卓就是这样），别在 yaml 里留个 0
            if port:
                clean["port"] = port
        if clean:
            out[key] = clean
    return out


def _clean_phone(value: Any, current: Any) -> dict[str, Any]:
    """手机端外观/电源/天气/布局：以当前值为底，覆盖提交到的字段，再自增 rev。

    校验交给 config._parse_phone（枚举、上下限都在那里），这里只负责合并与
    盖章。任何一项没通过都会整单拒绝——半截配置发到手机上比报错难受得多。
    外观（tile/type/screen/weather）与布局（layout）是两个独立的 rev：只存
    布局不该顺带把外观顶一遍，反过来也一样。
    """
    from dock_hub.config import _parse_phone

    base = getattr(current, "phone", None) or PhoneConfig()
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("phone 必须是对象")
    merged = phone_to_raw(base)
    touched_look = False
    for key in ("tile", "type", "screen", "weather"):
        entry = value.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"phone.{key} 必须是对象")
        merged[key].update({k: v for k, v in entry.items() if v is not None and v != ""})
        touched_look = True
    if touched_look:
        merged["rev"] = max(int(base.rev) + 1, 1)

    layout = value.get("layout")
    touched_layout = False
    if layout is not None:
        if not isinstance(layout, dict):
            raise ValueError("phone.layout 必须是对象")
        modules = layout.get("modules")
        if modules is None:
            if "rev" in layout and int(layout.get("rev") or 0) <= int(base.layout.rev):
                touched_layout = False  # 只是顺手带回来的旧值，不动
            else:
                modules = base.layout.modules  # 没给 modules：保持现有槽位
        else:
            if not isinstance(modules, dict):
                raise ValueError("phone.layout.modules 必须是对象")
            touched_layout = True  # 整包替换：编辑器发来的就是全部要存的槽位
        if touched_layout:
            merged["layout"] = {
                "rev": max(int(base.layout.rev) + 1, 1),
                "modules": modules,
            }
    _parse_phone(merged)  # 越界 / 枚举不对就在这里抛
    return merged


def _clean_probe(value: Any, current: Any) -> dict[str, Any]:
    base = getattr(current, "probe", None) or ProbeConfig()
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("probe 必须是对象")
    out: dict[str, Any] = {
        "url": base.url,
        "stale_ms": base.stale_ms,
        "hub_url": base.hub_url,
        "listen": base.listen,
    }
    if base.token:
        out["token"] = base.token
    if "url" in value:
        out["url"] = _clean_http(value.get("url"), DEFAULT_PROBE_PORT, "探针地址")
    if "hub_url" in value:
        out["hub_url"] = _clean_http(
            value.get("hub_url"), int(getattr(current, "port", 0) or 17890), "回连地址"
        )
    if "listen" in value:
        out["listen"] = _clean_listen(value.get("listen"), "探针监听")
    stale = str(value.get("stale_ms") or "").strip()
    if stale:
        if not stale.isdigit():
            raise ValueError("失联阈值要是毫秒数")
        out["stale_ms"] = max(200, min(600000, int(stale)))
    token = str(value.get("token") or "").strip()
    if token:
        out["token"] = token
    return out


def _clean_mini(value: Any, current: Any) -> dict[str, Any]:
    cur = getattr(current, "mini", None) or MiniLinkConfig()
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("mini 必须是对象")
    enabled = bool(value.get("enabled", cur.enabled))
    if not enabled:
        return {"enabled": False}
    out: dict[str, Any] = {
        "enabled": True,
        "base_url": cur.base_url,
        "token": cur.token,
        "pc_ms": cur.pc_ms,
        "mijia_ms": cur.mijia_ms,
    }
    if "base_url" in value:
        out["base_url"] = _clean_http(value.get("base_url"), 17891, "Mini 监控地址")
    token = str(value.get("token") or "").strip()
    if token:
        out["token"] = token
    for key, low, high, label in (
        ("pc_ms", 400, 60000, "电脑刷新间隔"),
        ("mijia_ms", 5000, 3600000, "米家刷新间隔"),
    ):
        text = str(value.get(key) or "").strip()
        if not text:
            continue
        if not text.isdigit():
            raise ValueError(f"{label}要是毫秒数")
        out[key] = max(low, min(high, int(text)))
    return out


def _clean_tts(value: dict[str, Any], current: Any) -> dict[str, Any]:
    comp = getattr(current, "companion", None)
    out: dict[str, Any] = {}
    if "base_url" in value:
        out["base_url"] = _clean_http(value.get("base_url"), 18100, "朗读服务地址")
    token = str(value.get("token") or "").strip()
    if token:
        out["token"] = token
    if "deliver" in value:
        out["deliver"] = bool(value.get("deliver"))
    if not out.get("token") and getattr(comp, "tts_token", None):
        out["token"] = comp.tts_token
    return out


def _payload_to_raw(payload: dict[str, Any], current: Any) -> dict[str, Any]:
    """把表单 → 一份完整的 yaml。

    起点是**当前配置的完整导回**，不是一张空表：向导里没出现的段落（schedules、
    dusk、alarm、theme、photos、访客模式…）必须原样带走，否则用户去功能开关里
    配了日落联动，回来点一次「保存」就把它全洗掉了。表单提交的字段再逐项覆盖。
    """
    dumped = hub_config_to_raw(current)
    raw: dict[str, Any] = dict(dumped)

    name = str(payload.get("name") or current.name).strip()
    token = str(payload.get("token") or current.token).strip()
    port = int(payload.get("port") or current.port)
    if port < 1 or port > 65535:
        raise ValueError("端口要落在 1–65535")
    raw["name"] = name
    raw["host"] = (
        _clean_hostname(payload.get("host"), "监听地址")
        if payload.get("host") is not None
        else current.host
    )
    raw["port"] = port
    raw["token"] = token

    if payload.get("pc") is not None:
        raw["pc"] = payload.get("pc")
    # 设备：浏览器没带这一项就保留原清单；带了就按 id 合并（绑定字段从旧值继承）
    if payload.get("devices") is not None:
        raw["devices"] = _merge_devices(payload.get("devices") or [], current)

    if "temperature" in payload:
        temp = payload.get("temperature")
        if temp:
            raw["temperature"] = temp
        else:
            raw.pop("temperature", None)
    if "nodes" in payload:
        raw["nodes"] = _clean_nodes(payload.get("nodes"))
    if "phone" in payload:
        raw["phone"] = _clean_phone(payload.get("phone"), current)
    if "probe" in payload:
        raw["probe"] = _clean_probe(payload.get("probe"), current)
    if "mini" in payload:
        raw["mini"] = _clean_mini(payload.get("mini"), current)
    if "media" in payload:
        raw["media"] = {"enabled": bool((payload.get("media") or {}).get("enabled", True))}
    # 主动播报的段向导里没有表单，保持原样（dumped 已带上，这里只防丢）
    if dumped.get("proactive") is not None:
        raw["proactive"] = dumped["proactive"]
    # 大模型覆盖（配置网页）：只覆盖表单里真正填了值的字段，留空 = 保持原值
    base_url_in = str(payload.get("llmBaseUrl") or "").strip()
    model_in = str(payload.get("llmModel") or payload.get("customModel") or "").strip()
    key_in = str(payload.get("llmApiKey") or payload.get("customApiKey") or "").strip()
    if base_url_in or model_in or key_in:
        companion_raw = raw.get("companion")
        if not isinstance(companion_raw, dict):
            companion_raw = {}
            raw["companion"] = companion_raw
        llm_raw = companion_raw.get("llm")
        if not isinstance(llm_raw, dict):
            llm_raw = {}
            companion_raw["llm"] = llm_raw
        if base_url_in:
            llm_raw["base_url"] = base_url_in
        if model_in:
            llm_raw["model"] = model_in
        if key_in:
            llm_raw["api_key"] = key_in
    # 伴侣自己的开关与 TTS（Mac 那台）：只改提交的字段
    companion_in = payload.get("companion")
    if isinstance(companion_in, dict):
        companion_raw = raw.get("companion")
        if not isinstance(companion_raw, dict):
            companion_raw = {}
            raw["companion"] = companion_raw
        if "enabled" in companion_in:
            companion_raw["enabled"] = bool(companion_in.get("enabled"))
        tts_in = companion_in.get("tts")
        if isinstance(tts_in, dict):
            tts_raw = companion_raw.get("tts")
            if not isinstance(tts_raw, dict):
                tts_raw = {}
                companion_raw["tts"] = tts_raw
            tts_raw.update(_clean_tts(tts_in, current))
            if not tts_raw.get("base_url"):
                companion_raw.pop("tts", None)
    if dumped.get("limits") is not None:
        # 向导的表单里没有限流那几格，不显式继承的话，用户手写在 yaml 里的
        # limits 会在点一次「保存」之后无声消失。
        raw["limits"] = dumped["limits"]
    if dumped.get("tokens"):
        # 表单里没有这一块：按设备的 token 只能手写 yaml，但点一次「保存」
        # 不能把它抹掉，否则那台设备下一次请求就是 401
        raw["tokens"] = dumped["tokens"]
    return raw


def _merge_devices(incoming: list[Any], current: Any) -> list[Any]:
    devices = [item for item in incoming if isinstance(item, dict)]
    seen = {str(item.get("id") or "") for item in devices}
    existing = {cfg.id: cfg for cfg in (getattr(current, "devices", []) or [])}
    for item in devices:
        # 绑定字段（on_prop / brightness_prop）由 Hub 自己维护，配置网页的表单不带它们。
        # 保存时若浏览器没给，就从当前配置继承，否则会被默认值 "on" 覆盖 -> 设备判为未绑定 -> 安卓按钮变灰。
        cfg = existing.get(str(item.get("id") or ""))
        if cfg is None:
            continue
        if not item.get("on_prop") and getattr(cfg, "on_prop", None):
            item["on_prop"] = cfg.on_prop
        if not item.get("brightness_prop") and getattr(cfg, "brightness_prop", None):
            item["brightness_prop"] = cfg.brightness_prop
    for cfg in getattr(current, "devices", []) or []:
        if cfg.is_ws2812 and cfg.id not in seen:
            devices.append(device_to_raw(cfg))
            seen.add(cfg.id)
    return devices


def handle_setup_get(hub: DockHub, path: str, handler: Any) -> bool:
    """Return True if request handled."""
    if path == "/setup":
        handler._html(200, setup_page_html(), "text/html; charset=utf-8")  # noqa: SLF001
        return True
    if path == "/setup/api/state":
        handler._json(200, build_state(hub))  # noqa: SLF001
        return True
    if path == "/setup/api/mijia-props":
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(getattr(handler, "path", "")).query)
        name = (query.get("name") or [""])[0]
        props, error = fetch_mijia_props(hub, name)
        handler._json(200, {"ok": error is None, "name": name, "props": props, "message": error})  # noqa: SLF001
        return True
    if path == "/setup/api/mijia-login/status":
        handler._json(200, mijia_login_status())  # noqa: SLF001
        return True
    if path == "/setup/api/features":
        from dock_hub.features import describe

        handler._json(200, {"ok": True, **describe(hub.config)})  # noqa: SLF001
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
    if path == "/setup/api/token/reveal":
        # 用户点「复制」时才取一次原文；调用方已过 ensure_local + CSRF 检查
        handler._json(200, {"token": hub.config.token})  # noqa: SLF001
        return True
    if path == "/setup/api/phone":
        try:
            result = apply_phone(hub, body)
        except (ValueError, TypeError) as exc:
            handler._json(400, {"ok": False, "message": str(exc)})  # noqa: SLF001
            return True
        handler._json(200, result)  # noqa: SLF001
        return True
    if path == "/setup/api/llm-models":
        base_url, _, api_key = _resolve_llm(hub, body)
        models, error = fetch_llm_models(base_url, api_key)
        handler._json(  # noqa: SLF001
            200, {"ok": error is None, "models": models, "message": error, "count": len(models)}
        )
        return True
    if path == "/setup/api/llm-test":
        base_url, model, api_key = _resolve_llm(hub, body)
        handler._json(200, test_llm_model(base_url, model, api_key))  # noqa: SLF001
        return True
    if path == "/setup/api/voice":
        try:
            result = apply_voice(hub, body)
        except ValueError as exc:
            handler._json(400, {"ok": False, "message": str(exc)})  # noqa: SLF001
            return True
        handler._json(200, result)  # noqa: SLF001
        return True
    if path == "/setup/api/features":
        from dock_hub.features import apply_feature

        try:
            result = apply_feature(hub, str(body.get("key") or ""), body.get("value"))
        except (ValueError, TypeError) as exc:
            handler._json(400, {"ok": False, "message": str(exc)})  # noqa: SLF001
            return True
        handler._json(200, result)  # noqa: SLF001
        return True
    return False


def guard_write(handler: Any) -> None:
    """配置写入的 CSRF 防线。

    本机访问是免 Token 的（页面就在 127.0.0.1 上），所以只要用户浏览器打开
    了恶意页面，一个 text/plain 的简单请求就能改掉 token / llm.base_url。
    这里要求两点：
    1. Content-Type 必须是 application/json —— 跨站简单请求做不到，会触发预检；
    2. 带 Origin 时必须是同源 —— 顺手挡住预检被放行的漏网情况。
    """
    ctype = (handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        raise HubError("unsupported", "配置写入需要 application/json", status=415)
    origin = (handler.headers.get("Origin") or "").strip()
    if not origin:
        return
    host = (handler.headers.get("Host") or "").strip().lower()
    if not host or urlparse(origin).netloc.lower() != host:
        raise HubError("forbidden", "拒绝跨站写入配置")


def ensure_local(handler: Any) -> None:
    chats_ok = getattr(handler, "_chats_ok", None)
    if callable(chats_ok):
        if chats_ok():
            return
        raise HubError("forbidden", "配置向导：本机直接开，局域网请带 Hub Token 或先打开 /chats 登录")
    if is_local_client(handler.client_address):
        return
    raise HubError("forbidden", "配置向导：本机直接开，局域网请带 Hub Token 或先打开 /chats 登录")
