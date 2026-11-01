"""米家全量设备能力目录：把账号里每台设备能干什么写成给大脑看的清单。

参考 xiaomi-home-mcp / Home Assistant Assist 的思路——控制不该只限「绑上
书桌的那几盏灯」：账号里每台设备的 miot spec（可写属性 + 动作）都进目录，
连同值域、枚举一起注入对话提示词，由大脑用通用的
``ACTION: <id>.set.<属性>.<值>`` / ``ACTION: <id>.do.<动作>`` 落成真实调用。
执行器在 service 侧（_run_device_prop / _run_device_action），这里只管
「看 spec 说话」：构建目录、拼提示词、格式化实况读数、把一句值钳进值域。

纯函数 + spec 缓存（get_device_info 自带磁盘缓存），不持有网络状态；
API 从外面传进来，所以能离线测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from dock_hub.intents import parse_cn_int
from dock_hub.mijia_bridge import _voice_device_id

# 目录里每台设备最多列多少条，防止一台网关类设备把提示词撑爆
MAX_PROPS_PER_DEVICE = 10
MAX_ACTIONS_PER_DEVICE = 6
# 一次实况读数最多带几台设备（读多了延迟受不了）
MAX_READ_DEVICES = 4
MAX_READ_PROPS = 8


@dataclass
class CatalogProp:
    name: str
    siid: int
    piid: int
    type: str
    desc: str
    writable: bool
    readable: bool
    value_min: float | None = None
    value_max: float | None = None
    value_step: float | None = None
    value_list: list[tuple[str, str]] = field(default_factory=list)  # (值, 含义)


@dataclass
class CatalogAction:
    name: str
    siid: int
    aiid: int
    desc: str


@dataclass
class CatalogEntry:
    id: str
    name: str
    model: str
    did: str
    props: list[CatalogProp] = field(default_factory=list)
    actions: list[CatalogAction] = field(default_factory=list)

    def find_prop(self, name: str) -> CatalogProp | None:
        """按名字找属性，容忍连字符/下划线混写。"""
        wanted = (name or "").strip()
        if not wanted:
            return None
        for prop in self.props:
            if prop.name == wanted:
                return prop
        for prop in self.props:
            if prop.name.replace("-", "_") == wanted.replace("-", "_"):
                return prop
        return None

    def find_action(self, name: str) -> CatalogAction | None:
        wanted = (name or "").strip()
        for action in self.actions:
            if action.name == wanted:
                return action
        return None


def _parse_rw(rw: Any) -> tuple[bool, bool]:
    flags = str(rw or "")
    return "w" in flags, "r" in flags


def _parse_value_list(raw: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        desc = str(item.get("description") or "").strip()
        if value is None:
            continue
        items.append((str(value), desc))
    return items


def _parse_range(raw: Any) -> tuple[float | None, float | None, float | None]:
    if not isinstance(raw, list) or len(raw) < 2:
        return None, None, None
    def _num(x: Any) -> float | None:
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    lo = _num(raw[0])
    hi = _num(raw[1])
    step = _num(raw[2]) if len(raw) > 2 else None
    return lo, hi, step


def _spec_entries(model: str, api: Any) -> dict[str, Any]:
    """拉一台设备的 spec。get_device_info 自带磁盘缓存，重复构建很便宜。"""
    from mijiaAPI import get_device_info
    from mijiaAPI.errors import GetDeviceInfoError

    try:
        return get_device_info(model, cache_path=_cache_dir(api))
    except GetDeviceInfoError:
        return {}


def _cache_dir(api: Any) -> Any:
    return getattr(getattr(api, "auth_data_path", None), "parent", None)


def build_catalog(
    api: Any,
    *,
    name_to_id: dict[str, str] | None = None,
) -> list[CatalogEntry]:
    """账号里每台设备都进目录。书桌上已绑的设备沿用配置里的 id，
    其余的起 m-xxx 的语音 id——两种 id 执行器都认。"""
    entries: list[CatalogEntry] = []
    used: set[str] = set()
    try:
        listed = api.get_devices_list()
    except Exception:  # noqa: BLE001 - 米家没登录等情况直接空目录
        return entries
    for item in listed:
        name = str(item.get("name") or "").strip()
        model = item.get("model")
        did = str(item.get("did") or "").strip()
        if not name or not did or not isinstance(model, str) or not model:
            continue
        ident = (name_to_id or {}).get(name) or _voice_device_id(did, used)
        used.add(ident)
        entry = CatalogEntry(id=ident, name=name, model=model, did=did)
        info = _spec_entries(model, api)
        for prop in info.get("properties") or []:
            pname = str(prop.get("name") or "").strip()
            if not pname:
                continue
            writable, readable = _parse_rw(prop.get("rw"))
            lo, hi, step = _parse_range(prop.get("range"))
            entry.props.append(
                CatalogProp(
                    name=pname,
                    siid=int(prop.get("method", {}).get("siid", 0)),
                    piid=int(prop.get("method", {}).get("piid", 0)),
                    type=str(prop.get("type") or "string"),
                    desc=str(prop.get("description") or "").strip(),
                    writable=writable,
                    readable=readable,
                    value_min=lo,
                    value_max=hi,
                    value_step=step,
                    value_list=_parse_value_list(prop.get("value-list")),
                )
            )
        for action in info.get("actions") or []:
            aname = str(action.get("name") or "").strip()
            if not aname:
                continue
            entry.actions.append(
                CatalogAction(
                    name=aname,
                    siid=int(action.get("method", {}).get("siid", 0)),
                    aiid=int(action.get("method", {}).get("aiid", 0)),
                    desc=str(action.get("description") or "").strip(),
                )
            )
        entries.append(entry)
    return entries


def _clean_desc(desc: str) -> str:
    """spec 的描述常是「Switch Status / 开关」这种英前中后——取带中文的那段。"""
    text = (desc or "").strip()
    if not text:
        return ""
    if "/" in text:
        parts = [p.strip() for p in text.split("/") if p.strip()]
        chinese = [p for p in parts if re.search(r"[\u4e00-\u9fff]", p)]
        if chinese:
            text = chinese[-1]
    return text[:16]


def _prop_line(prop: CatalogProp) -> str | None:
    label = _clean_desc(prop.desc) or prop.name
    bits: list[str] = []
    if prop.writable:
        if prop.type == "bool":
            bits.append("开/关")
        elif prop.value_list:
            bits.append("枚举 " + "/".join(f"{v}={d or '?'}" for v, d in prop.value_list[:8]))
        elif prop.value_min is not None and prop.value_max is not None:
            lo = int(prop.value_min) if float(prop.value_min).is_integer() else prop.value_min
            hi = int(prop.value_max) if float(prop.value_max).is_integer() else prop.value_max
            bits.append(f"{lo}–{hi}")
    elif not prop.readable:
        return None
    text = f"{label}（{prop.name}）"
    if bits:
        text += " " + "；".join(bits)
    return text


def _prompt_sort_key(prop: CatalogProp) -> tuple[int, int]:
    """能写的排前面，其中开/关再排最前——大脑最常写的就是这些。"""
    if prop.writable:
        return (0, 0 if prop.type == "bool" else 1)
    return (1, 0)


def catalog_prompt(entries: list[CatalogEntry]) -> str:
    """目录 prompt 块。没有任何可控设备就返回空串，别浪费 token。"""
    lines: list[str] = []
    for entry in entries:
        props = sorted(
            (p for p in entry.props if p.writable or p.readable),
            key=_prompt_sort_key,
        )[:MAX_PROPS_PER_DEVICE]
        # 只读属性若描述里没中文（纯调试字段），丢出 prompt
        kept: list[str] = []
        for prop in props:
            text = _prop_line(prop)
            if text is None:
                continue
            if not prop.writable and not re.search(r"[\u4e00-\u9fff]", _clean_desc(prop.desc) or ""):
                continue
            kept.append(text)
        actions = [
            f"{_clean_desc(a.desc) or a.name}（{a.name}）"
            for a in entry.actions[:MAX_ACTIONS_PER_DEVICE]
        ]
        head = f"- {entry.name}（{entry.id}）"
        segs = kept + ([("动作: " + "、".join(actions))] if actions else [])
        if not segs:
            continue
        lines.append(head + "：" + "；".join(segs))
    if not lines:
        return ""
    doc = [
        "家里米家设备的完整能力清单（能读能写都在这，别再说做不到）：",
        *lines,
        "写属性：ACTION: <id>.set.<属性名>.<值>。bool 用 true/false；枚举用编号；数值在范围内。",
        "执行动作：ACTION: <id>.do.<动作名>。",
        "对方问某台设备的状态时（开了没/多少度/湿度/电量），用「设备实况」里的读数回答。",
        "清单里的设备同样短应一声再写 ACTION 行；闲聊不要写 ACTION。",
    ]
    return "\n".join(doc)


def entries_mentioned(entries: list[CatalogEntry], text: str) -> list[CatalogEntry]:
    """原话里点名的设备。名字 >=2 字才算命中，别让「一」这种单字瞎匹配。"""
    stripped = (text or "").strip()
    if not stripped:
        return []
    lowered = stripped.lower()
    hits: list[CatalogEntry] = []
    for entry in entries:
        needle = entry.name.strip()
        if len(needle) >= 2 and needle in stripped:
            hits.append(entry)
            continue
        if len(entry.id) >= 3 and entry.id.lower() in lowered:
            hits.append(entry)
    return hits


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "开" if value else "关"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def reading_lines(entry: CatalogEntry, rows: list[dict[str, Any]]) -> str:
    """把一次批量读的结果格式化成一行实况。读失败/离线的属性略过。"""
    by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            key = (int(row.get("siid")), int(row.get("piid")))
        except (TypeError, ValueError):
            continue
        by_key[key] = row
    bits: list[str] = []
    for prop in entry.props:
        if not prop.readable:
            continue
        row = by_key.get((prop.siid, prop.piid))
        if row is None or int(row.get("code", -1)) != 0:
            continue
        label = prop.desc or prop.name
        bits.append(f"{label} {_format_value(row.get('value'))}")
    if not bits:
        return ""
    return f"{entry.name}：" + "，".join(bits)


def read_plan(entries: list[CatalogEntry]) -> list[tuple[CatalogEntry, list[dict[str, Any]]]]:
    """实况读数的批量参数：每台挑前 MAX_READ_PROPS 个可读属性。"""
    plan: list[tuple[CatalogEntry, list[dict[str, Any]]]] = []
    for entry in entries[:MAX_READ_DEVICES]:
        params = [
            {"did": entry.did, "siid": prop.siid, "piid": prop.piid}
            for prop in entry.props
            if prop.readable
        ][:MAX_READ_PROPS]
        if params:
            plan.append((entry, params))
    return plan


def coerce_catalog_value(prop: CatalogProp, raw: Any) -> Any:
    """把模型给的值钳进 spec 值域。非法就抛 ValueError，让执行器报「不支持」。"""
    if prop.type == "bool":
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in {"true", "1", "on", "开", "打开"}:
                return True
            if lowered in {"false", "0", "off", "关", "关闭"}:
                return False
            raise ValueError(f"{prop.name} 要 true 或 false")
        return bool(raw)
    if prop.type in {"int", "uint"}:
        if isinstance(raw, bool):
            raise ValueError(f"{prop.name} 要数字")
        value = int(round(float(str(raw).strip())))
        return _clamp(prop, value)
    if prop.type == "float":
        if isinstance(raw, bool):
            raise ValueError(f"{prop.name} 要数字")
        value = float(str(raw).strip())
        return _clamp(prop, value)
    text = str(raw).strip()
    if not text:
        raise ValueError(f"{prop.name} 要字符串")
    return text


def _clamp(prop: CatalogProp, value: float) -> float:
    if prop.value_min is not None:
        value = max(prop.value_min, value)
    if prop.value_max is not None:
        value = min(prop.value_max, value)
    return value


def value_list_desc(prop: CatalogProp, value: Any) -> str:
    """枚举值 → 中文含义。没有就空串。"""
    for item_value, desc in prop.value_list:
        if str(item_value) == str(value):
            return desc or ""
    return ""


# ---- 常规口令的确定性兜底 --------------------------------------------------
# 「空调二十四度」「空调制热」「风速调大」这类高频指令不该赌模型自觉写
# ACTION 行（Home Assistant Assist 也是这么分层的：简单口令确定性执行，
# 复杂的才交给模型）。这里按目录数据匹配，猜不出就交回对话。

_CAT_TEMP = re.compile(r"(?:调到|调成|设成|设为|设定|调至|开到)?\s*([0-9]{1,2}|[零〇一二两三四五六七八九十]+)\s*度")
_TEMP_PROP_NAMES = ("target-temperature", "target_temperature", "temperature")


def _temp_props(entry: CatalogEntry) -> list[CatalogProp]:
    return [
        prop
        for prop in entry.props
        if prop.writable
        and (
            prop.name in _TEMP_PROP_NAMES
            or _clean_desc(prop.desc) in {"目标温度", "温度设定", "设定温度"}
        )
    ]


def _entry_in_text(entry: CatalogEntry, text: str) -> bool:
    return len(entry.name) >= 2 and entry.name in text


def infer_catalog_set(
    text: str,
    entries: list[CatalogEntry],
) -> list[tuple[CatalogEntry, CatalogProp, Any]]:
    """原话 → [(设备, 属性, 值)]。认不出返回空表。

    现在管两类：温度（N 度）与枚举词（制冷/制热、低中高风速这类，直接拿
    value-list 的中文描述对原话）。设备点名了就只动那台；没点名但唯一命中
    也算——家里通常就一台空调。
    """
    stripped = (text or "").strip()
    if not stripped:
        return []
    out: list[tuple[CatalogEntry, CatalogProp, Any]] = []
    # 温度
    if "亮度" not in stripped and "色温" not in stripped:
        match = _CAT_TEMP.search(stripped)
        if match:
            value = parse_cn_int(match.group(1))
            if value is not None:
                candidates = [
                    (entry, prop)
                    for entry in entries
                    for prop in _temp_props(entry)
                ]
                named = [
                    (entry, prop)
                    for entry, prop in candidates
                    if _entry_in_text(entry, stripped)
                    or ("空调" in stripped and "空调" in entry.name)
                ]
                picked = named or (candidates if len(candidates) == 1 else [])
                for entry, prop in picked[:1]:
                    out.append((entry, prop, value))
    # 枚举词：拿 value-list 的中文描述对原话
    for entry in entries:
        if not _entry_in_text(entry, stripped) and len(entries) > 1:
            continue
        for prop in entry.props:
            if not prop.writable or not prop.value_list:
                continue
            if prop in [p for e, p, _v in out]:
                continue
            for value, desc in prop.value_list:
                if desc and len(desc) <= 4 and desc in stripped:
                    out.append((entry, prop, value))
                    break
    return out
