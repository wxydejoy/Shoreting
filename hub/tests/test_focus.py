"""打游戏自动切场景：配置引用要对得上，触发只在边沿上。"""

import tempfile
import unittest
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.errors import HubError
from dock_hub.service import DockHub


def _cfg(**focus):
    raw = {
        "name": "study",
        "token": "secret-token-value",
        "host": "127.0.0.1",
        "port": 0,
        "devices": [
            {
                "id": "lamp",
                "name": "台灯",
                "type": "light",
                "mijia_name": "台灯",
                "on_prop": "on-2",
            },
            {
                "id": "game",
                "name": "无畏契约",
                "type": "action",
                "program": "C:\\game.exe",
            },
        ],
        "scenes": [
            {"id": "game-on", "name": "开黑", "off": ["lamp"]},
            {"id": "game-off", "name": "退了", "on": ["lamp"]},
        ],
        "focus": focus or {"on": "开黑", "off": "退了"},
    }
    return parse_config(raw)


class TestFocusConfig(unittest.TestCase):
    def test_by_name_resolves_to_id(self) -> None:
        cfg = _cfg(on="开黑", off="game-off")
        self.assertEqual(cfg.focus.on, "game-on")
        self.assertEqual(cfg.focus.off, "game-off")

    def test_off_optional(self) -> None:
        cfg = _cfg(on="开黑")
        self.assertTrue(cfg.focus.enabled)
        self.assertEqual(cfg.focus.off, "")

    def test_missing_scene_raises(self) -> None:
        with self.assertRaises(ValueError):
            _cfg(on="不存在")

    def test_off_by_default(self) -> None:
        cfg = parse_config(
            {"name": "s", "token": "secret-token-value", "devices": [], "scenes": []}
        )
        self.assertFalse(cfg.focus.enabled)
        self.assertNotIn("focus", hub_config_to_raw(cfg))

    def test_roundtrip(self) -> None:
        cfg = _cfg()
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.focus, cfg.focus)

    def test_wizard_save_keeps_focus(self) -> None:
        from dock_hub.setup_ui import _payload_to_raw

        cfg = _cfg()
        raw = _payload_to_raw(
            {"name": "study", "token": "secret-token-value", "port": 17890, "devices": []},
            cfg,
        )
        self.assertEqual(raw["focus"]["on"], "game-on")


class TestFocusEvaluate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hub = DockHub(_cfg())
        self.hub._focus_stop.set()  # 测试里手动调，不要后台线程
        self.ran: list[str] = []
        self.hub.run_scene = lambda scene_id, **kw: self.ran.append(scene_id) or {
            "id": scene_id,
            "name": scene_id,
            "ok": True,
            "actions": [],
            "said": False,
        }

    def _game(self, running: bool) -> None:
        self.hub._game_running = lambda: running  # type: ignore[method-assign]

    def test_edge_trigger_only(self) -> None:
        self._game(False)
        self.hub._evaluate_focus()  # 第一眼只记录
        self.assertEqual(self.ran, [])
        self._game(True)
        self.hub._evaluate_focus()
        self.assertEqual(self.ran, ["game-on"])
        self.hub._evaluate_focus()  # 还在玩：不重复切
        self.assertEqual(self.ran, ["game-on"])
        self._game(False)
        self.hub._evaluate_focus()
        self.assertEqual(self.ran, ["game-on", "game-off"])

    def test_boot_with_game_open_does_not_trigger(self) -> None:
        self._game(True)
        self.hub._evaluate_focus()
        self.assertEqual(self.ran, [])

    def test_no_off_scene_is_silent_on_exit(self) -> None:
        self.hub.config = _cfg(on="开黑")
        self._game(False)
        self.hub._evaluate_focus()
        self._game(True)
        self.hub._evaluate_focus()
        self.assertEqual(self.ran, ["game-on"])
        self._game(False)
        self.hub._evaluate_focus()
        self.assertEqual(self.ran, ["game-on"], "没配恢复场景就安静退出去")

    def test_scene_error_does_not_crash(self) -> None:
        def boom(scene_id, **kw):
            raise HubError("not_found", f"没有场景 {scene_id}")

        self.hub.run_scene = boom
        self._game(False)
        self.hub._evaluate_focus()
        self._game(True)
        self.hub._evaluate_focus()  # 只打印，不炸
        self.assertEqual(self.hub._focus_active, True)


    def test_mutes_on_game_start_and_restores_on_exit(self) -> None:
        self.hub._read_volume = lambda: 40
        written: list[int] = []
        self.hub._write_volume = written.append
        self._game(False)
        self.hub._evaluate_focus()
        self._game(True)
        self.hub._evaluate_focus()
        self.assertEqual(written, [0])
        self.assertEqual(self.hub._volume_before_game, 40)
        self._game(False)
        self.hub._evaluate_focus()
        self.assertEqual(written, [0, 40])
        self.assertIsNone(self.hub._volume_before_game)

    def test_mute_off_leaves_volume_alone(self) -> None:
        self.hub.config = _cfg(on="开黑", off="game-off", mute=False)
        written: list[int] = []
        self.hub._write_volume = written.append
        self._game(False)
        self.hub._evaluate_focus()
        self._game(True)
        self.hub._evaluate_focus()
        self.assertEqual(written, [])

    def test_unreadable_volume_skips_mute(self) -> None:
        self.hub._read_volume = lambda: None
        written: list[int] = []
        self.hub._write_volume = written.append
        self._game(False)
        self.hub._evaluate_focus()
        self._game(True)
        self.hub._evaluate_focus()
        self.assertEqual(written, [])
        self.assertIsNone(self.hub._volume_before_game)

    def test_config_reload_restores_stuck_volume(self) -> None:
        written: list[int] = []
        self.hub._volume_before_game = 30
        self.hub._write_volume = written.append
        self.hub.config = _cfg(on="开黑", off="game-off", mute=False)
        self.hub._start_focus()
        self.hub._focus_stop.set()  # 别让后台线程跑
        self.assertEqual(written, [30])
        self.assertIsNone(self.hub._volume_before_game)


if __name__ == "__main__":
    unittest.main()
