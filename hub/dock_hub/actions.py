"""把模型回的 ACTION 行落成真实的设备动作。

一段对话里，守岸人先写话，后面跟着 `ACTION: 灯 on` 这种行。这里负责把那些行
（再加上从用户原话里推出来的兜底意图）翻成 device 调用，逐台执行、单台失败不
影响其余，并把每台的结果收成事件列表给安卓回执。

它是纯函数式的：技能表、别名表、world 回调、以及「怎么真的去开设备」都从外面传进来。
这么拆的原因是这段逻辑以前和 Companion 的锁、TTS 队列、朗读线程混在一起，
想单独验一句「说开灯但句子里没有一个灯名」要起半个 Hub。
"""

from __future__ import annotations

from typing import Any, Callable

from dock_hub.errors import HubError
from dock_hub.intents import (
    constrain_light_actions,
    infer_brightness,
    infer_control,
    infer_temp,
    looks_like_control,
    parse_action_lines,
    parse_bri_actions,
    parse_do_actions,
    parse_set_actions,
    parse_temp_actions,
    resolve_skill,
)
from dock_hub.persona import cn_num

Note = Callable[..., None]
SkillRunner = Callable[..., Any]

MISS = "这次没动成。"


def call_skill(
    fn: SkillRunner | None,
    ident: str,
    on: bool,
    *,
    brightness: int | None = None,
    props: dict[str, Any] | None = None,
) -> None:
    """调一次设备技能。老签名只认 (id, on)，带参数会被 TypeError 拒掉，退回两参。"""
    if fn is None:
        return
    kwargs: dict[str, Any] = {}
    if brightness is not None:
        kwargs["brightness"] = brightness
    if props:
        kwargs["props"] = props
    try:
        fn(ident, on, **kwargs)
    except TypeError:
        fn(ident, on)


def apply_device_skills(
    *,
    user_text: str,
    raw_reply: str,
    turn: str,
    skills: list[dict[str, str]],
    aliases: dict[str, dict[str, Any]],
    run_skill: SkillRunner | None,
    world: dict[str, Any],
    note: Note,
) -> tuple[bool, list[dict[str, Any]]]:
    """返回 (动了没有, 每台的执行结果)。"""
    events: list[dict[str, Any]] = []
    # 目录控制（set/do）不依赖书桌技能表——一台灯没绑也能指挥全屋。
    set_wanted = parse_set_actions(raw_reply)
    do_wanted = parse_do_actions(raw_reply)
    if not skills or run_skill is None:
        gate = set_wanted or do_wanted
        if gate and not looks_like_control(user_text):
            return False, events
        applied = False
        prop_runner = world.get("device_prop")
        for token, prop_name, raw_value in set_wanted:
            if not callable(prop_runner):
                continue
            events.extend(
                _run_catalog_call(note, turn, prop_runner, (token, prop_name, raw_value), label=prop_name)
            )
            if events[-1].get("ok"):
                applied = True
        action_runner = world.get("device_action")
        for token, action_name in do_wanted:
            if not callable(action_runner):
                continue
            events.extend(_run_catalog_call(note, turn, action_runner, (token, action_name), label=action_name))
            if events[-1].get("ok"):
                applied = True
        return applied, events
    wanted = parse_action_lines(raw_reply)
    bri_wanted = parse_bri_actions(raw_reply)
    temp_wanted = parse_temp_actions(raw_reply)
    gate = wanted or bri_wanted or temp_wanted or set_wanted or do_wanted
    if gate and not looks_like_control(user_text):
        wanted = []
        bri_wanted = []
        temp_wanted = []
        set_wanted = []
        do_wanted = []
    wanted = constrain_light_actions(user_text, wanted, skills)
    bri_wanted = constrain_light_actions(user_text, bri_wanted, skills)
    if not wanted:
        wanted = infer_control(user_text, skills, aliases)
    if not bri_wanted:
        bri_wanted = infer_brightness(user_text, skills)
    if not temp_wanted:
        temp_wanted = infer_temp(user_text, skills)

    applied = False
    for token, on in wanted:
        ident = resolve_skill(token, skills)
        if not ident:
            continue
        name = next((item["name"] for item in skills if item["id"] == ident), ident)
        try:
            call_skill(run_skill, ident, on)
        except HubError as exc:
            note(turn, "skill_error", id=ident, code=exc.code)
            events.append({"id": ident, "name": name, "on": on, "ok": False, "code": exc.code})
            continue
        applied = True
        note(turn, "skill_ok", id=ident, on=int(on))
        events.append({"id": ident, "name": name, "on": on, "ok": True})

    getter = world.get("device")
    for token, spec in bri_wanted:
        ident = resolve_skill(token, skills)
        if not ident:
            continue
        name = next((item["name"] for item in skills if item["id"] == ident), ident)
        target = spec.get("value")
        if target is None:
            current = None
            if callable(getter):
                state = getter(ident)
                if isinstance(state, dict):
                    current = state.get("brightness")
            base = int(current) if isinstance(current, int) else 50
            target = max(1, min(100, base + int(spec.get("delta") or 0)))
        target = max(1, min(100, int(target)))
        try:
            call_skill(run_skill, ident, True, brightness=target)
        except HubError as exc:
            note(turn, "skill_error", id=ident, code=exc.code)
            events.append(
                {
                    "id": ident,
                    "name": name,
                    "on": True,
                    "brightness": target,
                    "ok": False,
                    "code": exc.code,
                }
            )
            continue
        applied = True
        note(turn, "skill_ok", id=ident, on=1, bri=target)
        events.append({"id": ident, "name": name, "on": True, "brightness": target, "ok": True})

    for token, spec in temp_wanted:
        ident = resolve_skill(token, skills)
        if not ident:
            continue
        item = next((row for row in skills if row["id"] == ident), None)
        name = (item or {}).get("name") or ident
        prop_id = str((item or {}).get("temp_id") or "temp")
        value = int(spec["value"])
        try:
            call_skill(run_skill, ident, True, props={prop_id: value})
        except HubError as exc:
            note(turn, "skill_error", id=ident, code=exc.code)
            events.append(
                {
                    "id": ident,
                    "name": name,
                    "on": True,
                    "temp": value,
                    "ok": False,
                    "code": exc.code,
                }
            )
            continue
        applied = True
        note(turn, "skill_ok", id=ident, on=1, temp=value)
        events.append({"id": ident, "name": name, "on": True, "temp": value, "ok": True})

    # ---- 全量米家目录：通用属性写与动作 --------------------------------------
    # 目录条目不在 skills 里（那是绑上书桌的设备），所以不走 resolve_skill，
    # 直接按 id 或名字在目录里找。执行靠 world 里的 device_prop / device_action。

    prop_runner = world.get("device_prop")
    for token, prop_name, raw_value in set_wanted:
        if not callable(prop_runner):
            continue
        events.extend(
            _run_catalog_call(
                note, turn, prop_runner, (token, prop_name, raw_value),
                label=prop_name,
            )
        )
        if events[-1].get("ok"):
            applied = True

    action_runner = world.get("device_action")
    for token, action_name in do_wanted:
        if not callable(action_runner):
            continue
        events.extend(
            _run_catalog_call(note, turn, action_runner, (token, action_name), label=action_name)
        )
        if events[-1].get("ok"):
            applied = True

    return applied, events


