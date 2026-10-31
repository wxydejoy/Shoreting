"""功能开关：把 hub.yaml 里那些「只能手写」的段落收成一份清单。

配置向导（/setup）只管设备、大模型和音量；F7 之后的场景、主动开口、访客模式、
日落联动这些段落一直没有界面，想改就去翻 yaml。这里给每个开关定义四件事：
它叫什么、对应哪个 yaml 路径、值是什么类型、写错会怎样。

写回 yaml 是**按行改**，不是 safe_dump 整篇重写——手写的注释和段落顺序是
hub.yaml 的一部分，改一个布尔不该把它们全洗掉。改完立刻用 parse_config 重新
解析一遍，读回来对不上就不写盘。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from dock_hub.config import HubConfig, default_config_path, parse_clock, parse_config

_TOP_KEY = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):(?P<rest>.*)$")


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    blurb: str = ""


@dataclass(frozen=True)
class Feature:
    key: str
    group: str
    label: str
    path: tuple[str, ...]
    get: Callable[[HubConfig], Any]
    kind: str = "bool"  # bool | int | number | time | text | device | scene | quiet
    hint: str = ""
    low: float | None = None
    high: float | None = None
    step: float | None = None
    unit: str = ""
    default: Any = None
    # device：允许挑哪几类设备；留空 = 所有能开关的
    types: tuple[str, ...] = ()
    # 值清空时删掉这一段，而不是写一个空串
    empty_removes: bool = False
    # 时刻类：留空是「关着」，不算写错
    optional: bool = False
    clear_path: tuple[str, ...] | None = None


GROUPS: tuple[Group, ...] = (
    Group("talk", "主动开口", "她什么时候可以不等人喊就开口。总闸关着时，她只在你喊「小岸」时说话。"),
    Group("routine", "作息", "起床、天黑、睡前——按时间自己动，不用每次说一句。"),
    Group("home", "环境与灯", "灯带跟着桌面在干嘛走。"),
    Group("memory", "记录与隐私", "她记得什么、给谁看。"),
    Group("face", "脸", "屏幕长什么样。"),
    Group("safety", "安全", "限流：token 泄露或脚本刷接口时唯一还生效的止损点。"),
)


FEATURES: tuple[Feature, ...] = (
    # ------------------------------------------------------------ 主动开口
    Feature(
        "proactive.enabled",
        "talk",
        "主动开口",
        ("proactive", "enabled"),
        lambda cfg: cfg.proactive.enabled,
        hint="总闸。关掉以后早报、提醒、问候全停，她只回应你。",
        default=False,
    ),
    Feature(
        "proactive.daily_limit",
        "talk",
        "每天最多说几句",
        ("proactive", "daily_limit"),
        lambda cfg: cfg.proactive.daily_limit,
        kind="int",
        hint="超过配额的活动直接丢弃，不补发到第二天。",
        low=0,
        high=12,
        step=1,
        unit="句",
        default=6,
    ),
    Feature(
        "proactive.quiet",
        "talk",
        "免打扰时段",
        ("proactive", "quiet"),
        lambda cfg: list(cfg.proactive.quiet),
        kind="quiet",
        hint="这段时间她一句都不主动说；被闸拦下的那天配额不算用掉。",
        default=["23:00", "07:00"],
    ),
    Feature(
        "proactive.morning.at",
        "talk",
        "早间简报",
        ("proactive", "morning", "at"),
        lambda cfg: cfg.proactive.morning.at,
        kind="time",
        optional=True,
        hint="到点说一句天气、室温；留空 = 不播报。",
        default="",
    ),
    Feature(
        "proactive.weekly_at",
        "talk",
        "周日晚周报",
        ("proactive", "weekly_at"),
        lambda cfg: cfg.proactive.weekly_at,
        kind="time",
        optional=True,
        hint="把这一周在桌、朗读、最晚、说了几句话念一句；留空 = 不念。",
        default="",
    ),
    Feature(
        "proactive.alerts.weather",
        "talk",
        "天气突变提醒",
        ("proactive", "alerts", "weather"),
        lambda cfg: cfg.proactive.alerts.weather,
        hint="未来两小时有雨、骤降、大风，只在 07:00–21:00 看。",
        default=True,
    ),
    Feature(
        "proactive.alerts.temp_celsius",
        "talk",
        "电脑过热提醒",
        ("proactive", "alerts", "temp_celsius"),
        lambda cfg: cfg.proactive.alerts.temp_celsius,
        kind="int",
        hint="热满 3 分钟才说；0 = 不提。",
        low=0,
        high=120,
        step=1,
        unit="°C",
        default=85,
    ),
    Feature(
        "proactive.alerts.memory_percent",
        "talk",
        "内存爆了提醒",
        ("proactive", "alerts", "memory_percent"),
        lambda cfg: cfg.proactive.alerts.memory_percent,
        kind="int",
        hint="占用超这个百分比、且满 3 分钟才说；0 = 不提。",
        low=0,
        high=100,
        step=1,
        unit="%",
        default=90,
    ),
    Feature(
        "proactive.alerts.sedentary_minutes",
        "talk",
        "久坐提醒",
        ("proactive", "alerts", "sedentary_minutes"),
        lambda cfg: cfg.proactive.alerts.sedentary_minutes,
        kind="int",
        hint="连续用电脑多久提醒起来走走；键鼠空闲 5 分钟算休息过；0 = 不提。",
        low=0,
        high=480,
        step=5,
        unit="分钟",
        default=90,
    ),
    Feature(
        "proactive.alerts.offline_minutes",
        "talk",
        "设备离线提醒",
        ("proactive", "alerts", "offline_minutes"),
        lambda cfg: cfg.proactive.alerts.offline_minutes,
        kind="int",
        hint="米家设备掉线多久提醒；0 = 不提。",
        low=0,
        high=1440,
        step=1,
        unit="分钟",
        default=10,
    ),
    Feature(
        "proactive.alerts.air_pm25",
        "talk",
        "PM2.5 超标提醒",
        ("proactive", "alerts", "air_pm25"),
        lambda cfg: cfg.proactive.alerts.air_pm25,
        kind="int",
        hint="由好变坏那一下说一句「别开窗」，一直坏就闭嘴；0 = 不提。",
        low=0,
        high=500,
        step=1,
        unit="μg/m³",
        default=75,
    ),
    Feature(
        "proactive.presence.home_text",
        "talk",
        "回家问候语",
        ("proactive", "presence", "home_text"),
        lambda cfg: cfg.proactive.presence.home_text,
        kind="text",
        hint="手机连上家里 WiFi 的那一刻说；留空 = 不说。",
        default="回来了。",
    ),
    Feature(
        "proactive.presence.desk_text",
        "talk",
        "回座问候语",
        ("proactive", "presence", "desk_text"),
        lambda cfg: cfg.proactive.presence.desk_text,
        kind="text",
        hint="离座满 5 分钟再回来才说；留空 = 不说。",
        default="回来了。",
    ),
    # ---------------------------------------------------------------- 作息
    Feature(
        "alarm.at",
        "routine",
        "起床闹钟",
        ("alarm", "at"),
        lambda cfg: cfg.alarm.at,
        kind="time",
        optional=True,
        hint="到点屏幕渐亮、灯带爬亮，爬完轻声说一句；留空 = 关着。",
        default="",
    ),
    Feature(
        "alarm.weekdays_only",
        "routine",
        "只在工作日叫",
        ("alarm", "weekdays_only"),
        lambda cfg: cfg.alarm.weekdays_only,
        hint="周末不叫，但灯和屏幕照常不动。",
        default=True,
    ),
    Feature(
        "alarm.device",
        "routine",
        "闹钟亮哪盏灯",
        ("alarm", "device"),
        lambda cfg: cfg.alarm.device,
        kind="device",
        hint="不选 = 只亮屏幕不亮灯。",
        types=("light", "ws2812"),
        default="",
    ),
    Feature(
        "alarm.brightness",
        "routine",
        "闹钟亮度",
        ("alarm", "brightness"),
        lambda cfg: cfg.alarm.brightness,
        kind="int",
        low=1,
        high=100,
        step=1,
        unit="%",
        default=30,
    ),
    Feature(
        "alarm.ramp_min",
        "routine",
        "爬亮用几分钟",
        ("alarm", "ramp_min"),
        lambda cfg: cfg.alarm.ramp_min,
        kind="int",
        low=1,
        high=120,
        step=1,
        unit="分钟",
        default=15,
    ),
    Feature(
        "dusk.device",
        "routine",
        "日落自动开灯",
        ("dusk", "device"),
        lambda cfg: cfg.dusk.device,
        kind="device",
        hint="日落后把灯带慢慢推到这个亮度；不选 = 关着。",
        types=("light", "ws2812"),
        default="",
    ),
    Feature(
        "dusk.offset_min",
        "routine",
        "日落后几分钟开始",
        ("dusk", "offset_min"),
        lambda cfg: cfg.dusk.offset_min,
        kind="int",
        low=0,
        high=240,
        step=5,
        unit="分钟",
        default=30,
    ),
    Feature(
        "dusk.until",
        "routine",
        "推到几点为止",
        ("dusk", "until"),
        lambda cfg: cfg.dusk.until,
        kind="time",
        hint="过了这个点就不再推，交给睡前场景接着降。",
        default="22:00",
    ),
    Feature(
        "dusk.brightness",
        "routine",
        "日落亮度",
        ("dusk", "brightness"),
        lambda cfg: cfg.dusk.brightness,
        kind="int",
        low=1,
        high=100,
        step=1,
        unit="%",
        default=40,
    ),
    Feature(
        "dusk.ramp_min",
        "routine",
        "用几分钟推上去",
        ("dusk", "ramp_min"),
        lambda cfg: cfg.dusk.ramp_min,
        kind="int",
        low=1,
        high=240,
        step=5,
        unit="分钟",
        default=45,
    ),
    Feature(
        "focus.on",
        "routine",
        "游戏开始切场景",
        ("focus", "on"),
        lambda cfg: cfg.focus.on,
        kind="scene",
        hint="启动项里的程序一跑起来就切；不选 = 关着。",
        default="",
    ),
    Feature(
        "focus.off",
        "routine",
        "退出游戏切回",
        ("focus", "off"),
        lambda cfg: cfg.focus.off,
        kind="scene",
        hint="不选 = 退出后不恢复。",
        default="",
    ),
    Feature(
        "focus.mute",
        "routine",
        "游戏时静音",
        ("focus", "mute"),
        lambda cfg: cfg.focus.mute,
        hint="进游戏把 Hub 音量收到 0，退出来恢复。",
        default=True,
    ),
    # ------------------------------------------------------------ 环境与灯
    Feature(
        "ambience.device",
        "home",
        "灯光氛围",
        ("ambience", "device"),
        lambda cfg: cfg.ambience.device,
        kind="device",
        hint="番茄钟抬到专注档、睡前朗读压到睡前档；不选 = 关着。",
        types=("light", "ws2812"),
        default="",
    ),
    Feature(
        "ambience.timer_brightness",
        "home",
        "专注档亮度",
        ("ambience", "timer_brightness"),
        lambda cfg: cfg.ambience.timer_brightness,
        kind="int",
        low=1,
        high=100,
        step=1,
        unit="%",
        default=60,
    ),
    Feature(
        "ambience.reading_brightness",
        "home",
        "睡前档亮度",
        ("ambience", "reading_brightness"),
        lambda cfg: cfg.ambience.reading_brightness,
        kind="int",
        low=1,
        high=100,
        step=1,
        unit="%",
        default=15,
    ),
    # -------------------------------------------------------- 记录与隐私
    Feature(
        "guest.enabled",
        "memory",
        "访客模式",
        ("guest",),
        lambda cfg: cfg.guest.enabled,
        hint="说一句「有客人」，聊天、日记、周报、记忆连页带口一起藏起来。",
        default=True,
    ),
    Feature(
        "guest.hours",
        "memory",
        "访客模式自动恢复",
        ("guest", "hours"),
        lambda cfg: cfg.guest.hours,
        kind="number",
        hint="忘了说「客人走了」，到点自己恢复。",
        low=0.5,
        high=48,
        step=0.5,
        unit="小时",
        default=6,
    ),
    Feature(
        "diary.enabled",
        "memory",
        "守岸人日记",
        ("diary",),
        lambda cfg: cfg.diary.enabled,
        hint="每天拿台账和当天的话写一段，/diary 页可回看可删。",
        default=True,
    ),
    Feature(
        "diary.at",
        "memory",
        "日记几点写",
        ("diary", "at"),
        lambda cfg: cfg.diary.at,
        kind="time",
        default="21:30",
    ),
    Feature(
        "photos",
        "memory",
        "回忆相框目录",
        ("photos",),
        lambda cfg: (
            "false"
            if cfg.photos_off
            else ("" if cfg.photos is None else str(cfg.photos))
        ),
        kind="text",
        hint="留空 = 自动用 hub.yaml 旁边的 photos/；填 false 关掉，填路径换地方。",
        empty_removes=True,
        default="",
    ),
    Feature(
        "calendar.url",
        "memory",
        "日程 ICS 链接",
        ("calendar", "url"),
        lambda cfg: cfg.calendar.url if cfg.calendar else "",
        kind="text",
        hint="iCloud / Google 日历的「秘密地址」，webcal:// 也认；留空 = 不知道日程。",
        empty_removes=True,
        clear_path=("calendar",),
        default="",
    ),
    # ------------------------------------------------------------------ 脸
    Feature(
        "wallpaper.enabled",
        "face",
        "一句话壁纸",
        ("wallpaper",),
        lambda cfg: cfg.wallpaper.enabled,
        hint="屏幕底边飘一行她今天想说的话，一天一句。",
        default=True,
    ),
    Feature(
        "theme.birthday",
        "face",
        "生日",
        ("theme", "birthday"),
        lambda cfg: cfg.theme.birthday,
        kind="text",
        hint='写成 "MM-DD"。当天她说一句生日快乐，屏幕换蜜糖色；留空 = 不提。',
        default="",
    ),
    # ---------------------------------------------------------------- 安全
    Feature(
        "limits.enabled",
        "safety",
        "限流",
        ("limits", "enabled"),
        lambda cfg: cfg.limits.enabled,
        hint="按来源 IP + 端点各一个令牌桶；关掉就等于把门敞开。",
        default=True,
    ),
    Feature(
        "limits.chat_per_min",
        "safety",
        "对话 每分钟",
        ("limits", "chat_per_min"),
        lambda cfg: cfg.limits.chat_per_min,
        kind="int",
        low=0,
        high=600,
        step=1,
        unit="次",
        hint="一次 chat 是几千 token；0 = 这一档不拦。",
        default=20,
    ),
    Feature(
        "limits.announce_per_min",
        "safety",
        "主动播报 每分钟",
        ("limits", "announce_per_min"),
        lambda cfg: cfg.limits.announce_per_min,
        kind="int",
        low=0,
        high=600,
        step=1,
        unit="次",
        default=10,
    ),
    Feature(
        "limits.command_per_min",
        "safety",
        "设备控制 每分钟",
        ("limits", "command_per_min"),
        lambda cfg: cfg.limits.command_per_min,
        kind="int",
        low=0,
        high=600,
        step=1,
        unit="次",
        default=60,
    ),
    Feature(
        "limits.read_per_min",
        "safety",
        "朗读 每分钟",
        ("limits", "read_per_min"),
        lambda cfg: cfg.limits.read_per_min,
        kind="int",
        low=0,
        high=600,
        step=1,
        unit="次",
        hint="每个任务都要合成音频，比开关贵得多。",
        default=20,
    ),
    Feature(
        "limits.login_per_min",
        "safety",
        "登录尝试 每分钟",
        ("limits", "login_per_min"),
        lambda cfg: cfg.limits.login_per_min,
        kind="int",
        low=0,
        high=600,
        step=1,
        unit="次",
        default=10,
    ),
    Feature(
        "limits.chat_lanes",
        "safety",
        "同时进行的对话",
        ("limits", "chat_lanes"),
        lambda cfg: cfg.limits.chat_lanes,
        kind="int",
        low=0,
        high=16,
        step=1,
        unit="个",
        hint="满了回 503 让手机稍后再试；0 = 不限。",
        default=2,
    ),
    Feature(
        "limits.read_lanes",
        "safety",
        "同时进行的朗读",
        ("limits", "read_lanes"),
        lambda cfg: cfg.limits.read_lanes,
        kind="int",
        low=0,
        high=16,
        step=1,
        unit="个",
        default=2,
    ),
)

BY_KEY: dict[str, Feature] = {item.key: item for item in FEATURES}


# ------------------------------------------------------------------ 读


def _plain(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    return value


def describe(config: HubConfig) -> dict[str, Any]:
    """给控制台的清单：分组 + 每个开关的当前值、范围和选项。"""
    return {
        "groups": [
            {"id": item.id, "title": item.title, "blurb": item.blurb} for item in GROUPS
        ],
        "features": [_describe_feature(item, config) for item in FEATURES],
        "config_path": str(config.path or default_config_path()),
    }


def _describe_feature(feature: Feature, config: HubConfig) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "key": feature.key,
        "group": feature.group,
        "label": feature.label,
        "hint": feature.hint,
        "kind": feature.kind,
        "value": _plain(feature.get(config)),
        "default": feature.default,
        "unit": feature.unit,
        "optional": feature.optional,
    }
    if feature.kind in {"int", "number"}:
        entry["low"] = feature.low
        entry["high"] = feature.high
        entry["step"] = feature.step or 1
    if feature.kind in {"device", "scene"}:
        entry["options"] = _options(feature, config)
    return entry


def _options(feature: Feature, config: HubConfig) -> list[dict[str, str]]:
    if feature.kind == "scene":
        picked = [{"value": "", "label": "不切（关着）"}]
        picked += [{"value": item.id, "label": item.name} for item in config.scenes]
        return picked
    allowed = feature.types
    picked = [{"value": "", "label": "不选（关着）"}]
    for item in config.devices:
        if not item.is_switchable:
            continue
        if allowed and item.type not in allowed:
            continue
        picked.append({"value": item.id, "label": f"{item.name}（{item.id}）"})
    return picked


# ------------------------------------------------------------------ 写


def coerce(feature: Feature, value: Any, config: HubConfig) -> Any:
    """把浏览器送来的一坨值收成配置里该有的样子；收不了就报人话。"""
    kind = feature.kind
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "on", "1", "yes"}:
            return True
        if text in {"false", "off", "0", "no", ""}:
            return False
        raise ValueError(f"{feature.label} 只能是开或关")

    if kind in {"int", "number"}:
        raw = str(value).strip()
        if not raw:
            raise ValueError(f"{feature.label} 不能留空")
        try:
            number = int(raw) if kind == "int" else round(float(raw), 2)
        except ValueError as exc:
            raise ValueError(f"{feature.label} 要填数字") from exc
        low, high = feature.low, feature.high
        if low is not None and number < low or high is not None and number > high:
            raise ValueError(f"{feature.label} 要在 {_num(low)}–{_num(high)} 之间")
        return number

    if kind == "time":
        text = str(value or "").strip()
        if not text:
            if feature.optional:
                return ""
            raise ValueError(f"{feature.label} 要写成 07:30 这种")
        return parse_clock(text, feature.label)

    if kind == "text":
        text = str(value if value is not None else "").strip()
        if not text and feature.empty_removes:
            return None
        if feature.key == "theme.birthday" and text:
            parts = text.split("-")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError('生日要写成 "05-21" 这种')
            month, day = int(parts[0]), int(parts[1])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                raise ValueError('生日要写成 "05-21" 这种')
            return f"{month:02d}-{day:02d}"
        if feature.key == "photos" and text == "false":
            return False
        if len(text) > 400:
            raise ValueError(f"{feature.label} 太长了")
        return text

    if kind in {"device", "scene"}:
        text = str(value or "").strip()
        if not text:
            return ""
        pool = config.scenes if kind == "scene" else config.devices
        if text not in {item.id for item in pool}:
            raise ValueError(f"{feature.label}：没有 {text} 这个{'场景' if kind == 'scene' else '设备'}")
        return text

    if kind == "quiet":
        if isinstance(value, str):
            parts = [part.strip() for part in value.split("-") if part.strip()]
        else:
            parts = [str(part).strip() for part in (value or [])]
        if len(parts) != 2 or not all(parts):
            raise ValueError("免打扰时段要两个时刻：开始和结束")
        return [parse_clock(parts[0], "免打扰开始"), parse_clock(parts[1], "免打扰结束")]

    raise ValueError(f"不认识的开关类型：{kind}")


def _num(value: float | None) -> str:
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else str(value)


def _matches(feature: Feature, got: Any, coerced: Any) -> bool:
    """读回来的跟写进去的对不对得上。

    配置里好几种「空」是等价的（没配 / 空串 / false），不能死比。
    """
    if coerced is None:
        return got in ("", None)
    if coerced is False and feature.kind == "text":
        return got in ("", "false", False)
    return got == coerced


def apply_feature(hub: Any, key: str, value: Any) -> dict[str, Any]:
    """改一个开关：改 yaml 文本 → 重新解析 → 对不上就不写盘 → 热加载。"""
    feature = BY_KEY.get(str(key))
    if feature is None:
        raise ValueError(f"没有这个开关：{key}")
    config = hub.config
    coerced = coerce(feature, value, config)
    path = Path(config.path or default_config_path())
    if not path.is_file():
        raise ValueError(f"找不到 {path}")
    original = path.read_text(encoding="utf-8")

    if coerced is None:
        new_text = remove_path(original, feature.clear_path or feature.path)
    else:
        new_text = patch_text(original, feature.path, coerced)

    try:
        raw = yaml.safe_load(new_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"改完不是合法 YAML，没写盘：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("改完不是 YAML 对象，没写盘")
    fresh = parse_config(raw, path)
    got = _plain(feature.get(fresh))
    if not _matches(feature, got, coerced):
        raise ValueError(f"改完读回来是 {got!r}，不是 {_plain(coerced)!r}，没写盘")

    if new_text != original:
        _write_atomic(path, new_text)
    hub.reload_config(fresh)
    return {"ok": True, "key": feature.key, "value": got, "message": "已生效，不用重启"}


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------ yaml 按行改


def format_value(value: Any) -> str:
    """写成 yaml 标量。字符串一律带引号——07:20 这种不引会被 YAML 1.1 当六十进制。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # 6.0 这种写成 6，别让人看见 yaml 里凭空多个小数点
        if float(value).is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def patch_text(text: str, path: tuple[str, ...], value: Any) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    _set(lines, tuple(path), format_value(value), 0, 0, len(lines))
    body = newline.join(lines)
    if text.endswith(("\n", "\r\n")):
        body += newline
    return body


