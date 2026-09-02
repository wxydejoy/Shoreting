from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dock_hub import DEFAULT_PORT
from dock_hub.errors import HubError

ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DEVICE_TYPES = {"switch", "light", "action"}


@dataclass
class TemperatureConfig:
    id: str
    name: str
    mijia_name: str
    celsius_prop: str = "temperature"
    humidity_prop: str | None = None


@dataclass
class PcConfig:
    enabled: bool = True
    sample_ms: int = 1000
    cpu_temp: bool = True
    gpu: bool = True


@dataclass
class MediaConfig:
    enabled: bool = True


@dataclass
class MiniLinkConfig:
    enabled: bool = True
    base_url: str = "http://10.83.22.121:17891"
    token: str = "helm-mini-weiekko"
    pc_ms: int = 1000
    mijia_ms: int = 15000


@dataclass
class DeviceConfig:
    id: str
    name: str
    type: str
    mijia_name: str | None = None
    on_prop: str = "on"
    brightness_prop: str | None = None
    icon: str | None = None
    program: str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    wait: bool = False
    timeout_sec: float = 8.0
    process: list[str] = field(default_factory=list)

    @property
    def is_action(self) -> bool:
        return self.type == "action"

    @property
    def is_mijia(self) -> bool:
        return self.type in {"switch", "light"}


@dataclass
class TethysConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8642/v1"
    model: str = "hermes-agent"
    api_key: str | None = None
    timeout_sec: float = 120.0
    polish: bool = True
    speak: bool = True
    ack: str | None = "好。"
    weixin_notify: bool = True
    session_key: str = "tethys:wanderer"
    notify_url: str | None = None


@dataclass
class CompanionConfig:
    enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:4b"
    llm_api_key: str | None = None
    timeout_sec: float = 30.0
    num_ctx: int = 4096
    num_predict: int = 256
    tts_base_url: str | None = None
    tts_timeout_sec: float = 20.0
    tts_deliver: bool = False
    persona: str | None = None
    tethys: TethysConfig | None = None


@dataclass
class HubConfig:
    name: str
    token: str
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    temperature: TemperatureConfig | None = None
    pc: PcConfig | None = None
    media: MediaConfig | None = None
    devices: list[DeviceConfig] = field(default_factory=list)
    companion: CompanionConfig | None = None
    mini: MiniLinkConfig | None = None
    path: Path | None = None

    def device(self, device_id: str) -> DeviceConfig:
        for item in self.devices:
            if item.id == device_id:
                return item
        raise HubError("not_found", f"设备 {device_id} 不在白名单")


def config_search_paths(explicit: Path | None = None) -> list[Path]:
    if explicit is not None:
        return [explicit]
    here = Path(__file__).resolve().parent.parent / "hub.yaml"
    home = Path.home() / ".config" / "dock-hub" / "hub.yaml"
    cwd = Path.cwd() / "hub.yaml"
    return [cwd, here, home]


def default_config_path() -> Path:
    return Path.home() / ".config" / "dock-hub" / "hub.yaml"


def ensure_config_file(explicit: Path | None = None) -> Path:
    """Create a minimal hub.yaml when missing so the wizard can finish setup."""
    path = explicit or default_config_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    save_config_raw(
        {
            "name": "study",
            "host": "0.0.0.0",
            "port": DEFAULT_PORT,
            "token": secrets.token_urlsafe(32),
            "pc": {"enabled": True, "sample_ms": 1000, "cpu_temp": True, "gpu": True},
            "devices": [],
        },
        path,
    )
    return path


def find_config_path(explicit: Path | None = None) -> Path:
    for path in config_search_paths(explicit):
        if path.is_file():
            return path
    searched = "\n".join(f"  {p}" for p in config_search_paths(explicit))
    raise FileNotFoundError(
        "找不到 hub.yaml。用 `dock-hub --init` 生成，或把文件放到：\n" + searched
    )


def load_config(explicit: Path | None = None) -> HubConfig:
    path = find_config_path(explicit)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 不是有效的 YAML 对象")
    return parse_config(raw, path)


