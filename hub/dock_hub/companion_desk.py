"""伴侣的 Desk 技能侧：口头场景、递话、离家、访客、番茄钟、媒体音量、电源、慢慢关灯。

从 companion.py 搬出来的那一坨（原 932–1491 行段）。「听懂一句话」的
纯函数部分在 intents.py，这里只负责「听懂之后怎么办」——调 world 里
挂进来的回调、把结果翻成她说的话和事件列表。
"""

from __future__ import annotations

import threading
import time
import uuid
import re
from typing import Any

from dock_hub.actions import apply_device_skills, call_skill, launch_apps, set_temperature
from dock_hub.clarify import PendingClarify, take_answer
from dock_hub.errors import HubError
from dock_hub.guest import parse_guest
from dock_hub.intents import (
    infer_control,
    infer_media,
    infer_music,
    infer_noise,
    infer_power,
    infer_volume,
    looks_like_control,
    normalize_phrase,
    parse_leave_action,
    parse_leaving,
    parse_media_action,
    parse_stopwatch_action,
    parse_stopwatch_start,
    parse_stopwatch_stop,
    parse_timer,
    parse_timer_action,
    parse_volume_action,
    resolve_skill,
)
from dock_hub.notify import parse_relay
from dock_hub.persona import speak_timer
from dock_hub.timers import speak_stopwatch_result

from dock_hub.companion_support import _call_world, _latency

# F 收尾：慢慢关灯。12 秒从当前亮度收到 1 再断电，不经过模型
SLOW_DIM_SEC = 12.0
SLOW_DIM_STEPS = 12
_SLOW_DIM = re.compile(r"(慢慢|渐渐|缓缓|一点点).{0,8}(关|暗|灭)|渐暗|淡出")

# 白噪音没放成时的统一口径（_handle_desk 里用它判断 ok）
_NOISE_FAIL_SPOKEN = ("这次没动成。", "这台 Mini 还不能放白噪音。")

# ---- F21 电源动作：问出口的那句、做成的短确认、等多久作废 -------------------

POWER_TTL_SEC = 60.0
"""确认悬着多久作废。比 F3 的两分钟紧——这是要动真格的动作。"""

_POWER_ASK = {
    "shutdown": "真的要关电脑吗？确认我就关了。",
    "sleep": "让电脑睡吗？确认我就动手。",
    "lock": "锁屏吗？确认就锁。",
}
_POWER_ACK = {"shutdown": "好，五秒后关机。", "sleep": "好，睡了。", "lock": "锁了。"}
_POWER_LABEL = {"shutdown": "关机", "sleep": "睡眠", "lock": "锁屏"}
_POWER_CONFIRM_RE = re.compile(r"确认|确定|动手吧|就这么办")
# 「好 / 对 / 嗯」这种一个字的答应，只有整句就是它的时候才算——
# 「今天好累」里的「好」不能变成「好，动手」。
_POWER_YES_RE = re.compile(r"^(?:好的?|好呀|行|嗯+|对呀?|是的?|要)[吧呀啊。.!！]?$")
_POWER_CANCEL_RE = re.compile(r"算了|不用|取消|别弄|不弄|先不|不关|不锁|不睡")


