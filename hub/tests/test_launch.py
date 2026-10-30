from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from dock_hub.config import DeviceConfig
from dock_hub.launch import is_url, launch, launch_running, process_names


class ProcessNamesTest(unittest.TestCase):
    def test_generic_launcher_is_ignored(self) -> None:
        cfg = DeviceConfig(
            id="wegame",
            name="无畏契约",
            type="action",
            program=r"D:\WeGameApps\game\WeGameLauncher\launcher.exe",
        )
        self.assertEqual(process_names(cfg), [])
        self.assertFalse(launch_running(cfg, {"launcher.exe", "wegame.exe"}))

    def test_explicit_process_wins(self) -> None:
        cfg = DeviceConfig(
            id="wegame",
            name="无畏契约",
            type="action",
            program=r"D:\WeGameApps\game\WeGameLauncher\launcher.exe",
            process=["VALORANT-Win64-Shipping.exe"],
        )
        self.assertEqual(process_names(cfg), ["valorant-win64-shipping.exe"])
        self.assertTrue(launch_running(cfg, {"valorant-win64-shipping.exe"}))
        self.assertFalse(launch_running(cfg, {"launcher.exe"}))

    def test_specific_exe_is_used(self) -> None:
        cfg = DeviceConfig(
            id="steam",
            name="Steam",
            type="action",
            program=str(Path("C:/Program Files (x86)/Steam/steam.exe")),
        )
        self.assertEqual(process_names(cfg), ["steam.exe"])

    def test_wegame_protocol_is_url(self) -> None:
        self.assertTrue(is_url("wegame://action=StartFor&game_id=2001715"))
        self.assertTrue(is_url("steam://rungameid/730"))

    def test_launch_protocol_uses_startfile(self) -> None:
        cfg = DeviceConfig(
            id="wegame",
            name="无畏契约",
            type="action",
            program="wegame://action=StartFor&game_id=2001715",
            process=["VALORANT-Win64-Shipping.exe"],
        )
        with patch("dock_hub.launch._startfile") as mocked:
            launch(cfg)
        mocked.assert_called_once()


class FinishedWatchTest(unittest.TestCase):
    def test_first_scan_is_baseline_only(self) -> None:
        from dock_hub.launch import FinishedWatch

        watch = FinishedWatch()
        self.assertEqual(watch.diff({"a.exe", "b.exe"}, 0.0), [])

    def test_disappearance_reports_with_duration(self) -> None:
        from dock_hub.launch import FinishedWatch

        watch = FinishedWatch()
        watch.diff({"a.exe", "b.exe"}, 0.0)
        events = watch.diff({"b.exe"}, 125.0)
        self.assertEqual(events, [{"process": "a.exe", "ran_sec": 125.0}])

    def test_reappear_resets_the_clock(self) -> None:
        from dock_hub.launch import FinishedWatch

        watch = FinishedWatch()
        watch.diff({"a.exe"}, 0.0)
        watch.diff(set(), 10.0)
        watch.diff({"a.exe"}, 20.0)
        self.assertEqual(watch.diff(set(), 30.0), [{"process": "a.exe", "ran_sec": 10.0}])

    def test_still_running_reports_nothing(self) -> None:
        from dock_hub.launch import FinishedWatch

        watch = FinishedWatch()
        watch.diff({"a.exe"}, 0.0)
        watch.diff({"a.exe"}, 500.0)
        self.assertEqual(watch.diff({"a.exe"}, 1000.0), [])