def parse_config(raw: dict[str, Any], path: Path | None = None) -> HubConfig:
    name = str(raw.get("name") or "").strip()
    token = str(raw.get("token") or "").strip()
    if not name:
        raise ValueError("配置缺少 name")
    if not token or token == "replace-with-a-long-random-string":
        raise ValueError("请把 token 换成一段足够长的随机字符串")
    host = str(raw.get("host") or "0.0.0.0").strip()
    port = int(raw.get("port") or DEFAULT_PORT)
    if port < 1 or port > 65535:
        raise ValueError("port 无效")

    temperature = None
    if raw.get("temperature"):
        temperature = _parse_temperature(raw["temperature"])
    pc = _parse_pc(raw.get("pc"))
    media = _parse_media(raw.get("media"))
    companion = _parse_companion(raw.get("companion"))
    mini = _parse_mini(raw.get("mini"))

    devices: list[DeviceConfig] = []
    seen: set[str] = set()
    for item in raw.get("devices") or []:
        device = _parse_device(item)
        if device.id in seen:
            raise ValueError(f"重复的设备 id：{device.id}")
        seen.add(device.id)
        devices.append(device)

    return HubConfig(
        name=name,
        token=token,
        host=host,
        port=port,
        temperature=temperature,
        pc=pc,
        media=media,
        devices=devices,
        companion=companion,
        mini=mini,
        path=path,
    )


def _parse_temperature(raw: dict[str, Any]) -> TemperatureConfig:
    ident = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    mijia_name = str(raw.get("mijia_name") or "").strip()
    if not ident or not name or not mijia_name:
        raise ValueError("temperature 需要 id、name、mijia_name")
    _check_id(ident)
    humidity = raw.get("humidity_prop")
    humidity_prop = str(humidity).strip() if humidity else None
    return TemperatureConfig(
        id=ident,
        name=name,
        mijia_name=mijia_name,
        celsius_prop=str(raw.get("celsius_prop") or "temperature").strip(),
        humidity_prop=humidity_prop or None,
    )


def _parse_device(raw: dict[str, Any]) -> DeviceConfig:
    ident = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    dtype = str(raw.get("type") or "").strip()
    if not ident or not name or not dtype:
        raise ValueError("devices[] 需要 id、name、type")
    _check_id(ident)
    if ident == "media":
        raise ValueError("media 是保留 id，不能放进 devices[]")
    if dtype not in DEVICE_TYPES:
        raise ValueError(f"不支持的 type：{dtype}（{ident}）")

    icon = str(raw["icon"]).strip() if raw.get("icon") else None
    mijia_name = str(raw.get("mijia_name") or "").strip() or None
    brightness = _prop_name(raw.get("brightness_prop"), default="") or None

    if dtype == "action":
        program, args, cwd, wait, timeout_sec = _parse_run(ident, raw)
        process = _parse_process(raw)
        return DeviceConfig(
            id=ident,
            name=name,
            type=dtype,
            icon=icon,
            program=program,
            args=args,
            cwd=cwd,
            wait=wait,
            timeout_sec=timeout_sec,
            process=process,
        )

    if not mijia_name:
        raise ValueError(f"{dtype} {ident} 需要 mijia_name")
    if dtype == "switch" and brightness:
        raise ValueError(f"switch {ident} 不能配置 brightness_prop")
    return DeviceConfig(
        id=ident,
        name=name,
        type=dtype,
        mijia_name=mijia_name,
        on_prop=_prop_name(raw.get("on_prop"), default="on"),
        brightness_prop=brightness,
        icon=icon,
    )


def _parse_pc(raw: Any) -> PcConfig | None:
    if raw is None:
        return None
    if raw is False:
        return PcConfig(enabled=False)
    if not isinstance(raw, dict):
        raise ValueError("pc 必须是对象，或删掉整段以关闭")
    enabled = raw.get("enabled", True)
    if enabled is False:
        return PcConfig(enabled=False)
    return PcConfig(
        enabled=True,
        sample_ms=int(raw.get("sample_ms") or 1000),
        cpu_temp=bool(raw.get("cpu_temp", True)),
        gpu=bool(raw.get("gpu", True)),
    )


def _parse_media(raw: Any) -> MediaConfig:
    if raw is None:
        return MediaConfig(enabled=True)
    if raw is False:
        return MediaConfig(enabled=False)
    if not isinstance(raw, dict):
        raise ValueError("media 必须是对象，或 enabled: false")
    if raw.get("enabled") is False:
        return MediaConfig(enabled=False)
    return MediaConfig(enabled=True)


def _parse_mini(raw: Any) -> MiniLinkConfig:
    if raw is False:
        return MiniLinkConfig(enabled=False)
    if raw is None:
        return MiniLinkConfig()
    if not isinstance(raw, dict):
        raise ValueError("mini 必须是对象，或 enabled: false")
    if raw.get("enabled") is False:
        return MiniLinkConfig(enabled=False)
    token = str(raw.get("token") or "helm-mini-weiekko").strip() or "helm-mini-weiekko"
    return MiniLinkConfig(
        enabled=True,
        base_url=str(raw.get("base_url") or "http://10.83.22.121:17891").strip(),
        token=token,
        pc_ms=max(400, int(raw.get("pc_ms") or 1000)),
        mijia_ms=max(5000, int(raw.get("mijia_ms") or 15000)),
    )


