from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
import wave
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

from pathlib import Path

from dock_hub.companion import (
    Companion,
    MAX_READ_CHARS,
    _clean_reply,
    _join,
    appraise_user,
    build_messages,
    chunk_read_text,
    clip_announce,
    cn_clock,
    cut_sentences,
    estimate_read_seconds,
    join_wavs,
    pack_read_chunks,
    spoken_chars,
    wav_duration_sec,
    enforce_ask_budget,
    facts_from_snapshot,
    desk_facts_prompt,
    infer_brightness,
    infer_control,
    infer_temp,
    constrain_light_actions,
    fold_game_asr,
    infer_media,
    infer_volume,
    looks_like_control,
    parse_bri_actions,
    parse_temp_actions,
    parse_teach,
    parse_action_lines,
    parse_timer,
    query_kind,
    recent_prompt,
    resolve_skill,
    skill_prompt,
    speak_media,
    speak_pc,
    speak_mini,
    speak_temp,
    speak_time,
    speak_timer,
    unsolicited_block_reason,
)
from dock_hub.config import CompanionConfig, hub_config_to_raw, parse_config
from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.errors import HubError
from dock_hub.server import make_handler
from dock_hub.service import DockHub


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _FakeLineResp:
    def __init__(self, raw: bytes, status: int = 200) -> None:
        self.status = status
        self._buf = io.BytesIO(raw)

    def read(self) -> bytes:
        return self._buf.read()

    def __iter__(self) -> "_FakeLineResp":
        self._buf.seek(0)
        return self

    def __next__(self) -> bytes:
        line = self._buf.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self) -> "_FakeLineResp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _GatedLineResp:
    """第一行要等 gate 放行，用来模拟「还在往外流过字幕」的那一刻。"""

    def __init__(self, lines: list[str], gate: threading.Event) -> None:
        self.status = 200
        self._lines = lines
        self._gate = gate
        self._i = 0

    def __enter__(self) -> "_GatedLineResp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def __iter__(self) -> "_GatedLineResp":
        return self

    def __next__(self) -> bytes:
        if self._i >= len(self._lines):
            raise StopIteration
        if self._i == 0 and not self._gate.wait(5):
            raise StopIteration
        line = self._lines[self._i]
        self._i += 1
        return line.encode("utf-8")


def _ndjson_chat(content: str) -> _FakeLineResp:
    blob = (
        json.dumps({"message": {"content": content}, "done": False})
        + "\n"
        + json.dumps({"done": True})
        + "\n"
    )
    return _FakeLineResp(blob.encode("utf-8"))


def _chat_is_stream(req: Request) -> bool:
    raw = req.data or b"{}"
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(isinstance(body, dict) and body.get("stream"))


class _FakeBytes:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeBytes":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _tiny_wav(*, frames: int = 2400, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def _fake_read_tts(wav: bytes, seen: list[dict]):
    def urlopen(req: Request, timeout: object = None) -> _FakeResp | _FakeBytes:
        url = req.get_full_url()
        if url.endswith("/health"):
            return _FakeResp({"ok": True, "ready": True, "service": "helm-companion-tts"})
        if url.endswith("/v1/stop"):
            return _FakeResp({"ok": True})
        if url.endswith("/v1/speak"):
            body = json.loads((req.data or b"{}").decode("utf-8"))
            seen.append(body)
            if body.get("play_only"):
                return _FakeResp({"ok": True, "play": True}, status=202)
            return _FakeBytes(wav)
        raise AssertionError(url)

    return urlopen


def _fake_ollama(content: str = "晚上好，漂泊者。", wav: bytes | None = None):
    def urlopen(req: Request, timeout: object = None) -> _FakeResp | _FakeBytes:
        url = req.get_full_url()
        if url.endswith("/api/tags"):
            return _FakeResp({"models": [{"name": "qwen3.5:4b"}]})
        if url.endswith("/api/chat"):
            if _chat_is_stream(req):
                return _ndjson_chat(content)
            return _FakeResp({"message": {"content": content}})
        if url.endswith("/health"):
            return _FakeResp({"ok": True, "ready": True, "service": "helm-companion-tts"})
        if url.endswith("/v1/speak"):
            if wav is None:
                raise URLError("tts down")
            return _FakeBytes(wav)
        if url.endswith("/v1/stop"):
            return _FakeResp({"ok": True})
        raise AssertionError(url)

    return urlopen


