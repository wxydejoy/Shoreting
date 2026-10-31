from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from dock_console import __main__ as entry


class FollowParentTest(unittest.TestCase):
    def test_launchd_counts_as_no_parent(self) -> None:
        with mock.patch.object(entry.os, "getppid", return_value=1):
            self.assertIsNone(entry.follow_parent(poll=0.01))

    def test_origin_still_here(self) -> None:
        with mock.patch.object(entry.os, "getppid", return_value=500):
            self.assertTrue(entry.parent_is_origin(500))

    def test_reparented_to_launchd(self) -> None:
        # app 被强杀后子进程被 launchd 收养：ppid 从 500 变成 1
        with mock.patch.object(entry.os, "getppid", return_value=1):
            self.assertFalse(entry.parent_is_origin(500))


class PortTest(unittest.TestCase):
    def test_default_port_matches_the_app(self) -> None:
        # menubar/main.swift 里写死 17880，改一边别忘了另一边
        self.assertEqual(entry.PORT, 17880)
        source = (Path(entry.__file__).resolve().parents[1] / "menubar" / "main.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"private let port = {entry.PORT}", source)


if __name__ == "__main__":
    unittest.main()