def _parse_companion(raw: Any) -> CompanionConfig | None:
    if raw is None or raw is False:
        return None
    if not isinstance(raw, dict):
        raise ValueError("companion 必须是对象，或删掉整段以关闭")
    enabled = raw.get("enabled", True)
    if enabled is False:
        return CompanionConfig(enabled=False)
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    tts = raw.get("tts") if isinstance(raw.get("tts"), dict) else {}
    persona = str(raw["persona"]).strip() if raw.get("persona") else None
    tts_url = str(tts.get("base_url") or "").strip() or None
    api_key = str(llm.get("api_key") or "").strip() or None
    tethys_raw = raw.get("tethys") if isinstance(raw.get("tethys"), dict) else None
    tethys: TethysConfig | None = None
    if tethys_raw is not None:
        tethys_enabled = tethys_raw.get("enabled", True)
        if tethys_enabled is not False:
            ack_raw = tethys_raw.get("ack", "好。")
            ack = None if ack_raw is False or ack_raw is None else str(ack_raw).strip() or None
            tethys_key = str(tethys_raw.get("api_key") or "").strip() or None
            session_key = str(tethys_raw.get("session_key") or "tethys:wanderer").strip() or "tethys:wanderer"
            notify_url = str(tethys_raw.get("notify_url") or "").strip() or None
            tethys = TethysConfig(
                enabled=True,
                base_url=str(tethys_raw.get("base_url") or "http://127.0.0.1:8642/v1").strip(),
                model=str(tethys_raw.get("model") or "hermes-agent").strip(),
                api_key=tethys_key,
                timeout_sec=float(tethys_raw.get("timeout_sec") or 120),
                polish=bool(tethys_raw.get("polish", True)),
                speak=bool(tethys_raw.get("speak", True)),
                ack=ack,
                weixin_notify=bool(tethys_raw.get("weixin_notify", True)),
                session_key=session_key,
                notify_url=notify_url,
            )
    return CompanionConfig(
        enabled=True,
        llm_base_url=str(llm.get("base_url") or "http://127.0.0.1:11434").strip(),
        llm_model=str(llm.get("model") or "qwen3.5:4b").strip(),
        llm_api_key=api_key,
        timeout_sec=float(llm.get("timeout_sec") or 30),
        num_ctx=int(llm.get("num_ctx") or 4096),
        num_predict=int(llm.get("num_predict") or 256),
        tts_base_url=tts_url,
        tts_timeout_sec=float(tts.get("timeout_sec") or 20),
        tts_deliver=bool(tts.get("deliver", False)),
        persona=persona or None,
        tethys=tethys,
    )


def _parse_run(ident: str, raw: dict[str, Any]) -> tuple[str, list[str], str | None, bool, float]:
    run = raw.get("run")
    if isinstance(run, dict):
        program = str(run.get("program") or "").strip()
        args_raw = run.get("args") or []
        cwd = str(run["cwd"]).strip() if run.get("cwd") else None
        wait = bool(run.get("wait", False))
        timeout_sec = float(run.get("timeout_sec") or 8)
    else:
        program = str(raw.get("path") or raw.get("program") or "").strip()
        args_raw = raw.get("args") or []
        cwd = str(raw["cwd"]).strip() if raw.get("cwd") else None
        wait = bool(raw.get("wait", False))
        timeout_sec = float(raw.get("timeout_sec") or 8)
    if not program:
        raise ValueError(f"action {ident} 需要 run.program（本机程序/脚本，不会发给手机）")
    if not isinstance(args_raw, list):
        raise ValueError(f"{ident} 的 args 必须是列表")
    return program, [str(x) for x in args_raw], cwd, wait, timeout_sec


def _parse_process(raw: dict[str, Any]) -> list[str]:
    run = raw.get("run")
    source = None
    if isinstance(run, dict) and run.get("process") is not None:
        source = run.get("process")
    elif raw.get("process") is not None:
        source = raw.get("process")
    if source is None:
        return []
    if isinstance(source, str):
        items = [source]
    elif isinstance(source, list):
        items = source
    else:
        raise ValueError("process 必须是进程名或进程名列表")
    return [str(item).strip() for item in items if str(item).strip()]


def _prop_name(value: Any, default: str) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    if value is None or value == "":
        return default
    return str(value).strip()


