"""F29 守岸人日记：材料、到点、落盘、兜底，都用假时钟验，不真等 21:30。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.diary import (
    DayMaterial,
    DiaryStore,
    DiaryWriter,
    IMPRESSION_KEY,
    IMPRESSION_MAX,
    build_material,
    clean_entry,
    clean_impression,
    diary_due,
    diary_messages,
    fallback_text,
    impression_messages,
    render_material,
)
from dock_hub.weekly import DailyLedger


def _at(hour: int, minute: int = 0, day: date | None = None) -> datetime:
    day = day or date(2026, 10, 31)
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


def _material(**overrides) -> DayMaterial:
    return build_material(
        now=_at(21, 30),
        ledger_row=overrides.get("ledger_row", {"desk_sec": 5 * 3600, "chats": 12}),
        occasions=overrides.get("occasions", []),
        wallpaper=overrides.get("wallpaper", "书在老地方。"),
        said=overrides.get("said", ["帮我开一下灯"]),
    )


class BuildMaterialTest(unittest.TestCase):
    def test_row_numbers_become_minutes(self) -> None:
        m = build_material(now=_at(21, 30), ledger_row={"desk_sec": 7200, "read_sec": 1500, "chats": 8, "boots": 2})
        self.assertEqual(m.desk_min, 120)
        self.assertEqual(m.read_min, 25)
        self.assertEqual(m.chats, 8)
        self.assertEqual(m.boots, 2)
        self.assertEqual(m.weekday, "周六")

    def test_said_clips_dedupes_and_caps(self) -> None:
        said = ["开灯", "开灯", "x", "这句特别长" * 10, "念书吧", "雨声", "晚安", "几点了", "谢谢"]
        m = build_material(now=_at(21), said=said)
        self.assertEqual(m.said, ["开灯", "念书吧", "雨声", "晚安", "几点了", "谢谢"])

    def test_empty_day_not_worth_writing(self) -> None:
        m = build_material(now=_at(21))
        self.assertFalse(m.worth_writing())
        self.assertTrue(_material().worth_writing())
        self.assertTrue(build_material(now=_at(21), said=["有人在吗"]).worth_writing())

    def test_renders_only_what_it_has(self) -> None:
        text = render_material(_material(occasions=["生日"]))
        self.assertIn("2026-10-31", text)
        self.assertIn("在桌：约 300 分钟", text)
        self.assertIn("说了 12 句话", text)
        self.assertIn("今天特别：生日", text)
        self.assertIn("书在老地方。", text)
        self.assertIn("帮我开一下灯", text)
        # 没有的部分别占行：没有朗读就别提朗读
        self.assertNotIn("朗读", text)


class FallbackTextTest(unittest.TestCase):
    def test_reads_the_numbers(self) -> None:
        text = fallback_text(_material())
        self.assertIn("5 小时", text)
        self.assertIn("12 句", text)

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(fallback_text(build_material(now=_at(21))), "")


class CleanEntryTest(unittest.TestCase):
    def test_strips_wrappers_and_newlines(self) -> None:
        self.assertEqual(clean_entry("「今天读完了。\n灯我留着。」"), "今天读完了。 灯我留着。")
        self.assertEqual(clean_entry('<think>嗯</think>雨停了。'), "雨停了。")

    def test_long_is_clipped(self) -> None:
        self.assertEqual(len(clean_entry("字" * 500)), 400)


class DiaryDueTest(unittest.TestCase):
    def test_not_before_at(self) -> None:
        self.assertIsNone(diary_due(_at(21, 29), "21:30", None))

    def test_due_inside_window(self) -> None:
        self.assertEqual(diary_due(_at(21, 30), "21:30", None), "2026-10-31")
        self.assertEqual(diary_due(_at(23, 0), "21:30", None), "2026-10-31")

    def test_late_is_dropped_not_deferred(self) -> None:
        # 过了窗口（凌晨补昨天）不算：丢弃，不补发——新的一天还没到点，不该写
        self.assertIsNone(diary_due(_at(0, 30), "21:30", None))
        self.assertIsNone(diary_due(_at(2, 0, day=date(2026, 11, 1)), "21:30", "2026-10-31"))

    def test_written_day_short_circuits(self) -> None:
        self.assertIsNone(diary_due(_at(22), "21:30", "2026-10-31"))


class DiaryStoreTest(unittest.TestCase):
    def _store(self, tmp: str) -> DiaryStore:
        return DiaryStore(Path(tmp) / "companion-diary.jsonl")

    def test_write_list_replace_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.write("2026-10-30", "昨天的一段。", "llm", "2026-10-30T21:31:00+08:00")
            store.write("2026-10-31", "今天的一段。", "llm", "2026-10-31T21:31:00+08:00")
            store.write("2026-10-31", "今天重写的一段。", "log", "2026-10-31T21:40:00+08:00")
            self.assertEqual(len(store), 2)
            days = [item["date"] for item in store.list()]
            self.assertEqual(days, ["2026-10-31", "2026-10-30"])  # 新→旧
            self.assertEqual(store.get("2026-10-31")["text"], "今天重写的一段。")
            self.assertTrue(store.delete("2026-10-30"))
            self.assertFalse(store.delete("2026-10-30"))
            self.assertIsNone(store.get("2026-10-30"))

    def test_reloads_from_disk_and_skips_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion-diary.jsonl"
            path.write_text(
                '{"date":"2026-10-30","text":"旧的一段","source":"llm","written_at":"x"}\n'
                "这不是JSON\n"
                '{"date":"不是日期","text":"坏行"}\n',
                encoding="utf-8",
            )
            store = DiaryStore(path)
            self.assertEqual([item["text"] for item in store.list()], ["旧的一段"])

    def test_old_entries_pruned_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion-diary.jsonl"
            old = "2020-01-01"
            path.write_text(
                '{"date":"' + old + '","text":"太老了","source":"llm","written_at":"x"}\n'
                '{"date":"2026-10-31","text":"新的","source":"llm","written_at":"x"}\n',
                encoding="utf-8",
            )
            store = DiaryStore(path)
            self.assertEqual([item["date"] for item in store.list()], ["2026-10-31"])

    def test_empty_text_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.write("2026-10-31", "   ", "llm", "x")
            self.assertEqual(len(store), 0)


class DiaryWriterTest(unittest.TestCase):
    def _writer(self, tmp: str, material, compose) -> DiaryWriter:
        return DiaryWriter(DiaryStore(Path(tmp) / "d.jsonl"), material, compose)

    def test_writes_once_at_the_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            writer = self._writer(
                tmp, _material, lambda m: (calls.append(m.day), "灯留了一盏，人走了。")[1]
            )
            self.assertIsNone(writer.tick(_at(21, 29)))
            self.assertEqual(len(calls), 0)
            self.assertEqual(writer.tick(_at(21, 30)), "灯留了一盏，人走了。")
            entry = writer.store.get("2026-10-31")
            self.assertEqual(entry["source"], "llm")
            # 同一天再 tick 不重写：日记一天一段
            self.assertIsNone(writer.tick(_at(21, 45)))
            self.assertEqual(len(calls), 1)

    def test_brain_down_falls_back_to_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:

            def boom(m: DayMaterial) -> str:
                raise RuntimeError("大脑不在")

            writer = self._writer(tmp, _material, boom)
            text = writer.tick(_at(21, 30))
            self.assertIsNotNone(text)
            self.assertIn("5 小时", text)
            self.assertEqual(writer.store.get("2026-10-31")["source"], "log")

    def test_empty_day_marked_done_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = self._writer(
                tmp, lambda: build_material(now=_at(21)), lambda m: "不该被叫到"
            )
            self.assertIsNone(writer.tick(_at(21, 30)))
            self.assertEqual(len(writer.store), 0)
            # 这天到此为止：材料后来变了也不补写（丢弃，不补发）
            self.assertIsNone(writer.tick(_at(21, 50)))

    def test_disabled_never_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = self._writer(tmp, _material, lambda m: "x")
            writer.configure(False, "21:30")
            self.assertIsNone(writer.tick(_at(21, 30)))
            self.assertEqual(len(writer.store), 0)

    def test_messages_carry_the_house_rules(self) -> None:
        messages = diary_messages("材料")
        self.assertIn("两句话以内", messages[0]["content"])
        self.assertIn("材料", messages[1]["content"])

    def test_after_runs_with_same_material_once_written(self) -> None:
        # F41：日记真写了，印象更新才跟着来；空材料的日子不叫
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[DayMaterial] = []
            writer = DiaryWriter(
                DiaryStore(Path(tmp) / "d.jsonl"),
                _material,
                lambda m: "灯留了一盏，人走了。",
                after=seen.append,
            )
            writer.tick(_at(21, 30))
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].day, "2026-10-31")

    def test_after_not_called_on_empty_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[DayMaterial] = []
            writer = DiaryWriter(
                DiaryStore(Path(tmp) / "d.jsonl"),
                lambda: build_material(now=_at(21)),
                lambda m: "不该被叫到",
                after=seen.append,
            )
            writer.tick(_at(21, 30))
            self.assertEqual(seen, [])

    def test_after_failure_does_not_hurt_diary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def boom(m: DayMaterial) -> None:
                raise RuntimeError("印象挂了")

            writer = DiaryWriter(
                DiaryStore(Path(tmp) / "d.jsonl"), _material, lambda m: "灯留了一盏。", after=boom
            )
            self.assertEqual(writer.tick(_at(21, 30)), "灯留了一盏。")
            self.assertIsNotNone(writer.store.get("2026-10-31"))


class ImpressionTest(unittest.TestCase):
    def test_messages_carry_current_impression_and_rules(self) -> None:
        messages = impression_messages("在桌 5 小时", "他今天话不多。")
        self.assertIn("不许编", messages[0]["content"])
        self.assertIn("原样照抄", messages[0]["content"])
        self.assertIn("他今天话不多。", messages[1]["content"])
        self.assertIn("在桌 5 小时", messages[1]["content"])

    def test_messages_without_current(self) -> None:
        messages = impression_messages("在桌 5 小时", "")
        self.assertNotIn("你现在的印象", messages[1]["content"])

    def test_clean_impression_strips_and_caps(self) -> None:
        self.assertEqual(clean_impression("「他今晚聊了股票。」"), "他今晚聊了股票。")
        self.assertEqual(clean_impression("印象：他忙。"), "他忙。")
        long = "很" * (IMPRESSION_MAX + 20)
        out = clean_impression(long)
        self.assertLessEqual(len(out), IMPRESSION_MAX)
        self.assertTrue(out.endswith("…"))

    def test_key_is_stable(self) -> None:
        # 记忆里就认这条 key，改名会丢掉她已经攒下的印象
        self.assertEqual(IMPRESSION_KEY, "主人印象")


class UpdateImpressionTest(unittest.TestCase):
    """service._update_impression：只摸 memory 和 companion，用 seam 造最小对象。"""

    def _hub(self, tmp: str, compose):
        from types import SimpleNamespace

        from dock_hub.memory import MemoryStore
        from dock_hub.service import DockHub

        hub = object.__new__(DockHub)
        hub.memory = MemoryStore(Path(tmp) / "memory.yaml")
        hub.companion = SimpleNamespace(impression_compose=compose)
        return hub

    def test_impression_lands_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hub = self._hub(tmp, lambda material, current: "他今晚连问三次股票，心里没底。")
            hub._update_impression(_material())
            items = hub.memory.entries()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["key"], "主人印象")
            self.assertIn("股票", items[0]["value"])
            self.assertEqual(items[0]["source"], "diary")
            # 记忆的 facts 会把她带进明天的对话
            self.assertIn("股票", hub.memory.facts())

    def test_brain_down_keeps_old_impression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def boom(material: str, current: str) -> str:
                raise RuntimeError("大脑不在")

            hub = self._hub(tmp, boom)
            hub._update_impression(_material())
            self.assertEqual(len(hub.memory), 0)

            # 已有旧印象时：写不动就保持原样（记忆会剥句尾标点）
            hub.memory.remember("主人印象", "旧印象。", source="diary")
            hub._update_impression(_material())
            self.assertEqual(hub.memory.entries()[0]["value"], "旧印象")

    def test_unchanged_reply_does_not_touch_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def same(material: str, current: str) -> str:
                calls.append(current)
                return current or "第一版印象。"

            hub = self._hub(tmp, same)
            hub._update_impression(_material())
            first = hub.memory.entries()[0]["learned_at"]
            hub._update_impression(_material())
            self.assertEqual(hub.memory.entries()[0]["learned_at"], first)
            # 第二轮拿到了当前的印象作为底稿（记忆剥了句尾标点）
            self.assertEqual(calls[1], "第一版印象")


class DiaryConfigTest(unittest.TestCase):
    def _parse(self, raw: dict) -> object:
        return parse_config(
            {"name": "study", "token": "secret-token-value", "devices": [], **raw}
        )

    def test_on_by_default_at_2130(self) -> None:
        cfg = self._parse({})
        self.assertTrue(cfg.diary.enabled)
        self.assertEqual(cfg.diary.at, "21:30")

    def test_false_turns_off_and_roundtrips(self) -> None:
        cfg = self._parse({"diary": False})
        self.assertFalse(cfg.diary.enabled)
        again = parse_config(hub_config_to_raw(cfg))
        self.assertFalse(again.diary.enabled)

    def test_at_override_roundtrips(self) -> None:
        cfg = self._parse({"diary": {"at": "23:00"}})
        self.assertEqual(cfg.diary.at, "23:00")
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.diary.at, "23:00")

    def test_default_at_drops_from_raw(self) -> None:
        self.assertNotIn("diary", hub_config_to_raw(self._parse({})))

    def test_bad_values_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._parse({"diary": "每天写"})
        with self.assertRaises(ValueError):
            self._parse({"diary": {"at": "25:00"}})


class LedgerDayTest(unittest.TestCase):
    def test_day_returns_copy_without_open_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DailyLedger(Path(tmp) / "stats.jsonl")
            ledger.note_desk(True, now=_at(9))
            ledger.note_chat(now=_at(10))
            row = ledger.day(date(2026, 10, 31))
            self.assertEqual(row["chats"], 1)
            self.assertNotIn("desk_sec", row)  # 开着的区间不虚计
            row["chats"] = 99
            self.assertEqual(ledger.day(date(2026, 10, 31))["chats"], 1)
            self.assertEqual(ledger.day(date(2026, 10, 30)), {})


if __name__ == "__main__":
    unittest.main()
