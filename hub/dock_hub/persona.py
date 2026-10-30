"""守岸人怎么说话、怎么记事：人设之外的那一半。

和 intents.py 分工：那边把用户的话拆成「要动哪个设备」，这边决定「这句话
该怎么说出来」——几点、多少度、在放什么歌，以及她自己的情绪要不要收着点。

这里同样是纯函数加一层磁盘读写（mood.yaml），不碰网络、不碰 TTS 队列，所以
可以单独喂一个假的状态进来验文案，不用起 Hub。

搬的是位置不是设计：函数体一行没改，companion.py 会把名字原样再导出。
"""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from dock_hub.intents import skill_nicknames

# 情绪标签：对外是英文 key，落盘也是英文，只有文案用中文
# （改这里等于改 mood.yaml 的格式，老文件的键要能继续读，所以别动 key）

_RECENT_TURNS = 4
_RECENT_CLIP = 72
_USER_TIRED = re.compile(r"(累|困|乏|不想动|好累|疲惫)")
_USER_FRUSTRATED = re.compile(r"(烦|气死|吵|够了|烦死)")
_USER_BRIGHT = re.compile(r"(开心|谢谢|哈哈|好呀|不错)")
_USER_GREET = re.compile(r"^(小岸|岸宝|晚上好|早上好|中午好|下午好|晚安|嗨|你好)[。！？.?]*$")


_CN_SMALL = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
_WEEKDAYS = "一二三四五六日"


def cn_num(n: int) -> str:
    n = max(0, int(n))
    if n <= 10:
        return _CN_SMALL[n]
    if n < 20:
        return "十" + ("" if n == 10 else _CN_SMALL[n - 10])
    tens, ones = divmod(n, 10)
    if n >= 100:
        return str(n)
    return _CN_SMALL[tens] + "十" + ("" if ones == 0 else _CN_SMALL[ones])


def cn_clock(hour: int, minute: int) -> str:
    clock = cn_num(hour) + "点"
    if minute == 0:
        return clock
    if minute < 10:
        return clock + "零" + _CN_SMALL[minute]
    return clock + cn_num(minute)


def period_label(hour: int) -> str:
    if 5 <= hour < 11:
        return "清晨"
    if 11 <= hour < 17:
        return "午后"
    if 17 <= hour < 23:
        return "夜里"
    return "深夜"


def appraise_user(text: str) -> str:
    stripped = (text or "").strip()
    if _USER_GREET.match(stripped):
        return "greeting"
    if _USER_TIRED.search(stripped):
        return "tired"
    if _USER_FRUSTRATED.search(stripped):
        return "frustrated"
    if _USER_BRIGHT.search(stripped):
        return "bright"
    return "calm"


def next_self(prev: str, user: str, hour: int) -> str:
    late = hour >= 23 or hour < 5
    if user == "tired" or (late and user != "bright"):
        target = "concerned"
    elif user == "frustrated":
        target = "distant"
    elif user == "bright":
        target = "warm"
    elif user == "greeting" and not late:
        target = "warm"
    elif late:
        target = "weary"
    else:
        target = "quiet"
    if prev == "concerned" and target in {"quiet", "warm"} and user != "bright":
        return "concerned"
    return target


def allow_ask(mood: dict[str, Any], control: bool) -> bool:
    if control or mood.get("last_ask"):
        return False
    user = str(mood.get("user") or "calm")
    if user in {"greeting", "frustrated"}:
        return False
    return True


def decay_mood(mood: dict[str, Any], now: datetime) -> dict[str, Any]:
    raw = mood.get("updated_at")
    if not raw:
        return mood
    try:
        then = datetime.fromisoformat(str(raw))
    except ValueError:
        return mood
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    if now - then < timedelta(hours=1):
        return mood
    out = dict(mood)
    out["self"] = "quiet"
    out["user"] = "calm"
    out["last_ask"] = False
    return out


def unsolicited_block_reason(
    *,
    connected: bool,
    game: bool,
) -> str | None:
    if not connected:
        return "disconnected"
    if game:
        return "game"
    return None


def default_mood() -> dict[str, Any]:
    return {"user": "calm", "self": "quiet", "last_ask": False, "allow_ask": False}


_USER_CN = {
    "calm": "平静",
    "tired": "疲倦",
    "frustrated": "烦",
    "bright": "还轻快",
    "greeting": "在打招呼",
}
_SELF_CN = {
    "quiet": "安静",
    "warm": "温",
    "concerned": "担心",
    "weary": "乏",
    "distant": "远一点",
}


