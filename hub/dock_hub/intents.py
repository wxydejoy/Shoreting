"""把一句中文听成设备要做什么事。纯函数，不碰网络、不碰状态。

从 companion.py 搬出来的第一批。那个文件难改的原因是「听懂话」和「说话 /
联网 / 朗读」混在一起：想给「灯带」加一个口误别名，得在 2800 行里翻。
这里只留文本进、结构出的函数——输入是用户原话和技能表，输出是 (设备 id, 参数)
或一句话该怎么回，所以能单独测，也能被别处复用（比如场景的口头触发）。

搬的是位置，不是设计：函数体一行没改。调用方继续写
`from dock_hub.companion import infer_control` 也照样能用，
companion 会把下面这些名字再导出一次。
"""

from __future__ import annotations

import re
from typing import Any

# ---- 模型回复里的 ACTION 行，以及「这句像不像指令」的判据 ----

_ACTION_PARSE = re.compile(r"^\s*ACTION:\s*(\S+?)\.(on|off|run)\s*$", re.IGNORECASE | re.MULTILINE)
_ACTION_BRI = re.compile(
    r"^\s*ACTION:\s*(\S+?)\.(?:bri|brightness)\.(\d{1,3})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ACTION_TEMP = re.compile(
    r"^\s*ACTION:\s*(\S+?)\.(?:temp|temperature)\.(\d{1,2})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CONTROL_HINT = re.compile(
    r"(打开|关掉|关闭|关上|开启|开一下|关一下|开灯|关灯|开空调|关空调|启动|"
    r"无畏契约|valorant|打瓦|开黑|打游戏|玩游戏|启动游戏|开游戏|wegame|"
    r"抖音|亮一点|调亮|暗一点|调暗|调到|调成|设成|设为|设定|再亮|再暗|灯带|台灯|挂灯|"
    r"暂停|下一首|上一首|切歌|继续播放|继续放|静音|音量|声音大|声音小|"
    r"叫我|提醒我|喊我)"
)
_QUERY_TIME = re.compile(r"(几点|几号|星期几|周几|礼拜几|什么日子|现在.*时间)")
_QUERY_TEMP = re.compile(
    r"(多少度|几度|室温|潮不潮|湿度|屋里.*(?:度|温|热|潮|湿)|室内.*(?:度|温|热|潮|湿))"
)
_QUERY_PC = re.compile(r"(热|忙|卡|占用|cpu|内存|显卡)", re.IGNORECASE)
_QUERY_LIGHT = re.compile(r"(开了没|亮着吗|开着吗|关了没|亮着没有)")
_QUERY_MEDIA = re.compile(r"(在播什么|放的什么|在放什么|现在放的|播放的是什么|放什么歌)")
_QUERY_LAST = re.compile(r"(刚才那句|刚才你说|刚才说了什么|上一句|你刚说)")
_QUERY_MINI = re.compile(r"(mac\s*mini|mini|麦金塔|迷你|咪妮|麦克迷)", re.IGNORECASE)

# ---- 亮度与温度 ----

_BRIGHT_UP = re.compile(r"(亮一点|调亮|再亮|亮一些)")
_BRIGHT_DOWN = re.compile(r"(暗一点|调暗|再暗|暗一些|暗一点)")
_BRIGHT_SET = re.compile(
    r"(?:调到|亮度(?:调到|设为|设成)?)\s*([0-9]{1,3}|[零〇一二两三四五六七八九十百]+)"
)
_TEMP_SET = re.compile(
    r"(?:调到|调成|设成|设为|设定|调至)?\s*([0-9]{2}|[零〇一二两三四五六七八九十]+)\s*度"
)
_TEMP_DEGREE = re.compile(r"(?:\d|[零〇一二两三四五六七八九十]+)\s*度")

# ---- 开关词、教会口令、语音识别的常见口误 ----

