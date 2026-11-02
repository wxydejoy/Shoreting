"""llm.py：拼提示词、读回复。

提示词拼错不会报错，只会让守岸人答得不对，所以这里验的是内容：人设进得去、
书桌状态进得去、没问的东西不该出现。回复解析验的则是两种流式格式和
「<think> 不许出现在字幕里」。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from dock_hub.companion_llm import LLM_RETRY_AFTER_CAP, retry_delay, with_retry
from dock_hub.config import parse_config
from dock_hub.errors import HubError
from dock_hub.llm import (
    DEFAULT_PERSONA,
    _clean_reply,
    _join,
    _ollama_chunk,
    _openai_delta_content,
    _openai_message_content,
    _openrouter,
    build_messages,
    desk_facts_prompt,
    facts_from_snapshot,
    openai_mode,
)


class BuildMessagesTest(unittest.TestCase):
    def test_default_persona_is_used_when_none_given(self) -> None:
        messages = build_messages("晚上好。", "")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], DEFAULT_PERSONA.strip())
        self.assertEqual(messages[1], {"role": "user", "content": "晚上好。"})

    def test_persona_tolerates_mixed_speech(self) -> None:
        # F6：中英混着说、带口音，她听意思不挑发音
        self.assertIn("中英混着说", DEFAULT_PERSONA)

    def test_persona_has_no_unfillable_time_template(self) -> None:
        # 「拿到的是几时之前的信息」这种模板填不上（模型不知道几时），
        # 曾经被它原样抄出来当回复。人设里不许再出现没有数据来源的占位话术
        self.assertNotIn("几时之前", DEFAULT_PERSONA)
        # 预测/建议类题必须给实质内容，不许拿实时数据躲
        self.assertIn("不许拿「没有实时数据」躲", DEFAULT_PERSONA)
        # 现在的时间由 chat 的 facts 注入（companion_flow），人设只说怎么用
        self.assertIn("现在的时间", DEFAULT_PERSONA)

    def test_custom_persona_wins(self) -> None:
        messages = build_messages("在吗", "", persona="你是别的助手。")
        self.assertEqual(messages[0]["content"], "你是别的助手。")

    def test_facts_are_appended_to_the_system_prompt(self) -> None:
        messages = build_messages("灯开着吗", "台灯：开 亮度60")
        self.assertIn("台灯：开 亮度60", messages[0]["content"])
        # 事实不进用户消息，免得被当成指令
        self.assertEqual(messages[1]["content"], "灯开着吗")


class FactsTest(unittest.TestCase):
    def snapshot(self) -> dict:
        return {
            "temperature": {"celsius": 23.4, "humidity": 55},
            "pc": {"online": True, "cpu": {"percent": 12, "temp_celsius": 48}, "memory": {"percent": 61}},
            "devices": [
                {"id": "lamp", "name": "台灯", "on": True, "brightness": 60},
                {"id": "steam", "name": "Steam", "type": "action", "on": False},
            ],
        }

    def test_facts_from_snapshot_is_pc_only(self) -> None:
        # 对话路径只塞电脑占用：室温、灯的开关都不往里放，免得她没被问就聊起来
        text = facts_from_snapshot(self.snapshot())
        self.assertIn("CPU 12%", text)
        self.assertIn("内存 61%", text)
        self.assertNotIn("23.4", text)
        self.assertNotIn("台灯", text)

    def test_desk_facts_prompt_adds_the_do_not_bring_it_up_rule(self) -> None:
        desk = {"windows": self.snapshot(), "mini": {"pc": {"online": True, "cpu": {"percent": 8}}}}
        text = desk_facts_prompt(desk)
        self.assertIn("禁止提起", text)
        self.assertIn("Windows", text)
        self.assertIn("Mac Mini", text)

    def test_empty_desk_gives_nothing(self) -> None:
        self.assertEqual(desk_facts_prompt({}), "")
        self.assertEqual(desk_facts_prompt(None), "")


class CleanReplyTest(unittest.TestCase):
    def test_think_block_is_removed(self) -> None:
        raw = "<think>先想一下</think>灯我关了。\n好。"
        self.assertEqual(_clean_reply(raw), "灯我关了。\n好。")

    def test_action_line_is_removed_from_the_subtitle(self) -> None:
        raw = "好。\nACTION: lamp.off\n"
        self.assertEqual(_clean_reply(raw), "好。")

    def test_blank_runs_collapse(self) -> None:
        self.assertEqual(_clean_reply("一。\n\n\n二。"), "一。\n二。")


class StreamParsingTest(unittest.TestCase):
    def test_ollama_shape(self) -> None:
        self.assertEqual(_ollama_chunk({"message": {"content": "在。"}}), "在。")
        self.assertEqual(_ollama_chunk({"message": {}}), "")
        self.assertEqual(_ollama_chunk({}), "")

    def test_openai_delta_shape(self) -> None:
        self.assertEqual(_openai_delta_content({"choices": [{"delta": {"content": "嗯"}}]}), "嗯")
        # 首个 chunk 只有 role，没有 content
        self.assertEqual(_openai_delta_content({"choices": [{"delta": {"role": "assistant"}}]}), "")
        self.assertEqual(_openai_delta_content({"choices": []}), "")

    def test_openai_full_message(self) -> None:
        body = {"choices": [{"message": {"content": "灯关了。"}}]}
        self.assertEqual(_openai_message_content(body), "灯关了。")

    def test_malformed_body_raises(self) -> None:
        for body in ({}, {"choices": []}, {"choices": [{}]}, {"choices": ["x"]}):
            with self.assertRaises(HubError):
                _openai_message_content(body)

    def test_join(self) -> None:
        self.assertEqual(_join("http://127.0.0.1:11434/", "/api/chat"), "http://127.0.0.1:11434/api/chat")


class ModeTest(unittest.TestCase):
    def config(self, **llm):
        return parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "companion": {"enabled": True, "llm": llm},
            }
        ).companion

    def test_api_key_means_openai_compatible(self) -> None:
        self.assertTrue(openai_mode(self.config(base_url="http://127.0.0.1:11434", api_key="sk-x")))

    def test_v1_path_means_openai_compatible(self) -> None:
        self.assertTrue(openai_mode(self.config(base_url="https://gw.example.com/v1")))

    def test_plain_ollama_is_not(self) -> None:
        self.assertFalse(openai_mode(self.config(base_url="http://127.0.0.1:11434")))

    def test_openrouter_detection(self) -> None:
        self.assertTrue(_openrouter("https://openrouter.ai/api/v1"))
        self.assertFalse(_openrouter("http://127.0.0.1:11434"))
        self.assertFalse(_openrouter(None))


class IndependenceTest(unittest.TestCase):
    def test_llm_does_not_drag_in_the_companion(self) -> None:
        code = "import sys, dock_hub.llm; print('dock_hub.companion' in sys.modules)"
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


class LlmRetryTest(unittest.TestCase):
    """429/5xx 的退避重试：一次限流不该让整句话丢掉。"""

    def test_retries_rate_limit_then_succeeds(self) -> None:
        calls: list[int] = []
        sleeps: list[float] = []

        def attempt() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise HubError("companion_unavailable", "大脑拒绝请求：429", retriable=True)
            return "好。"

        out = with_retry(attempt, delays=(0.2, 0.7), sleep=sleeps.append)
        self.assertEqual(out, "好。")
        self.assertEqual(sleeps, [0.2])
        self.assertEqual(len(calls), 2)

    def test_retry_after_header_wins(self) -> None:
        # 上游说了等 2 秒就等 2 秒，别按我们自己的节奏撞第二下
        sleeps: list[float] = []
        state = {"n": 0}

        def attempt() -> str:
            state["n"] += 1
            if state["n"] == 1:
                raise HubError(
                    "companion_unavailable",
                    "大脑拒绝请求：429",
                    headers={"Retry-After": "2"},
                    retriable=True,
                )
            return "好。"

        with_retry(attempt, delays=(0.2,), sleep=sleeps.append)
        self.assertEqual(sleeps, [2.0])

    def test_retry_after_is_capped(self) -> None:
        self.assertEqual(
            retry_delay(
                HubError("companion_unavailable", "x", headers={"Retry-After": "90"}),
                0.5,
            ),
            LLM_RETRY_AFTER_CAP,
        )

    def test_non_retriable_error_raises_at_once(self) -> None:
        calls: list[int] = []

        def attempt() -> str:
            calls.append(1)
            raise HubError("companion_unavailable", "大脑拒绝请求：403", retriable=False)

        with self.assertRaises(HubError):
            with_retry(attempt, delays=(0.2,), sleep=lambda _s: None)
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_all_delays(self) -> None:
        calls: list[int] = []

        def attempt() -> str:
            calls.append(1)
            raise HubError("companion_unavailable", "大脑拒绝请求：503", retriable=True)

        with self.assertRaises(HubError):
            with_retry(attempt, delays=(0.1, 0.2), sleep=lambda _s: None)
        self.assertEqual(len(calls), 3)  # 首发 + 两趟重试


if __name__ == "__main__":
    unittest.main()
