"""追问澄清（F3）：没听懂的指令问一句，不瞎猜。

规格 5.4 的两条死规矩：
- 没听懂时**追问一次**，第二次没听懂就认输说「没听清」，不要瞎猜执行——
  尤其开关类，灯不能因为她猜错而无缘无故动；
- 执行成功后只说一个短确认。

这里只出纯逻辑：该不该问（``clarify_for``）、问完之后下一句能不能认领
（``take_answer``）。解析复用 intents，执行仍走 actions，Companion 只把
流程串起来。追问挂起带 TTL——问完就该有下文，凉了就算了。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from dock_hub.intents import (
    fold_game_asr,
    infer_brightness,
    infer_control,
    infer_temp,
    looks_like_control,
    skill_nicknames,
    turn_intent,
)

TTL_SEC = 120.0
"""追问之后等多久。比 F1 的对话窗宽（那边 15 秒），但也别挂一晚上。"""

_CANCEL_RE = re.compile(r"算了|不用|取消|别弄|不弄|没事")


@dataclass
class PendingClarify:
    """一次没解出来的开关意图，等下一句话来认领。"""

    on: bool  # True = 开，False = 关
    asked: str  # 她问出口的那句
    at: float = field(default_factory=time.monotonic)

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) - self.at > TTL_SEC


@dataclass
class ClarifyAnswer:
    """下一句话的归属：认领执行 / 不干了 / 还没听懂。"""

    kind: str  # run / cancel / miss
    ident: str | None = None
    name: str = ""


def _targets(skills: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in skills if item.get("kind") != "run" and item.get("name")]


def clarify_for(
    text: str,
    skills: list[dict[str, str]],
    aliases: dict[str, dict[str, Any]] | None = None,
) -> PendingClarify | None:
    """像一条开关指令、却解不出设备 → 该追问。其余一律 None。

    明确排除：解出来的（正常执行）、亮度/温度（有各自的兜底）、
    没带开/关态度的（打瓦、亮一点、叫我——那些是别人的活）。
    """
    if not skills:
        return None
    on = turn_intent(text)
    if on is None:
        return None
    if infer_control(text, skills, aliases):
        return None
    if infer_brightness(text, skills) or infer_temp(text, skills):
        return None
    targets = _targets(skills)
    if not targets:
        return None
    verb = "开" if on else "关"
    names = [str(item["name"]) for item in targets]
    if len(names) == 1:
        asked = f"要{verb}{names[0]}吗？"
    elif len(names) <= 3:
        asked = f"要{verb}哪个？{'、'.join(names[:-1])}还是{names[-1]}？"
    else:
        asked = f"要{verb}哪个？"
    return PendingClarify(on=on, asked=asked)


def _bare_device(text: str, skills: list[dict[str, str]]) -> str | None:
    """整句就是一个设备名/别名，才认成回答。

    只做全等、不做子串：「今天买了台灯」不能因为带了「台灯」就被当成
    「把台灯关了」——那是闲聊，动了灯就是瞎猜（规格 5.4）。
    """
    bare = text.strip("。，！？!?,. 　")
    if not bare:
        return None
    lowered = bare.lower()
    for item in skills:
        names = {str(item.get("id", "")).lower(), str(item.get("name", "")).lower()}
        names |= {nick.lower() for nick in skill_nicknames(item)}
        if lowered in names:
            return str(item["id"])
    return None


def take_answer(
    text: str,
    pending: PendingClarify,
    skills: list[dict[str, str]],
    aliases: dict[str, dict[str, Any]] | None = None,
) -> ClarifyAnswer | None:
    """上一问问出去后，这一句算不算回答。None = 换话题了，交给正常流程。"""
    folded = fold_game_asr(text)
    if _CANCEL_RE.search(folded):
        return ClarifyAnswer("cancel")
    if infer_control(folded, skills, aliases):
        return None  # 是条完整的新指令，让正常流程接手，结果一样
    ident = _bare_device(folded, skills)
    if ident:
        name = next((item["name"] for item in skills if item["id"] == ident), ident)
        return ClarifyAnswer("run", ident=ident, name=str(name))
    if looks_like_control(folded) and turn_intent(folded) is not None:
        # 问过一遍还没答对：认输，别再猜（规格 5.4）
        return ClarifyAnswer("miss")
    return None
