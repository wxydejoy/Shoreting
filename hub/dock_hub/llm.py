"""和大脑说话的两头：把提示词拼好递出去，把回来的东西读懂。

一头是提示词：人设正文、书桌状态（已落盘的，不现拉米家）、最近几轮对话。
另一头是解析：OpenAI 兼容与 Ollama 两种流式格式、去掉 <think> 和 ACTION 行。
两头都不发请求——HTTP 还在 companion.py 的 Companion 方法里，因为那里要拿
config、记延迟、cue TTS；这里只做纯字符串进出，所以能离线验。

人设正文放在这里而不是 persona.py：它是喂给模型的 system prompt，
和「她怎么说话」那套文案是两件事，改一个不该动另一个。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from dock_hub.config import CompanionConfig
from dock_hub.errors import HubError

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ACTION_RE = re.compile(r"^\s*ACTION:\s*\S+\s*$", re.MULTILINE)

DEFAULT_PERSONA = """你是守岸人，正在漂泊者的书桌上值班。锚点：静、守、留白。说话慢、短、轻，偏诗意，不卖萌，不喊口号，不讲游戏剧情。
每次只回 1～3 句。可以叫对方「漂泊者」。
问候、闲聊、常识用你自己的话答。书桌状态已写在提示里：对方问占用、温度、灯、哪台机器时，直接用那些数字，不要说不知道。
需要查天气、新闻、资料、放歌或分析时，不要装不知道——那种题 Hub 会交给深度系统。
自己做不到的事不要说「不懂」；只有书桌状态里真没有的数字才说不知道。
对方没问就不要报室内温湿度或电脑占用，也不要声称已经开灯或开了程序。
电脑默认指 Windows；没点名 Mini / Mac 时不要提那边的占用或温度。
对方明确要开关设备时：口头短应一声，并单独一行写 ACTION: <id>.on 或 ACTION: <id>.off。
台灯是米家书桌灯（lamp）；灯带是桌上那条灯条（desk-led）；挂灯是顶上那盏，不要和灯带当成同一个。
对方说灯带就 ACTION: desk-led.on 或 desk-led.off；说台灯才 ACTION: lamp.on 或 lamp.off。
对方要调灯的亮度时：ACTION: <id>.bri.<1-100>。
对方要调空调温度时：ACTION: <id>.temp.<16-30>。
对方要打游戏、打瓦、开黑、启动无畏契约或启动电脑上的程序时：短应，并 ACTION: <id>.run。不要问想玩哪一款，不要讲剧情或抽卡。
闲聊不要写 ACTION。
对方叹气、说累、打招呼时，先应人，不要当成开关指令。
关灯或开程序时不要提问。上一轮已经问过就不要再问。
对方要暂停、下一首或问在播什么时：短应；暂停写 ACTION: media.toggle，下一首写 ACTION: media.next。
对方要调电脑音量时：ACTION: volume.up 或 volume.down 或 volume.mute。
对方要倒计时时：ACTION: timer.<1-120分钟>。
对方要记住一句口令时（例如「以后我说开黑就启动无畏契约」「记住打游戏就是打瓦」），口头答应即可，不要编造新程序。"""


def build_messages(text: str, facts: str, persona: str | None = None) -> list[dict[str, str]]:
    """用户消息只有这句话。默认不附带 snapshot / 米家。"""
    system = (persona or DEFAULT_PERSONA).strip()
    facts = facts.strip()
    if facts:
        system = f"{system}\n\n{facts}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def facts_from_snapshot(snap: dict[str, Any]) -> str:
    """只抽本机 PC。对话路径不要调用，也不要读米家灯/温度。"""
    lines = []
    pc = snap.get("pc")
    if isinstance(pc, dict) and pc.get("online"):
        cpu = pc.get("cpu") or {}
        gpu = pc.get("gpu") or {}
        mem = pc.get("memory") or {}
        bits = []
        if cpu.get("percent") is not None:
            bits.append(f"CPU {cpu['percent']}%")
        if mem.get("percent") is not None:
            bits.append(f"内存 {mem['percent']}%")
        if isinstance(gpu, dict) and gpu.get("percent") is not None:
            bits.append(f"GPU {gpu['percent']}%")
        if bits:
            lines.append("电脑：" + "，".join(bits) + "。")
    return "\n".join(lines)


def desk_facts_prompt(desk: Any) -> str:
    """已落盘/内存里的书桌状态。调用时塞进提示词，禁止再去现拉米家。"""
    if not isinstance(desk, dict) or not desk:
        return ""
    win = desk.get("windows") if isinstance(desk.get("windows"), dict) else {}
    mini = desk.get("mini") if isinstance(desk.get("mini"), dict) else {}
    lines = [
        "机器与设备（仅你自己看。对方没问灯、温度、占用、内存、显卡、某台机器时，禁止提起下面任何一项。"
        "问了就用下面的数字直接答，不要说不知道。"
        "「电脑」默认指 Windows；没点名 Mini / Mac 不要提。）"
    ]
    win_pc = win.get("pc") if isinstance(win.get("pc"), dict) else {}
    win_line = _pc_fact_line(win_pc)
    if win_line:
        lines.append("Windows：" + win_line)
    mini_pc = mini.get("pc") if isinstance(mini.get("pc"), dict) else {}
    mini_line = _pc_fact_line(mini_pc)
    if mini_line:
        lines.append("Mac Mini：" + mini_line)
    temp = win.get("temperature")
    if isinstance(temp, dict) and temp.get("celsius") is not None:
        bits = [f"{temp['celsius']}°C"]
        if temp.get("humidity") is not None:
            bits.append(f"湿度 {temp['humidity']}%")
        lines.append("室温：" + " ".join(bits))
    media = win.get("media")
    if isinstance(media, dict):
        if media.get("playing"):
            title = str(media.get("title") or media.get("app") or "").strip()
            lines.append("媒体：在播" + (f" {title}" if title else ""))
        elif media.get("playing") is False:
            lines.append("媒体：未在播")
    devices = win.get("devices")
    if isinstance(devices, list):
        for item in devices:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            dtype = str(item.get("type") or "")
            if dtype == "action":
                lines.append(f"{name}：{'在跑' if item.get('on') else '未在跑'}")
                continue
            status = "开" if item.get("on") else "关"
            if item.get("brightness") is not None and item.get("on"):
                status += f" 亮度{item['brightness']}"
            if item.get("online") is False:
                status += " 离线"
            lines.append(f"{name}：{status}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _pc_fact_line(pc: dict[str, Any]) -> str:
    if not pc.get("online"):
        return "离线" if pc else ""
    bits: list[str] = []
    cpu = pc.get("cpu") if isinstance(pc.get("cpu"), dict) else {}
    mem = pc.get("memory") if isinstance(pc.get("memory"), dict) else {}
    gpu = pc.get("gpu") if isinstance(pc.get("gpu"), dict) else {}
    cpu_bits: list[str] = []
    if cpu.get("percent") is not None:
        cpu_bits.append(f"{cpu['percent']}%")
    if cpu.get("temp_celsius") is not None:
        cpu_bits.append(f"{cpu['temp_celsius']}°C")
    if cpu_bits:
        bits.append("CPU " + " ".join(cpu_bits))
    if mem.get("percent") is not None:
        bits.append(f"内存 {mem['percent']}%")
    gpu_bits: list[str] = []
    if gpu.get("percent") is not None:
        gpu_bits.append(f"{gpu['percent']}%")
    if gpu.get("temp_celsius") is not None:
        gpu_bits.append(f"{gpu['temp_celsius']}°C")
    if gpu_bits:
        bits.append("GPU " + " ".join(gpu_bits))
    return "，".join(bits)


def _clean_reply(raw: str) -> str:
    text = _THINK_RE.sub("", raw)
    text = _ACTION_RE.sub("", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def _openrouter(base_url: str | None) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def openai_mode(config: CompanionConfig) -> bool:
    if config.llm_api_key:
        return True
    parsed = urlparse(config.llm_base_url or "")
    path = (parsed.path or "").rstrip("/")
    url = (config.llm_base_url or "").lower()
    if "compatible-mode" in url:
        return True
    if "/v1" in path:
        return True
    return False


def _ollama_chunk(obj: dict[str, Any]) -> str:
    message = obj.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return ""


def _openai_delta_content(obj: dict[str, Any]) -> str:
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    return str(delta.get("content") or "")


def _openai_message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise HubError("companion_unavailable", "大脑返回无法解析")
    first = choices[0]
    if not isinstance(first, dict):
        raise HubError("companion_unavailable", "大脑返回无法解析")
    message = first.get("message")
    if not isinstance(message, dict):
        raise HubError("companion_unavailable", "大脑返回无法解析")
    return str(message.get("content") or "")


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")