def remove_path(text: str, path: tuple[str, ...]) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    _remove(lines, tuple(path), 0, 0, len(lines))
    body = newline.join(lines)
    if text.endswith(("\n", "\r\n")):
        body += newline
    return body


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _match_key(line: str, indent: int) -> re.Match[str] | None:
    if _indent(line) != indent:
        return None
    return _TOP_KEY.match(line[indent:])


def _block_end(lines: list[str], start: int, indent: int) -> int:
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if _is_content(line) and _indent(line) <= indent:
            break
        index += 1
    return index


def _find(lines: list[str], key: str, indent: int, lo: int, hi: int) -> int | None:
    for index in range(lo, min(hi, len(lines))):
        match = _match_key(lines[index], indent)
        if match and match.group("key") == key:
            return index
    return None


def _last_content(lines: list[str], lo: int, hi: int) -> int:
    """块里最后一行的位置（跳过尾部空行，插入点放它后面）。"""
    index = min(hi, len(lines)) - 1
    while index >= lo and not lines[index].strip():
        index -= 1
    return index


def _split_comment(rest: str) -> tuple[str, str]:
    """把 `false  # 关掉` 拆成值和解说，改值的时候别把解说丢了。"""
    quote: str | None = None
    for index, char in enumerate(rest):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "#" and index > 0 and rest[index - 1] in " \t":
            return rest[:index].rstrip(), rest[index - 1 :]
    return rest.rstrip(), ""