def _run_catalog_call(
    note: Note,
    turn: str,
    runner: SkillRunner,
    args: tuple[Any, ...],
    *,
    label: str,
) -> list[dict[str, Any]]:
    """跑一次目录执行器，把结果折成事件。找不到设备的报错最常见，单独一个码。"""
    try:
        payload = runner(*args)
    except HubError as exc:
        note(turn, "skill_error", id=args[0], what=label, code=exc.code)
        return [{"id": args[0], "name": str(args[0]), "what": label, "ok": False, "code": exc.code}]
    except Exception:  # noqa: BLE001 - 执行器内部已兜住米家错误，这里只剩意外
        note(turn, "skill_error", id=args[0], what=label, code="mijia_error")
        return [{"id": args[0], "name": str(args[0]), "what": label, "ok": False, "code": "mijia_error"}]
    if not isinstance(payload, dict):
        payload = {}
    note(turn, "skill_ok", id=payload.get("id") or args[0], what=label)
    event = {"id": payload.get("id") or args[0], "name": payload.get("name") or str(args[0]), "what": label, "ok": True}
    if "value" in payload:
        event["value"] = payload["value"]
    return [event]


def launch_apps(
    *,
    text: str,
    turn: str,
    skills: list[dict[str, str]],
    aliases: dict[str, dict[str, Any]],
    run_skill: SkillRunner | None,
    note: Note,
) -> tuple[str, list[dict[str, Any]]] | None:
    """口头要开电脑上的程序：「打开 Remix」→ 开白名单里的那个。命不中就 None。"""
    if not skills or run_skill is None:
        return None
    for token, on in infer_control(text, skills, aliases):
        if not on:
            continue
        ident = resolve_skill(token, skills)
        if not ident:
            continue
        item = next((s for s in skills if s["id"] == ident), None)
        if not item or item.get("kind") != "run":
            continue
        name = item.get("name") or ident
        try:
            call_skill(run_skill, ident, True)
        except HubError as exc:
            note(turn, "skill_error", id=ident, code=exc.code)
            return (
                MISS,
                [{"id": ident, "name": name, "on": True, "ok": False, "code": exc.code}],
            )
        note(turn, "skill_ok", id=ident, on=1)
        return f"好，开{name}。", [{"id": ident, "name": name, "on": True, "ok": True}]
    return None


def set_temperature(
    *,
    text: str,
    turn: str,
    skills: list[dict[str, str]],
    run_skill: SkillRunner | None,
    note: Note,
) -> tuple[str, list[dict[str, Any]]] | None:
    """「空调二十六度」。没有空调技能就 None，让话继续走普通对话。"""
    wanted = infer_temp(text, skills)
    if not wanted or run_skill is None:
        return None
    events: list[dict[str, Any]] = []
    applied = False
    spoken = ""
    for token, spec in wanted:
        ident = resolve_skill(token, skills)
        if not ident:
            continue
        item = next((row for row in skills if row["id"] == ident), None)
        if not item:
            continue
        name = item.get("name") or ident
        prop_id = str(item.get("temp_id") or "temp")
        value = int(spec["value"])
        try:
            call_skill(run_skill, ident, True, props={prop_id: value})
        except HubError as exc:
            note(turn, "skill_error", id=ident, code=exc.code)
            return (
                MISS,
                [
                    {
                        "id": ident,
                        "name": name,
                        "on": True,
                        "temp": value,
                        "ok": False,
                        "code": exc.code,
                    }
                ],
            )
        applied = True
        note(turn, "skill_ok", id=ident, on=1, temp=value)
        events.append({"id": ident, "name": name, "on": True, "temp": value, "ok": True})
        spoken = f"好，{name}{cn_num(value)}度。"
    if not applied:
        return None
    return spoken, events
