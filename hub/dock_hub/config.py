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
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEVICE_TYPES = {"switch", "light", "action", "ws2812", "sensor"}


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
class LimitsConfig:
    """谁调得太勤就拦一会儿。

    按端点分开算，因为它们的代价差着量级：一次 chat 是几千 token 的大模型调用，
    一次 command 只是往米家发一条指令。用一个全局数字要么太松（防不住刷 chat），
    要么太紧（手机正常用都会撞墙）。

    单位统一是「每分钟几次」，`per_minute <= 0` 的那一档等于关闭。
    """

    enabled: bool = True
    chat_per_min: int = 20
    announce_per_min: int = 10
    command_per_min: int = 60
    login_per_min: int = 10
    read_per_min: int = 20


@dataclass
class TokenConfig:
    """一枚给某台设备的 token。删掉这一条就等于吊销它。"""

    id: str
    value: str
    name: str | None = None
class MorningConfig:
    """早间播报：到点说一句，一天一次。"""

    at: str = ""
    text: str = ""


@dataclass
class AlertsConfig:
    """异常提醒的阈值。0 表示那条不提醒。"""

    temp_celsius: int = 85
    offline_minutes: int = 10
    # 连续用电脑多久提醒起来走走（键鼠空闲 5 分钟就算休息过了）；0 = 不提醒
    sedentary_minutes: int = 90


@dataclass
class ProactiveConfig:
    """她不等人喊也可以开口的时刻。

    默认整段关着。主动说话是这套东西里最容易变成噪音的功能，
    所以规则写成白名单（只有配了的时刻才说），再加每日配额与免打扰时段兜底。
    """

    enabled: bool = False
    daily_limit: int = 6
    quiet: tuple[str, str] = ("23:00", "07:00")
    morning: MorningConfig = field(default_factory=MorningConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)


@dataclass
class MiniLinkConfig:
    enabled: bool = True
    base_url: str = "http://10.83.22.121:17891"
    token: str = "helm-mini-weiekko"
    pc_ms: int = 1000
    mijia_ms: int = 15000


@dataclass
class PropConfig:
    id: str
    prop: str
    name: str | None = None
    role: str = "auto"
    write: bool = True
    unit: str | None = None
    min: float | None = None
    max: float | None = None


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
    base_url: str | None = None
    on_brightness: int = 48
    follow_pc: bool = False
    props: list[PropConfig] = field(default_factory=list)

    @property
    def is_action(self) -> bool:
        return self.type == "action"

    @property
    def is_sensor(self) -> bool:
        return self.type == "sensor"

    @property
    def is_mijia(self) -> bool:
        return self.type in {"switch", "light", "sensor"}

    @property
    def is_ws2812(self) -> bool:
        return self.type == "ws2812"

    def effective_props(self) -> list[PropConfig]:
        if self.props:
            return list(self.props)
        if self.is_sensor:
            return [
                PropConfig(id="celsius", prop="temperature", name="温度", write=False, unit="°C"),
                PropConfig(id="humidity", prop="humidity", name="湿度", write=False, unit="%"),
            ]
        if self.type == "light":
            items = [PropConfig(id="on", prop=self.on_prop, name="开关", role="toggle")]
            if self.brightness_prop:
                items.append(
                    PropConfig(
                        id="brightness",
                        prop=self.brightness_prop,
                        name="亮度",
                        role="number",
                        min=1,
                        max=100,
                        unit="%",
                    )
                )
            return items
        if self.type == "switch":
            return [PropConfig(id="on", prop=self.on_prop, name="开关", role="toggle")]
        return []


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
    voice_volume: int | None = None
    voice_speed: float = 1.0


DEFAULT_PROBE_PORT = 17892
DEFAULT_PROBE_LISTEN = "0.0.0.0:17892"
DEFAULT_PROBE_HUB_URL = "http://10.83.22.121:17890"


