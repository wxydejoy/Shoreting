from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from dock_hub.config import (
    ScheduleConfig,
    hub_config_to_raw,
    parse_config,
)
from dock_hub.schedule import Scheduler, is_due, minute_key
from dock_hub.service import DockHub

BASE = {
    "name": "study",
    "token": "secret-token-value",
    "host": "127.0.0.1",
    "port": 0,
    "devices": [
        {"id": "lamp", "name": "台灯", "type": "action", "path": "/tmp/dock-lamp.sh"},
        {"id": "plug", "name": "显示器", "type": "action", "path": "/tmp/dock-plug.sh"},
    ],
    "mini": False,
}


def _with_schedules(*schedules: dict) -> dict:
    raw = dict(BASE)
    raw["schedules"] = list(schedules)
    return raw


class ParseScheduleTest(unittest.TestCase):
    def test_full_entry(self) -> None:
        cfg = parse_config(
            _with_schedules(
                {
                    "id": "morning",
                    "at": "07:30",
                    "weekdays": [7, 1, 3],
                    "on": ["lamp"],
                    "off": ["plug"],
                    "say": "早。",
                }
            )
        )
        self.assertEqual(len(cfg.schedules), 1)
        sched = cfg.schedules[0]
        self.assertEqual(sched.at, "07:30")
        self.assertEqual(sched.weekdays, [1, 3, 7])
        self.assertEqual(sched.on, ["lamp"])
        self.assertEqual(sched.off, ["plug"])
        self.assertEqual(sched.say, "早。")

    def test_no_schedules_is_empty(self) -> None:
        self.assertEqual(parse_config(BASE).schedules, [])

    def test_bad_time_rejected(self) -> None:
        for bad in ("7:30", "24:00", "0730", "", "abc"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_config(_with_schedules({"id": "x", "at": bad, "on": ["lamp"]}))

    def test_bad_weekday_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                _with_schedules({"id": "x", "at": "07:30", "weekdays": [0], "on": ["lamp"]})
            )
        with self.assertRaises(ValueError):
            parse_config(
                _with_schedules({"id": "x", "at": "07:30", "weekdays": ["周一"], "on": ["lamp"]})
            )

    def test_unknown_device_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(_with_schedules({"id": "x", "at": "07:30", "on": ["ghost"]}))

    def test_duplicate_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                _with_schedules(
                    {"id": "x", "at": "07:30", "on": ["lamp"]},
                    {"id": "x", "at": "08:00", "on": ["plug"]},
                )
            )

    def test_missing_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(_with_schedules({"at": "07:30", "on": ["lamp"]}))

    def test_dedupes_device_ids(self) -> None:
        cfg = parse_config(
            _with_schedules({"id": "x", "at": "07:30", "on": ["lamp", "lamp"]})
        )
        self.assertEqual(cfg.schedules[0].on, ["lamp"])

    def test_round_trips_through_raw(self) -> None:
        cfg = parse_config(
            _with_schedules(
                {"id": "night", "at": "23:10", "off": ["lamp"], "say": "晚安。"},
                {"id": "work", "at": "09:00", "weekdays": [1, 2, 3, 4, 5], "on": ["plug"]},
            )
        )
        raw = hub_config_to_raw(cfg)
        self.assertEqual(len(raw["schedules"]), 2)
        again = parse_config(raw)
        self.assertEqual([item.at for item in again.schedules], ["23:10", "09:00"])
        self.assertEqual(again.schedules[1].weekdays, [1, 2, 3, 4, 5])
        self.assertEqual(again.schedules[0].say, "晚安。")

    def test_without_schedules_raw_has_no_key(self) -> None:
        self.assertNotIn("schedules", hub_config_to_raw(parse_config(BASE)))


class SchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runs: list[str] = []
        self.scheds = [
            ScheduleConfig(id="morning", at="07:30", on=["lamp"]),
            ScheduleConfig(id="weekend", at="07:30", weekdays=[6, 7], off=["lamp"]),
        ]
        self.scheduler = Scheduler(self.scheds, lambda s: self.runs.append(s.id))

    def test_is_due_matches_minute(self) -> None:
        monday = datetime(2026, 10, 26, 7, 30)
        self.assertTrue(is_due(self.scheds[0], monday))
        self.assertFalse(is_due(self.scheds[1], monday))
        self.assertTrue(is_due(self.scheds[1], datetime(2026, 10, 31, 7, 30)))
        self.assertFalse(is_due(self.scheds[0], datetime(2026, 10, 26, 7, 31)))

    def test_tick_fires_once_per_minute(self) -> None:
        moment = datetime(2026, 10, 26, 7, 30, 5)
        self.assertEqual(self.scheduler.tick(moment), ["morning"])
        self.assertEqual(self.scheduler.tick(moment), [])
        self.assertEqual(self.runs, ["morning"])
        self.assertEqual(self.scheduler.last_fired_id, "morning")
        self.assertEqual(self.scheduler.last_fired_at, minute_key(moment))

    def test_next_minute_fires_again(self) -> None:
        self.scheduler.tick(datetime(2026, 10, 26, 7, 30))
        self.scheduler.tick(datetime(2026, 10, 27, 7, 30))
        self.assertEqual(self.runs, ["morning", "morning"])

    def test_runner_error_does_not_stop_the_rest(self) -> None:
        def boom(sched: ScheduleConfig) -> None:
            self.runs.append(sched.id)
            if sched.id == "morning":
                raise RuntimeError("灯不在")

        scheduler = Scheduler(self.scheds, boom)
        fired = scheduler.tick(datetime(2026, 10, 31, 7, 30))
        self.assertEqual(fired, ["morning", "weekend"])
        self.assertEqual(self.runs, ["morning", "weekend"])
        self.assertIsNone(scheduler.last_error)

    def test_failed_run_is_reported(self) -> None:
        def boom(sched: ScheduleConfig) -> None:
            raise RuntimeError("灯不在")

        scheduler = Scheduler([self.scheds[0]], boom)
        scheduler.tick(datetime(2026, 10, 26, 7, 30))
        self.assertIn("灯不在", scheduler.last_error or "")

    def test_no_schedules_never_starts_a_thread(self) -> None:
        empty = Scheduler([], lambda s: None)
        empty.start()
        self.assertIsNone(empty._thread)  # noqa: SLF001


class HubScheduleRunTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = DockHub(
            parse_config(
                _with_schedules(
                    {"id": "night", "at": "23:10", "off": ["lamp"], "on": ["plug"], "say": "晚安。"}
                ),
                Path(tmp.name) / "hub.yaml",
            )
        )
        # 手动 tick 就够了，别让后台线程在测试里真的跑起来
        self.hub.scheduler.stop()
        self.addCleanup(self.hub.scheduler.stop)

    def test_run_schedule_drives_devices_and_voice(self) -> None:
        sched = self.hub.scheduler.schedules[0]
        with (
            patch.object(self.hub, "command") as command,
            patch.object(self.hub, "companion_announce") as announce,
        ):
            self.hub._run_schedule(sched)  # noqa: SLF001
        self.assertEqual(
            [call.args for call in command.call_args_list],
            [("plug", {"on": True}), ("lamp", {"on": False})],
        )
        announce.assert_called_once_with({"text": "晚安。"})

    def test_schedule_without_say_stays_quiet(self) -> None:
        sched = ScheduleConfig(id="quiet", at="08:00", on=["plug"])
        with (
            patch.object(self.hub, "command") as command,
            patch.object(self.hub, "companion_announce") as announce,
        ):
            self.hub._run_schedule(sched)  # noqa: SLF001
        command.assert_called_once_with("plug", {"on": True})
        announce.assert_not_called()

    def test_tick_through_hub_touches_devices(self) -> None:
        with patch.object(self.hub, "command") as command, patch.object(
            self.hub, "companion_announce"
        ):
            fired = self.hub.scheduler.tick(datetime(2026, 10, 26, 23, 10))
        self.assertEqual(fired, ["night"])
        self.assertEqual(command.call_count, 2)


if __name__ == "__main__":
    unittest.main()