def _check_id(ident: str) -> None:
    if not ID_RE.match(ident):
        raise ValueError(f"id 必须 URL 安全（字母数字 . _ -）：{ident}")


def device_to_raw(device: DeviceConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": device.id, "name": device.name, "type": device.type}
    if device.icon:
        raw["icon"] = device.icon
    if device.is_action:
        run: dict[str, Any] = {
            "program": device.program or "",
            "args": list(device.args),
            "wait": device.wait,
        }
        if device.cwd:
            run["cwd"] = device.cwd
        if device.timeout_sec != 8.0:
            run["timeout_sec"] = device.timeout_sec
        if device.process:
            run["process"] = list(device.process)
        raw["run"] = run
        return raw
    raw["mijia_name"] = device.mijia_name
    if device.on_prop != "on":
        raw["on_prop"] = device.on_prop
    if device.brightness_prop:
        raw["brightness_prop"] = device.brightness_prop
    return raw


def temperature_to_raw(temp: TemperatureConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": temp.id,
        "name": temp.name,
        "mijia_name": temp.mijia_name,
        "celsius_prop": temp.celsius_prop,
    }
    if temp.humidity_prop:
        raw["humidity_prop"] = temp.humidity_prop
    return raw


def hub_config_to_raw(config: HubConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "name": config.name,
        "host": config.host,
        "port": config.port,
        "token": config.token,
        "devices": [device_to_raw(item) for item in config.devices],
    }
    if config.temperature:
        raw["temperature"] = temperature_to_raw(config.temperature)
    if config.pc is not None:
        raw["pc"] = {
            "enabled": config.pc.enabled,
            "sample_ms": config.pc.sample_ms,
            "cpu_temp": config.pc.cpu_temp,
            "gpu": config.pc.gpu,
        }
    if config.media is not None:
        raw["media"] = {"enabled": config.media.enabled}
    if config.companion is not None:
        companion: dict[str, Any] = {"enabled": config.companion.enabled}
        if config.companion.enabled:
            llm: dict[str, Any] = {
                "base_url": config.companion.llm_base_url,
                "model": config.companion.llm_model,
                "timeout_sec": config.companion.timeout_sec,
            }
            if config.companion.llm_api_key:
                llm["api_key"] = config.companion.llm_api_key
            if config.companion.num_ctx != 4096:
                llm["num_ctx"] = config.companion.num_ctx
            if config.companion.num_predict != 256:
                llm["num_predict"] = config.companion.num_predict
            companion["llm"] = llm
            if config.companion.tts_base_url:
                tts: dict[str, Any] = {"base_url": config.companion.tts_base_url}
                if config.companion.tts_timeout_sec != 20.0:
                    tts["timeout_sec"] = config.companion.tts_timeout_sec
                if config.companion.tts_deliver:
                    tts["deliver"] = True
                companion["tts"] = tts
            if config.companion.persona:
                companion["persona"] = config.companion.persona
            if config.companion.tethys and config.companion.tethys.enabled:
                tethys: dict[str, Any] = {
                    "enabled": True,
                    "base_url": config.companion.tethys.base_url,
                    "model": config.companion.tethys.model,
                    "timeout_sec": config.companion.tethys.timeout_sec,
                    "polish": config.companion.tethys.polish,
                    "speak": config.companion.tethys.speak,
                }
                if config.companion.tethys.api_key:
                    tethys["api_key"] = config.companion.tethys.api_key
                if config.companion.tethys.ack is None:
                    tethys["ack"] = False
                elif config.companion.tethys.ack != "好。":
                    tethys["ack"] = config.companion.tethys.ack
                if not config.companion.tethys.weixin_notify:
                    tethys["weixin_notify"] = False
                if config.companion.tethys.session_key != "tethys:wanderer":
                    tethys["session_key"] = config.companion.tethys.session_key
                if config.companion.tethys.notify_url:
                    tethys["notify_url"] = config.companion.tethys.notify_url
                companion["tethys"] = tethys
        raw["companion"] = companion
    if config.mini is not None:
        mini: dict[str, Any] = {"enabled": config.mini.enabled}
        if config.mini.enabled:
            mini["base_url"] = config.mini.base_url
            mini["token"] = config.mini.token
            if config.mini.pc_ms != 1000:
                mini["pc_ms"] = config.mini.pc_ms
            if config.mini.mijia_ms != 15000:
                mini["mijia_ms"] = config.mini.mijia_ms
        raw["mini"] = mini
    return raw


def save_config_raw(raw: dict[str, Any], path: Path) -> HubConfig:
    cfg = parse_config(raw, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return cfg