@dataclass
class ProbeConfig:
    url: str = ""
    token: str = ""
    stale_ms: int = 4000
    hub_url: str = DEFAULT_PROBE_HUB_URL
    listen: str = DEFAULT_PROBE_LISTEN


@dataclass
class ScheduleConfig:
    """一句定时：到点开/关几件设备，可选让守岸人说句话。"""

    id: str
    at: str  # "07:30"，24 小时制
    weekdays: list[int] = field(default_factory=list)  # 1=周一 … 7=周日；空=每天
    say: str = ""
    on: list[str] = field(default_factory=list)
    off: list[str] = field(default_factory=list)


@dataclass
class FocusConfig:
    """打游戏时自动切场景。

    检测的就是「启动项里的程序在不在跑」（跟守岸人判断在打游戏是同一套）。
    `on` 是游戏开起来要切的场景（id 或名字），`off` 是退出来恢复的场景；
    `off` 不写就不恢复。守岸人在游戏期间本来就不主动开口，不用另配。
    """

    enabled: bool = False
    on: str = ""
    off: str = ""


@dataclass
class SceneConfig:
    """一组「一起动」的设备，可以带一句话和一个语音口令。"""

    id: str
    name: str
    on: list[str] = field(default_factory=list)
    off: list[str] = field(default_factory=list)
    brightness: dict[str, int] = field(default_factory=dict)
    say: str | None = None
    phrases: list[str] = field(default_factory=list)

    def triggers(self) -> list[str]:
        """能被语音命中的说法：口令 + 场景名本身。"""
        said = [item for item in self.phrases if item]
        if self.name and self.name not in said:
            said.append(self.name)
        return said


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
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)
    mini: MiniLinkConfig | None = None
    probe: ProbeConfig = field(default_factory=ProbeConfig)
    schedules: list[ScheduleConfig] = field(default_factory=list)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    # 除主 token 之外按设备分发的 token；删一条即吊销一台
    tokens: list[TokenConfig] = field(default_factory=list)
    scenes: list[SceneConfig] = field(default_factory=list)
    focus: FocusConfig = field(default_factory=FocusConfig)
    path: Path | None = None

    def auth_values(self) -> tuple[str, ...]:
        """所有算数的 token：主 token + 每台设备那一枚。拿去常量时间比对。"""
        values = [self.token]
        values.extend(item.value for item in self.tokens if item.value)
        return tuple(dict.fromkeys(v for v in values if v))

    def device(self, device_id: str) -> DeviceConfig:
        for item in self.devices:
            if item.id == device_id:
                return item
        raise HubError("not_found", f"设备 {device_id} 不在白名单")

    def scene(self, scene_id: str) -> SceneConfig:
        for item in self.scenes:
            if item.id == scene_id:
                return item
        raise HubError("not_found", f"没有场景 {scene_id}")


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
    probe = _parse_probe(raw.get("probe"))
    limits = _parse_limits(raw.get("limits"))
    tokens = _parse_tokens(raw.get("tokens"), token)
    proactive = _parse_proactive(raw.get("proactive"))

    devices: list[DeviceConfig] = []
    seen: set[str] = set()
    for item in raw.get("devices") or []:
        device = _parse_device(item)
        if device.id in seen:
            raise ValueError(f"重复的设备 id：{device.id}")
        seen.add(device.id)
        devices.append(device)

    scenes = _parse_scenes(raw.get("scenes"), devices)
    focus = _parse_focus(raw.get("focus"), scenes)

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
        proactive=proactive,
        mini=mini,
        probe=probe,
        schedules=_parse_schedules(raw.get("schedules"), devices),
        limits=limits,
        tokens=tokens,
        scenes=scenes,
        focus=focus,
        path=path,
    )


def _parse_schedules(raw: Any, devices: list[DeviceConfig]) -> list[ScheduleConfig]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("schedules 应该是列表")
    known = {item.id for item in devices}
    out: list[ScheduleConfig] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("schedules[] 应该是对象")
        sched = _parse_schedule(item, known)
        if sched.id in seen:
            raise ValueError(f"重复的定时任务 id：{sched.id}")
        seen.add(sched.id)
        out.append(sched)
    return out


