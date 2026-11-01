"""F28 长期记忆：她记得的事，可见、可删、不进向量库。

设计取向（features.md F28）：
- 一个 yaml 文件（hub.yaml 同目录 companion-memory.yaml），条目化存事实；
- 几十条事实用文件足够，还能人工打开改——做向量库反而是把「她记得什么」变黑盒；
- 记忆必须可见可撤销，否则会从「贴心」变成「毛骨悚然」。

两条来源：
- ``taught``：明确说「记住，……」，当场确认；
- ``chat``：从日常说话里捞的高置信事实（忌口、生日、称呼、宠物这类固定句式）。

上限 60 条。超了先挤「chat」来源里最旧的——自己教的（taught）不该被闲聊挤掉。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

MEMORY_LIMIT = 60
VALUE_LIMIT = 60

# 候选抽取：只收高置信句式。宁可漏收，不可乱记——记错一次，她就不值得信任了。
# 每条 (识别正则, key)。value 在匹配后清洗；带 .+ 的一律贪婪到句尾（非贪婪会把
# 「冰的」吞成「冰」）。
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"我(?:不喝|不能喝|别给我)(?:了)?(.+)"), "忌口"),
    (re.compile(r"我(?:不吃|不能吃|别给我吃|戒)(?:了)?(.+)"), "忌口"),
    (re.compile(r"我(?:对|吃)(.{1,12}?)过敏"), "过敏"),
    (re.compile(r"我的?生日(?:是|在)?(.+)"), "生日"),
    (re.compile(r"我(?:的名字)?(?:叫|是)([\u4e00-\u9fa5A-Za-z]{1,10})"), "称呼"),
    (re.compile(r"我?的?(猫|狗|鸟|鱼|仓鼠|兔子)(?:叫|名字叫|名字是)([\u4e00-\u9fa5A-Za-z0-9]{1,10})"), "宠物"),
    (re.compile(r"我在(.{2,16}?)上班|我在(.{2,16}?)工作"), "工作"),
    (re.compile(r"我(?:每周|周|每)([一二三四五六日天])((?:晚上?|早上?)?.*)"), "每周安排"),
    (re.compile(r"我的?(?:手机号|电话)(?:号码)?是([\d\s-]{5,20})"), "电话"),
)

_TEACH_RE = re.compile(r"^(?:请|麻烦|帮我|你|给我)*记住[，,：:.\s]+(.+)$")
_FORGET_RE = re.compile(r"^(?:请|麻烦|帮我|你)*(?:忘掉|忘了|别记(?:着)?了|删掉)[，,：:.\s]*(?:刚才?那个|那条)?[，,：:.\s]*(.*)$")

_STRIP = "。！？!?；;，, \n\r\t\"'「」『』【】（）()"


def _clean(value: str) -> str:
    return (value or "").strip(_STRIP).strip()[:VALUE_LIMIT]


def extract_facts(text: str) -> list[tuple[str, str]]:
    """从一句话里捞候选事实。返回 (key, value) 列表，可能为空。

    纯函数，没有副作用——落不落盘由调用方决定。
    """
    heard = (text or "").strip()
    if not heard or len(heard) > 80 or _TEACH_RE.match(heard):
        # 太长的段落大概率在叙述事情，不是在陈述偏好；「记住」走 taught 通道
        return []
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern, key in _PATTERNS:
        match = pattern.search(heard)
        if not match:
            continue
        groups = [group for group in match.groups() if group]
        if key == "宠物" and len(groups) == 2:
            # 「猫叫年糕」：key 带上动物名（宠物猫），多只宠物不互相挤
            key = f"宠物{groups[0]}"
            value = _clean(groups[1])
        else:
            value = _clean("".join(groups))
        if not value or (key, value) in seen:
            continue
        seen.add((key, value))
        found.append((key, value))
    return found


def parse_teach(text: str) -> str | None:
    """「记住，我不喝冰的」→「我不喝冰的」。不是教的话返回 None。"""
    match = _TEACH_RE.match((text or "").strip())
    if not match:
        return None
    return _clean(match.group(1))


def parse_forget(text: str) -> str | None:
    """「忘掉那条」→ "" 表示「删最近一条」；「忘掉年糕」→「年糕」按内容找。

    不是忘事的话返回 None（区别于「忘掉那条」的空串）。
    """
    match = _FORGET_RE.match((text or "").strip())
    if not match:
        return None
    return _clean(match.group(1))


def memory_prompt(entries: list[dict[str, Any]]) -> str:
    """把记忆渲染进提示词：要自然用上，不许报菜名。"""
    if not entries:
        return ""
    lines = ["你早就记住的关于漂泊者的事（自然用上，别逐条复述）："]
    for item in entries:
        lines.append(f"- {item['key']}：{item['value']}")
    return "\n".join(lines)


# F30 记忆由大脑判断：每轮对话后拿一轮话问一遍「有没有值得记的」，
# 正则抽取（extract_facts）继续留着当不花钱的兜底。
MEMORY_EXTRACT_PROMPT = """你是守岸人。下面给你一轮对话（漂泊者说的、你回的），和你已经记住的事。
判断这轮对话里有没有值得长期记住的、关于漂泊者的新信息：他的喜好、习惯、身份、身边的人或物、在进行的计划，或者明显的情绪倾向。
已经记住的事不要重复记；纯设备口令（开灯关空调这类）和寒暄不用记，大多数轮次是什么都不用记的。
但他带时间地点的计划要记——哪怕话里带着「帮我」，那是请你记住，不是让你现在就办。
值得记就每行一条，格式严格是「key = value」：key 是两三个字的名目，value 是一句具体的、以后用得上的话，用中文。
一条都没有就只回一个「无」字。材料里没有的事不许编。