class DeskMixin:
    """混合进 Companion：设备动作与口头技能的执行半场。"""

    def _handle_scene(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「我要睡了」这类口令直接跑预置场景，不用绕模型。"""
        scenes = self._world.get("scenes") or []
        run = self._world.get("scene")
        if not scenes or not callable(run):
            return None
        needle = normalize_phrase(text)
        if not needle:
            return None
        hits: list[tuple[int, dict[str, Any]]] = []
        for item in scenes:
            for phrase in item.get("phrases") or []:
                key = normalize_phrase(str(phrase))
                if key and key in needle:
                    hits.append((len(key), item))
                    break
        if not hits:
            return None
        hits.sort(key=lambda row: row[0], reverse=True)
        item = hits[0][1]
        ident = str(item.get("id") or "")
        name = str(item.get("name") or ident)
        try:
            result = run(ident, say=False)
        except HubError as exc:
            _latency(turn, "scene_error", id=ident, code=exc.code)
            return (
                "这次没动成。",
                [{"id": ident, "name": name, "scene": True, "ok": False, "code": exc.code}],
            )
        ok = bool(result.get("ok"))
        _latency(turn, "scene_ok", id=ident, ok=1 if ok else 0)
        spoken = str(item.get("say") or "").strip() or f"好，{name}。"
        return spoken, [{"id": ident, "name": name, "scene": True, "ok": ok}]

    def _handle_relay(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「跟她说我晚点回」（F33 反向 / F4 语音留言）：递到手机，她只确认一句。

        失败不重试——递话是即发即忘的，用户会自己再说一次。
        """
        content = parse_relay(text)
        if content is None:
            return None
        post = self._world.get("notify")
        if not callable(post):
            return None
        try:
            post(content)
        except Exception as exc:  # noqa: BLE001 - 递话失败不是聊天失败
            _latency(turn, "relay_error", err=type(exc).__name__)
            return "这会儿没递到，等会儿再说一遍。", [{"id": "relay", "ok": False}]
        _latency(turn, "relay_ok", chars=len(content))
        return "写下来了，手机上会响一下。", [{"id": "relay", "ok": True}]

    def _handle_leaving(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「我出门了」（F18）：能关的全关，报告没关掉的。没接 Hub 就不认。"""
        if not parse_leaving(text) and not parse_leave_action(text):
            return None
        leave = self._world.get("leave")
        if not callable(leave):
            return None
        try:
            result = leave()
        except HubError as exc:
            _latency(turn, "leave_error", code=exc.code)
            return "这次没动成。", [{"id": "leave", "leave": True, "ok": False, "code": exc.code}]
        spoken = str(result.get("spoken") or "都关好了，路上小心。")
        _latency(turn, "leave_ok", ok=1 if result.get("ok") else 0, left=len(result.get("left") or []))
        return spoken, [{"id": "leave", "leave": True, "ok": bool(result.get("ok"))}]

    def _handle_guest(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        """「有客人」「客人走了」（F26）：把记录收起来、再放回来。

        world 里没挂 guest（配置 guest: false）时就不认识这句话，让它落到
        闲聊里去——「有客人」本来也可能是在聊天。
        """
        act = parse_guest(text)
        if act is None:
            return None
        toggle = self._world.get("guest")
        if not callable(toggle):
            return None
        try:
            result = toggle(act)
        except HubError as exc:
            _latency(turn, "guest_error", code=exc.code)
            return "这会儿没动成。", [{"id": "guest", "action": act, "ok": False}]
        _latency(turn, "guest_ok", act=act)
        return str(result.get("spoken") or "好。"), [
            {"id": "guest", "action": act, "ok": bool(result.get("ok"))}
        ]

    def _handle_desk(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        dimmed = self._run_slow_dim(text, turn)
        if dimmed:
            return dimmed
        act = parse_stopwatch_action(text)
        if act is None:
            if parse_stopwatch_start(text):
                act = "start"
            elif parse_stopwatch_stop(text):
                act = "stop"
        if act == "start":
            self.timers.start_stopwatch()
            _latency(turn, "stopwatch_start")
            return "好，开始计时。", [{"id": "stopwatch", "action": "start", "ok": True}]
        if act == "stop":
            elapsed = self.timers.stop_stopwatch()
            if elapsed is None:
                return "还没开始记呢。", [{"id": "stopwatch", "action": "stop", "ok": False}]
            _latency(turn, "stopwatch_stop", sec=elapsed)
            return speak_stopwatch_result(elapsed), [
                {"id": "stopwatch", "action": "stop", "sec": elapsed, "ok": True}
            ]
        seconds = parse_timer(text)
        if seconds is not None:
            spoken = self._arm_timer(seconds)
            return spoken, [{"id": "timer", "sec": seconds, "ok": True}]
        media_act = infer_media(text)
        if media_act:
            spoken = self._run_media(media_act, turn)
            ok = spoken not in {"这次没动成。", "播放控制关着。", "这台电脑还不能控播放。"}
            return spoken, [{"id": "media", "action": media_act, "ok": ok}]
        volume_act = infer_volume(text)
        if volume_act:
            spoken = self._run_volume(volume_act, turn)
            ok = spoken not in {"这次没动成。", "音量我还调不了。"}
            return spoken, [{"id": "volume", "action": volume_act, "ok": ok}]
        noise_act = infer_noise(text)
        if noise_act:
            spoken = self._run_noise(noise_act, turn)
            ok = spoken not in _NOISE_FAIL_SPOKEN
            return spoken, [{"id": "noise", **noise_act, "ok": ok}]
        music_act = infer_music(text)
        if music_act:
            spoken = self._run_music(music_act, turn)
            ok = spoken not in _NOISE_FAIL_SPOKEN
            return spoken, [{"id": "music", **music_act, "ok": ok}]
        launched = self._run_launch(text, turn)
        if launched:
            return launched
        climate = self._run_temp(text, turn)
        if climate:
            return climate
        return None

    def _run_slow_dim(
        self, text: str, turn: str
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """「慢慢关灯」：不啪一下黑掉，用十几秒把亮度收到底再关。"""
        if not self._skills or self._run_skill is None:
            return None
        if not _SLOW_DIM.search(text or ""):
            return None
        targets = self._slow_dim_targets(text)
        if not targets:
            return None
        getter = self._world.get("device")
        with self._lock:
            self._dim_gen += 1
            gen = self._dim_gen
        events: list[dict[str, Any]] = []
        workers: list[threading.Thread] = []
        for item in targets:
            ident = str(item["id"])
            name = str(item.get("name") or ident)
            if not item.get("brightness"):
                # 不能调亮度就老实关掉，别假装在暗
                try:
                    self._run_skill(ident, False)
                except HubError as exc:
                    events.append({"id": ident, "name": name, "ok": False, "code": exc.code})
                    continue
                _latency(turn, "skill_ok", id=ident, on=0)
                events.append({"id": ident, "name": name, "on": False, "ok": True})
                continue
            start: int | None = None
            already_off = False
            if callable(getter):
                state = getter(ident)
                if isinstance(state, dict):
                    if isinstance(state.get("brightness"), int):
                        start = int(state["brightness"])
                    already_off = state.get("on") is False
            if already_off:
                continue
            worker = threading.Thread(
                target=self._dim_worker,
                args=(ident, start if start and start > 1 else 60, gen),
                name=f"slow-dim-{ident}",
                daemon=True,
            )
            worker.start()
            workers.append(worker)
            _latency(turn, "skill_ok", id=ident, on=0, slow=1)
            events.append({"id": ident, "name": name, "on": False, "slow": True, "ok": True})
        if not events:
            return None
        self._dim_thread = workers[-1] if workers else None
        _latency(turn, "slow_dim", n=len(events))
        return "好，灯慢慢暗。", events

    def _slow_dim_targets(self, text: str) -> list[dict[str, Any]]:
        picked: list[dict[str, Any]] = []
        for token, on in infer_control(text, self._skills, self._aliases):
            if on:
                continue
            ident = resolve_skill(token, self._skills)
            item = next((row for row in self._skills if row["id"] == ident), None)
            if item is not None and item.get("kind") == "toggle":
                picked.append(item)
        if picked:
            return picked
        # 只说「慢慢关灯」时，把正开着、能调亮度的灯都算上
        getter = self._world.get("device")
        if not callable(getter):
            return []
        for item in self._skills:
            if item.get("kind") != "toggle" or not item.get("brightness"):
                continue
            state = getter(item["id"])
            if isinstance(state, dict) and state.get("on"):
                picked.append(item)
        return picked

    def _dim_worker(self, ident: str, start: int, gen: int) -> None:
        step_wait = max(0.02, SLOW_DIM_SEC / max(1, SLOW_DIM_STEPS))
        for step in range(1, SLOW_DIM_STEPS + 1):
            if gen != self._dim_gen:
                return
            level = max(1, int(round(start * (1 - step / SLOW_DIM_STEPS))))
            try:
                self._run_skill(ident, True, brightness=level)
            except HubError:
                return
            time.sleep(step_wait)
        if gen != self._dim_gen:
            return
        try:
            self._run_skill(ident, False)
        except HubError:
            pass

    def _run_launch(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        return launch_apps(
            text=text,
            turn=turn,
            skills=self._skills,
            aliases=self._aliases,
            run_skill=self._run_skill,
            note=_latency,
        )

    # ---- F3 追问澄清 ---------------------------------------------------------

    def _pop_clarify(self) -> PendingClarify | None:
        """取走上一次追问。过期的当没问过。"""
        pending = self._pending_clarify
        self._pending_clarify = None
        if pending is None or pending.expired():
            return None
        return pending

    def _take_pending_answer(
        self, text: str, pending: PendingClarify, turn: str
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """上一问有了下文就直接办。None = 换话题了，正常流程接着走。"""
        answer = take_answer(text, pending, self._skills, self._aliases)
        if answer is None:
            return None
        if answer.kind == "run":
            events = self._run_clarified(str(answer.ident), pending.on, turn)
            spoken = "好。" if events[-1].get("ok") else "这次没动成。"
            return spoken, events
        if answer.kind == "cancel":
            return "好。", []
        return "没听清。", []

    def _run_clarified(self, ident: str, on: bool, turn: str) -> list[dict[str, Any]]:
        """追问认领后的那一按，事件形状跟 apply_device_skills 一致。"""
        name = next((item["name"] for item in self._skills if item["id"] == ident), ident)
        try:
            call_skill(self._run_skill, ident, on)
        except HubError as exc:
            _latency(turn, "skill_error", id=ident, code=exc.code)
            return [{"id": ident, "name": name, "on": on, "ok": False, "code": exc.code}]
        _latency(turn, "skill_ok", id=ident, on=int(on))
        return [{"id": ident, "name": name, "on": on, "ok": True}]

    # ---- F21 电源动作：危险，必须两步 ---------------------------------------

    def _pop_power(self) -> str | None:
        """取走挂着的确认。过期的当没问过——「确认」不能隔一夜还算数。"""
        pending = self._pending_power
        self._pending_power = None
        if pending is None or time.monotonic() - pending[1] > POWER_TTL_SEC:
            return None
        return pending[0]

    def _handle_power(
        self, text: str, turn: str, pending: str | None
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """「关电脑 / 锁屏 / 让电脑睡会儿」（F21）：先问后做，听见「确认」才动手。

        两步是死规矩（规格 5.2：危险动作单独说、单独确认）。问出去之后，
        同一个动作再说一遍也算数（有人就这么答）；换动作就改问新的；
        说别的去了当没问过，正常流程接着走。
        """
        action = infer_power(text)
        if pending is None:
            if action is None or not callable(self._world.get("power")):
                return None
            self._pending_power = (action, time.monotonic())
            _latency(turn, "power_ask", action=action)
            return _POWER_ASK[action], [
                {"id": "power", "action": action, "confirm": True, "ok": False}
            ]
        if action == pending:
            return self._fire_power(action, turn)
        if action is not None:
            self._pending_power = (action, time.monotonic())
            _latency(turn, "power_ask", action=action)
            return _POWER_ASK[action], [
                {"id": "power", "action": action, "confirm": True, "ok": False}
            ]
        if _POWER_CANCEL_RE.search(text):
            return "好，不动了。", [{"id": "power", "action": pending, "ok": False}]
        if _POWER_CONFIRM_RE.search(text) or _POWER_YES_RE.fullmatch(text.strip()):
            return self._fire_power(pending, turn)
        return None

    def _fire_power(self, action: str, turn: str) -> tuple[str, list[dict[str, Any]]]:
        self._pending_power = None
        power = self._world.get("power")
        if not callable(power):
            return "这边还没接上电脑。", [{"id": "power", "action": action, "ok": False}]
        try:
            power(action)
        except HubError as exc:
            _latency(turn, "power_error", action=action, code=exc.code)
            return f"{_POWER_LABEL[action]}没动成。", [
                {"id": "power", "action": action, "ok": False, "code": exc.code}
            ]
        _latency(turn, "power_ok", action=action)
        return _POWER_ACK[action], [{"id": "power", "action": action, "ok": True}]

    def _run_temp(self, text: str, turn: str) -> tuple[str, list[dict[str, Any]]] | None:
        return set_temperature(
            text=text,
            turn=turn,
            skills=self._skills,
            run_skill=self._run_skill,
            note=_latency,
        )

    def _run_media(self, action: str, turn: str) -> str:
        fn = self._world.get("media_command")
        if not callable(fn):
            return "这台电脑还不能控播放。"
        try:
            snap = fn(action)
        except HubError as exc:
            _latency(turn, "skill_error", id="media", code=exc.code)
            if exc.code == "not_found":
                return "播放控制关着。"
            return "这次没动成。"
        _latency(turn, "skill_ok", id="media", action=action)
        if action == "toggle" and isinstance(snap, dict) and snap.get("playing") is False:
            return "好，先停着。"
        if action == "next":
            return "好，下一首。"
        if action == "previous":
            return "好，上一首。"
        return "好。"

    def _run_volume(self, action: str, turn: str) -> str:
        fn = self._world.get("volume")
        if not callable(fn):
            return "音量我还调不了。"
        try:
            fn(action)
        except HubError as exc:
            _latency(turn, "skill_error", id="volume", code=exc.code)
            return "这次没动成。"
        _latency(turn, "skill_ok", id="volume", action=action)
        if action == "mute":
            return "好，静音。"
        if action == "down":
            return "好，小声一点。"
        return "好，大声一点。"

    def _run_noise(self, act: dict[str, Any], turn: str) -> str:
        """F31 白噪音：放/停。到点她自己淡出，不用再喊一声。"""
        fn = self._world.get("noise")
        if not callable(fn):
            return "这台 Mini 还不能放白噪音。"
        try:
            result = fn(act)
        except HubError as exc:
            _latency(turn, "skill_error", id="noise", code=exc.code)
            return _NOISE_FAIL_SPOKEN[0]
        except Exception:  # noqa: BLE001 - Mini 连不上也算没动成
            _latency(turn, "skill_error", id="noise", code="offline")
            return _NOISE_FAIL_SPOKEN[0]
        if not isinstance(result, dict) or not result.get("ok"):
            return _NOISE_FAIL_SPOKEN[0]
        if act.get("action") == "off":
            _latency(turn, "skill_ok", id="noise", action="off")
            return "关掉了。"
        labels = {"rain": "雨声", "waves": "海浪", "fan": "风扇", "brown": "白噪音"}
        label = labels.get(str(result.get("name") or ""), "白噪音")
        minutes = result.get("minutes") or 30
        _latency(turn, "skill_ok", id="noise", name=result.get("name"), minutes=minutes)
        return f"{label}放上了，{minutes} 分钟后停。"

    def _run_music(self, act: dict[str, Any], turn: str) -> str:
        """F32 本地电台：放/停。歌在 Mini 那边的 music/ 目录里，到点自己淡出。"""
        fn = self._world.get("music")
        if not callable(fn):
            return "这台 Mini 还不能放音乐。"
        try:
            result = fn(act)
        except HubError as exc:
            _latency(turn, "skill_error", id="music", code=exc.code)
            return "这次没动成。"
        except Exception:  # noqa: BLE001 - Mini 连不上也算没动成
            _latency(turn, "skill_error", id="music", code="offline")
            return "这次没动成。"
        if not isinstance(result, dict):
            return "这次没动成。"
        if not result.get("ok"):
            if result.get("error") == "empty":
                return "这台 Mini 还没有能放的音乐，往 music 目录里放几首。"
            return "这次没动成。"
        if act.get("action") == "off":
            _latency(turn, "skill_ok", id="music", action="off")
            return "停了。"
        minutes = result.get("minutes") or 60
        _latency(turn, "skill_ok", id="music", minutes=minutes)
        return f"放歌了，{minutes} 分钟后停。"

    def _arm_timer(self, seconds: int) -> str:
        self._cancel_timer()
        timer = threading.Timer(seconds, self._timer_fire)
        timer.daemon = True
        with self._lock:
            self._timer = timer
        timer.start()
        self.timers.arm(seconds)
        _latency("-", "timer_set", sec=seconds)
        return speak_timer(seconds)

    def _timer_fire(self) -> None:
        with self._lock:
            self._timer = None
            self._seq += 1
            seq = self._seq
        self.timers.clear()
        spoken = "到时间了。"
        turn = uuid.uuid4().hex[:12]
        self._cue_sentence(spoken, turn, seq)
        self._remember("（计时）", spoken)
        _latency(turn, "timer_fire")

    def _cancel_timer(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
        self.timers.clear()
        if timer is not None:
            timer.cancel()

    def _apply_device_skills(
        self, user_text: str, raw_reply: str, turn: str
    ) -> tuple[bool, list[dict[str, Any]]]:
        applied, events = apply_device_skills(
            user_text=user_text,
            raw_reply=raw_reply,
            turn=turn,
            skills=self._skills,
            aliases=self._aliases,
            run_skill=self._run_skill,
            world=self._world,
            note=_latency,
        )
        desk_ok = self._apply_desk_actions(user_text, raw_reply, turn, events)
        return applied or desk_ok, events

    def _apply_desk_actions(
        self,
        user_text: str,
        raw_reply: str,
        turn: str,
        events: list[dict[str, Any]],
    ) -> bool:
        if not looks_like_control(user_text):
            return False
        applied = False
        media_act = parse_media_action(raw_reply)
        if media_act:
            spoken = self._run_media(media_act, turn)
            ok = spoken not in {"这次没动成。", "播放控制关着。", "这台电脑还不能控播放。"}
            events.append({"id": "media", "action": media_act, "ok": ok})
            applied = applied or ok
        volume_act = parse_volume_action(raw_reply)
        if volume_act:
            spoken = self._run_volume(volume_act, turn)
            ok = spoken != "这次没动成。" and spoken != "音量我还调不了。"
            events.append({"id": "volume", "action": volume_act, "ok": ok})
            applied = applied or ok
        seconds = parse_timer_action(raw_reply)
        if seconds:
            self._arm_timer(seconds)
            events.append({"id": "timer", "sec": seconds, "ok": True})
            applied = True
        if parse_leave_action(raw_reply):
            leave = self._world.get("leave")
            if callable(leave):
                try:
                    result = leave()
                except HubError as exc:
                    events.append(
                        {"id": "leave", "leave": True, "ok": False, "code": exc.code}
                    )
                else:
                    events.append({"id": "leave", "leave": True, "ok": bool(result.get("ok"))})
                    applied = True
        return applied