def _nested(path: tuple[str, ...], formatted: str, indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}{path[0]}:" + ("" if len(path) > 1 else f" {formatted}")]
    if len(path) > 1:
        lines += _nested(path[1:], formatted, indent + 2)
    return lines


def _set(
    lines: list[str],
    path: tuple[str, ...],
    formatted: str,
    indent: int,
    lo: int,
    hi: int,
) -> None:
    key = path[0]
    index = _find(lines, key, indent, lo, hi)
    if index is None:
        at = _last_content(lines, lo, hi) + 1
        lines[at:at] = _nested(path, formatted, indent)
        return

    match = _match_key(lines[index], indent)
    assert match is not None
    rest = match.group("rest").strip()
    if len(path) == 1:
        _, comment = _split_comment(rest)
        end = _block_end(lines, index, indent)
        # 本来是块（diary:\n  at: ...），改成一个标量就把 children 一起收掉
        tail = end if not rest or rest.startswith("#") else index + 1
        while tail > index + 1 and not lines[tail - 1].strip():
            tail -= 1
        lines[index:tail] = [f"{' ' * indent}{key}: {formatted}{comment}"]
        return

    end = _block_end(lines, index, indent)
    if rest and not rest.startswith("#"):
        if rest.startswith(("{", "[")) or rest.startswith("-"):
            raise ValueError(f"{key} 这一行写成了行内形式，先改成多行再改")
        # `guest: false` 这种标量要展开成块，才能往里写 children
        _, comment = _split_comment(rest)
        lines[index : index + 1] = [f"{' ' * indent}{key}:{comment}"] + _nested(
            path[1:], formatted, indent + 2
        )
        return

    child_indent = indent + 2
    for line in lines[index + 1 : end]:
        if _is_content(line):
            child_indent = _indent(line)
            break
    _set(lines, path[1:], formatted, child_indent, index + 1, end)


def _remove(
    lines: list[str],
    path: tuple[str, ...],
    indent: int,
    lo: int,
    hi: int,
) -> None:
    index = _find(lines, path[0], indent, lo, hi)
    if index is None:
        return
    if len(path) == 1:
        end = _block_end(lines, index, indent)
        while end > index + 1 and not lines[end - 1].strip():
            end -= 1
        del lines[index:end]
        return
    end = _block_end(lines, index, indent)
    _remove(lines, path[1:], indent + 2, index + 1, end)
