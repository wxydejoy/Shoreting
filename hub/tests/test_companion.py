from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

from pathlib import Path

from dock_hub.companion import (
    Companion,
    _clean_reply,
    _join,
    appraise_user,
    build_messages,
    clip_announce,
    cn_clock,
    cut_sentences,
    enforce_ask_budget,
    facts_from_snapshot,
    desk_facts_prompt,
    infer_brightness,
    infer_control,
    infer_media,
    infer_volume,
    looks_like_control,
    nudge_block_reason,
    nudge_line,
    parse_bri_actions,
    parse_teach,
    parse_action_lines,
    parse_timer,
    query_kind,
    recent_prompt,
    resolve_skill,
    skill_prompt,
    speak_media,
    speak_pc,
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
        self.assertEqual(infer_control("打开空调。", skills), [("ac", True)])
        self.assertEqual(infer_control("关掉加湿器。", skills + [{"id": "m222", "name": "加湿器", "kind": "toggle"}]), [("m222", False)])
        self.assertEqual(infer_control("启动无畏契约。", skills), [("wegame", True)])
        self.assertEqual(infer_control("打瓦。", skills), [("wegame", True)])
        self.assertEqual(infer_control("开黑。", skills, {"开黑": {"id": "wegame", "on": True}}), [("wegame", True)])
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
        self.assertIsNone(parse_teach("以后我说开黑就启动原神。", skills))
        self.assertIsNone(parse_teach("晚上好。", skills))

    def test_messages_are_just_the_utterance(self) -> None:
        messages = build_messages("岸宝", "")
        self.assertEqual(messages[1]["content"], "岸宝")
        self.assertNotIn("台灯", messages[0]["content"])
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

    def test_nudge_never_when_game_offline_or_disconnected(self) -> None:
        tz = timezone(timedelta(hours=8))
        now = datetime(2026, 9, 2, 23, 40, tzinfo=tz)
        ok = dict(
            connected=True,
            game=False,
            hour=23,
            self_tag="quiet",
            speaking=False,
            media_playing=False,
            last_nudge=None,
            last_user=None,
            nudge_count=0,
            nudge_day=None,
            now=now,
        )
        self.assertIsNone(nudge_block_reason(**ok))
        self.assertEqual(unsolicited_block_reason(connected=True, game=False), None)
        self.assertEqual(nudge_block_reason(**{**ok, "connected": False}), "disconnected")
        self.assertEqual(nudge_block_reason(**{**ok, "game": True}), "game")
        self.assertEqual(nudge_block_reason(**{**ok, "hour": 15}), "hour")
        self.assertEqual(nudge_block_reason(**{**ok, "self_tag": "distant"}), "mood")
        self.assertEqual(nudge_block_reason(**{**ok, "speaking": True}), "speaking")
        self.assertEqual(nudge_block_reason(**{**ok, "media_playing": True}), "media")
        self.assertEqual(
            nudge_block_reason(**{**ok, "last_user": now - timedelta(minutes=10)}),
            "recent",
        )
        self.assertEqual(
            nudge_block_reason(**{**ok, "nudge_count": 2, "nudge_day": "2026-09-02"}),
            "cap",
        )
        self.assertEqual(
            nudge_block_reason(**{**ok, "last_nudge": now - timedelta(minutes=30)}),
            "cooldown",
        )
        self.assertEqual(nudge_line("concerned"), "夜已经深了。")
        self.assertNotIn("？", nudge_line("concerned"))
        self.assertNotIn("？", nudge_line("quiet"))

    def test_maybe_nudge_stays_silent_until_hub_says_yes(self) -> None:
        tz = timezone(timedelta(hours=8))
        late = datetime(2026, 9, 2, 23, 40, tzinfo=tz)
        companion = Companion(CompanionConfig(enabled=True))
        companion._world = {
            "pc": lambda: {"online": False},
            "game": lambda: False,
        }
        companion._mood = {"user": "calm", "self": "quiet", "last_ask": False}
        self.assertIsNone(companion._maybe_nudge(late))
        companion.note_presence()
        self.assertEqual(companion._maybe_nudge(late), "该歇了。")
        self.assertIsNone(companion._maybe_nudge(late))
        companion._mood["last_nudge_at"] = None
        companion._mood["nudge_count"] = 0
        companion._world["game"] = lambda: True
        self.assertIsNone(companion._maybe_nudge(late))
        companion._world["game"] = lambda: False
        companion._world["pc"] = lambda: {"online": False}
        self.assertEqual(companion._maybe_nudge(late), "该歇了。")

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

    def test_nudge_ignores_phone_until_snapshot(self) -> None:
        self.assertFalse(self.hub.companion.k20_connected())
        late = datetime(2026, 9, 2, 1, 10, tzinfo=timezone(timedelta(hours=8)))
        self.hub.companion._mood = {
            "user": "calm",
            "self": "concerned",
            "last_ask": False,
        }
        self.hub.companion._world = {
            **self.hub.companion._world,
            "game": lambda: False,
        }
        self.assertIsNone(self.hub.companion._maybe_nudge(late))
        self._request("GET", "/v1/snapshot")
        self.assertEqual(self.hub.companion._maybe_nudge(late), "夜已经深了。")

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

    def test_chat_launches_valorant(self) -> None:
        seen: list[tuple[str, bool]] = []
        self.hub.companion.set_skills(
            [{"id": "wegame", "name": "无畏契约", "kind": "run"}],
            lambda ident, on: seen.append((ident, on)),
        )
        fake = _fake_ollama("好。")
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            status, body = self._request("POST", "/v1/companion/chat", {"text": "启动无畏契约。"})
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "好。")
        self.assertEqual(seen, [("wegame", True)])

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
        self.assertEqual(result["body"]["text"], "晚上好，漂泊者。")
        self.assertIsNone(result["body"]["audio_id"])
        self.assertFalse(any(url.endswith("/v1/speak") for url in seen))

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
        self.assertEqual(payloads[0].get("model"), "qwen3.8-flash")

    def test_tethys_session_key_header(self) -> None:
        seen: list[Request] = []

        def fake(req: Request, timeout: object = None) -> _FakeResp:
            seen.append(req)
            if req.get_full_url().endswith("/chat/completions"):
                return _FakeResp({"choices": [{"message": {"content": "结论。"}}]})
            raise AssertionError(req.get_full_url())

        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            text = self.hub.companion._complete_endpoint(
                [{"role": "user", "content": "hi"}],
                base_url="http://127.0.0.1:8642/v1",
                model="hermes-agent",
                api_key="tethys-key",
                timeout_sec=2,
                num_predict=64,
                extra_headers={"X-Hermes-Session-Key": "tethys:wanderer"},
            )
        self.assertEqual(text, "结论。")
        sent = {key.lower(): value for key, value in seen[0].header_items()}
        self.assertEqual(sent.get("x-hermes-session-key"), "tethys:wanderer")
        self.assertEqual(sent.get("authorization"), "Bearer tethys-key")

    def test_notify_weixin_posts_full_result(self) -> None:
        posted: list[tuple[str, str]] = []

        def fake(req: Request, timeout: object = None) -> _FakeResp:
            posted.append((req.get_full_url(), (req.data or b"").decode("utf-8")))
            return _FakeResp({"ok": True})

        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "companion": {
                    "enabled": True,
                    "llm": {"base_url": "http://127.0.0.1:9", "model": "qwen3.5:4b"},
                    "tethys": {
                        "enabled": True,
                        "base_url": "http://10.0.0.8:8642/v1",
                        "api_key": "tethys-key",
                        "notify_url": "http://10.0.0.8:8643/v1/weixin",
                    },
                },
                "devices": [],
            }
        ).companion
        self.hub.companion.configure(cfg)
        assert cfg.tethys is not None
        with patch("dock_hub.companion.urllib.request.urlopen", fake):
            self.hub.companion._notify_weixin("查基金", "涨了。", cfg.tethys)
        self.assertEqual(posted[0][0], "http://10.0.0.8:8643/v1/weixin")
        self.assertIn("书桌任务：查基金", posted[0][1])
        self.assertIn("涨了。", posted[0][1])


if __name__ == "__main__":
    unittest.main()
