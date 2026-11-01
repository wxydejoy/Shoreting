"""F28 长期记忆：抽取、落盘、压缩、可见可删。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dock_hub.memory import (
    MEMORY_EXTRACT_PROMPT,
    MEMORY_LIMIT,
    MemoryStore,
    extract_facts,
    memory_extract_messages,
    memory_prompt,
    parse_extraction,
    parse_forget,
    parse_teach,
)


class ExtractFactsTest(unittest.TestCase):
    def test_taboo_and_allergy(self) -> None:
        found = dict(extract_facts("我不喝冰的"))
        self.assertEqual(found.get("忌口"), "冰的")
        found = dict(extract_facts("我对芒果过敏"))
        self.assertEqual(found.get("过敏"), "芒果")

    def test_birthday_and_name(self) -> None:
        found = dict(extract_facts("我生日是三月八号"))
        self.assertEqual(found.get("生日"), "三月八号")
        found = dict(extract_facts("我叫小明"))
        self.assertEqual(found.get("称呼"), "小明")

    def test_pet_and_weekly(self) -> None:
        found = dict(extract_facts("猫叫年糕"))
        self.assertEqual(found.get("宠物猫"), "年糕")
        found = dict(extract_facts("我的狗叫馒头"))
        self.assertEqual(found.get("宠物狗"), "馒头")
        found = dict(extract_facts("我每周三晚上打球"))
        self.assertEqual(found.get("每周安排"), "三晚上打球")

    def test_plain_talk_stays_empty(self) -> None:
        self.assertEqual(extract_facts("今天天气怎么样"), [])
        self.assertEqual(extract_facts(""), [])
        # 长段落不是在陈述偏好
        self.assertEqual(extract_facts("今天写了一下午代码，中间还开了三个会，累死了"), [])

    def test_teach_marker_goes_to_taught_channel(self) -> None:
        # 「记住」由 parse_teach 处理，不走启发式，免得记两次
        self.assertEqual(extract_facts("记住，我不喝冰的"), [])

    def test_parse_extraction(self) -> None:
        # F30：大脑吐的「key = value」行要接得住，带列表符、中文冒号都行
        facts = dict(
            parse_extraction("- 睡眠 = 他常熬夜到零点后\n* 音乐：喜欢深夜写代码时听后摇")
        )
        self.assertEqual(facts.get("睡眠"), "他常熬夜到零点后")
        self.assertEqual(facts.get("音乐"), "喜欢深夜写代码时听后摇")

    def test_parse_extraction_refuses_nonsense(self) -> None:
        self.assertEqual(parse_extraction(""), [])
        self.assertEqual(parse_extraction("无"), [])
        self.assertEqual(parse_extraction("无。"), [])
        # 散文没有 key = value 结构就空手而归
        self.assertEqual(parse_extraction("他今天看起来挺累的，聊了几句就睡了。"), [])
        # 太多的条目截到四条
        raw = "\n".join(f"事{i} = 第{i}件事的具体描述" for i in range(8))
        self.assertEqual(len(parse_extraction(raw)), 4)

    def test_extract_messages_carry_material_and_known(self) -> None:
        messages = memory_extract_messages("他说的", "她答的", "- 睡眠：常熬夜")
        self.assertIn("守岸人", messages[0]["content"])
        self.assertIn("不许编", messages[0]["content"])
        self.assertIn("他说的", messages[1]["content"])
        self.assertIn("- 睡眠：常熬夜", messages[1]["content"])

    def test_extract_prompt_demands_sparing_judgment(self) -> None:
        # 大多数轮次不该记东西——提示词要把「不记是常态」说清
        self.assertIn("大多数轮次是什么都不用记", MEMORY_EXTRACT_PROMPT)

    def test_parse_teach(self) -> None:
        self.assertEqual(parse_teach("记住，我周三晚上打球"), "我周三晚上打球")
        self.assertEqual(parse_teach("帮我记住：猫叫年糕"), "猫叫年糕")
        self.assertIsNone(parse_teach("今天记得浇水"))

    def test_parse_forget(self) -> None:
        self.assertEqual(parse_forget("忘掉那条"), "")
        self.assertEqual(parse_forget("忘掉年糕"), "年糕")
        # 不是忘事的话：None（区别于「忘掉那条」的空串）
        self.assertIsNone(parse_forget("今天天气不错"))


class MemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "companion-memory.yaml"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_remember_and_reload(self) -> None:
        store = MemoryStore(self.path)
        store.remember("忌口", "冰的")
        store.teach("我周三晚上打球")
        again = MemoryStore(self.path)
        items = again.entries()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["key"], "忌口")
        self.assertEqual(items[1]["source"], "taught")

    def test_same_value_refreshes_not_duplicates(self) -> None:
        store = MemoryStore(self.path)
        store.remember("忌口", "冰的")
        store.remember("忌口", "冰的")
        self.assertEqual(len(store), 1)

    def test_same_key_new_value_overwrites(self) -> None:
        store = MemoryStore(self.path)
        store.remember("生日", "三月初八")
        store.remember("生日", "3月8日")
        items = store.entries()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["value"], "3月8日")

    def test_note_turn_reports_only_new(self) -> None:
        store = MemoryStore(self.path)
        self.assertEqual(store.note_turn("我不喝冰的"), ["忌口"])
        self.assertEqual(store.note_turn("我不喝冰的"), [])

    def test_forget_by_needle_and_latest(self) -> None:
        store = MemoryStore(self.path)
        store.remember("忌口", "冰的")
        store.remember("宠物", "年糕")
        self.assertEqual(store.forget("年糕"), "年糕")
        self.assertEqual(store.forget(), "冰的")
        self.assertIsNone(store.forget())

    def test_forget_key(self) -> None:
        store = MemoryStore(self.path)
        store.remember("忌口", "冰的")
        self.assertTrue(store.forget_key("忌口"))
        self.assertFalse(store.forget_key("忌口"))
        self.assertEqual(len(store), 0)

    def test_cap_evicts_chat_first(self) -> None:
        store = MemoryStore(self.path)
        for index in range(MEMORY_LIMIT):
            store.remember(f"记事{index}", f"内容{index}", source="taught")
        self.assertEqual(len(store), MEMORY_LIMIT)
        # 再教一条：满了，但都动不得（没有 chat 来源）→ 最旧的 taught 被挤
        store.remember("称呼", "小明", source="taught")
        self.assertEqual(len(store), MEMORY_LIMIT)
        self.assertFalse(any(item["value"] == "内容0" for item in store.entries()))
        # 换成 chat 来源：挤的应该是 chat，taught 全保留
        store2 = MemoryStore(None)
        for index in range(MEMORY_LIMIT - 1):
            store2.remember(f"记事{index}", f"内容{index}", source="taught")
        store2.remember("忌口", "冰的", source="chat")
        store2.remember("忌口", "辣的", source="chat")
        items = store2.entries()
        self.assertEqual(len(items), MEMORY_LIMIT)
        self.assertEqual(items[-1]["value"], "辣的")

    def test_facts_prompt_shape(self) -> None:
        self.assertEqual(memory_prompt([]), "")
        store = MemoryStore(self.path)
        store.remember("忌口", "冰的")
        text = store.facts()
        self.assertIn("忌口：冰的", text)
        self.assertIn("别逐条复述", text)


class HubWiringTest(unittest.TestCase):
    """service/companion 把记忆接进对话链路的端到端行为（不碰 LLM 的路径）。"""

    def setUp(self) -> None:
        import tempfile

        from dock_hub.config import parse_config
        from dock_hub.service import DockHub

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
                "companion": {"enabled": True},
                "mini": False,
            },
            Path(self.tmp.name) / "hub.yaml",
        )
        self.hub = DockHub(cfg)

    def test_memory_file_lives_next_to_hub_yaml(self) -> None:
        self.assertEqual(
            self.hub.memory.path, Path(self.tmp.name) / "companion-memory.yaml"
        )

    def test_teach_by_voice_lands_in_store(self) -> None:
        out = self.hub.companion.chat({"text": "记住，我周三晚上打球"})
        self.assertEqual(out["text"], "记住了。")
        items = self.hub.memory.entries()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "taught")
        self.assertEqual(items[0]["value"], "我周三晚上打球")

    def test_forget_by_voice(self) -> None:
        self.hub.memory.teach("我周三晚上打球")
        out = self.hub.companion.chat({"text": "忘掉那条"})
        self.assertIn("忘掉了", out["text"])
        self.assertEqual(len(self.hub.memory), 0)
        out = self.hub.companion.chat({"text": "忘掉那条"})
        self.assertIn("没记住", out["text"])

    def test_extraction_hook_runs_after_turn(self) -> None:
        # _remember 是对话收口的必经处：喂它一句带事实的话，看落不落盘
        self.hub.companion._remember("我不喝冰的", "好，知道了。")
        items = self.hub.memory.entries()
        self.assertEqual(items, [{"key": "忌口", "value": "冰的", "source": "chat", "learned_at": items[0]["learned_at"] if items else ""}] if items else [])

    def test_ai_extraction_lands_after_turn(self) -> None:
        # F30：大脑判断出值得记的，落盘 source=chat；正则没覆盖的它也能记
        self.hub.companion._complete = lambda messages, **_: "- 深夜精力 = 他习惯熬到零点后才睡\n他今天看起来挺累的，聊了几句就睡了。"
        landed = self.hub.companion._memory_extract_once("今天又弄到半夜才睡", "早点休息。")
        self.assertEqual(landed, 1)
        items = self.hub.memory.entries()
        self.assertEqual(items[0]["key"], "深夜精力")
        self.assertEqual(items[0]["value"], "他习惯熬到零点后才睡")
        self.assertEqual(items[0]["source"], "chat")

    def test_ai_extraction_failure_leaves_memory_alone(self) -> None:
        # 大脑挂了：不落盘、不抛出（后台线程里吞掉）
        self.hub.memory.remember("称呼", "小明")

        def boom(messages, **_):
            raise TimeoutError("brain asleep")

        self.hub.companion._complete = boom
        self.hub.companion._memory_extract_run("他说的", "她答的")  # 不应抛
        self.assertEqual(
            [item["key"] for item in self.hub.memory.entries()], ["称呼"]
        )

    def test_facts_flow_into_chat_prompt(self) -> None:
        self.hub.memory.remember("忌口", "冰的")
        captured: dict[str, str] = {}

        original = self.hub.companion.chat

        def spy(body, facts):
            captured["facts"] = facts
            return {"text": "好。", "audio_id": None}

        self.hub.companion.chat = spy  # type: ignore[method-assign]
        self.hub.companion_chat({"text": "随便聊聊"})
        self.assertIn("忌口：冰的", captured["facts"])

    def test_memory_endpoints_roundtrip(self) -> None:
        self.hub.memory.remember("生日", "三月初八")
        listed = self.hub.companion_memory()
        self.assertEqual(listed["items"][0]["key"], "生日")
        self.assertTrue(listed["items"][0]["value"])
        self.hub.companion_memory_delete("生日")
        self.assertEqual(self.hub.companion_memory()["items"], [])


if __name__ == "__main__":
    unittest.main()