def _parse_schedule(raw: dict[str, Any], known: set[str]) -> ScheduleConfig:
    ident = str(raw.get("id") or "").strip()
    if not ident:
        raise ValueError("schedules[] 需要 id")
    _check_id(ident)
    at = str(raw.get("at") or "").strip()
    if not TIME_RE.match(at):
        raise ValueError(f"定时任务 {ident} 的 at 要写成 HH:MM（24 小时制）")
    weekdays: list[int] = []
    for item in raw.get("weekdays") or []:
        try:
            day = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"定时任务 {ident} 的 weekdays 只接受 1–7（周一=1）") from exc
        if day < 1 or day > 7:
            raise ValueError(f"定时任务 {ident} 的 weekdays 只接受 1–7（周一=1）")
        if day not in weekdays:
            weekdays.append(day)
    weekdays.sort()

    def ids(key: str) -> list[str]:
        picked: list[str] = []
        for item in raw.get(key) or []:
            device_id = str(item).strip()
            if not device_id:
                continue
            if device_id not in known:
                raise ValueError(f"定时任务 {ident} 引用了不存在的设备：{device_id}")
            if device_id not in picked:
                picked.append(device_id)
        return picked

    say = str(raw.get("say") or "").strip()[:200]
    return ScheduleConfig(
        id=ident,
        at=at,
        weekdays=weekdays,
        say=say,
        on=ids("on"),
        off=ids("off"),
    )


def _parse_scenes(raw: Any, devices: list[DeviceConfig]) -> list[SceneConfig]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("scenes 必须是列表")
    by_id = {item.id: item for item in devices}
    scenes: list[SceneConfig] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("scenes[] 每一项都要是对象")
        ident = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not ident or not name:
            raise ValueError("scenes[] 需要 id、name")
        _check_id(ident)
        if ident in seen:
            raise ValueError(f"重复的场景 id：{ident}")
        seen.add(ident)
        on = _scene_ids(ident, "on", item.get("on"), by_id)
        off = _scene_ids(ident, "off", item.get("off"), by_id)
        both = sorted(set(on) & set(off))
        if both:
            raise ValueError(f"场景 {ident} 里 {'、'.join(both)} 同时出现在 on 和 off")
        brightness: dict[str, int] = {}
        raw_brightness = item.get("brightness")
        if raw_brightness is not None:
            if not isinstance(raw_brightness, dict):
                raise ValueError(f"场景 {ident} 的 brightness 要写成 {{设备id: 亮度}}")
            for key, value in raw_brightness.items():
                device_id = str(key).strip()
                if device_id not in by_id:
                    raise ValueError(f"场景 {ident} 的 brightness 引用了不存在的设备：{device_id}")
                if by_id[device_id].is_action:
                    raise ValueError(f"场景 {ident} 里 {device_id} 是启动项，不能调亮度")
                try:
                    level = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"场景 {ident} 里 {device_id} 的亮度不是整数") from exc
                if not 1 <= level <= 100:
                    raise ValueError(f"场景 {ident} 里 {device_id} 的亮度必须是 1–100")
                brightness[device_id] = level
        say = str(item.get("say") or "").strip() or None
        raw_phrases = item.get("phrases") or []
        if not isinstance(raw_phrases, list):
            raise ValueError(f"场景 {ident} 的 phrases 必须是列表")
        phrases = [str(p).strip() for p in raw_phrases if str(p).strip()]
        scenes.append(
            SceneConfig(
                id=ident,
                name=name,
                on=on,
                off=off,
                brightness=brightness,
                say=say,
                phrases=phrases,
            )
        )
    return scenes