_TURN_ON = re.compile(r"(打开|开启|开一下|开灯|开空调|启动)")
_TURN_OFF = re.compile(r"(关掉|关闭|关上|关一下|关灯|关空调)")
_TEACH = re.compile(
    r"(?:帮我)?(?:创建|新建|加一个)(?:一个)?技能[，,：:]?"
    r"(?:以后)?(?:我说|我喊)?[「『\"“]?(?P<phrase>.{2,16}?)[」』\"”]?"
    r"就(?:帮我)?(?:去)?(?:打开|启动|开启|关掉|关闭|关上|开)(?P<target>.+)"
)
_TEACH_ALT = re.compile(
    r"以后(?:我说|我喊)(?P<phrase>.{2,16}?)就(?:帮我)?(?:打开|启动|开启|关掉|关闭|关上|开)(?P<target>.+)"
)
_TEACH_GAME = re.compile(
    r"(?P<phrase>打游戏|玩游戏|开黑).{0,16}?就是.{0,12}?"
    r"(?P<target>打瓦|无畏契约|valorant|无畏|瓦)",
    re.IGNORECASE,
)
_ASR_CONTRACT = re.compile(r"五[位味微]契约|无[谓位味]契约")
_ASR_LEDSTRIP = re.compile(r"(?<![台挂])(?:灯带|灯袋|灯戴|登带|凳带)")
_ASR_LEDSTRIP_ASR = re.compile(r"(打开|关掉|关闭|关上|开一下|关一下|开启).{0,6}当代")
_ASR_LAMP = re.compile(r"(?:台灯|台登|太灯|抬灯)")
_ASR_HIT_ME = re.compile(r"(?<![别不])打我(?=[啊呀啦哈。！？.?]|$)")
_ASR_WANT_HIT = re.compile(r"(?:我要|我想)打我")
_PUNCT = re.compile(r"[。！？，,、.\s「」『』\"“”]+")
_FORBIDDEN_PHRASE = frozenset({"技能", "创建技能", "打开", "启动", "关掉", "关闭", "开启", "关上"})

# ---- 从原话到设备动作 ----

def skill_prompt(skills: list[dict[str, str]]) -> str:
    toggles = [item for item in skills if item.get("kind", "toggle") not in {"run", "sensor"}]
    launches = [item for item in skills if item.get("kind") == "run"]
    dimmable = [item for item in toggles if item.get("brightness")]
    lines: list[str] = []
    if toggles:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in toggles)
        lines.append(f"可开关（未告知当前开没开）：{names}。")
        lines.append("对方明确要开关时，口头短应一声，并单独一行写 ACTION: <id>.on 或 ACTION: <id>.off。")
    if dimmable:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in dimmable)
        lines.append(f"可调亮度：{names}。对方要调亮暗时，ACTION: <id>.bri.<1-100>。")
    climate = [item for item in toggles if item.get("temp")]
    if climate:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in climate)
        lines.append(f"可调温度：{names}。对方要调空调温度时，ACTION: <id>.temp.<16-30>。")
    if launches:
        names = "、".join(f"{item['name']}（{item['id']}）" for item in launches)
        lines.append(f"可启动：{names}。")
        lines.append("对方明确要启动时，口头短应一声，并单独一行写 ACTION: <id>.run。")
    if skills:
        lines.append("闲聊不要写 ACTION。对方要把口令绑到上述设备时，口头答应即可，不要编造新程序。")
    lines.append(
        "暂停/下一首：ACTION: media.toggle 或 ACTION: media.next。"
        "音量：ACTION: volume.up / volume.down / volume.mute。"
        "倒计时：ACTION: timer.<分钟>。"
        "正计时（记时长）：ACTION: stopwatch.start 开始，ACTION: stopwatch.stop 停表。"
    )
    return "\n".join(lines)


def parse_action_lines(raw: str) -> list[tuple[str, bool]]:
    found: list[tuple[str, bool]] = []
    for match in _ACTION_PARSE.finditer(raw):
        found.append((match.group(1), match.group(2).lower() != "off"))
    return found


def parse_bri_actions(raw: str) -> list[tuple[str, dict[str, int]]]:
    found: list[tuple[str, dict[str, int]]] = []
    for match in _ACTION_BRI.finditer(raw):
        value = int(match.group(2))
        if 1 <= value <= 100:
            found.append((match.group(1), {"value": value}))
    return found