例：
漂泊者说：我下周想去北京出差两天，帮我留意那边的天气 → 出差计划 = 下周要去北京出差两天
漂泊者说：帮我把空调关了 → 无（口令）
漂泊者说：以后周三晚上别找我，我要打球 → 每周安排 = 周三晚上打球，那会儿别打扰
漂泊者说：现在几点了 → 无（随口一问）"""


def memory_extract_messages(heard: str, spoken: str, known: str) -> list[dict[str, str]]:
    known = known.strip() or "（还没记住什么）"
    return [
        {"role": "system", "content": MEMORY_EXTRACT_PROMPT},
        {
            "role": "user",
            "content": f"漂泊者说：{heard}\n你回了：{spoken}\n\n已经记住的事：\n{known}",
        },
    ]


def parse_extraction(raw: str) -> list[tuple[str, str]]:
    """解析大脑吐出的「key = value」行。返回 (key, value) 列表，可能为空。

    大脑不听话（回散文、回「无」）时尽量空手而归，不抛错。
    """
    facts: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-*·• ").strip()
        if not line or _clean(line) in ("无", "没有"):
            continue
        match = re.match(r"^([^=：:]{1,8})\s*[=：:]\s*(.+)$", line)
        if not match:
            continue
        key = _clean(match.group(1))
        value = _clean(match.group(2))
        if not key or not value or len(value) < 2:
            continue
        if (key, value) in seen:
            continue
        seen.add((key, value))
        facts.append((key, value))
    return facts[:4]


@dataclass
class MemoryEntry:
    key: str
    value: str
    source: str = "chat"
    learned_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "learned_at": self.learned_at,
        }


class MemoryStore:
    """companion-memory.yaml 的读写。线程安全（对话线程与设置页都会碰）。"""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._items: list[MemoryEntry] = []
        self._load()

    # ---- 生命周期 -------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return
        out: list[MemoryEntry] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            key = _clean(str(row.get("key") or ""))
            value = _clean(str(row.get("value") or ""))
            if not key or not value:
                continue
            out.append(
                MemoryEntry(
                    key=key,
                    value=value,
                    source=str(row.get("source") or "chat"),
                    learned_at=str(row.get("learned_at") or ""),
                )
            )
        self._items = out[:MEMORY_LIMIT]

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {"items": [item.to_dict() for item in self._items]}
        self.path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    # ---- 读 -------------------------------------------------------------

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_dict() for item in self._items]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def facts(self) -> str:
        with self._lock:
            return memory_prompt([item.to_dict() for item in self._items])

    # ---- 写 -------------------------------------------------------------

    def remember(self, key: str, value: str, source: str = "chat") -> str | None:
        """落一条。同 key 覆盖（新的赢），同 value 刷新时间不重复记。返回落下的 key。"""
        key = _clean(key)
        value = _clean(value)
        if not key or not value:
            return None
        now = datetime.now().astimezone().date().isoformat()
        with self._lock:
            for item in self._items:
                if item.value == value:
                    item.learned_at = now
                    self._save()
                    return item.key
            # 同 key 换说法（生日改了）：新的赢
            for index, item in enumerate(self._items):
                if item.key == key:
                    self._items[index] = MemoryEntry(
                        key=key, value=value, source=source, learned_at=now
                    )
                    self._save()
                    return key
            self._items.append(MemoryEntry(key=key, value=value, source=source, learned_at=now))
            self._compress()
            self._save()
            return key

    def note_turn(self, text: str) -> list[str]:
        """对话一轮后跑一遍候选抽取，落盘并返回这次新记下的 key。"""
        learned: list[str] = []
        for key, value in extract_facts(text):
            before = {item.value for item in self._items}
            landed = self.remember(key, value, source="chat")
            if landed and value not in before:
                learned.append(landed)
        return learned

    def teach(self, value: str) -> str | None:
        """「记住，……」：整句当事实存，key 用自增的「记事N」。"""
        value = _clean(value)
        if not value:
            return None
        with self._lock:
            index = 1
            taken = {item.key for item in self._items}
            while f"记事{index}" in taken:
                index += 1
        return self.remember(f"记事{index}", value, source="taught")

    def forget(self, needle: str = "") -> str | None:
        """删一条。needle 为空删最近一条；给了内容就按 value 包含匹配。返回被删的 value。"""
        with self._lock:
            if not self._items:
                return None
            index = len(self._items) - 1
            if needle:
                index = next(
                    (i for i, item in enumerate(self._items) if needle in item.value), -1
                )
                if index < 0:
                    return None
            gone = self._items.pop(index)
            self._save()
            return gone.value

    def forget_key(self, key: str) -> bool:
        """设置页按 key 删。"""
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.key != key]
            changed = len(self._items) != before
            if changed:
                self._save()
            return changed

    def _compress(self) -> None:
        """超限的压缩：先挤 chat 来源里最旧的，教进去的最后动。"""
        while len(self._items) > MEMORY_LIMIT:
            chat_rows = [i for i, item in enumerate(self._items) if item.source != "taught"]
            self._items.pop(chat_rows[0] if chat_rows else 0)
