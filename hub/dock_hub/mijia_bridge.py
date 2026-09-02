from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re

from mijiaAPI import mijiaAPI, get_device_info
from mijiaAPI.errors import ERROR_CODE, GetDeviceInfoError

from dock_hub.config import DeviceConfig, TemperatureConfig, ID_RE

OFFLINE_CODES = {
    -10007,
    -704042011,
    -704042001,
    -704090001,
    -704010000,
}


@dataclass
class PropRef:
    name: str
    siid: int
    piid: int
    type: str


@dataclass
class BoundDevice:
    cfg: DeviceConfig
    did: str | None = None
    model: str | None = None
    on_prop: PropRef | None = None
    brightness_prop: PropRef | None = None
    last_on: bool | None = None
    last_brightness: int | None = None
    missing: str | None = None
    voice_only: bool = False


@dataclass
class BoundTemperature:
    cfg: TemperatureConfig
    did: str | None = None
    celsius_prop: PropRef | None = None
    humidity_prop: PropRef | None = None
    missing: str | None = None


@dataclass
class MijiaSession:
    api: mijiaAPI
    devices: dict[str, BoundDevice] = field(default_factory=dict)
    temperature: BoundTemperature | None = None
    last_error: str | None = None

    def available(self) -> bool:
        try:
            return bool(self.api.available)
        except Exception:
            return False


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


AUTH_PATH = Path.home() / ".config" / "mijia-api" / "auth.json"
LOGIN_HINT_PATH = Path.home() / ".config" / "dock-hub" / "mijia-login.txt"


def clear_mijia_auth() -> None:
    if AUTH_PATH.is_file():
        AUTH_PATH.unlink()


def login_or_qr(
    api: mijiaAPI | None = None,
    *,
    force: bool = False,
    open_browser: bool = True,
) -> mijiaAPI:
    """登录米家。open_browser=False 时不弹浏览器（由配置向导内嵌二维码）。"""
    import webbrowser

    if force:
        clear_mijia_auth()
        client = mijiaAPI()
    else:
        client = api or mijiaAPI()
        if client.available:
            return client

    login_data = client._get_qr_login_data()  # noqa: SLF001 — mijiaAPI 公开流程
    if login_data.get("refreshed"):
        return client

    login_url = str(login_data.get("loginUrl") or "")
    qr_url = str(login_data.get("qr") or login_url)
    if not qr_url:
        raise RuntimeError("米家未返回二维码链接")

    LOGIN_HINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGIN_HINT_PATH.write_text(
        "请用【米家 App】扫码登录（不是小米账号网页随便扫）。\n"
        f"二维码图片：{qr_url}\n"
        f"原始链接：{login_url}\n",
        encoding="utf-8",
    )
    if open_browser:
        print("米家需要扫码登录。正在打开浏览器二维码…", flush=True)
        print(f"若浏览器没弹：打开 {LOGIN_HINT_PATH}", flush=True)
        print(f"二维码图片：{qr_url}", flush=True)
        try:
            webbrowser.open(qr_url)
        except Exception as exc:
            print(f"无法自动打开浏览器：{exc}", flush=True)
    else:
        print("米家需要扫码登录。请打开配置向导扫码。", flush=True)
        print(f"二维码链接：{LOGIN_HINT_PATH}", flush=True)

    # 有 TTY 时再打 ASCII，方便 --no-tray 控制台扫
    stdout = getattr(__import__("sys"), "stdout", None)
    if stdout is not None and getattr(stdout, "isatty", lambda: False)() and login_url:
        client._print_qr(login_url)  # noqa: SLF001

    client._complete_qr_login(login_data)  # noqa: SLF001
    print("米家登录成功", flush=True)
    return client


ON_PROP_CANDIDATES = ("on", "switch-status", "power", "switch")


def bind_from_config(
    api: mijiaAPI,
    devices: list[DeviceConfig],
    temperature: TemperatureConfig | None,
) -> MijiaSession:
    session = MijiaSession(api=api)
    listed = _device_index(api)
    for cfg in devices:
        if not cfg.is_mijia:
            continue
        session.devices[cfg.id] = _bind_device(cfg, listed)
    if temperature is not None:
        session.temperature = _bind_temperature(temperature, listed)
    taken_names = {cfg.mijia_name for cfg in devices if cfg.is_mijia and cfg.mijia_name}
    if temperature is not None:
        taken_names.add(temperature.mijia_name)
    taken_ids = set(session.devices)
    for extra in discover_switchable(listed, taken_names=taken_names, taken_ids=taken_ids):
        session.devices[extra.cfg.id] = extra
    return session