def _scene_ids(
    scene_id: str,
    field_name: str,
    raw: Any,
    by_id: dict[str, DeviceConfig],
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"场景 {scene_id} 的 {field_name} 必须是列表")
    out: list[str] = []
    for value in raw:
        device_id = str(value).strip()
        if not device_id:
            continue
        if device_id not in by_id:
            raise ValueError(f"场景 {scene_id} 的 {field_name} 引用了不存在的设备：{device_id}")
        if by_id[device_id].is_action:
            raise ValueError(f"场景 {scene_id} 的 {field_name} 里有启动项 {device_id}，它不能开关")
        if device_id not in out:
            out.append(device_id)
    return out


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

    if dtype == "ws2812":
        base_url = str(raw.get("base_url") or raw.get("url") or "").strip().rstrip("/")
        if not base_url:
            raise ValueError(f"ws2812 {ident} 需要 base_url")
        on_brightness = int(raw.get("on_brightness") if raw.get("on_brightness") is not None else 48)
        if not 1 <= on_brightness <= 255:
            raise ValueError(f"ws2812 {ident} 的 on_brightness 必须是 1–255")
        follow_pc = True if raw.get("follow_pc") is None else bool(raw.get("follow_pc"))
        timeout_sec = float(raw.get("timeout_sec") or 2.0)
        return DeviceConfig(
            id=ident,
            name=name,
            type=dtype,
            icon=icon,
            base_url=base_url,
            on_brightness=on_brightness,
            follow_pc=follow_pc,
            timeout_sec=timeout_sec,
        )

    if not mijia_name:
        raise ValueError(f"{dtype} {ident} 需要 mijia_name")
    if dtype == "switch" and brightness:
        raise ValueError(f"switch {ident} 不能配置 brightness_prop")
    props = _parse_props(raw.get("props"))
    if dtype == "sensor":
        if not props:
            props = [
                PropConfig(id="celsius", prop="temperature", name="温度", write=False, unit="°C"),
                PropConfig(id="humidity", prop="humidity", name="湿度", write=False, unit="%"),
            ]
        return DeviceConfig(
            id=ident,
            name=name,
            type=dtype,
            mijia_name=mijia_name,
            icon=icon,
            props=props,
        )
    return DeviceConfig(
        id=ident,
        name=name,
        type=dtype,
        mijia_name=mijia_name,
        on_prop=_prop_name(raw.get("on_prop"), default="on"),
        brightness_prop=brightness,
        icon=icon,
        props=props,
    )


def _parse_limits(raw: Any) -> LimitsConfig:
    if raw is None:
        return LimitsConfig()
    if raw is False:
        return LimitsConfig(enabled=False)
    if not isinstance(raw, dict):
        raise ValueError("limits 必须是对象，或用 limits: false 整段关掉")
    if raw.get("enabled") is False:
        return LimitsConfig(enabled=False)

    def rate(key: str, default: int, label: str) -> int:
        value = raw.get(key)
        if value is None:
            return default
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"limits.{key} 必须是整数（{label}）") from exc
        if number < 0:
            raise ValueError(f"limits.{key} 不能是负数（{label}）")
        return number

    return LimitsConfig(
        enabled=True,
        chat_per_min=rate("chat_per_min", 20, "伴侣对话"),
        announce_per_min=rate("announce_per_min", 10, "主动播报"),
        command_per_min=rate("command_per_min", 60, "设备控制"),
        login_per_min=rate("login_per_min", 10, "登录尝试"),
        read_per_min=rate("read_per_min", 20, "朗读"),
    )
def _parse_tokens(raw: Any, owner: str) -> list[TokenConfig]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("tokens 必须是列表，每项写成 {id, value}")
    seen: set[str] = set()
    values: set[str] = {owner}
    parsed: list[TokenConfig] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"tokens 第 {index} 项必须是对象")
        ident = str(item.get("id") or "").strip()
        value = str(item.get("value") or "").strip()
        if not ident:
            raise ValueError(f"tokens 第 {index} 项缺少 id")
        _check_id(ident)
        if ident in seen:
            raise ValueError(f"重复的 token id：{ident}")
        seen.add(ident)
        if len(value) < 8:
            # 短 token 在局域网里等于没有：脚本几秒就能撞出来
            raise ValueError(f"token {ident} 太短，至少 8 个字符")
        if value in values:
            raise ValueError(f"token {ident} 和别的 token 重复")
        values.add(value)
        name = str(item.get("name") or "").strip() or None
        parsed.append(TokenConfig(id=ident, value=value, name=name))
    return parsed


CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_clock(value: Any, label: str) -> str:
    """`7:30` / `07:30` → `07:30`。写错就报错，不要静悄悄当成没配。"""
    text = str(value or "").strip()
    match = CLOCK_RE.match(text)
    if not match:
        raise ValueError(f"{label} 要写成 07:30 这种，收到 {text!r}")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _threshold(value: Any, default: int, label: str) -> int:
    """阈值：不写用默认，写 0 表示这条不提醒，负数直接报错。"""
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是整数") from exc
    if number < 0:
        raise ValueError(f"{label} 不能是负数")
    return number


def _parse_proactive(raw: Any) -> ProactiveConfig:
    if raw is None or raw is False:
        return ProactiveConfig()
    if not isinstance(raw, dict):
        raise ValueError("proactive 必须是对象，或用 proactive: false 关掉")
    if raw.get("enabled") is False:
        return ProactiveConfig()
    limit_raw = raw.get("daily_limit", 6)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("proactive.daily_limit 必须是整数") from exc
    if limit < 0:
        raise ValueError("proactive.daily_limit 不能是负数")

    quiet_raw = raw.get("quiet", ["23:00", "07:00"])
    if isinstance(quiet_raw, str):
        parts = [part.strip() for part in quiet_raw.split("-") if part.strip()]
    elif isinstance(quiet_raw, (list, tuple)):
        parts = [str(part).strip() for part in quiet_raw]
    else:
        raise ValueError('proactive.quiet 写成 ["23:00", "07:00"] 或 "23:00-07:00"')
    if len(parts) != 2:
        raise ValueError("proactive.quiet 要两个时刻：开始和结束")
    quiet = (parse_clock(parts[0], "proactive.quiet"), parse_clock(parts[1], "proactive.quiet"))

    morning_raw = raw.get("morning") or {}
    if not isinstance(morning_raw, dict):
        raise ValueError("proactive.morning 必须是对象")
    at = morning_raw.get("at")
    morning = MorningConfig(
        at=parse_clock(at, "proactive.morning.at") if at else "",
        text=str(morning_raw.get("text") or "").strip(),
    )
    alerts_raw = raw.get("alerts")
    if alerts_raw is None or alerts_raw is True:
        alerts = AlertsConfig()
    elif alerts_raw is False:
        alerts = AlertsConfig(temp_celsius=0, offline_minutes=0, sedentary_minutes=0)
    elif isinstance(alerts_raw, dict):
        alerts = AlertsConfig(
            temp_celsius=_threshold(alerts_raw.get("temp_celsius"), 85, "proactive.alerts.temp_celsius"),
            offline_minutes=_threshold(
                alerts_raw.get("offline_minutes"), 10, "proactive.alerts.offline_minutes"
            ),
            sedentary_minutes=_threshold(
                alerts_raw.get("sedentary_minutes"), 90, "proactive.alerts.sedentary_minutes"
            ),
        )
    else:
        raise ValueError("proactive.alerts 必须是对象，或用 alerts: false 全关")
    return ProactiveConfig(
        enabled=True, daily_limit=limit, quiet=quiet, morning=morning, alerts=alerts
    )