def parse_temp_actions(raw: str) -> list[tuple[str, dict[str, int]]]:
    found: list[tuple[str, dict[str, int]]] = []
    for match in _ACTION_TEMP.finditer(raw):
        value = int(match.group(2))
        if 16 <= value <= 30:
            found.append((match.group(1), {"value": value}))
    return found


def fold_game_asr(text: str) -> str:
    """把打瓦 / 无畏契约 / 灯带台灯的常见口误折回技能能认的词。"""
    out = (text or "").strip()
    if not out:
        return out
    out = _ASR_CONTRACT.sub("无畏契约", out)
    out = out.replace("微企业", "WeGame")
    out = _ASR_WANT_HIT.sub("我要打瓦", out)
    out = _ASR_HIT_ME.sub("打瓦", out)
    if _ASR_LEDSTRIP_ASR.search(out):
        out = _ASR_LEDSTRIP_ASR.sub(lambda m: m.group(0).replace("当代", "灯带"), out)
    if _ASR_LEDSTRIP.search(out) and "台灯" not in out:
        out = _ASR_LEDSTRIP.sub("灯带", out)
    elif _ASR_LAMP.search(out) and "灯带" not in out:
        out = _ASR_LAMP.sub("台灯", out)
    return out


def looks_like_control(text: str) -> bool:
    return bool(_CONTROL_HINT.search(fold_game_asr(text)))


def looks_like_query(text: str) -> bool:
    return query_kind(text) is not None