def _device_index(api: mijiaAPI) -> dict[str, dict[str, Any]]:
    listed = api.get_devices_list()
    index: dict[str, dict[str, Any]] = {}
    for item in listed:
        name = item.get("name")
        if isinstance(name, str) and name not in index:
            index[name] = item
    return index


def _bind_device(cfg: DeviceConfig, listed: dict[str, dict[str, Any]]) -> BoundDevice:
    raw = listed.get(cfg.mijia_name or "")
    if raw is None:
        return BoundDevice(cfg=cfg, missing=f"米家里没有名为「{cfg.mijia_name}」的设备")
    try:
        info = get_device_info(raw["model"], cache_path=_spec_cache())
    except GetDeviceInfoError:
        return BoundDevice(cfg=cfg, missing=f"无法获取 {cfg.mijia_name} 的规格（{raw.get('model')}）")
    props = _alias_props(info.get("properties", []))
    on_prop = _find_prop(props, cfg.on_prop)
    if on_prop is None:
        return BoundDevice(cfg=cfg, missing=f"{cfg.mijia_name} 没有属性 {cfg.on_prop}")
    brightness_prop = None
    if cfg.type == "light" and cfg.brightness_prop:
        brightness_prop = _find_prop(props, cfg.brightness_prop)
        if brightness_prop is None:
            return BoundDevice(cfg=cfg, missing=f"{cfg.mijia_name} 没有属性 {cfg.brightness_prop}")
    return BoundDevice(
        cfg=cfg,
        did=str(raw["did"]),
        model=raw.get("model"),
        on_prop=on_prop,
        brightness_prop=brightness_prop,
    )


def discover_switchable(
    listed: dict[str, dict[str, Any]],
    *,
    taken_names: set[str],
    taken_ids: set[str],
) -> list[BoundDevice]:
    """Bind every Mijia device that has an on-like property, except those already on the dock."""
    extras: list[BoundDevice] = []
    used = set(taken_ids)
    for name, raw in listed.items():
        if not name or name in taken_names:
            continue
        did = str(raw.get("did") or "").strip()
        model = raw.get("model")
        if not did or not isinstance(model, str):
            continue
        try:
            info = get_device_info(model, cache_path=_spec_cache())
        except GetDeviceInfoError:
            continue
        props = _alias_props(info.get("properties", []))
        on_prop = None
        for candidate in ON_PROP_CANDIDATES:
            on_prop = _find_prop(props, candidate)
            if on_prop:
                break
        if on_prop is None:
            continue
        ident = _voice_device_id(did, used)
        used.add(ident)
        cfg = DeviceConfig(
            id=ident,
            name=name,
            type="switch",
            mijia_name=name,
            on_prop=on_prop.name,
        )
        extras.append(
            BoundDevice(
                cfg=cfg,
                did=did,
                model=model,
                on_prop=on_prop,
                voice_only=True,
            )
        )
    return extras


def _voice_device_id(did: str, used: set[str]) -> str:
    base = "m" + re.sub(r"[^A-Za-z0-9._-]", "-", did).strip("-._")
    if not base or base == "m" or not ID_RE.match(base):
        base = "m-dev"
    ident = base[:64]
    n = 2
    while ident in used or not ID_RE.match(ident):
        suffix = f"-{n}"
        ident = (base[: 64 - len(suffix)] + suffix)
        n += 1
    return ident


def _bind_temperature(
    cfg: TemperatureConfig,
    listed: dict[str, dict[str, Any]],
) -> BoundTemperature:
    raw = listed.get(cfg.mijia_name)
    if raw is None:
        return BoundTemperature(cfg=cfg, missing=f"米家里没有名为「{cfg.mijia_name}」的设备")
    try:
        info = get_device_info(raw["model"], cache_path=_spec_cache())
    except GetDeviceInfoError:
        return BoundTemperature(cfg=cfg, missing=f"无法获取 {cfg.mijia_name} 的规格（{raw.get('model')}）")
    props = _alias_props(info.get("properties", []))
    celsius = _find_prop(props, cfg.celsius_prop)
    if celsius is None:
        return BoundTemperature(cfg=cfg, missing=f"{cfg.mijia_name} 没有属性 {cfg.celsius_prop}")
    humidity = None
    if cfg.humidity_prop:
        humidity = _find_prop(props, cfg.humidity_prop)
        if humidity is None:
            return BoundTemperature(cfg=cfg, missing=f"{cfg.mijia_name} 没有属性 {cfg.humidity_prop}")
    return BoundTemperature(
        cfg=cfg,
        did=str(raw["did"]),
        celsius_prop=celsius,
        humidity_prop=humidity,
    )