def _parse_probe(raw: Any) -> ProbeConfig:
    if raw is None or raw is False:
        return ProbeConfig()
    if not isinstance(raw, dict):
        raise ValueError("probe 必须是对象")
    stale = int(raw.get("stale_ms") or 4000)
    return ProbeConfig(
        url=str(raw.get("url") or "").strip(),
        token=str(raw.get("token") or "").strip(),
        stale_ms=max(200, stale),
        hub_url=str(raw.get("hub_url") or DEFAULT_PROBE_HUB_URL).strip() or DEFAULT_PROBE_HUB_URL,
        listen=str(raw.get("listen") or DEFAULT_PROBE_LISTEN).strip() or DEFAULT_PROBE_LISTEN,
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
    voice = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    voice_volume = voice.get("volume")
    if voice_volume is not None:
        voice_volume = max(0, min(100, int(voice_volume)))
    voice_speed = float(voice.get("speed") or 1.0)
    voice_speed = max(0.7, min(1.5, voice_speed))
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
        voice_volume=voice_volume,
        voice_speed=voice_speed,
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


def _parse_props(raw: Any) -> list[PropConfig]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("props 必须是列表")
    items: list[PropConfig] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("props[] 必须是对象")
        ident = str(row.get("id") or row.get("prop") or "").strip()
        prop = str(row.get("prop") or row.get("id") or "").strip()
        if not ident or not prop:
            raise ValueError("props[] 需要 id 和 prop")
        _check_id(ident)
        if ident in seen:
            raise ValueError(f"重复的属性 id：{ident}")
        seen.add(ident)
        write = True if row.get("write") is None else bool(row.get("write"))
        role = str(row.get("role") or "auto").strip() or "auto"
        unit = str(row["unit"]).strip() if row.get("unit") else None
        name = str(row["name"]).strip() if row.get("name") else None
        min_v = float(row["min"]) if row.get("min") is not None else None
        max_v = float(row["max"]) if row.get("max") is not None else None
        items.append(
            PropConfig(
                id=ident,
                prop=prop,
                name=name,
                role=role,
                write=write,
                unit=unit,
                min=min_v,
                max=max_v,
            )
        )
    return items


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
    if device.is_ws2812:
        raw["base_url"] = device.base_url
        raw["on_brightness"] = device.on_brightness
        raw["follow_pc"] = device.follow_pc
        if device.timeout_sec != 2.0:
            raw["timeout_sec"] = device.timeout_sec
        return raw
    raw["mijia_name"] = device.mijia_name
    if device.on_prop != "on" and not device.is_sensor:
        raw["on_prop"] = device.on_prop
    if device.brightness_prop:
        raw["brightness_prop"] = device.brightness_prop
    if device.props:
        raw["props"] = [prop_to_raw(item) for item in device.props]
    return raw


def _scene_ref(scenes: list[SceneConfig], value: str, label: str) -> str:
    """id 或名字都行，落成 id。写了一个又不存在的就直接报错。"""
    for item in scenes:
        if item.id == value or (item.name and item.name == value):
            return item.id
    raise ValueError(f"{label} 指向的场景 {value!r} 不存在")


def _parse_focus(raw: Any, scenes: list[SceneConfig]) -> FocusConfig:
    if raw is None or raw is False:
        return FocusConfig()
    if not isinstance(raw, dict):
        raise ValueError("focus 必须是对象，或用 focus: false 关掉")
    on = str(raw.get("on") or "").strip()
    if not on:
        return FocusConfig()
    return FocusConfig(
        enabled=True,
        on=_scene_ref(scenes, on, "focus.on"),
        off=_scene_ref(scenes, str(raw.get("off") or "").strip(), "focus.off")
        if str(raw.get("off") or "").strip()
        else "",
    )


def scene_to_raw(scene: SceneConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": scene.id, "name": scene.name}
    if scene.on:
        raw["on"] = list(scene.on)
    if scene.off:
        raw["off"] = list(scene.off)
    if scene.brightness:
        raw["brightness"] = dict(scene.brightness)
    if scene.say:
        raw["say"] = scene.say
    if scene.phrases:
        raw["phrases"] = list(scene.phrases)
    return raw


def prop_to_raw(item: PropConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": item.id, "prop": item.prop}
    if item.name:
        raw["name"] = item.name
    if item.role != "auto":
        raw["role"] = item.role
    if not item.write:
        raw["write"] = False
    if item.unit:
        raw["unit"] = item.unit
    if item.min is not None:
        raw["min"] = item.min
    if item.max is not None:
        raw["max"] = item.max
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


def schedule_to_raw(sched: ScheduleConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": sched.id, "at": sched.at}
    if sched.weekdays:
        raw["weekdays"] = list(sched.weekdays)
    if sched.on:
        raw["on"] = list(sched.on)
    if sched.off:
        raw["off"] = list(sched.off)
    if sched.say:
        raw["say"] = sched.say
def proactive_to_raw(config: ProactiveConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "enabled": True,
        "daily_limit": config.daily_limit,
        "quiet": list(config.quiet),
    }
    if config.morning.at:
        morning: dict[str, Any] = {"at": config.morning.at}
        if config.morning.text:
            morning["text"] = config.morning.text
        raw["morning"] = morning
    if (
        config.alerts.temp_celsius <= 0
        and config.alerts.offline_minutes <= 0
        and config.alerts.sedentary_minutes <= 0
    ):
        raw["alerts"] = False
    else:
        raw["alerts"] = {
            "temp_celsius": config.alerts.temp_celsius,
            "offline_minutes": config.alerts.offline_minutes,
            "sedentary_minutes": config.alerts.sedentary_minutes,
        }
    return raw


def hub_config_to_raw(config: HubConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "name": config.name,
        "host": config.host,
        "port": config.port,
        "token": config.token,
        "devices": [device_to_raw(item) for item in config.devices],
    }
    if config.scenes:
        raw["scenes"] = [scene_to_raw(item) for item in config.scenes]
    if config.focus.enabled:
        focus_raw: dict[str, Any] = {"on": config.focus.on}
        if config.focus.off:
            focus_raw["off"] = config.focus.off
        raw["focus"] = focus_raw
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
            voice: dict[str, Any] = {}
            if config.companion.voice_volume is not None:
                voice["volume"] = int(config.companion.voice_volume)
            if abs(float(config.companion.voice_speed) - 1.0) > 0.001:
                voice["speed"] = round(float(config.companion.voice_speed), 2)
            if voice:
                companion["voice"] = voice
        raw["companion"] = companion
    if config.proactive.enabled:
        raw["proactive"] = proactive_to_raw(config.proactive)
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
    if config.limits != LimitsConfig():
        limits: dict[str, Any] = {"enabled": config.limits.enabled}
        defaults = LimitsConfig()
        for key in ("chat_per_min", "announce_per_min", "command_per_min", "login_per_min", "read_per_min"):
            value = getattr(config.limits, key)
            if value != getattr(defaults, key):
                limits[key] = value
        raw["limits"] = limits
    if config.tokens:
        raw["tokens"] = [
            {"id": item.id, "value": item.value, **({"name": item.name} if item.name else {})}
            for item in config.tokens
        ]
    probe = config.probe
    if probe.url or probe.token or probe.stale_ms != 4000 or probe.hub_url != DEFAULT_PROBE_HUB_URL or probe.listen != DEFAULT_PROBE_LISTEN:
        dumped: dict[str, Any] = {}
        if probe.url:
            dumped["url"] = probe.url
        if probe.token:
            dumped["token"] = probe.token
        if probe.stale_ms != 4000:
            dumped["stale_ms"] = probe.stale_ms
        if probe.hub_url != DEFAULT_PROBE_HUB_URL:
            dumped["hub_url"] = probe.hub_url
        if probe.listen != DEFAULT_PROBE_LISTEN:
            dumped["listen"] = probe.listen
        if dumped:
            raw["probe"] = dumped
    if config.schedules:
        raw["schedules"] = [schedule_to_raw(item) for item in config.schedules]
    return raw


def save_config_raw(raw: dict[str, Any], path: Path) -> HubConfig:
    cfg = parse_config(raw, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return cfg