def query_kind(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _QUERY_TIME.search(stripped):
        return "time"
    if _QUERY_LAST.search(stripped):
        return "last"
    if _QUERY_MEDIA.search(stripped):
        return "media"
    if re.search(r"(内存|运存)", stripped, re.IGNORECASE):
        return "pc"
    if re.search(r"(电脑|cpu|处理器|显卡|gpu)", stripped, re.IGNORECASE) and re.search(
        r"(温度|多少度|几度)", stripped
    ):
        return "pc"
    if _QUERY_MINI.search(stripped) and re.search(
        r"(温度|多少度|几度|占用|内存|热|忙)", stripped
    ):
        return "mini"
    if re.search(r"(显卡|gpu)", stripped, re.IGNORECASE):
        return "pc"
    if "电脑" in stripped and _QUERY_PC.search(stripped):
        return "pc"
    if _QUERY_TEMP.search(stripped):
        return "temp"
    if _QUERY_LIGHT.search(stripped) and ("灯" in stripped or "台灯" in stripped):
        return "light"
    return None


def is_teach_attempt(text: str) -> bool:
    folded = fold_game_asr(text)
    if "以后我说" in folded or "以后我喊" in folded:
        return True
    if ("技能" in folded) and any(word in folded for word in ("创建", "新建", "加一个")):
        return True
    if _TEACH_GAME.search(folded):
        return True
    return False


def skill_nicknames(item: dict[str, str]) -> list[str]:
    names = [item["id"], item["name"]]
    if "无畏契约" in item["name"] or item["id"] == "wegame":
        names.extend(
            [
                "无畏契约",
                "无畏",
                "valorant",
                "打瓦",
                "瓦",
                "打游戏",
                "玩游戏",
                "开黑",
                "启动游戏",
                "开游戏",
                "五位契约",
                "WeGame",
                "wegame",
            ]
        )
    if "抖音" in item["name"] or item["id"] == "douyin":
        names.extend(["抖音", "tiktok", "刷抖音"])
    if "灯带" in item["name"] or item["id"] in {"desk-led", "ledstrip", "ws2812"}:
        names.extend(["灯带", "氛围灯", "LED灯带"])
    return [name for name in names if name]


def _is_ledstrip(item: dict[str, str]) -> bool:
    if item.get("kind") == "run":
        return False
    name = str(item.get("name") or "")
    ident = str(item.get("id") or "")
    return "灯带" in name or ident in {"desk-led", "ledstrip", "ws2812"}


def constrain_light_actions(
    text: str,
    wanted: list[tuple[Any, Any]],
    skills: list[dict[str, str]],
) -> list[tuple[Any, Any]]:
    """User said 灯带: don't operate 挂灯 / 台灯 even if the model wrote ACTION: 挂灯.off."""
    if "灯带" not in (text or "") or not wanted:
        if "台灯" in (text or "") and "灯带" not in (text or "") and wanted:
            lamp_ids = {
                item["id"]
                for item in skills
                if item.get("id") == "lamp" or item.get("name") == "台灯"
            }
            kept_lamp: list[tuple[Any, Any]] = []
            for token, payload in wanted:
                ident = resolve_skill(str(token), skills)
                if ident and ident in lamp_ids:
                    kept_lamp.append((token, payload))
            return kept_lamp
        return wanted
    led_ids = {item["id"] for item in skills if _is_ledstrip(item)}
    kept: list[tuple[Any, Any]] = []
    for token, payload in wanted:
        ident = resolve_skill(str(token), skills)
        if ident and ident in led_ids:
            kept.append((token, payload))
    return kept


def ack_ledstrip(text: str, spoken: str, events: list[dict[str, Any]]) -> str:
    if "灯带" not in (text or ""):
        return spoken
    ok = any(
        item.get("ok")
        and (
            "灯带" in str(item.get("name") or "")
            or str(item.get("id") or "") in {"desk-led", "ledstrip", "ws2812"}
        )
        for item in events
    )
    if ok:
        return spoken
    return "灯带还没接到 Hub。"


def resolve_skill(token: str, skills: list[dict[str, str]]) -> str | None:
    needle = token.strip().lower()
    if not needle:
        return None
    for item in skills:
        if item["id"].lower() == needle or item["name"].lower() == needle:
            return item["id"]
        for nick in skill_nicknames(item):
            if nick.lower() == needle:
                return item["id"]
    for item in skills:
        for nick in skill_nicknames(item):
            if len(nick) >= 2 and nick.lower() in needle:
                return item["id"]
    return None


def infer_control(
    text: str,
    skills: list[dict[str, str]],
    aliases: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[str, bool]]:
    text = fold_game_asr(text)
    hits: list[tuple[str, bool]] = []
    for phrase, spec in (aliases or {}).items():
        if phrase and phrase in text:
            ident = spec.get("id") if isinstance(spec, dict) else spec
            on = spec.get("on", True) if isinstance(spec, dict) else True
            if ident:
                hits.append((str(ident), bool(on)))
    if hits:
        return hits
    if not looks_like_control(text):
        return []
    want_on = bool(_TURN_ON.search(text))
    want_off = bool(_TURN_OFF.search(text))
    if want_on == want_off:
        if "关" in text and "开" not in text:
            want_on, want_off = False, True
        elif "开" in text and "关" not in text:
            want_on, want_off = True, False
        elif any(
            nick in text
            for item in skills
            if item.get("kind") == "run"
            for nick in skill_nicknames(item)
            if len(nick) >= 2
        ):
            want_on, want_off = True, False
        else:
            return []
    on = want_on
    if "灯带" in text:
        leds = [item for item in skills if _is_ledstrip(item)]
        if leds:
            return [(leds[0]["id"], on)]
        return []
    if "台灯" in text:
        lamps = [
            item
            for item in skills
            if item.get("kind") != "run"
            and (item.get("id") == "lamp" or item.get("name") == "台灯")
        ]
        if lamps:
            return [(lamps[0]["id"], on)]
    for item in skills:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            if item.get("kind") == "run":
                if want_off:
                    continue
                hits.append((item["id"], True))
            else:
                hits.append((item["id"], on))
    if hits:
        return hits
    if re.search(r"[开关].*灯|灯.*[开关]|开灯|关灯", text):
        lamps = [
            item
            for item in skills
            if item.get("kind") != "run"
            and (item.get("id") == "lamp" or item.get("name") == "台灯")
        ]
        if lamps:
            return [(lamps[0]["id"], on)]
        lights = [
            item
            for item in skills
            if "灯" in item.get("name", "")
            and not _is_ledstrip(item)
            and item.get("name") != "挂灯"
        ]
        if lights:
            return [(lights[0]["id"], on)]
    if re.search(r"空调", text):
        acs = [item for item in skills if "空调" in item["name"]]
        if acs:
            return [(acs[0]["id"], on)]
    return []


def _dimmable(skills: list[dict[str, str]]) -> list[dict[str, str]]:
    lit = [item for item in skills if item.get("brightness")]
    if lit:
        return lit
    return [item for item in skills if "灯" in item.get("name", "")]


def infer_brightness(
    text: str, skills: list[dict[str, str]]
) -> list[tuple[str, dict[str, int]]]:
    if not skills:
        return []
    if _TEMP_DEGREE.search(text) and "亮度" not in text:
        return []
    delta = 0
    value: int | None = None
    match = _BRIGHT_SET.search(text)
    if match:
        parsed = parse_cn_int(match.group(1))
        if parsed is not None and 1 <= parsed <= 100:
            value = parsed
    elif _BRIGHT_UP.search(text):
        delta = 15
    elif _BRIGHT_DOWN.search(text):
        delta = -15
    else:
        return []
    lights = _dimmable(skills)
    if not lights:
        return []
    chosen = lights[0]
    for item in lights:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            chosen = item
            break
    spec: dict[str, int] = {"value": value} if value is not None else {"delta": delta}
    return [(chosen["id"], spec)]


def infer_temp(
    text: str, skills: list[dict[str, str]]
) -> list[tuple[str, dict[str, int]]]:
    climate = [item for item in skills if item.get("temp")]
    if not climate:
        return []
    match = _TEMP_SET.search(text)
    if not match:
        return []
    parsed = parse_cn_int(match.group(1))
    if parsed is None:
        return []
    chosen = climate[0]
    for item in climate:
        if any(nick in text for nick in skill_nicknames(item) if len(nick) >= 2):
            chosen = item
            break
    else:
        if "空调" not in text and len(climate) != 1:
            return []
    lo = int(chosen.get("temp_min") or 16)
    hi = int(chosen.get("temp_max") or 30)
    if parsed < lo or parsed > hi:
        parsed = max(lo, min(hi, parsed))
    return [(chosen["id"], {"value": parsed})]


def parse_cn_int(raw: str) -> int | None:
    text = (raw or "").strip()
    digits = re.search(r"(\d{1,3})", text)
    if digits:
        return int(digits.group(1))
    if "百" in text:
        return 100
    digits_map = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = 1 if not left else digits_map.get(left)
        ones = 0 if not right else digits_map.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(text) == 1:
        return digits_map.get(text)
    return None


# ---- 媒体 / 音量 / 定时：另一类「一句话」里常出现的东西 ----

_MEDIA_NEXT = re.compile(r"(下一首|切歌|换一首|下一曲)")
_MEDIA_PREV = re.compile(r"(上一首|上一曲)")
_MEDIA_PAUSE = re.compile(r"(暂停|停下|别放了)")
_MEDIA_RESUME = re.compile(r"(继续放|继续播放|接着放)")
_VOLUME_UP = re.compile(r"(声音大|音量大|大声点|大声一)")
_VOLUME_DOWN = re.compile(r"(声音小|音量小|小声点|小声一)")
_VOLUME_UNMUTE = re.compile(r"(取消静音|打开声音)")
_VOLUME_MUTE = re.compile(r"(静音)")
_ACTION_MEDIA = re.compile(
    r"^\s*ACTION:\s*media\.(toggle|next|previous)\s*$", re.IGNORECASE | re.MULTILINE
)
_ACTION_VOLUME = re.compile(
    r"^\s*ACTION:\s*volume\.(up|down|mute)\s*$", re.IGNORECASE | re.MULTILINE
)
_ACTION_TIMER = re.compile(
    r"^\s*ACTION:\s*timer\.(\d{1,3})\s*$", re.IGNORECASE | re.MULTILINE
)
_ACTION_STOPWATCH = re.compile(
    r"^\s*ACTION:\s*stopwatch\.(start|stop)\s*$", re.IGNORECASE | re.MULTILINE
)

# 正计时（F9）：「开始正计时」「帮我记一下时长」起表，「停/结束」落表。
# 放在 parse_timer 之前判断，免得「开始计时」被当成定时。
_STOPWATCH_START = re.compile(r"(开始|启动|来一个|开一个)(个)?(正计时|计时|计时器)")
_STOPWATCH_STOP = re.compile(r"(停止|结束|停掉|关掉|落)(个)?(正计时|计时|计时器)|计时结束")


def parse_stopwatch_start(text: str) -> bool:
    return bool(_STOPWATCH_START.search((text or "").strip()))


def parse_stopwatch_stop(text: str) -> bool:
    return bool(_STOPWATCH_STOP.search((text or "").strip()))


def infer_media(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _MEDIA_NEXT.search(stripped):
        return "next"
    if _MEDIA_PREV.search(stripped):
        return "previous"
    if _MEDIA_RESUME.search(stripped):
        return "toggle"
    if _MEDIA_PAUSE.search(stripped) and "灯" not in stripped:
        return "toggle"
    return parse_media_action(stripped)


def infer_volume(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _VOLUME_UNMUTE.search(stripped):
        return "up"
    if _VOLUME_MUTE.search(stripped):
        return "mute"
    if _VOLUME_UP.search(stripped):
        return "up"
    if _VOLUME_DOWN.search(stripped):
        return "down"
    return parse_volume_action(stripped)


def parse_media_action(raw: str) -> str | None:
    match = _ACTION_MEDIA.search(raw or "")
    if not match:
        return None
    return match.group(1).lower()


def parse_volume_action(raw: str) -> str | None:
    match = _ACTION_VOLUME.search(raw or "")
    if not match:
        return None
    return match.group(1).lower()


def parse_timer_action(raw: str) -> int | None:
    match = _ACTION_TIMER.search(raw or "")
    if not match:
        return None
    minutes = int(match.group(1))
    if 1 <= minutes <= 120:
        return minutes * 60
    return None


def parse_stopwatch_action(raw: str) -> str | None:
    match = _ACTION_STOPWATCH.search(raw or "")
    return match.group(1).lower() if match else None


def parse_timer(text: str) -> int | None:
    action = parse_timer_action(text)
    if action is not None:
        return action
    stripped = (text or "").strip()
    if not re.search(r"(叫我|提醒我|喊我|叫一声)", stripped):
        return None
    if re.search(r"半(?:个)?小时", stripped):
        return 30 * 60
    match = re.search(
        r"(?:再)?(?:过)?(?P<num>\d{1,3}|[零〇一二两三四五六七八九十]+)"
        r"(?P<unit>秒钟|秒|分钟|分|小时|钟头)后",
        stripped,
    )
    if not match:
        if re.search(r"(一会儿|过一会).*(叫我|提醒我)", stripped):
            return 5 * 60
        return None
    number = parse_cn_int(match.group("num"))
    if number is None or number < 1:
        return None
    unit = match.group("unit")
    if unit in {"秒", "秒钟"}:
        seconds = number
    elif unit in {"小时", "钟头"}:
        seconds = number * 3600
    else:
        seconds = number * 60
    if seconds < 10 or seconds > 2 * 3600:
        return None
    return seconds


def parse_teach(text: str, skills: list[dict[str, str]]) -> dict[str, Any] | None:
    folded = fold_game_asr(text)
    match = _TEACH.search(folded) if "技能" in folded else None
    if match is None:
        match = _TEACH_ALT.search(folded)
    if match is None:
        match = _TEACH_GAME.search(folded)
    if match is None:
        return None
    phrase = normalize_phrase(match.group("phrase"))
    target = normalize_phrase(match.group("target"))
    if not phrase or phrase in _FORBIDDEN_PHRASE or len(phrase) < 2:
        return None
    ident = resolve_skill(target, skills)
    if not ident:
        return None
    name = next((item["name"] for item in skills if item["id"] == ident), ident)
    on = not bool(_TURN_OFF.search(match.group(0)))
    return {"phrase": phrase, "id": ident, "name": name, "on": on}


def normalize_phrase(raw: str) -> str:
    return _PUNCT.sub("", (raw or "").strip())