def mood_prompt(mood: dict[str, Any], hour: int) -> str:
    rules: list[str] = []
    user = str(mood.get("user") or "calm")
    self_tag = str(mood.get("self") or "quiet")
    if user == "tired":
        rules.append("先应人，短一些。")
    if user == "frustrated":
        rules.append("少说话，先办事，不要讲道理。")
    if self_tag == "weary":
        rules.append("更短，少堆诗意。")
    if mood.get("allow_ask"):
        rules.append("可以轻轻问一句。")
    else:
        rules.append("不要提问。")
    return (
        f"此刻：{period_label(hour)}。"
        f"漂泊者：{_USER_CN.get(user, '平静')}。"
        f"你：{_SELF_CN.get(self_tag, '安静')}。\n"
        f"策略：{''.join(rules)}"
    )


def speak_time(now: datetime, self_tag: str, text: str = "") -> str:
    asked = text or ""
    if re.search(r"(星期几|周几|礼拜几)", asked):
        return f"今天星期{_WEEKDAYS[now.weekday()]}。"
    if re.search(r"(几号|什么日子)", asked) and not re.search(r"几点", asked):
        return f"今天{cn_num(now.month)}月{cn_num(now.day)}号。"
    clock = cn_clock(now.hour, now.minute)
    late = now.hour >= 23 or now.hour < 5
    if self_tag == "concerned" or late:
        return f"已经{clock}了。"
    return f"现在是{clock}。"


def speak_temp(payload: Any, text: str) -> str:
    if not isinstance(payload, dict) or payload.get("celsius") is None:
        return "我这边还没有室温。"
    celsius = payload.get("celsius")
    try:
        value = float(celsius)
    except (TypeError, ValueError):
        return "我这边还没有室温。"
    spoken = f"屋里{value:g}度。"
    if "潮" in text or "湿" in text:
        humidity = payload.get("humidity")
        if humidity is None:
            return spoken + "湿度我还不清楚。"
        try:
            humid = float(humidity)
        except (TypeError, ValueError):
            return spoken
        if humid >= 70:
            return spoken + "有一点潮。"
        return spoken + "还不潮。"
    return spoken


def _pc_num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def speak_memory(payload: dict[str, Any]) -> str:
    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
    pct = _pc_num(memory.get("percent"))
    used = _pc_num(memory.get("used_gb"))
    if pct is None:
        return "内存我还没读到。"
    if used is not None:
        return f"内存{int(pct)}%，用了{used:g}G。"
    return f"内存{int(pct)}%。"


def speak_gpu(payload: dict[str, Any]) -> str:
    gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
    pct = _pc_num(gpu.get("percent"))
    if pct is None:
        return "显卡占用我还没读到。"
    name = str(gpu.get("name") or "").strip()
    if name:
        return f"{name}百分之{int(pct)}。"
    return f"显卡百分之{int(pct)}。"


def speak_pc_temp(payload: dict[str, Any]) -> str:
    cpu = payload.get("cpu") if isinstance(payload.get("cpu"), dict) else {}
    gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
    cpu_t = _pc_num(cpu.get("temp_celsius"))
    gpu_t = _pc_num(gpu.get("temp_celsius"))
    bits: list[str] = []
    if cpu_t is not None:
        bits.append(f"CPU {int(cpu_t)}度")
    if gpu_t is not None:
        bits.append(f"显卡 {int(gpu_t)}度")
    if not bits:
        return "温度我还没读到。"
    return "电脑现在" + "，".join(bits) + "。"


def speak_mini(desk: Any, text: str = "") -> str:
    mini = desk.get("mini") if isinstance(desk, dict) else None
    pc = mini.get("pc") if isinstance(mini, dict) else None
    if not isinstance(pc, dict) or not pc:
        return "Mini 那边还没报到。"
    payload = dict(pc)
    if "online" not in payload:
        payload["online"] = True
    asked = text or ""
    if re.search(r"(温度|多少度|几度)", asked):
        line = speak_pc_temp(payload)
        return line.replace("电脑现在", "Mini 现在", 1) if line.startswith("电脑") else line
    if re.search(r"(内存|运存)", asked):
        line = speak_memory(payload)
        return "Mini " + line if not line.startswith("Mini") else line
    spoken = speak_pc(payload, asked)
    return spoken.replace("电脑", "Mini", 1)


def speak_pc(payload: Any, text: str = "") -> str:
    if not isinstance(payload, dict) or not payload.get("online"):
        return "电脑那边还没连上。"
    asked = text or ""
    if re.search(r"(内存|运存)", asked):
        return speak_memory(payload)
    if re.search(r"(温度|多少度|几度)", asked):
        return speak_pc_temp(payload)
    if re.search(r"(显卡|gpu)", asked, re.IGNORECASE):
        return speak_gpu(payload)
    cpu = payload.get("cpu") if isinstance(payload.get("cpu"), dict) else {}
    load = _pc_num(cpu.get("percent") if isinstance(cpu, dict) else None)
    if load is None:
        cpu_line = "电脑在，占用我还没读到。"
    elif load < 40:
        cpu_line = "电脑还不热。"
    elif load < 75:
        cpu_line = f"电脑有一点忙，CPU {int(load)}%。"
    else:
        cpu_line = f"电脑挺忙的，CPU {int(load)}%。"
    if re.search(r"占用", asked) and not re.search(r"(热|忙|cpu)", asked, re.IGNORECASE):
        mem = speak_memory(payload)
        if mem != "内存我还没读到。":
            return cpu_line.rstrip("。") + "，" + mem
    return cpu_line