class CompanionLogicTest(unittest.TestCase):
    def test_strips_think_and_action(self) -> None:
        raw = "<think>long</think>\n晚上好。\nACTION: lamp.on\n还早。"
        self.assertEqual(_clean_reply(raw), "晚上好。\n还早。")

    def test_skill_parse_and_infer(self) -> None:
        skills = [
            {"id": "lamp", "name": "台灯", "kind": "toggle"},
            {"id": "ac", "name": "空调", "kind": "toggle"},
            {"id": "wegame", "name": "无畏契约", "kind": "run"},
            {"id": "douyin", "name": "抖音", "kind": "run"},
        ]
        self.assertEqual(parse_action_lines("好。\nACTION: lamp.off\n"), [("lamp", False)])
        self.assertEqual(parse_action_lines("好。\nACTION: wegame.run\n"), [("wegame", True)])
        self.assertEqual(parse_bri_actions("好。\nACTION: lamp.bri.40\n"), [("lamp", {"value": 40})])
        self.assertEqual(resolve_skill("台灯", skills), "lamp")
        self.assertEqual(resolve_skill("打瓦", skills), "wegame")
        self.assertTrue(looks_like_control("关灯。"))
        self.assertFalse(looks_like_control("晚上好。"))
        self.assertEqual(infer_control("关灯。", skills), [("lamp", False)])
        hang = skills + [{"id": "m553687922", "name": "挂灯", "kind": "toggle"}]
        self.assertEqual(infer_control("关掉灯带。", hang), [])
        self.assertEqual(
            infer_control("关掉灯带。", hang + [{"id": "desk-led", "name": "灯带", "kind": "toggle"}]),
            [("desk-led", False)],
        )
        self.assertEqual(
            infer_control("关掉台灯。", hang + [{"id": "desk-led", "name": "灯带", "kind": "toggle"}]),
            [("lamp", False)],
        )
        self.assertEqual(fold_game_asr("他打开当代"), "他打开灯带")
        self.assertEqual(fold_game_asr("打开台灯"), "打开台灯")
        self.assertEqual(
            constrain_light_actions(
                "关掉灯带。",
                [("挂灯", False)],
                hang + [{"id": "desk-led", "name": "灯带", "kind": "toggle"}],
            ),
            [],
        )
        self.assertEqual(infer_control("打开空调。", skills), [("ac", True)])
        self.assertEqual(infer_control("关掉加湿器。", skills + [{"id": "m222", "name": "加湿器", "kind": "toggle"}]), [("m222", False)])
        self.assertEqual(infer_control("启动无畏契约。", skills), [("wegame", True)])
        self.assertEqual(infer_control("打瓦。", skills), [("wegame", True)])
        self.assertEqual(infer_control("我要打游戏", skills), [("wegame", True)])
        self.assertEqual(infer_control("帮我启动游戏", skills), [("wegame", True)])
        self.assertEqual(infer_control("我要打五位契约", skills), [("wegame", True)])
        self.assertEqual(infer_control("我要打我啊", skills), [("wegame", True)])
        self.assertEqual(infer_control("开黑。", skills), [("wegame", True)])
        self.assertEqual(infer_control("开黑。", skills, {"开黑": {"id": "wegame", "on": True}}), [("wegame", True)])
        self.assertTrue(looks_like_control("我要打游戏"))
        self.assertTrue(looks_like_control("我要打我啊"))
        self.assertEqual(resolve_skill("刷抖音", skills), "douyin")
        self.assertTrue(looks_like_control("打开抖音。"))
        self.assertEqual(infer_control("打开抖音。", skills), [("douyin", True)])
        self.assertEqual(infer_control("刷抖音。", skills), [("douyin", True)])
        self.assertEqual(infer_control("晚上好。", skills), [])
        self.assertFalse(looks_like_control("累了。"))
        self.assertEqual(query_kind("现在几点。"), "time")
        self.assertEqual(query_kind("屋里多少度。"), "temp")
        self.assertEqual(query_kind("电脑热不热。"), "pc")
        self.assertEqual(query_kind("内存占用。"), "pc")
        self.assertEqual(query_kind("电脑内存多少。"), "pc")
        self.assertEqual(query_kind("电脑温度。"), "pc")
        self.assertEqual(query_kind("电脑多少度。"), "pc")
        self.assertEqual(query_kind("我麦克咪妮的温度怎么样啊"), "mini")
        self.assertEqual(query_kind("Mac Mini 占用。"), "mini")
        self.assertEqual(query_kind("灯开了没。"), "light")
        self.assertEqual(query_kind("今天几号。"), "time")
        self.assertEqual(query_kind("星期几。"), "time")
        self.assertEqual(query_kind("在播什么。"), "media")
        self.assertEqual(query_kind("刚才你说什么。"), "last")
        self.assertIsNone(query_kind("帮我查一下基金。"))
        self.assertIsNone(query_kind("关灯。"))
        dim = skills + [{"id": "lamp", "name": "台灯", "kind": "toggle", "brightness": True}]
        self.assertEqual(infer_brightness("亮一点。", dim), [("lamp", {"delta": 15})])
        self.assertEqual(infer_brightness("调到四十。", dim), [("lamp", {"value": 40})])
        self.assertEqual(infer_brightness("把空调调到二十四度。", dim), [])
        climate = skills + [{"id": "ac", "name": "空调", "kind": "toggle", "temp": True, "temp_id": "temp", "temp_min": 16, "temp_max": 30}]
        self.assertTrue(looks_like_control("把空调调成二十四度"))
        self.assertIsNone(query_kind("把空调调成二十四度"))
        self.assertEqual(infer_temp("把空调调成二十四度。", climate), [("ac", {"value": 24})])
        self.assertEqual(infer_temp("空调二十五度。", climate), [("ac", {"value": 25})])
        self.assertEqual(infer_temp("把空调调到16度。", climate), [("ac", {"value": 16})])
        self.assertEqual(infer_temp("屋里多少度。", climate), [])
        self.assertEqual(parse_temp_actions("好。\nACTION: ac.temp.24\n"), [("ac", {"value": 24})])
        self.assertEqual(infer_media("下一首。"), "next")
        self.assertEqual(infer_media("暂停。"), "toggle")
        self.assertEqual(infer_volume("声音大一点。"), "up")
        self.assertEqual(infer_volume("静音。"), "mute")
        self.assertEqual(parse_timer("十分钟后叫我。"), 600)
        self.assertEqual(parse_timer("十秒后叫我。"), 10)
        self.assertEqual(speak_timer(600), "好，十分钟后叫你。")
        self.assertEqual(speak_media({"playing": False}), "现在没在放。")
        self.assertIn("夜曲", speak_media({"playing": True, "title": "夜曲"}))
        self.assertIn("漂泊者", recent_prompt([("晚上好。", "嗯。")]))
        self.assertFalse(looks_like_control("在播什么。"))
        self.assertTrue(looks_like_control("下一首。"))
        self.assertEqual(appraise_user("累了。"), "tired")
        self.assertEqual(appraise_user("晚上好。"), "greeting")
        self.assertEqual(cn_clock(8, 17), "八点十七")
        from datetime import datetime as dt

        noon = dt(2026, 9, 2, 12, 0)
        self.assertEqual(speak_time(noon, "quiet", "今天几号。"), "今天九月二号。")
        self.assertEqual(speak_time(noon, "quiet", "星期几。"), "今天星期三。")
        self.assertEqual(speak_time(noon, "quiet", "现在几点。"), "现在是十二点。")
        self.assertEqual(speak_pc({"online": True, "cpu": {"percent": 12}}), "电脑还不热。")
        self.assertEqual(
            speak_pc(
                {
                    "online": True,
                    "cpu": {"percent": 12},
                    "memory": {"percent": 41.0, "used_gb": 13.1, "total_gb": 32.0},
                },
                "内存占用。",
            ),
            "内存41%，用了13.1G。",
        )
        self.assertEqual(
            speak_pc({"online": True, "cpu": {"percent": 12}}, "内存占用。"),
            "内存我还没读到。",
        )
        self.assertEqual(
            speak_pc(
                {
                    "online": True,
                    "cpu": {"percent": 12, "temp_celsius": 48},
                    "gpu": {"percent": 5, "temp_celsius": 44},
                },
                "电脑温度。",
            ),
            "电脑现在CPU 48度，显卡 44度。",
        )
        self.assertEqual(
            speak_pc({"online": True, "cpu": {"percent": 12}}, "电脑多少度。"),
            "温度我还没读到。",
        )
        self.assertEqual(
            speak_mini(
                {"mini": {"pc": {"online": True, "cpu": {"temp_celsius": 35.1}}}},
                "我麦克咪妮的温度怎么样啊",
            ),
            "Mini 现在CPU 35度。",
        )
        self.assertEqual(speak_mini({}, "Mini 温度"), "Mini 那边还没报到。")
        self.assertIn("潮", speak_temp({"celsius": 23.5, "humidity": 80}, "潮不潮"))
        self.assertEqual(enforce_ask_budget("好。还要开灯吗？", False), "好。")
        self.assertEqual(enforce_ask_budget("今晚还开着吗？", False), "嗯。")
        self.assertEqual(enforce_ask_budget("还开着吗？", True), "还开着吗？")
        prompt = skill_prompt(dim)
        self.assertIn("台灯（lamp）", prompt)
        self.assertIn("无畏契约（wegame）", prompt)
        self.assertIn("ACTION: <id>.run", prompt)
        self.assertIn("ACTION: <id>.bri.<1-100>", prompt)

    def test_parse_teach_binds_existing_skill(self) -> None:
        skills = [{"id": "wegame", "name": "无畏契约", "kind": "run"}]
        spec = parse_teach("以后我说开黑就启动无畏契约。", skills)
        self.assertEqual(spec["phrase"], "开黑")
        self.assertEqual(spec["id"], "wegame")
        self.assertTrue(spec["on"])
        spec = parse_teach("创建一个技能，我说打瓦就打开无畏契约", skills)
        self.assertEqual(spec["phrase"], "打瓦")
        spec = parse_teach("你要记住打游戏就是打瓦", skills)
        self.assertEqual(spec["phrase"], "打游戏")
        self.assertEqual(spec["id"], "wegame")
        spec = parse_teach("我跟你说打游戏就是想打我啊记住了吗", skills)
        self.assertEqual(spec["phrase"], "打游戏")
        self.assertEqual(spec["id"], "wegame")
        self.assertIsNone(parse_teach("以后我说开黑就启动原神。", skills))
        self.assertIsNone(parse_teach("晚上好。", skills))
        self.assertIsNone(parse_teach("我要打游戏", skills))

    def test_messages_are_just_the_utterance(self) -> None:
        messages = build_messages("岸宝", "")
        self.assertEqual(messages[1]["content"], "岸宝")
        self.assertNotIn("当前状态", messages[0]["content"])
        self.assertNotIn("后台状态", messages[0]["content"])

    def test_facts_from_snapshot_skips_mijia(self) -> None:
        facts = facts_from_snapshot(
            {
                "temperature": {"celsius": 24.0, "humidity": 50},
                "pc": {
                    "online": True,
                    "cpu": {"percent": 10},
                    "memory": {"percent": 40},
                    "gpu": {"percent": 5},
                },
                "devices": [
                    {"type": "light", "name": "台灯", "on": True},
                    {"type": "action", "name": "Steam", "on": False},
                ],
            }
        )
        self.assertIn("CPU 10%", facts)
        self.assertNotIn("台灯", facts)
        self.assertNotIn("24.0", facts)
        self.assertNotIn("Steam", facts)

    def test_desk_facts_prompt_includes_cached_devices(self) -> None:
        text = desk_facts_prompt(
            {
                "windows": {
                    "pc": {
                        "online": True,
                        "cpu": {"percent": 12, "temp_celsius": 48},
                        "memory": {"percent": 40},
                        "gpu": {"percent": 5, "temp_celsius": 44},
                    },
                    "temperature": {"celsius": 24.0, "humidity": 50},
                    "devices": [
                        {"type": "light", "name": "台灯", "on": True, "brightness": 60},
                        {"type": "action", "name": "无畏契约", "on": False},
                    ],
                },
                "mini": {"pc": {"online": True, "cpu": {"percent": 8}}},
            }
        )
        self.assertIn("禁止提起", text)
        self.assertIn("Windows：CPU 12% 48°C", text)
        self.assertIn("Mac Mini：CPU 8%", text)
        self.assertIn("室温：24.0°C", text)
        self.assertIn("台灯：开 亮度60", text)
        self.assertIn("无畏契约：未在跑", text)
        self.assertEqual(desk_facts_prompt({}), "")

    def test_cut_sentences_keeps_tail(self) -> None:
        ready, rest = cut_sentences("晚上好。还")
        self.assertEqual(ready, ["晚上好。"])
        self.assertEqual(rest, "还")
        ready, rest = cut_sentences("嗯。好的。尾")
        self.assertEqual(ready, ["嗯。", "好的。"])
        self.assertEqual(rest, "尾")

    def test_clip_announce_keeps_three_sentences(self) -> None:
        self.assertEqual(clip_announce("灯关了。屋里安静。先这样。还有一句不要。"), "灯关了。屋里安静。先这样。")
        self.assertEqual(clip_announce("  好。  "), "好。")
        self.assertEqual(clip_announce(""), "")

    def test_estimate_read_seconds_uses_four_chars_per_sec(self) -> None:
        self.assertEqual(spoken_chars("  今晚 睡前。 "), 5)
        self.assertEqual(estimate_read_seconds(""), 0)
        self.assertEqual(estimate_read_seconds("啊"), 1)
        self.assertEqual(estimate_read_seconds("字" * 240), 60)

    def test_chunk_read_text_keeps_leftover_and_splits_long(self) -> None:
        self.assertEqual(chunk_read_text("晚上好。还早"), ["晚上好。", "还早"])
        long = "啊" * 250
        parts = chunk_read_text(long, limit=120)
        self.assertEqual("".join(parts), long)
        self.assertTrue(all(len(p) <= 120 for p in parts))

    def test_join_wavs_appends_pcm(self) -> None:
        a = _tiny_wav(frames=2400)
        b = _tiny_wav(frames=4800)
        joined = join_wavs([a, b])
        self.assertAlmostEqual(wav_duration_sec(joined), 0.3, places=3)

    def test_pack_read_chunks_merges_short_sentences(self) -> None:
        parts = [f"第{i}句。" for i in range(40)]
        packed = pack_read_chunks(parts, target=90, limit=160)
        self.assertGreater(len(parts), 20)
        self.assertLessEqual(len(packed), 8)
        self.assertEqual("".join(packed), "".join(parts))
        self.assertTrue(all(len(p) <= 160 for p in packed))
        self.assertEqual(pack_read_chunks(["。", "好。", "！"]), ["好。"])

    def test_pack_read_chunks_evens_out_a_short_then_long_sentence(self) -> None:
        text = (
            "他感觉像是经历了一场思维风暴和认知洗礼。"
            "这一刻，程乞将目光聚焦在【量子型梦洞捕捉仪】的介绍上，简单来说，"
            "它就是一种探测器，就像是金属探测器一般，一旦扫描到埋藏在的地下的金属，会发出对应的提示。"
        )
        packed = pack_read_chunks(chunk_read_text(text))
        self.assertEqual("".join(packed), text)
        self.assertGreater(len(packed), 1)
        self.assertTrue(all(len(part) <= 52 for part in packed))
        self.assertLess(max(len(part) for part in packed) - min(len(part) for part in packed), 16)
        self.assertNotIn("探测器，就像", packed[0][-12:])

    def test_pack_read_chunks_default_stays_near_clone(self) -> None:
        parts = [f"第{i}句。" for i in range(40)]
        packed = pack_read_chunks(parts)
        aggressive = pack_read_chunks(parts, target=90, limit=160)
        self.assertGreater(len(packed), len(aggressive))
        self.assertTrue(all(len(p) <= 52 for p in packed))
        self.assertEqual("".join(packed), "".join(parts))

    def test_announce_stays_quiet_when_phone_is_gone(self) -> None:
        self.assertEqual(unsolicited_block_reason(connected=True, game=False), None)
        self.assertEqual(unsolicited_block_reason(connected=False, game=False), "disconnected")
        self.assertEqual(unsolicited_block_reason(connected=True, game=True), "game")

    def test_parse_and_dump_companion(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "companion": {
                    "enabled": True,
                    "llm": {"base_url": "http://10.0.0.8:11434", "model": "qwen3.5:4b"},
                    "tts": {"base_url": "http://10.0.0.8:18100"},
                },
                "devices": [],
            }
        )
        self.assertTrue(cfg.companion.enabled)
        self.assertEqual(cfg.companion.llm_base_url, "http://10.0.0.8:11434")
        self.assertEqual(cfg.companion.tts_base_url, "http://10.0.0.8:18100")
        self.assertFalse(cfg.companion.tts_deliver)
        dumped = hub_config_to_raw(cfg)
        self.assertEqual(dumped["companion"]["llm"]["model"], "qwen3.5:4b")
        self.assertEqual(dumped["companion"]["tts"]["base_url"], "http://10.0.0.8:18100")
        self.assertNotIn("deliver", dumped["companion"]["tts"])
        self.assertNotIn("api_key", dumped["companion"]["llm"])

    def test_parse_mini_defaults(self) -> None:
        cfg = parse_config({"name": "study", "token": "secret-token-value", "devices": []})
        self.assertTrue(cfg.mini.enabled)
        self.assertEqual(cfg.mini.base_url, "http://10.83.22.121:17891")
        self.assertEqual(cfg.mini.pc_ms, 1000)
        self.assertEqual(cfg.mini.mijia_ms, 15000)
        dumped = hub_config_to_raw(cfg)
        self.assertEqual(dumped["mini"]["base_url"], "http://10.83.22.121:17891")
        off = parse_config(
            {"name": "study", "token": "secret-token-value", "devices": [], "mini": False}
        )
        self.assertFalse(off.mini.enabled)

    def test_parse_and_dump_llm_api_key(self) -> None:
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "companion": {
                    "enabled": True,
                    "llm": {
                        "base_url": "https://example.com/compatible-mode/v1",
                        "model": "qwen3.8-flash",
                        "api_key": "sk-test-placeholder",
                    },
                },
                "devices": [],
            }
        )
        self.assertEqual(cfg.companion.llm_api_key, "sk-test-placeholder")
        dumped = hub_config_to_raw(cfg)
        self.assertEqual(dumped["companion"]["llm"]["api_key"], "sk-test-placeholder")
        self.assertEqual(dumped["companion"]["llm"]["base_url"], "https://example.com/compatible-mode/v1")
        self.assertEqual(dumped["companion"]["llm"]["model"], "qwen3.8-flash")

    def test_join_keeps_compatible_mode_path(self) -> None:
        self.assertEqual(
            _join("https://host.example/compatible-mode/v1", "/chat/completions"),
            "https://host.example/compatible-mode/v1/chat/completions",
        )
        self.assertEqual(
            _join("https://host.example/compatible-mode/v1", "/models"),
            "https://host.example/compatible-mode/v1/models",
        )
        self.assertEqual(
            _join("http://127.0.0.1:11434", "/api/tags"),
            "http://127.0.0.1:11434/api/tags",
        )


class CompanionProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "companion": {
                    "enabled": True,
                    "llm": {
                        "base_url": "http://127.0.0.1:9",
                        "model": "qwen3.5:4b",
                        "timeout_sec": 2,
                    },
                },
                "devices": [],
                "mini": False,
            },
            path=Path(self.tmp.name) / "hub.yaml",
        )
        self.hub = DockHub(cfg)
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _request(self, method: str, path: str, body: dict | None = None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer secret-token-value",
        }
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, payload

    def _enable_streaming_companion(self) -> None:
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9"},
                    },
                    "devices": [],
                }
            ).companion
        )

    def _raw(self, method: str, path: str):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {
            "Accept": "*/*",
            "Authorization": "Bearer secret-token-value",
        }
        conn.request(method, path, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        content_type = resp.getheader("Content-Type")
        status = resp.status
        conn.close()
        return status, content_type, payload

    def test_snapshot_ready_false_when_ollama_down(self) -> None:
        status, body = self._request("GET", "/v1/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(body["companion"]["ready"], False)
        self.assertFalse(body["companion"]["voice"])
        self.assertTrue(self.hub.companion.k20_connected())

    def test_chat_502_when_ollama_down(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "companion_unavailable")

    def test_chat_empty_text(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "  "})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_announce_does_not_call_llm(self) -> None:
        status, body = self._request("POST", "/v1/companion/announce", {"text": "灯已经关了。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "灯已经关了。")
        self.assertIsNone(body["audio_id"])
        items = self.hub.chat_log.list()
        self.assertEqual(items[-1]["user"], "（微信）")
        self.assertEqual(items[-1]["reply"], "灯已经关了。")

    def test_announce_empty_text(self) -> None:
        status, body = self._request("POST", "/v1/companion/announce", {"text": "  "})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_chat_ok_strips_action(self) -> None:
        fake = _fake_ollama("晚上好，漂泊者。\nACTION: lamp.on")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好，漂泊者。")
        self.assertIsNone(body["audio_id"])
        self.assertNotIn("ACTION", body["text"])
        items = self.hub.chat_log.list()
        self.assertEqual(items[-1]["user"], "晚上好。")
        self.assertEqual(items[-1]["reply"], "晚上好，漂泊者。")

    def test_chat_runs_lamp_off(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("好。\nACTION: lamp.off")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "关灯。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好。")
        self.assertEqual(seen, [("lamp", False)])

    def test_chat_infers_ac_without_action(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "ac", "name": "空调"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "开空调。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好。")
        self.assertEqual(seen, [("ac", True)])

    def test_chat_sets_ac_temp_without_llm(self) -> None:
        seen: list[dict[str, object]] = []

        def run(ident: str, on: bool, brightness: int | None = None, props: dict | None = None) -> None:
            seen.append({"id": ident, "on": on, "brightness": brightness, "props": props})

        self.hub.companion.set_skills(
            [
                {
                    "id": "ac",
                    "name": "空调",
                    "kind": "toggle",
                    "temp": True,
                    "temp_id": "temp",
                    "temp_min": 16,
                    "temp_max": 30,
                }
            ],
            run,
        )

        def boom(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("setting AC temp must not call LLM")

        with patch("dock_hub.companion.urllib.request.urlopen", boom):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "把空调调成二十四度"})
        self.assertEqual(status, 200)
        self.assertEqual(seen, [{"id": "ac", "on": True, "brightness": None, "props": {"temp": 24}}])
        self.assertIn("二十四", body["text"])

    def test_chat_launches_valorant(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "wegame", "name": "无畏契约", "kind": "run"}],
            lambda ident, on: seen.append((ident, on)),
        )
        def boom(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("launching a game must not call the LLM")

        with patch("dock_hub.companion.urllib.request.urlopen", boom):
            for utterance in (
                "启动无畏契约。",
                "打开无畏契约",
                "我要打瓦",
                "我要打游戏",
                "我要打五位契约",
                "我要打我啊",
            ):
                seen.clear()
                status, body = self._request(
                    "POST", "/v1/companion/chat", {"text": utterance}
                )
                self.assertEqual(status, 200, utterance)
                self.assertEqual(body["text"], "好，开无畏契约。", utterance)
                self.assertEqual(seen, [("wegame", True)], utterance)

    def test_chat_teaches_alias(self) -> None:
        seen: list[tuple[str, bool]] = []
        path = Path(self.tmp.name) / "companion-aliases.yaml"
        self.hub.companion.set_skills(
            [{"id": "wegame", "name": "无畏契约", "kind": "run"}],
            lambda ident, on: seen.append((ident, on)),
            path,
        )
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request(
                "POST",
                "/v1/companion/chat",
                {"text": "以后我说开黑就启动无畏契约。"},
            )
        self.assertEqual(status, 200)
        self.assertIn("开黑", body["text"])
        self.assertEqual(seen, [])
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request(
                "POST",
                "/v1/companion/chat",
                {"text": "记住打游戏就是打瓦"},
            )
        self.assertEqual(status, 200)
        self.assertIn("打游戏", body["text"])
        self.assertEqual(seen, [])
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "开黑。"})
        self.assertEqual(status, 200)
        self.assertEqual(seen, [("wegame", True)])

    def test_chat_cannot_invent_program(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "wegame", "name": "无畏契约", "kind": "run"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request(
                "POST",
                "/v1/companion/chat",
                {"text": "以后我说开黑就启动原神。"},
            )
        self.assertEqual(status, 200)
        self.assertIn("配好", body["text"])
        self.assertEqual(seen, [])

    def test_chat_ignores_action_on_greeting(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("晚上好，漂泊者。\nACTION: lamp.on")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好，漂泊者。")
        self.assertEqual(seen, [])

    def test_chat_does_not_snapshot(self) -> None:
        def boom() -> dict:
            raise AssertionError("chat must not pull mijia snapshot")

        self.hub.snapshot = boom  # type: ignore[method-assign]
        fake = _fake_ollama("晚上好，漂泊者。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好，漂泊者。")

    def test_chat_injects_cached_desk_not_live_snapshot(self) -> None:
        def boom() -> dict:
            raise AssertionError("chat must not pull mijia snapshot")

        self.hub.snapshot = boom  # type: ignore[method-assign]
        self.hub.reporter.remember(
            {
                "windows": {
                    "pc": {"online": True, "cpu": {"percent": 12, "temp_celsius": 48}},
                    "temperature": {"celsius": 24.0, "humidity": 50},
                    "devices": [
                        {"type": "light", "name": "台灯", "on": True, "brightness": 60}
                    ],
                },
                "mini": {"pc": {"online": True, "cpu": {"percent": 8}}},
            }
        )
        self.hub.companion._world["desk"] = self.hub.reporter.snapshot
        captured: list[list] = []

        def urlopen(req: Request, timeout: object = None):
            url = req.get_full_url()
            if url.endswith("/api/tags"):
                return _FakeResp({"models": [{"name": "qwen3.5:4b"}]})
            if url.endswith("/api/chat"):
                raw = json.loads((req.data or b"{}").decode("utf-8"))
                captured.append(raw.get("messages") or [])
                if _chat_is_stream(req):
                    return _ndjson_chat("晚上好，漂泊者。")
                return _FakeResp({"message": {"content": "晚上好，漂泊者。"}})
            if url.endswith("/v1/stop"):
                return _FakeResp({"ok": True})
            raise AssertionError(url)

        with patch("dock_hub.companion.urllib.request.urlopen", urlopen):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好，漂泊者。")
        system = captured[0][0]["content"]
        self.assertIn("禁止提起", system)
        self.assertIn("台灯：开 亮度60", system)
        self.assertIn("Windows：CPU 12% 48°C", system)
        self.assertIn("Mac Mini：CPU 8%", system)
        self.assertEqual(captured[0][1]["content"], "晚上好。")

    def test_chat_answers_time_without_llm(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "现在几点。"})
        self.assertEqual(status, 200)
        self.assertRegex(body["text"], r"(现在是|已经).+点")

    def test_chat_answers_date_without_llm(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "今天几号。"})
        self.assertEqual(status, 200)
        self.assertIn("号", body["text"])
        self.assertNotIn("点", body["text"])

    def test_chat_answers_pc(self) -> None:
        self.hub.companion.set_skills(
            [],
            lambda ident, on: None,
            world={"pc": lambda: {"online": True, "cpu": {"percent": 88}}},
        )
        status, body = self._request("POST", "/v1/companion/chat", {"text": "电脑热不热。"})
        self.assertEqual(status, 200)
        self.assertIn("忙", body["text"])

    def test_chat_answers_memory_without_saying_pc(self) -> None:
        self.hub.companion.set_skills(
            [],
            lambda ident, on: None,
            world={
                "pc": lambda: {
                    "online": True,
                    "cpu": {"percent": 12},
                    "memory": {"percent": 41.0, "used_gb": 13.1, "total_gb": 32.0},
                }
            },
        )
        status, body = self._request("POST", "/v1/companion/chat", {"text": "内存占用。"})
        self.assertEqual(status, 200)
        self.assertIn("内存", body["text"])
        self.assertIn("13.1", body["text"])
        self.assertNotIn("热", body["text"])

    def test_chat_strips_question_on_command(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("好。还要开空调吗？")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "关灯。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好。")
        self.assertEqual(seen, [("lamp", False)])

    def test_chat_answers_now_playing(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "在播什么。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "现在没在放。")

    def test_chat_skips_to_next_track(self) -> None:
        sent: list[str] = []
        self.hub.media._send = sent.append  # type: ignore[method-assign]
        status, body = self._request("POST", "/v1/companion/chat", {"text": "下一首。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好，下一首。")
        self.assertEqual(sent, ["next"])

    def test_chat_sets_timer_without_llm(self) -> None:
        with patch("dock_hub.companion.threading.Timer") as mocked:
            timer = mocked.return_value
            status, body = self._request("POST", "/v1/companion/chat", {"text": "十秒后叫我。"})
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args.args[0], 10)
            timer.start.assert_called_once()
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好，10秒后叫你。")
        self.hub.companion._cancel_timer()

    def test_chat_keeps_recent_turns(self) -> None:
        captured: list[list] = []

        def urlopen(req: Request, timeout: object = None):
            url = req.get_full_url()
            if url.endswith("/api/tags"):
                return _FakeResp({"models": [{"name": "qwen3.5:4b"}]})
            if url.endswith("/api/chat"):
                raw = json.loads((req.data or b"{}").decode("utf-8"))
                captured.append(raw.get("messages") or [])
                if _chat_is_stream(req):
                    return _ndjson_chat("嗯。岸边很静。")
                return _FakeResp({"message": {"content": "嗯。岸边很静。"}})
            if url.endswith("/v1/stop"):
                return _FakeResp({"ok": True})
            raise AssertionError(url)

        fake = _fake_ollama("晚上好，漂泊者。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        with patch("dock_hub.companion.urllib.request.urlopen", urlopen):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "你还在吗。"})
        self.assertEqual(status, 200)
        system = captured[0][0]["content"]
        self.assertIn("最近对话", system)
        self.assertIn("晚上好。", system)
        self.assertIn("晚上好，漂泊者。", system)

    def test_chat_recalls_last_reply(self) -> None:
        fake = _fake_ollama("晚上好，漂泊者。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        status, body = self._request("POST", "/v1/companion/chat", {"text": "刚才你说什么。"})
        self.assertEqual(status, 200)
        self.assertIn("晚上好，漂泊者。", body["text"])

    def test_chat_volume_up(self) -> None:
        sent: list[str] = []
        self.hub.media._send = sent.append  # type: ignore[method-assign]
        status, body = self._request("POST", "/v1/companion/chat", {"text": "声音大一点。"})
        self.assertEqual(status, 200)
        self.assertIn("大声", body["text"])
        self.assertEqual(sent, ["up"])

    def test_chat_noise_on(self) -> None:
        seen: list[dict] = []

        def noise(name: str, minutes: int | None = None) -> dict:
            seen.append({"name": name, "minutes": minutes})
            return {
                "ok": True,
                "playing": True,
                "name": name,
                "minutes": minutes or 30,
                "seconds_left": (minutes or 30) * 60,
            }

        self.hub.companion.tts.noise = noise  # type: ignore[method-assign]
        status, body = self._request("POST", "/v1/companion/chat", {"text": "放点雨声。"})
        self.assertEqual(status, 200)
        self.assertIn("雨声", body["text"])
        self.assertIn("分钟后停", body["text"])
        self.assertEqual(seen, [{"name": "rain", "minutes": None}])

    def test_chat_noise_on_for_a_while(self) -> None:
        seen: list[int | None] = []

        def noise(name: str, minutes: int | None = None) -> dict:
            seen.append(minutes)
            return {"ok": True, "playing": True, "name": name, "minutes": 45}

        self.hub.companion.tts.noise = noise  # type: ignore[method-assign]
        status, body = self._request("POST", "/v1/companion/chat", {"text": "放点雨声 45 分钟。"})
        self.assertEqual(status, 200)
        self.assertEqual(seen, [45])

    def test_chat_noise_off(self) -> None:
        self.hub.companion.tts.noise = lambda name, minutes=None: {  # type: ignore[method-assign]
            "ok": True,
            "playing": False,
            "name": None,
            "minutes": 0,
            "seconds_left": 0,
        }
        status, body = self._request("POST", "/v1/companion/chat", {"text": "把白噪音关了。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "关掉了。")

    def test_chat_noise_fails_when_tts_absent(self) -> None:
        # 测试的 hub 没配 TTS：放不上就说没动成，不假装放上了
        status, body = self._request("POST", "/v1/companion/chat", {"text": "放点雨声。"})
        self.assertEqual(status, 200)
        self.assertIn("没动成", body["text"])

    def test_chat_music_on(self) -> None:
        self.hub.companion.tts.music = lambda action="on", minutes=None: {  # type: ignore[method-assign]
            "ok": True, "playing": True, "track": "intro", "minutes": minutes or 60, "seconds_left": 3600,
        }
        status, body = self._request("POST", "/v1/companion/chat", {"text": "放点音乐。"})
        self.assertEqual(status, 200)
        self.assertIn("放歌了", body["text"])
        self.assertIn("分钟后停", body["text"])

    def test_chat_music_off(self) -> None:
        self.hub.companion.tts.music = lambda action="on", minutes=None: {  # type: ignore[method-assign]
            "ok": True, "playing": False, "track": None, "minutes": 0, "seconds_left": 0,
        }
        status, body = self._request("POST", "/v1/companion/chat", {"text": "把音乐停了。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "停了。")

    def test_chat_music_empty_shelf_says_so(self) -> None:
        # 目录里没歌不是链路坏了：她照实说，不说「没动成」
        self.hub.companion.tts.music = lambda action="on", minutes=None: {  # type: ignore[method-assign]
            "ok": False, "error": "empty",
        }
        status, body = self._request("POST", "/v1/companion/chat", {"text": "放点音乐。"})
        self.assertEqual(status, 200)
        self.assertIn("还没有能放的音乐", body["text"])

    def test_chat_music_fails_when_tts_absent(self) -> None:
        status, body = self._request("POST", "/v1/companion/chat", {"text": "放点音乐。"})
        self.assertEqual(status, 200)
        self.assertIn("没动成", body["text"])

    def test_chat_answers_temperature_from_cache(self) -> None:
        self.hub._last_temp = {"celsius": 23.5, "humidity": 80}
        status, body = self._request("POST", "/v1/companion/chat", {"text": "屋里多少度。"})
        self.assertEqual(status, 200)
        self.assertIn("23.5度", body["text"])

    def test_chat_answers_light_state(self) -> None:
        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯", "kind": "toggle"}],
            lambda ident, on: None,
            world={"device": lambda ident: {"on": True, "brightness": 40}},
        )
        status, body = self._request("POST", "/v1/companion/chat", {"text": "台灯亮着吗。"})
        self.assertEqual(status, 200)
        self.assertIn("亮着", body["text"])

    def test_chat_brightens_lamp(self) -> None:
        seen: list[tuple[str, bool, int | None]] = []

        def run(ident: str, on: bool, brightness: int | None = None) -> None:
            seen.append((ident, on, brightness))

        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯", "kind": "toggle", "brightness": True}],
            run,
            world={"device": lambda ident: {"on": True, "brightness": 40}},
        )
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "亮一点。"})
        self.assertEqual(status, 200)
        self.assertEqual(seen, [("lamp", True, 55)])

    def test_chat_tired_is_not_a_switch(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("先坐一会儿。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "累了。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "先坐一会儿。")
        self.assertEqual(seen, [])

    def test_chat_failed_skill_does_not_say_ok(self) -> None:
        def boom(ident: str, on: bool, brightness: int | None = None) -> None:
            raise HubError("offline", "down")

        self.hub.companion.set_skills(
            [{"id": "lamp", "name": "台灯"}],
            boom,
        )
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "关灯。"})
        self.assertEqual(status, 200)
        self.assertIn("没动成", body["text"])

    def test_chat_with_tts_returns_audio(self) -> None:
        wav = b"RIFF....WAVE"
        fake = _fake_ollama("晚上好，漂泊者。", wav=wav)
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9", "timeout_sec": 2, "deliver": True},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
            self.assertEqual(status, 200)
            self.assertEqual(body["text"], "晚上好，漂泊者。")
            audio_id = body["audio_id"]
            self.assertIsInstance(audio_id, str)
            self.assertTrue(audio_id)
            snap_status, snap = self._request("GET", "/v1/snapshot")
            self.assertEqual(snap_status, 200)
            self.assertTrue(snap["companion"]["voice"])
            wav_status, ctype, payload = self._raw("GET", f"/v1/companion/audio/{audio_id}")
        self.assertEqual(wav_status, 200)
        self.assertEqual(ctype, "audio/wav")
        self.assertEqual(payload, wav)

    def test_chat_uses_turn_id_as_audio_id(self) -> None:
        wav = b"RIFF....WAVE"
        fake = _fake_ollama("晚上好，漂泊者。", wav=wav)
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9", "timeout_sec": 2, "deliver": True},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request(
                "POST",
                "/v1/companion/chat",
                {"text": "晚上好。", "turn_id": "lat12ab34cd"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["audio_id"], "lat12ab34cd")

    def test_chat_tts_cues_mini_without_phone_wav(self) -> None:
        wav = b"RIFF....WAVE"
        fake = _fake_ollama("晚上好，漂泊者。", wav=wav)
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9", "timeout_sec": 2},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request(
                "POST",
                "/v1/companion/chat",
                {"text": "晚上好。", "turn_id": "lat12ab34cd"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好，漂泊者。")
        self.assertIsNone(body["audio_id"])
        wav_status, _, _ = self._raw("GET", "/v1/companion/audio/lat12ab34cd")
        self.assertEqual(wav_status, 404)

    def test_unknown_audio_is_404(self) -> None:
        status, body = self._request("GET", "/v1/companion/audio/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_bad_audio_id_is_404(self) -> None:
        status, body = self._request("GET", "/v1/companion/audio/../x")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_tts_down_still_returns_text(self) -> None:
        fake = _fake_ollama("岸边很安静。")
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9"},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "岸边很安静。")
        self.assertIsNone(body["audio_id"])

    def test_stop_ok_without_tts(self) -> None:
        status, body = self._request("POST", "/v1/companion/stop", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_stop_calls_mini_tts(self) -> None:
        seen: list[str] = []

        def fake(req: Request, timeout: object = None):
            url = req.get_full_url()
            seen.append(url)
            if url.endswith("/v1/stop"):
                return _FakeResp({"ok": True})
            raise AssertionError(url)

        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9"},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/stop", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(any(url.endswith("/v1/stop") for url in seen))

    def test_stop_skips_tts_after_slow_llm(self) -> None:
        gate = threading.Event()
        seen: list[str] = []

        def fake(req: Request, timeout: object = None):
            url = req.get_full_url()
            seen.append(url)
            if url.endswith("/api/tags") or url.endswith("/health"):
                return _FakeResp({"ok": True, "ready": True, "models": []})
            if url.endswith("/v1/stop"):
                return _FakeResp({"ok": True})
            if url.endswith("/api/chat"):
                gate.wait(2)
                return _ndjson_chat("晚上好，漂泊者。")
            if url.endswith("/v1/speak"):
                raise AssertionError("interrupted chat should not speak")
            raise AssertionError(url)

        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9"},
                    },
                    "devices": [],
                }
            ).companion
        )
        result: dict = {}

        def run_chat() -> None:
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
            result["status"] = status
            result["body"] = body

        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            worker = threading.Thread(target=run_chat)
            worker.start()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and not any(url.endswith("/api/chat") for url in seen):
                time.sleep(0.02)
            stop_status, stop_body = self._request("POST", "/v1/companion/stop", {})
            self.assertEqual(stop_status, 200)
            self.assertTrue(stop_body["ok"])
            gate.set()
            worker.join(timeout=3)
        self.assertEqual(result.get("status"), 200)
        # 被打断的这轮不再回文本：客户端已经放弃这条请求，回半句只会让旧字幕盖上来
        self.assertEqual(result["body"]["text"], "")
        self.assertIsNone(result["body"]["audio_id"])
        self.assertFalse(any(url.endswith("/v1/speak") for url in seen))

    def test_new_chat_cancels_the_one_still_streaming(self) -> None:
        """安卓重试 / 连着说两句：旧的那条流要立刻收手，别再 cue TTS、别再进记忆。"""
        streaming = threading.Event()
        release = threading.Event()
        spoken: list[str] = []
        chats: list[int] = []

        def fake(req: Request, timeout: object = None):
            url = req.get_full_url()
            if url.endswith("/api/tags") or url.endswith("/health") or url.endswith("/v1/stop"):
                return _FakeResp({"ok": True, "ready": True, "models": []})
            if url.endswith("/api/chat"):
                chats.append(1)
                if len(chats) == 1:
                    streaming.set()
                    return _GatedLineResp(
                        [
                            json.dumps({"message": {"content": "晚上好。"}, "done": False}) + "\n",
                            json.dumps({"message": {"content": "还早。"}, "done": False}) + "\n",
                            json.dumps({"done": True}) + "\n",
                        ],
                        gate=release,
                    )
                release.set()
                return _ndjson_chat("好。")
            if url.endswith("/v1/speak"):
                spoken.append(str(json.loads((req.data or b"{}").decode("utf-8")).get("text")))
                return _FakeBytes(b"RIFF")
            raise AssertionError(url)

        self._enable_streaming_companion()
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            first: dict = {}

            def slow_chat() -> None:
                first["status"], first["body"] = self._request(
                    "POST", "/v1/companion/chat", {"text": "在吗。"}
                )

            worker = threading.Thread(target=slow_chat, daemon=True)
            worker.start()
            self.assertTrue(streaming.wait(5), "第一条流没开始")
            status, body = self._request("POST", "/v1/companion/chat", {"text": "算了，别说了。"})
            worker.join(5)

        self.assertFalse(worker.is_alive(), "旧的那条流没有立刻收手")
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好。")
        # 被顶掉的那轮不能再开口，也不能拿到半句字幕；只有新一轮说了话
        self.assertEqual(first["status"], 200)
        self.assertEqual(first["body"]["text"], "")
        self.assertEqual(spoken, ["好。"])

    def test_stream_cues_each_sentence(self) -> None:
        spoken: list[str] = []

        def fake(req: Request, timeout: object = None):
            url = req.get_full_url()
            if url.endswith("/api/tags") or url.endswith("/health") or url.endswith("/v1/stop"):
                return _FakeResp({"ok": True, "ready": True, "models": []})
            if url.endswith("/api/chat"):
                blob = (
                    json.dumps({"message": {"content": "晚上好。"}, "done": False})
                    + "\n"
                    + json.dumps({"message": {"content": "还早。"}, "done": False})
                    + "\n"
                    + json.dumps({"done": True})
                    + "\n"
                )
                return _FakeLineResp(blob.encode("utf-8"))
            if url.endswith("/v1/speak"):
                body = json.loads((req.data or b"{}").decode("utf-8"))
                spoken.append(str(body.get("text") or ""))
                return _FakeBytes(b"RIFF")
            raise AssertionError(url)

        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9"},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "在吗。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好。还早。")
        self.assertEqual(spoken, ["晚上好。", "还早。"])

    def test_openai_sse_chat(self) -> None:
        seen: list[str] = []
        payloads: list[dict] = []

        def fake(req: Request, timeout: object = None):
            url = req.get_full_url()
            seen.append(url)
            if url.endswith("/models") or url.endswith("/health") or url.endswith("/v1/stop"):
                return _FakeResp({"ok": True, "ready": True, "data": []})
            if url.endswith("/chat/completions"):
                raw = req.data or b"{}"
                payloads.append(json.loads(raw.decode("utf-8")))
                blob = (
                    'data: {"choices":[{"delta":{"content":"晚上好，漂泊者。"}}]}\n'
                    "\n"
                    "data: [DONE]\n"
                )
                return _FakeLineResp(blob.encode("utf-8"))
            if url.endswith("/v1/speak"):
                return _FakeBytes(b"RIFF")
            raise AssertionError(url)

        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "https://example.com/compatible-mode/v1",
                            "model": "qwen3.8-flash",
                            "api_key": "sk-test-placeholder",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9"},
                    },
                    "devices": [],
                }
            ).companion
        )
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "晚上好。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "晚上好，漂泊者。")
        self.assertTrue(any(url.endswith("/compatible-mode/v1/chat/completions") for url in seen))
        self.assertTrue(payloads)
        self.assertFalse(payloads[0].get("enable_thinking"))
        self.assertNotIn("reasoning", payloads[0])
        self.assertEqual(payloads[0].get("model"), "qwen3.8-flash")

    def test_openrouter_requests_minimal_reasoning(self) -> None:
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "https://openrouter.ai/api/v1",
                            "model": "openrouter/free",
                            "api_key": "sk-test-placeholder",
                            "timeout_sec": 2,
                        },
                    },
                    "devices": [],
                }
            ).companion
        )
        payload = json.loads(
            self.hub.companion._chat_payload(
                [{"role": "user", "content": "在吗"}], stream=True
            )
        )
        self.assertEqual(payload.get("reasoning"), {"effort": "minimal"})
        self.assertFalse(payload.get("enable_thinking"))

    def _enable_tts(self) -> None:
        self.hub.companion.configure(
            parse_config(
                {
                    "name": "study",
                    "token": "secret-token-value",
                    "companion": {
                        "enabled": True,
                        "llm": {
                            "base_url": "http://127.0.0.1:9",
                            "model": "qwen3.5:4b",
                            "timeout_sec": 2,
                        },
                        "tts": {"base_url": "http://127.0.0.1:9", "timeout_sec": 2},
                    },
                    "devices": [],
                },
                path=Path(self.tmp.name) / "hub.yaml",
            ).companion
        )

    def test_read_empty_text_is_400(self) -> None:
        status, body = self._request("POST", "/v1/companion/read", {"text": "  ", "mode": "play"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_read_too_long_is_400(self) -> None:
        status, body = self._request(
            "POST",
            "/v1/companion/read",
            {"text": "字" * (MAX_READ_CHARS + 1), "mode": "play"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_read_play_cues_mini_without_file(self) -> None:
        self._enable_tts()
        seen: list[dict] = []
        with patch("dock_hub.companion.urllib.request.urlopen", _fake_read_tts(_tiny_wav(), seen)):
            status, body = self._request(
                "POST",
                "/v1/companion/read",
                {"text": "今晚睡前。先听这一段。", "mode": "play"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["mode"], "play")
            self.assertIn(body["status"], {"queued", "running", "done"})
            job = self._wait_read_job(body["id"])
        self.assertEqual(job["status"], "done")
        self.assertIsNone(job["audio_id"])
        self.assertEqual(job["estimate_sec"], 3)
        self.assertTrue(any(item.get("play_only") for item in seen))
        self.assertFalse(any(item.get("play") is False for item in seen))

    def _wait_read_job(self, job_id: str, timeout: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            status, body = self._request("GET", "/v1/companion/read/jobs")
            self.assertEqual(status, 200)
            items = body["items"]
            match = next((item for item in items if item["id"] == job_id), None)
            self.assertIsNotNone(match)
            assert match is not None
            last = match
            if match["status"] == "done":
                return match
            if match["status"] == "error":
                self.fail(match.get("error") or "read job error")
            time.sleep(0.04)
        self.fail(f"read job not done: {last}")

    def test_read_file_saves_wav_and_downloads(self) -> None:
        self._enable_tts()
        seen: list[dict] = []
        wav = _tiny_wav(frames=2400)
        with patch("dock_hub.companion.urllib.request.urlopen", _fake_read_tts(wav, seen)):
            status, body = self._request(
                "POST",
                "/v1/companion/read",
                {"text": "今晚睡前。先听这一段。", "mode": "file"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["mode"], "file")
            self.assertIn(body["status"], {"queued", "running", "done"})
            job_id = body["id"]
            self.assertTrue(job_id)
            job = self._wait_read_job(job_id)
            audio_id = job["audio_id"]
            self.assertTrue(audio_id)
            self.assertEqual(job["format"], "audio/wav")
            self.assertAlmostEqual(job["duration_sec"], 0.1, places=3)
            self.assertTrue(any(item.get("play") is False for item in seen))
            self.assertFalse(any(item.get("play_only") for item in seen))
            saved = Path(self.tmp.name) / "reads" / f"{audio_id}.wav"
            self.assertTrue(saved.is_file())
            wav_status, ctype, payload = self._raw("GET", f"/v1/companion/audio/{audio_id}?download=1")
        self.assertEqual(wav_status, 200)
        self.assertEqual(ctype, "audio/wav")
        self.assertEqual(payload, wav)
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "GET",
            f"/v1/companion/audio/{audio_id}?download=1",
            headers={"Authorization": "Bearer secret-token-value"},
        )
        resp = conn.getresponse()
        disp = resp.getheader("Content-Disposition") or ""
        resp.read()
        conn.close()
        self.assertIn("attachment", disp)
        self.assertIn(".wav", disp)

    def test_read_jobs_empty_then_lists_play(self) -> None:
        self._enable_tts()
        status, body = self._request("GET", "/v1/companion/read/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"], [])
        with patch("dock_hub.companion.urllib.request.urlopen", _fake_read_tts(_tiny_wav(), [])):
            status, posted = self._request(
                "POST",
                "/v1/companion/read",
                {"text": "今晚睡前。", "mode": "play"},
            )
        self.assertEqual(status, 200)
        status, body = self._request("GET", "/v1/companion/read/jobs")
        self.assertEqual(status, 200)
        job = self._wait_read_job(posted["id"])
        self.assertEqual(job["id"], posted["id"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["mode"], "play")
        self.assertNotIn("text", job)

    def test_read_job_cancel_delete_and_replay(self) -> None:
        self._enable_tts()
        seen: list[dict] = []
        with patch("dock_hub.companion.urllib.request.urlopen", _fake_read_tts(_tiny_wav(), seen)):
            status, posted = self._request(
                "POST",
                "/v1/companion/read",
                {"text": "今晚睡前。先听这一段。", "mode": "file"},
            )
            self.assertEqual(status, 200)
            job = self._wait_read_job(posted["id"])
            audio_id = job["audio_id"]
            self.assertTrue(audio_id)
            status, replayed = self._request(
                "POST", f"/v1/companion/read/jobs/{posted['id']}/replay", {}
            )
            self.assertEqual(status, 200)
            self.assertEqual(replayed["mode"], "play")
            play_job = self._wait_read_job(replayed["id"])
            self.assertEqual(play_job["status"], "done")
            status, cancelled = self._request(
                "POST", f"/v1/companion/read/jobs/{posted['id']}/cancel", {}
            )
            self.assertEqual(status, 200)
            self.assertEqual(cancelled["status"], "done")
            status, deleted = self._request(
                "POST", f"/v1/companion/read/jobs/{posted['id']}/delete", {}
            )
            self.assertEqual(status, 200)
            self.assertTrue(deleted["ok"])
            status, body = self._request("GET", "/v1/companion/read/jobs")
            self.assertEqual(status, 200)
            self.assertFalse(any(item["id"] == posted["id"] for item in body["items"]))
            saved = Path(self.tmp.name) / "reads" / f"{audio_id}.wav"
            self.assertFalse(saved.exists())


if __name__ == "__main__":
    unittest.main()