def _spec_cache() -> Path:
    return Path.home() / ".config" / "mijia-api"


def _alias_props(properties: list[dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for prop in properties:
        name = prop["name"]
        props[name] = prop
        props.setdefault(name.replace("-", "_"), prop)
        props.setdefault(name.replace("_", "-"), prop)
    return props


def _find_prop(props: dict[str, Any], name: str) -> PropRef | None:
    for candidate in _name_candidates(name):
        raw = props.get(candidate)
        if raw is None:
            continue
        method = raw["method"]
        return PropRef(name=candidate, siid=int(method["siid"]), piid=int(method["piid"]), type=raw["type"])
    return None


def _name_candidates(name: str) -> list[str]:
    aliases = {
        "humidity": ["relative-humidity", "relative_humidity"],
        "relative-humidity": ["humidity", "relative_humidity"],
        "relative_humidity": ["humidity", "relative-humidity"],
    }
    ordered = [name, name.replace("-", "_"), name.replace("_", "-"), *aliases.get(name, [])]
    unique: list[str] = []
    for item in ordered:
        if item not in unique:
            unique.append(item)
    return unique


def query_params(session: MijiaSession) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    temp = session.temperature
    if temp and temp.did and temp.celsius_prop:
        params.append(_param(temp.did, temp.celsius_prop))
        if temp.humidity_prop:
            params.append(_param(temp.did, temp.humidity_prop))
    for bound in session.devices.values():
        if bound.voice_only or not bound.did:
            continue
        if bound.on_prop:
            params.append(_param(bound.did, bound.on_prop))
        if bound.brightness_prop:
            params.append(_param(bound.did, bound.brightness_prop))
    return params


def _param(did: str, prop: PropRef) -> dict[str, Any]:
    return {"did": did, "siid": prop.siid, "piid": prop.piid}


def read_batch(api: mijiaAPI, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not params:
        return []
    result = api.get_devices_prop(params)
    if isinstance(result, dict):
        return [result]
    return list(result)


def result_map(rows: list[dict[str, Any]]) -> dict[tuple[str, int, int], dict[str, Any]]:
    mapped: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            key = (str(row["did"]), int(row["siid"]), int(row["piid"]))
        except (KeyError, TypeError, ValueError):
            continue
        mapped[key] = row
    return mapped


def lookup(mapped: dict[tuple[str, int, int], dict[str, Any]], did: str, prop: PropRef) -> dict[str, Any] | None:
    return mapped.get((did, prop.siid, prop.piid))


def is_ok(row: dict[str, Any] | None) -> bool:
    return row is not None and int(row.get("code", -1)) == 0


def is_offline(row: dict[str, Any] | None) -> bool:
    if row is None:
        return True
    code = int(row.get("code", -1))
    return code in OFFLINE_CODES


def error_message(row: dict[str, Any] | None, fallback: str) -> str:
    if row is None:
        return fallback
    code = row.get("code")
    text = ERROR_CODE.get(str(code), row.get("message") or fallback)
    return str(text)


def coerce_write(prop: PropRef, value: Any) -> Any:
    if prop.type == "bool":
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"true", "1", "on"}:
                return True
            if lowered in {"false", "0", "off"}:
                return False
        return bool(value)
    if prop.type in {"int", "uint"}:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    if prop.type == "float":
        return float(value)
    return value


def as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"true", "1", "on"}
    return bool(value)


def as_brightness(value: Any) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if 1 <= number <= 100:
        return number
    if number == 0:
        return None
    return max(1, min(100, number))


def updated_at(row: dict[str, Any] | None) -> str:
    if row and row.get("updateTime"):
        try:
            ts = int(row["updateTime"])
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError, OSError):
            pass
    return utc_now()