def speak_light(text: str, skills: list[dict[str, str]], getter) -> str:
    lights = [item for item in skills if "灯" in item.get("name", "")] or [
        item for item in skills if item.get("kind", "toggle") != "run"
    ]
    chosen = None
    for item in lights:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            chosen = item
            break
    if chosen is None and lights:
        chosen = lights[0]
    if chosen is None:
        return "我不确定灯现在开没开。"
    state = getter(chosen["id"]) if callable(getter) else None
    if not isinstance(state, dict) or state.get("on") is None:
        return "我不确定灯现在开没开。"
    name = chosen.get("name") or "灯"
    if state.get("on"):
        bri = state.get("brightness")
        if isinstance(bri, int):
            return f"{name}亮着，亮度{bri}。"
        return f"{name}亮着。"
    return f"{name}关着。"


def speak_media(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "播放控制关着。"
    title = str(payload.get("title") or "").strip()
    artist = str(payload.get("artist") or "").strip()
    if payload.get("playing"):
        if title and artist:
            return f"在放{title}，{artist}。"
        if title:
            return f"在放{title}。"
        return "电脑在放东西。"
    if title:
        return f"停着，刚才是{title}。"
    return "现在没在放。"


def speak_last(turns: deque[tuple[str, str]] | list[tuple[str, str]]) -> str:
    if not turns:
        return "我还没记住上一句。"
    _user, reply = list(turns)[-1]
    text = (reply or "").strip()
    if not text:
        return "我还没记住上一句。"
    if len(text) > _RECENT_CLIP:
        text = text[: _RECENT_CLIP - 1] + "…"
    return f"上一句是：{text}"


def speak_timer(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"好，{cn_num(hours)}小时后叫你。"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"好，{cn_num(minutes)}分钟后叫你。"
    return f"好，{seconds}秒后叫你。"


def recent_prompt(turns: deque[tuple[str, str]] | list[tuple[str, str]]) -> str:
    items = list(turns)[-_RECENT_TURNS:]
    if not items:
        return ""
    lines = ["最近对话（只作上下文，不要复述）："]
    for user, reply in items:
        lines.append(f"漂泊者：{_clip_turn(user)}")
        lines.append(f"你：{_clip_turn(reply)}")
    return "\n".join(lines)


def _clip_turn(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if len(value) <= _RECENT_CLIP:
        return value
    return value[: _RECENT_CLIP - 1] + "…"



def enforce_ask_budget(spoken: str, allow: bool) -> str:
    """Hub 执行问句额度：本轮不允许问时，丢掉带问号的句子。"""
    text = (spoken or "").strip()
    if allow or not text or not re.search(r"[？?]", text):
        return text
    pieces = re.split(r"(?<=[。！？!?])", text)
    kept = [piece for piece in pieces if piece.strip() and not re.search(r"[？?]", piece)]
    cleaned = "".join(kept).strip()
    return cleaned or "嗯。"


def ack_after_skills(spoken: str, events: list[dict[str, Any]]) -> str:
    if not events:
        return spoken
    ok = any(item.get("ok") for item in events)
    failed = any(item.get("ok") is False for item in events)
    if failed and not ok:
        stripped = (spoken or "").strip()
        if not stripped or stripped in {"好。", "好", "嗯。", "嗯"} or stripped.startswith("好"):
            return "这次没动成。"
    return spoken


def load_mood(path: Path | None) -> dict[str, Any]:
    mood = default_mood()
    if path is None or not path.is_file():
        return mood
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return mood
    if not isinstance(raw, dict):
        return mood
    if raw.get("user") in _USER_CN:
        mood["user"] = raw["user"]
    if raw.get("self") in _SELF_CN:
        mood["self"] = raw["self"]
    mood["last_ask"] = bool(raw.get("last_ask"))
    if raw.get("updated_at"):
        mood["updated_at"] = str(raw["updated_at"])
    if raw.get("last_user_at"):
        mood["last_user_at"] = str(raw["last_user_at"])
    return mood


def save_mood(path: Path | None, mood: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "user": mood.get("user") or "calm",
        "self": mood.get("self") or "quiet",
        "last_ask": bool(mood.get("last_ask")),
    }
    if mood.get("updated_at"):
        body["updated_at"] = mood["updated_at"]
    if mood.get("last_user_at"):
        body["last_user_at"] = mood["last_user_at"]
    path.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=True), encoding="utf-8")
