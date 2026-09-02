"""泰缇斯（Tethys）：守岸人背后的 Hermes 深度系统。

守岸人（初步 Agent / companion.llm）先判断 ROUTE；复杂任务交泰缇斯，
完成后由守岸人润色，可选语音播报。
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

Route = Literal["shore", "tethys"]

_ROUTE_LINE = re.compile(r"^\s*ROUTE:\s*(shore|tethys)\s*$", re.IGNORECASE | re.MULTILINE)
_ROUTE_INLINE = re.compile(r"ROUTE:\s*(shore|tethys)", re.IGNORECASE)

TETHYS_SYSTEM = """你是泰缇斯（Tethys），守岸人书桌下的深度推理系统。
完成漂泊者交代的复杂任务：查资料、分析、对比、规划、长文总结等。
用清晰的中文回答，可以稍长、有条理；守岸人会口语转述到书桌，完整原文会同步到微信。
不要假装能直接开关灯或启动程序——家居与启动项由守岸人处理。
不要给微信发消息——Hub 会发。"""

POLISH_SYSTEM = """你是守岸人，正在把泰缇斯刚完成的深度结果转述给漂泊者。
说话慢、短、轻，偏诗意，1～3 句口语即可；可以叫对方「漂泊者」。
不要提泰缇斯、系统、模型；不要念长清单或 Markdown 符号；保留关键结论。
若结果为空或失败，轻轻说没查清楚，不要编造。"""

ROUTE_SYSTEM = """你是守岸人的调度器，只判断漂泊者这句话该由谁处理。
只回复一行，格式严格为：ROUTE: shore 或 ROUTE: tethys

选 shore：问候、致谢、开关灯或程序、调亮度、报时、室温、电脑忙不忙、灯开了没、记口令、闲聊、常识、一两句能直接答清的。
选 tethys：需要查资料、联网搜索、对比分析、长文总结、多步规划或推理；或明显超出桌面助手一口能答的范围。

不要解释，不要换行，不要回答用户原问题。"""


def route_bypass(text: str) -> Route | None:
    """家居/启动/书桌短查询必走守岸人，不必再问模型。"""
    from dock_hub.companion import is_teach_attempt, looks_like_control, looks_like_query

    stripped = (text or "").strip()
    if not stripped:
        return "shore"
    if looks_like_control(stripped) or looks_like_query(stripped) or is_teach_attempt(stripped):
        return "shore"
    return None


def build_route_messages(user_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ROUTE_SYSTEM.strip()},
        {"role": "user", "content": user_text.strip()},
    ]


def parse_route(raw: str) -> Route | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = _ROUTE_LINE.search(text) or _ROUTE_INLINE.search(text)
    if not match:
        return None
    value = match.group(1).lower()
    return "tethys" if value == "tethys" else "shore"


def build_tethys_messages(user_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": TETHYS_SYSTEM.strip()},
        {"role": "user", "content": user_text.strip()},
    ]


def build_polish_messages(user_text: str, tethys_reply: str) -> list[dict[str, str]]:
    body = f"漂泊者刚才问：{user_text.strip()}\n\n泰缇斯的结果：\n{tethys_reply.strip()}"
    return [
        {"role": "system", "content": POLISH_SYSTEM.strip()},
        {"role": "user", "content": body},
    ]


def default_notify_url(base_url: str) -> str:
    """Hermes API :8642 → 同机微信投递 :8643。"""
    parsed = urlparse(base_url or "")
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    return f"{scheme}://{host}:8643/v1/weixin"


def strip_tethys_invoke(text: str) -> str:
    """去掉句首「让泰缇斯…」类前缀，只留任务正文。"""
    cleaned = (text or "").strip()
    cleaned = re.sub(
        r"^(?:请|麻烦)?(?:让|叫)?(?:泰缇斯|泰提斯|tethys)[，,：:\s]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^[，,：:\s]+", "", cleaned)
    return cleaned.strip() or (text or "").strip()
