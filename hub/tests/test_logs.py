from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from dock_hub import logs


class SwallowedTest(unittest.TestCase):
    def setUp(self) -> None:
        logs.reset()
        self.addCleanup(logs.reset)

    def _capture(self, context: str, exc: BaseException) -> str:
        buf = io.StringIO()
        with redirect_stderr(buf):
            logs.swallowed(context, exc)
        return buf.getvalue()

    def test_prints_type_and_message(self) -> None:
        out = self._capture("刷新米家设备缓存", ValueError("device list 超时"))
        self.assertIn("刷新米家设备缓存", out)
        self.assertIn("ValueError: device list 超时", out)

    def test_same_context_is_throttled(self) -> None:
        self.assertTrue(self._capture("拉取 GPU 温度", OSError("no nvml")))
        self.assertEqual(self._capture("拉取 GPU 温度", OSError("no nvml")), "")

    def test_different_context_still_prints(self) -> None:
        self._capture("拉取 GPU 温度", OSError("no nvml"))
        self.assertIn("上报桌面状态", self._capture("上报桌面状态", OSError("refused")))
