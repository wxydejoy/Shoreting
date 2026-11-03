"""T2 协议增补提案三条路由的状态逻辑单测（ScreenState，不依赖 DockHub 全家桶）。"""

from __future__ import annotations

import unittest

from dock_hub.errors import HubError
from dock_hub.screen_state import CAPTION_MAX_CHARS, ScreenState


class CaptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = ScreenState()

    def test_push_and_payload(self):
        self.screen.set_caption({"text": "漂泊者，晚上好。", "scale": 120})
        payload = self.screen.caption_payload()
        self.assertEqual(payload["text"], "漂泊者，晚上好。")
        self.assertEqual(payload["scale"], 120)
        self.assertIn("at", payload)

    def test_clear(self):
        self.screen.set_caption({"text": "一句话"})
        self.screen.set_caption({"clear": True})
        self.assertIsNone(self.screen.caption_payload())

    def test_empty_rejected(self):
        with self.assertRaises(HubError) as ctx:
            self.screen.set_caption({"text": "  "})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_scale_clamped_range(self):
        with self.assertRaises(HubError):
            self.screen.set_caption({"text": "x", "scale": 300})
        with self.assertRaises(HubError):
            self.screen.set_caption({"text": "x", "scale": "abc"})

    def test_long_text_truncated(self):
        self.screen.set_caption({"text": "长" * 500})
        self.assertEqual(len(self.screen.caption_payload()["text"]), CAPTION_MAX_CHARS)


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.screen = ScreenState(base_layout_rev=0, base_modules={"Clock": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3, "visible": True}})

    def test_apply_bumps_rev_and_merges(self):
        self.screen.apply_layout({"modules": {"Weather": {"visible": False}}})
        payload = self.screen.layout_payload()
        self.assertEqual(payload["rev"], 1)
        self.assertEqual(payload["modules"]["Weather"]["visible"], False)
        self.assertIn("Clock", payload["modules"])  # 与既有槽位合并

    def test_second_apply_rev_two(self):
        self.screen.apply_layout({"modules": {"Weather": {"visible": False}}})
        self.screen.apply_layout({"modules": {"Weather": {"visible": True}}})
        self.assertEqual(self.screen.layout_payload()["rev"], 2)

    def test_rect_ratio_validated(self):
        with self.assertRaises(HubError):
            self.screen.apply_layout({"modules": {"Clock": {"x": 5}}})
        with self.assertRaises(HubError):
            self.screen.apply_layout({"modules": {"Clock": {"visible": "yes"}}})

    def test_modules_required(self):
        with self.assertRaises(HubError):
            self.screen.apply_layout({"layout": "oops"})


class LockTests(unittest.TestCase):
    def test_request_sets_timestamp(self):
        screen = ScreenState()
        self.assertIsNone(screen.lock_payload())
        screen.request_lock()
        self.assertIn("requested_at", screen.lock_payload())


if __name__ == "__main__":
    unittest.main()
