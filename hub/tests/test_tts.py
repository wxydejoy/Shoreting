from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from dock_hub.config import CompanionConfig
from dock_hub.tts import CUE_TIMEOUT_SEC, READ_TIMEOUT_SEC, Tts, join_url


class FakeResponse:
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _open_returning(body: bytes = b"", status: int = 200):
    return MagicMock(return_value=FakeResponse(body, status))


class JoinUrlTest(unittest.TestCase):
    def test_double_slash_collapsed(self) -> None:
        self.assertEqual(join_url("http://a:1/", "/health"), "http://a:1/health")
        self.assertEqual(join_url("http://a:1", "v1/speak"), "http://a:1/v1/speak")


class PingTest(unittest.TestCase):
    def test_ready(self) -> None:
        tts = Tts("http://127.0.0.1:18100")
        with patch("dock_hub.tts.urllib.request.urlopen", _open_returning(b'{"ready": true}')):
            self.assertTrue(tts.ping())

    def test_not_ready(self) -> None:
        tts = Tts("http://127.0.0.1:18100")
        with patch("dock_hub.tts.urllib.request.urlopen", _open_returning(b'{"ready": false}')):
            self.assertFalse(tts.ping())

    def test_http_error_status(self) -> None:
        tts = Tts("http://127.0.0.1:18100")
        with patch("dock_hub.tts.urllib.request.urlopen", _open_returning(b"{}", status=503)):
            self.assertFalse(tts.ping())

    def test_unreachable(self) -> None:
        tts = Tts("http://127.0.0.1:18100")
        with patch(
            "dock_hub.tts.urllib.request.urlopen",
            MagicMock(side_effect=urllib.error.URLError("nope")),
        ):
            self.assertFalse(tts.ping())

    def test_garbage_body(self) -> None:
        tts = Tts("http://127.0.0.1:18100")
        with patch("dock_hub.tts.urllib.request.urlopen", _open_returning(b"<html>")):
            self.assertFalse(tts.ping())

    def test_unconfigured_never_calls(self) -> None:
        opened = MagicMock()
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            self.assertFalse(Tts(None).ping())
        opened.assert_not_called()


class SpeakTest(unittest.TestCase):
    def test_default_is_cue_only_without_deliver(self) -> None:
        # 没开 tts_deliver：TTS 自己播，Hub 只等回执，超时给 3 秒
        calls: list[tuple] = []
        tts = Tts("http://tts", log=lambda turn, stage, **kw: calls.append((stage, kw)))
        opened = _open_returning(b'{"ok": true}')
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            self.assertIsNone(tts.speak("晚上好。", "turn1"))
        req = opened.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertTrue(body["play_only"])
        self.assertEqual(body["text"], "晚上好。")
        self.assertEqual(body["turn_id"], "turn1")
        self.assertEqual(body["voice"], "shorekeeper")
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertEqual(opened.call_args[1]["timeout"], CUE_TIMEOUT_SEC)
        self.assertEqual(calls[0][0], "tts_cued")

    def test_deliver_returns_wav(self) -> None:
        tts = Tts("http://tts", deliver=True, timeout_sec=12.5)
        opened = _open_returning(b"RIFFxxxx")
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            wav = tts.speak("晚上好。", "turn1")
        self.assertEqual(wav, b"RIFFxxxx")
        req = opened.call_args[0][0]
        self.assertNotIn(b"play_only", req.data)
        self.assertEqual(req.get_header("Accept"), "audio/wav")
        self.assertEqual(opened.call_args[1]["timeout"], 12.5)

    def test_read_mode_gets_its_own_timeout(self) -> None:
        # 长文合成要几十秒，不能按「播一句」的 3 秒或 20 秒掐
        tts = Tts("http://tts", deliver=False, timeout_sec=20.0)
        opened = _open_returning(b"RIFF")
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            tts.speak("第一段。", "turn1", play_only=False, play=False)
        self.assertEqual(opened.call_args[1]["timeout"], READ_TIMEOUT_SEC)
        body = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertFalse(body["play"])

    def test_failure_returns_none_and_logs(self) -> None:
        calls: list[tuple] = []
        tts = Tts("http://tts", log=lambda turn, stage, **kw: calls.append((stage, kw)))
        with patch(
            "dock_hub.tts.urllib.request.urlopen",
            MagicMock(side_effect=urllib.error.URLError("down")),
        ):
            self.assertIsNone(tts.speak("喂", "turn1", play_only=False))
        self.assertEqual(calls[0][0], "tts_error")

    def test_empty_body_is_a_failure(self) -> None:
        calls: list[tuple] = []
        tts = Tts("http://tts", deliver=True, log=lambda turn, stage, **kw: calls.append((stage, kw)))
        with patch("dock_hub.tts.urllib.request.urlopen", _open_returning(b"")):
            self.assertIsNone(tts.speak("喂", "turn1"))
        self.assertEqual(calls[0][0], "tts_empty")

    def test_speed_is_rounded(self) -> None:
        tts = Tts("http://tts", speed=1.23456)
        opened = _open_returning(b"{}")
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            tts.speak("喂", "turn1")
        body = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body["speed"], 1.23)


class StopTest(unittest.TestCase):
    def test_posts_to_stop(self) -> None:
        tts = Tts("http://tts")
        opened = _open_returning(b"{}")
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            tts.stop()
        req = opened.call_args[0][0]
        self.assertEqual(req.full_url, "http://tts/v1/stop")
        self.assertEqual(req.get_method(), "POST")
        self.assertLessEqual(opened.call_args[1]["timeout"], 1.0)

    def test_swallows_failure(self) -> None:
        tts = Tts("http://tts")
        with patch(
            "dock_hub.tts.urllib.request.urlopen",
            MagicMock(side_effect=urllib.error.URLError("down")),
        ):
            tts.stop()  # 不该抛

    def test_unconfigured_never_calls(self) -> None:
        opened = MagicMock()
        with patch("dock_hub.tts.urllib.request.urlopen", opened):
            Tts("").stop()
        opened.assert_not_called()


class FromConfigTest(unittest.TestCase):
    def test_reads_companion_config(self) -> None:
        cfg = CompanionConfig(
            enabled=True,
            tts_base_url="http://127.0.0.1:18100/",
            tts_deliver=True,
            tts_timeout_sec=15.0,
            voice_speed=0.95,
        )
        tts = Tts.from_config(cfg)
        self.assertTrue(tts.configured)
        self.assertTrue(tts.deliver)
        self.assertEqual(tts.timeout_sec, 15.0)
        self.assertEqual(tts.speed, 0.95)
        # 结尾的斜杠要去掉，拼路径时才不会出现双斜杠
        self.assertEqual(tts.base_url, "http://127.0.0.1:18100")

    def test_missing_tts_means_unconfigured(self) -> None:
        tts = Tts.from_config(CompanionConfig(enabled=True))
        self.assertFalse(tts.configured)


if __name__ == "__main__":
    unittest.main()
