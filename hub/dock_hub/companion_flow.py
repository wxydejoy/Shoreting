"""伴侣的一轮对话与出声：chat 主流程、追问、记忆、心情、朗读与主动开口。

从 companion.py 搬出来的那一坨（原 403–931、1404–1506 行段）。
「听懂之后动设备」在 companion_desk，「连大脑」在 companion_llm，
这里管的是一轮话的流程本身：先办确定的事，办不了才去找大脑。
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any

from dock_hub.clarify import clarify_for
from dock_hub.config import ID_RE
from dock_hub.diary import clean_entry, clean_impression, diary_messages, impression_messages
from dock_hub.errors import HubError
from dock_hub import logs

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
from dock_hub.memory import parse_extraction, parse_forget as parse_memory_forget
from dock_hub.memory import memory_extract_messages
from dock_hub.memory import parse_teach as parse_memory_teach
from dock_hub.persona import (
    ack_after_skills,
    allow_ask,
    appraise_user,
    decay_mood,
    enforce_ask_budget,
    mood_prompt,
    next_self,
    recent_prompt,
    save_mood,
    speak_last,
    speak_light,
    speak_weather,
    speak_media,
    speak_mini,
    speak_pc,
    speak_schedule,
    speak_temp,
    speak_time,
    unsolicited_block_reason,
)
from dock_hub.reads import job_public
from dock_hub.speech import (
    chunk_read_text,
    clip_announce,
    estimate_read_seconds,
    pack_read_chunks,
    spoken_chars,
)
from dock_hub.intents import (
    ack_ledstrip,
    fold_game_asr,
    is_teach_attempt,
    looks_like_control,
    parse_teach,
    query_kind,
    schedule_day,
    skill_prompt,
)
from dock_hub.llm import build_messages, desk_facts_prompt, _clean_reply

from dock_hub.companion_support import (
    _TurnCancelled,
    _call_world,
    _clean_speaker,
    _latency,
    _ms,
    _turn_id,
    save_aliases,
    speaker_prompt,
)

MAX_CLIPS = 8
MAX_READ_CHARS = 8000
# 朗读旗标的保活：每念一句续一次，末句念完后这么久自然过期。
# 比 speaking 的 30s 保活宽一档——小说句子长，句间还有合成耗时。
READING_KEEPALIVE_SEC = 60.0

# 自闻/噪音转出来的无实义音节。真进大脑她就回「嗯。」，出喇叭又被麦收走，
# 转出来还是这种东西——「一直嗯」的自激就是这么烧起来的。
_FILLER_WORDS = {
    "嗯", "嗯嗯", "嗯呐", "啊", "啊啊", "哦", "哦哦", "噢", "喔",
    "诶", "欸", "喂", "嘿", "哈", "哈哈", "呃", "唔",
}


def _is_filler(text: str) -> bool:
    t = text.strip().strip("。，,．.?？!！~… ").lower()
    return not t or t in _FILLER_WORDS


# F1 免唤醒窗口的裁决权在大脑：回复是谁生成的谁最清楚「要不要等回答、
# 还要说多久」。手机只执行下发的策略，不再自己拿问句正则和字数估时猜。
# 问句开 20s（留足读字幕、想答案的时间），陈述句 8s；wait_ms 是她还要
# 说的估时，说完才开窗。等模型升级后可以让 LLM 直接给这个判定。
QUESTION_WINDOW_MS = 20_000
STATEMENT_WINDOW_MS = 8_000


def listen_policy(text: str) -> dict[str, int]:
    t = (text or "").strip()
    wait = min(len(t) * 260 + 1500, 20_000)
    open_ms = QUESTION_WINDOW_MS if re.search(r"[？?]", t) else STATEMENT_WINDOW_MS
    return {"wait_ms": wait, "open_ms": open_ms}


class ChatFlowMixin:
    """混合进 Companion：一轮对话的流程与出声的缝。"""

    # ---- 一轮对话 ------------------------------------------------------------

    def chat(self, body: dict[str, Any], facts: str = "") -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 必须是对象")
        heard = str(body.get("text") or "").strip()
        if not heard:
            raise HubError("bad_request", "text 不能为空")
        text = fold_game_asr(heard)
        # 无实义输入直接吞掉：不进大模型、不出声、不开新一轮（也就不停她
        # 正在说的话）。澄清待答时例外——「嗯」「没有」都可能是正经答案。
        pending_now = self._pending_clarify
        if (pending_now is None or pending_now.expired()) and _is_filler(text):
            _latency(_turn_id(body), "chat_filler", chars=len(heard))
            return {"text": "", "audio_id": None}
        speaker = _clean_speaker(body.get("speaker"))
        turn = _turn_id(body)
        started = time.monotonic()
        _latency(turn, "chat_recv", chars=len(heard))
        seq, cancel = self._begin_turn()
        self.tts.stop()
        pending = self._pop_clarify()
        power_pending = self._pop_power()
        hidden = self._guest_hidden()
        taught = None if hidden else self._remember_skill(text)
        if hidden and (
            taught or is_teach_attempt(text) or parse_memory_teach(text) is not None
        ):
            # F26：客人在场不学新东西——口令和事实都不收，主人想教就先把模式关了
            spoken = "客人在这儿，先不记这个。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker
            )
        if taught or is_teach_attempt(text):
            spoken = taught or "这个只能绑已经配好的灯或启动项，不能自己加程序。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker
            )
        # F28：「记住，……」当场落盘并短确认，不绕模型——记忆这事确定性比文采重要
        memo = parse_memory_teach(text)
        if memo and self._memory is not None:
            key = self._memory.teach(memo)
            spoken = "记住了。" if key else "这条太长，你拆成短一点的说。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            _latency(turn, "memory_teach", ok=1 if key else 0)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker
            )
        memo_forget = parse_memory_forget(text)
        if memo_forget is not None and self._memory is not None:
            gone = self._memory.forget(memo_forget)
            spoken = f"忘掉了，{gone}。" if gone else "这个我本来就没记住。"
            self._prepare_mood(text, control=False)
            self._commit_reply(spoken)
            _latency(turn, "memory_forget", ok=1 if gone else 0)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker
            )
        control = looks_like_control(text)
        mood_extra = self._prepare_mood(text, control=control)
        if pending is not None:
            # F3：上一问有了下文就直接办；换话题了就当没问过，正常往下走
            answered = self._take_pending_answer(text, pending, turn)
            if answered is not None:
                spoken, skills = answered
                self._commit_reply(spoken)
                _latency(turn, "clarify", chars=len(spoken))
                return self._finish_spoken(
                    spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=skills
                )
        asked = query_kind(text)
        if asked:
            spoken = self._answer_query(asked, text)
            self._commit_reply(spoken)
            _latency(turn, "query", kind=asked)
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker
            )
        scene = self._handle_scene(text, turn)
        if scene:
            spoken, events = scene
            self._commit_reply(spoken)
            _latency(turn, "scene", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=events
            )
        leaving = self._handle_leaving(text, turn)
        if leaving:
            spoken, events = leaving
            self._commit_reply(spoken)
            _latency(turn, "leave", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=events
            )
        guest = self._handle_guest(text, turn)
        if guest:
            spoken, events = guest
            self._commit_reply(spoken)
            _latency(turn, "guest", act=events[0].get("action") if events else "")
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=events
            )
        desk = self._handle_desk(text, turn)
        if desk:
            spoken, skills = desk
            self._commit_reply(spoken)
            _latency(turn, "desk", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=skills
            )
        relay = self._handle_relay(text, turn)
        if relay:
            spoken, skills = relay
            self._commit_reply(spoken)
            _latency(turn, "relay", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=skills
            )
        # 泰缇斯 / Hermes 已移除：不再做「简单还是复杂」的路由判定，
        # 所有对话直接交给伴侣模型（顺带省掉每次一条分类请求）。
        # F21：电源动作走两步确认，必须排在澄清前面——「关电脑」不能被
        # 反问成「要关哪个？」，也不能落到模型手里。
        power = self._handle_power(text, turn, power_pending)
        if power is not None:
            spoken, events = power
            self._commit_reply(spoken)
            _latency(turn, "power", chars=len(spoken))
            return self._finish_spoken(
                spoken, turn, seq, started, cue_llm=False, user=heard, speaker=speaker, skills=events
            )
        # F3：像开关指令却解不出设备的话，先问一句，别把「帮我关了」扔给模型瞎猜。
        question = (
            clarify_for(text, self._skills, self._aliases)
            if self._run_skill is not None and self._skills
            else None
        )
        if question is not None:
            self._pending_clarify = question
            self._commit_reply(question.asked)
            _latency(turn, "clarify_ask", chars=len(question.asked))
            return self._finish_spoken(
                question.asked, turn, seq, started, cue_llm=False, user=heard, speaker=speaker
            )
        catalog = skill_prompt(self._skills)
        memory = recent_prompt(self._recent)
        desk_facts = desk_facts_prompt(_call_world(self._world.get("desk")))
        # 当前时间必须告诉她：问「明天穿什么」「今天几号」全指着这个，
        # 也不知道现在几点的话，人设里的时效话术都填不上
        _now = datetime.now().astimezone()
        now_line = (
            f"现在的时间：{_now.year}年{_now.month}月{_now.day}日"
            f"{_WEEKDAYS[_now.weekday()]} {_now.strftime('%H:%M')}。"
        )
        # 全量米家目录 + 原话点到的设备实况读数（没点名就是空串，零开销）
        mijia_catalog = _call_world(self._world.get("catalog")) or ""
        readings = ""
        if mijia_catalog:
            readings = _call_world(self._world.get("catalog_read"), text)
            if readings:
                readings = "设备实况（仅自己看。对方问状态直接用这些数，不要说不知道）：\n" + readings
        extra = "\n".join(
            part
            for part in (
                now_line,
                catalog,
                mood_extra,
                memory,
                facts,
                speaker_prompt(speaker),
                desk_facts,
                mijia_catalog,
                readings,
            )
            if part.strip()
        )
        messages = build_messages(text, extra, self.config.persona)
        llm_at = time.monotonic()
        cue_as_we_go = self.tts.configured and not self.tts.deliver
        try:
            if cue_as_we_go:
                raw = self._stream_and_cue(messages, turn, seq, cancel)
            else:
                raw = self._complete(messages)
            if cancel.is_set():
                raise _TurnCancelled()
        except _TurnCancelled:
            _latency(turn, "barge_in", ms=_ms(llm_at))
            self._record_turn(turn, heard, "", _ms(started), [], "被下一轮顶掉", speaker=speaker)
            return {"text": "", "audio_id": None}
        except HubError as exc:
            _latency(turn, "ollama_error", ms=_ms(llm_at))
            self._record_turn(turn, heard, "", _ms(started), [], exc.message, speaker=speaker)
            raise
        spoken = _clean_reply(raw)
        spoken = enforce_ask_budget(spoken, bool(self._mood.get("allow_ask")))
        applied, skills = self._apply_device_skills(text, raw, turn)
        spoken = ack_after_skills(spoken, skills)
        spoken = ack_ledstrip(text, spoken, skills)
        filled = False
        if not spoken and applied:
            spoken = "好。"
            filled = True
        if not spoken:
            _latency(turn, "ollama_empty", ms=_ms(llm_at))
            self._record_turn(turn, heard, "", _ms(started), skills, "大脑没有说出话", speaker=speaker)
            raise HubError("companion_unavailable", "大脑没有说出话")
        _latency(turn, "ollama_done", ms=_ms(llm_at), chars=len(spoken))
        self._commit_reply(spoken)
        return self._finish_spoken(
            spoken, turn, seq, started, filled=filled, user=heard, speaker=speaker, skills=skills
        )

    def _finish_spoken(
        self,
        spoken: str,
        turn: str,
        seq: int,
        started: float,
        *,
        filled: bool = False,
        cue_llm: bool = True,
        user: str = "",
        speaker: str = "",
        skills: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._ready = True
        self._ready_at = time.monotonic()
        listen = listen_policy(spoken)
        audio_id = None
        cue_as_we_go = self.tts.configured and not self.tts.deliver
        if not cue_as_we_go:
            with self._lock:
                if seq != self._seq:
                    _latency(turn, "barge_in", ms=_ms(started))
                    self._record_turn(turn, user, spoken, _ms(started), skills or [], speaker=speaker)
                    return {"text": spoken, "audio_id": None, "listen": listen}
            audio_id = self._maybe_speak(spoken, turn)
        else:
            with self._lock:
                barged = seq != self._seq
            if barged:
                _latency(turn, "barge_in", ms=_ms(started))
            elif filled or not cue_llm:
                self._cue_sentence(spoken, turn, seq)
        _latency(turn, "chat_total", ms=_ms(started), audio=1 if audio_id else 0)
        self._record_turn(turn, user, spoken, _ms(started), skills or [], speaker=speaker)
        self._remember(user, spoken)
        return {"text": spoken, "audio_id": audio_id, "listen": listen}

    def _record_turn(
        self,
        turn: str,
        user: str,
        reply: str,
        ms: int,
        skills: list[dict[str, Any]],
        error: str | None = None,
        speaker: str = "",
    ) -> None:
        # F26：访客模式下这轮对话不落盘——跟客人的话更不该被记着
        if not self._chat_log or self._guest_hidden():
            return
        self._chat_log.append(
            turn=turn, user=user, reply=reply, ms=ms, skills=skills, error=error, speaker=speaker
        )

    # ---- 心情与记忆 -----------------------------------------------------------

    def _remember_skill(self, user_text: str) -> str | None:
        spec = parse_teach(user_text, self._skills)
        if not spec:
            return None
        self._aliases[spec["phrase"]] = {"id": spec["id"], "on": spec["on"]}
        save_aliases(self._aliases_path, self._aliases)
        verb = "开" if spec["on"] else "关"
        _latency("-", "skill_taught", phrase=spec["phrase"], id=spec["id"], on=int(spec["on"]))
        return f"好，以后说{spec['phrase']}我就{verb}{spec['name']}。"

    def _prepare_mood(self, text: str, *, control: bool) -> str:
        now = datetime.now().astimezone()
        mood = decay_mood(dict(self._mood), now)
        user = appraise_user(text)
        mood["user"] = user
        mood["self"] = next_self(str(mood.get("self") or "quiet"), user, now.hour)
        mood["updated_at"] = now.isoformat(timespec="seconds")
        mood["allow_ask"] = allow_ask(mood, control)
        mood["last_user_at"] = now.isoformat(timespec="seconds")
        self._mood = mood
        save_mood(self._mood_path, mood)
        return mood_prompt(mood, now.hour)

    def _commit_reply(self, spoken: str) -> None:
        self._mood["last_ask"] = bool(re.search(r"[？?]", spoken or ""))
        save_mood(self._mood_path, self._mood)

    def _answer_query(self, kind: str, text: str) -> str:
        self_tag = str(self._mood.get("self") or "quiet")
        if kind == "time":
            return speak_time(datetime.now().astimezone(), self_tag, text)
        if kind == "temp":
            return speak_temp(_call_world(self._world.get("temperature")), text)
        if kind == "pc":
            return speak_pc(_call_world(self._world.get("pc")), text)
        if kind == "mini":
            return speak_mini(_call_world(self._world.get("desk")), text)
        if kind == "light":
            getter = self._world.get("device")
            return speak_light(text, self._skills, getter if callable(getter) else None)
        if kind == "weather":
            return speak_weather(text)
        if kind == "media":
            return speak_media(_call_world(self._world.get("media")))
        if kind == "schedule":
            # F13 日程：日历拿不到就空手回，她照实说，不编
            getter = self._world.get("calendar")
            events: Any = None
            if callable(getter):
                try:
                    events = getter(schedule_day(text))
                except Exception:  # noqa: BLE001 - 日历挂了当没配
                    events = None
            return speak_schedule(events, schedule_day(text))
        if kind == "last":
            return speak_last(self._recent)
        return "我不太清楚。"

    def _remember(self, user: str, reply: str) -> None:
        spoken = (reply or "").strip()
        heard = (user or "").strip()
        if not spoken or not heard:
            return
        self._recent.append((heard, spoken))
        if (
            self._memory is not None
            and not looks_like_control(heard)
            and not self._guest_hidden()
        ):
            # F28：闲聊里捞候选事实。失败不影响本轮对话，留一行痕迹就好
            try:
                learned = self._memory.note_turn(heard)
            except Exception as exc:  # noqa: BLE001 - 记忆坏了不该连累说话
                logs.swallowed("长期记忆抽取", exc)
                return
            if learned:
                _latency("-", "memory_learn", n=len(learned))
            # F30：正则捞完再让大脑判断一遍值不值得记。放后台跑——
            # 这是给明天的对话攒的，不占这轮回复的时延
            if len(heard) >= 4:
                self._memory_extract_bg(heard, spoken)

    def _memory_extract_bg(self, heard: str, spoken: str) -> None:
        if not self._memory_bg.acquire(blocking=False):
            return  # 上一轮还没判完，这轮让路——记忆不差这几分钟
        thread = threading.Thread(
            target=self._memory_extract_run,
            args=(heard, spoken),
            name="memory-extract",
            daemon=True,
        )
        thread.start()

    def _memory_extract_run(self, heard: str, spoken: str) -> None:
        try:
            self._memory_extract_once(heard, spoken)
        except Exception as exc:  # noqa: BLE001 - 大脑挂了记忆照旧空着，不连累别人
            logs.swallowed("大脑记事", exc)
        finally:
            self._memory_bg.release()

    def _memory_extract_once(self, heard: str, spoken: str) -> int:
        """大脑判断一轮对话值不值得记。返回落下的条数；大脑不可用就抛。"""
        if self._memory is None:
            return 0
        known = "\n".join(
            f"- {item['key']}：{item['value']}" for item in self._memory.entries()
        )
        raw = self._complete(memory_extract_messages(heard, spoken, known), temperature=0.3)
        facts = parse_extraction(raw)
        landed = 0
        for key, value in facts:
            if self._memory.remember(key, value, source="chat"):
                landed += 1
        if landed:
            _latency("-", "memory_ai_learn", n=landed)
        return landed

    def _maybe_speak(self, spoken: str, turn: str) -> str | None:
        if not self.tts.configured:
            return None
        with self._lock:
            self._speaking = True
            # 估个时长随快照发给手机（见 set_speaking 注释）
            self._speaking_left_until = time.monotonic() + min(
                len(spoken) * 0.26 + 2.5,
                30.0,
            )
        try:
            wav = self.tts.speak(spoken, turn)
        finally:
            # play_only（cue 完即返回）时不能在这儿摘旗标——真正的播放才
            # 刚开始，交给上面的 ETA 到点自然过期；deliver 模式拿到成品
            # wav 才算真的说完了
            if self.tts.deliver:
                with self._lock:
                    self._speaking = False
                    self._speaking_left_until = 0.0
        if not self.tts.deliver or not wav:
            return None
        self.keep_clip(turn, wav)
        return turn

    def _desk_flags(self) -> tuple[bool, bool]:
        raw_game = _call_world(self._world.get("game"))
        if "game" not in self._world:
            game = False
        else:
            game = True if raw_game is None else bool(raw_game)
        return self.k20_connected(), game

    def _unsolicited_block(self) -> str | None:
        connected, game = self._desk_flags()
        return unsolicited_block_reason(connected=connected, game=game)

    # ---- 朗读 / 故事 / 主动开口 ----------------------------------------------

    def set_story_books(self, books: Any) -> None:
        self._story_books = books

    def begin_story_audio(self) -> None:
        self.reads.cancel_all()
        with self._lock:
            self._seq += 1
        self.set_reading(True)
        self.set_speaking(True)
        self.tts.stop()

    def cue_story_sentence(self, text: str) -> bool:
        if not self.config.enabled or not self.tts.configured:
            return False
        with self._lock:
            seq = self._seq
        ok = bool(self._cue_sentence(text, "story", seq))
        if ok:
            # 每句都给抑制窗续 30s：故事多长说不准，旗标跟着 cue 走，
            # 念完最后一句后 30s 自然过期
            self.set_speaking(True)
            self.set_reading(True)
        return ok

    def stop_story_audio(self) -> None:
        with self._lock:
            self._seq += 1
        self.set_speaking(False)
        self.set_reading(False)
        self.tts.stop()

    def stop(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if self._story_books is not None:
            self._story_books.pause()
        self._pending_clarify = None
        self._pending_power = None
        with self._lock:
            self._seq += 1
            self._speaking = False
            self._turn_cancel.set()
            self._dim_gen += 1
        self.reads.cancel_all()
        self.set_reading(False)
        self.tts.stop()
        _latency("-", "stop")
        return {"ok": True}

    def announce(self, body: dict[str, Any]) -> dict[str, Any]:
        """微信等侧路：让守岸人说一句，不走调度、不调泰缇斯。"""
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 必须是对象")
        text = clip_announce(str(body.get("text") or ""))
        if not text:
            raise HubError("bad_request", "text 不能为空")
        turn = _turn_id(body)
        started = time.monotonic()
        skipped = self._unsolicited_block()
        if skipped:
            _latency(turn, "announce_skip", reason=skipped, chars=len(text))
            self._commit_reply(text)
            self._record_turn(turn, "（微信）", text, _ms(started), [])
            return {"text": text, "audio_id": None}
        _latency(turn, "announce_recv", chars=len(text))
        with self._lock:
            self._seq += 1
            seq = self._seq
        self.tts.stop()
        self._commit_reply(text)
        return self._finish_spoken(
            text, turn, seq, started, cue_llm=False, user="（微信）"
        )

    def speak_up(self, text: str) -> bool:
        """主动开口：到点说一句。被三道闸拦下（手机不在、在打游戏、伴侣没开）
        就返回 False——不算配额，下个到点的时刻还会再试。"""
        if not self.config.enabled:
            return False
        spoken = clip_announce(str(text or ""))
        if not spoken:
            return False
        turn = _turn_id({"text": spoken})
        started = time.monotonic()
        skipped = self._unsolicited_block()
        if skipped:
            _latency(turn, "announce_skip", reason=skipped, source="proactive")
            return False
        _latency(turn, "announce_recv", chars=len(spoken), source="proactive")
        with self._lock:
            self._seq += 1
            seq = self._seq
        # 这里原来是 self._stop_tts()——那个方法在 companion-tts 拆分时删掉了，
        # 只有测试的 stub 掩住了这颗雷；现在跟 announce 一样直接停嘴巴。
        self.tts.stop()
        self._commit_reply(spoken)
        self._finish_spoken(spoken, turn, seq, started, cue_llm=False, user="（定时）")
        return True

    def diary_compose(self, material: str) -> str:
        """F29 日记：拿材料换一段她口吻的正文。一次性问完，不流式、不进对话史。

        大脑不在场或没开伴侣就抛错，由调用方退到兜底文案——日记缺一天的
        理由可以是「大脑不在」，不能是「她没试」。
        """
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        text = self._complete(diary_messages(str(material or "")), temperature=0.8)
        cleaned = clean_entry(text)
        if not cleaned:
            raise HubError("companion_unavailable", "日记没写出来")
        return cleaned

    def impression_compose(self, material: str, current: str) -> str:
        """F41 对主人的印象：日记写完的同一晚，把记忆里那条滚动更新一版。

        同 diary_compose 的规矩：一次性问完，不流式、不进对话史；大脑不在
        就抛错，由调用方决定「不动」——旧印象比错印象好。
        """
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        text = self._complete(
            impression_messages(str(material or ""), str(current or "")),
            temperature=0.7,
        )
        cleaned = clean_impression(text)
        if not cleaned:
            raise HubError("companion_unavailable", "印象没写出来")
        return cleaned

    # ---- 朗读任务的回调 ------------------------------------------------------
    # reads.ReadJobs 干活，出声和状态都在这一侧；下面几个方法就是两边之间的缝。

    def stop_playback(self) -> None:
        self.tts.stop()

    def cue_sentence(self, text: str, ident: str, seq: int) -> bool:
        return self._cue_sentence(text, ident, seq)

    def synthesize(self, text: str, ident: str) -> bytes | None:
        return self.tts.speak(text, ident, play_only=False, play=False)

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def still_current(self, seq: int) -> bool:
        with self._lock:
            return seq == self._seq

    def set_speaking(self, speaking: bool, eta_sec: float | None = None) -> None:
        """说话旗标 + 预计还要说多久。

        TTS 是「cue 完即返回」的异步播放：旗标要是只亮几毫秒，手机永远
        看不到「她在说」，B2 抑制和 F2 打断的门就都废了。所以说话时必须
        把预估时长一起写进 _speaking_left_until，快照按剩余毫秒发出去，
        到点自然过期。不报时长的路径（朗读故事）按 30s 保活、由下一次
        cue 续命。
        """
        with self._lock:
            self._speaking = speaking
            if not speaking:
                self._speaking_left_until = 0.0
            else:
                self._speaking_left_until = time.monotonic() + (
                    eta_sec if eta_sec is not None else 30.0
                )

    def set_reading(self, on: bool) -> None:
        """整本小说在念（F10）：手机端据此把「插话打断」的门再抬一档，
        并对长句做回声过滤——念书时最响的那个人是她自己（B2）。"""
        with self._lock:
            self._reading = on
            self._reading_until = time.monotonic() + READING_KEEPALIVE_SEC if on else 0.0

    def keep_clip(self, ident: str, wav: bytes) -> None:
        with self._lock:
            self._clips[ident] = wav
            while len(self._clips) > MAX_CLIPS:
                self._clips.popitem(last=False)

    def drop_clip(self, ident: str) -> None:
        with self._lock:
            self._clips.pop(ident, None)

    def note(self, turn: str, stage: str, **fields: Any) -> None:
        _latency(turn, stage, **fields)
        if stage == "read_job_done" and self.on_read_done is not None:
            seconds = fields.get("sec")
            try:
                seconds = float(seconds) if seconds is not None else 0.0
                if seconds > 0:
                    self.on_read_done(seconds)
            except Exception as exc:  # noqa: BLE001 - 台账坏了不该连累朗读
                logs.swallowed("周报朗读记账", exc)

    def read(self, body: dict[str, Any]) -> dict[str, Any]:
        """Hub 朗读页：预估时长后，书桌播放或生成可下载 wav。"""
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 必须是对象")
        text = str(body.get("text") or "").strip()
        if not text:
            raise HubError("bad_request", "text 不能为空")
        if len(text) > MAX_READ_CHARS:
            raise HubError("bad_request", "一次最多八千字")
        mode = str(body.get("mode") or "play").strip()
        if mode not in {"play", "file"}:
            raise HubError("bad_request", "mode 只能是 play 或 file")
        if not self.tts.configured:
            raise HubError("companion_unavailable", "还没接上嘴巴")
        if self._story_books is not None:
            self._story_books.pause()
        chars = spoken_chars(text)
        estimate = estimate_read_seconds(text)
        turn = _turn_id(body)
        raw_chunks = chunk_read_text(text)
        chunks = pack_read_chunks(raw_chunks)
        if not chunks:
            raise HubError("bad_request", "text 不能为空")
        _latency(turn, "read_start", mode=mode, chars=chars, chunks=len(chunks), raw=len(raw_chunks))
        job = self.reads.submit(
            turn,
            mode=mode,
            text=text,
            chars=chars,
            estimate=estimate,
            pieces=chunks,
        )
        out = job_public(job)
        out["text"] = text
        return out

    def read_jobs(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        return self.reads.list()

    def read_job_action(self, ident: str, action: str) -> dict[str, Any]:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not ident or not ID_RE.match(ident):
            raise HubError("not_found", "没有这个任务")
        action = (action or "").strip()
        if action == "cancel":
            return self.reads.cancel(ident)
        if action == "delete":
            return self.reads.delete(ident)
        if action == "replay":
            return self._replay_read_job(ident)
        raise HubError("bad_request", "action 只能是 cancel、delete 或 replay")

    def _replay_read_job(self, ident: str) -> dict[str, Any]:
        text = self.reads.text_of(ident)
        if not text:
            raise HubError("not_found", "这篇稿已经不在了")
        return self.read({"text": text, "mode": "play"})

    def audio(self, ident: str) -> bytes:
        if not self.config.enabled:
            raise HubError("not_found", "未开启桌面伴侣")
        if not ident or not ID_RE.match(ident):
            raise HubError("not_found", "没有这段声音")
        with self._lock:
            wav = self._clips.get(ident)
        if not wav:
            wav = self.reads.stored(ident)
        if not wav:
            raise HubError("not_found", "没有这段声音")
        _latency(ident, "audio_get", bytes=len(wav))
        return wav

    def voice(self) -> bool:
        if not self.config.enabled or not self.tts.configured:
            return False
        self._refresh_probes()
        return self._voice
