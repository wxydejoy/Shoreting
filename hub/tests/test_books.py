import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from dock_hub.config import parse_config
from dock_hub.server import make_handler
from dock_hub.service import DockHub

from dock_hub.companion import estimate_read_seconds

from dock_hub.books import (
    MAX_BOOK_BYTES,
    Books,
    load_progress,
    next_slice,
    safe_book_id,
    split_document,
)
from dock_hub.errors import HubError


class ShelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.books = Books(self.root)

    def test_rejects_unsafe_names(self) -> None:
        for name in ("", "a.md", "../x.txt", "a/b.txt", "a\\b.txt", "nodot"):
            with self.assertRaises(HubError) as caught:
                safe_book_id(name)
            self.assertEqual(caught.exception.status, 400)

    def test_lists_only_top_level_txt(self) -> None:
        (self.root / "雾海.txt").write_bytes("甲".encode())
        (self.root / "notes.md").write_text("no", encoding="utf-8")
        (self.root / "progress.json").write_text("{}", encoding="utf-8")
        nested = self.root / "sub"
        nested.mkdir()
        (nested / "隐藏.txt").write_text("藏", encoding="utf-8")
        body = self.books.list_books()
        self.assertEqual([item["id"] for item in body["items"]], ["雾海.txt"])
        self.assertEqual(body["items"][0]["title"], "雾海")
        self.assertEqual(body["timer_min"], 30)
        self.assertIsNone(body["current_id"])

    def test_upload_sets_current_without_byte_clamping(self) -> None:
        saved = self.books.save_upload("雾海.txt", "一二三四五".encode())
        self.assertEqual(saved, {"id": "雾海.txt", "bytes": len("一二三四五".encode())})
        listed = self.books.list_books()
        self.assertEqual(listed["current_id"], "雾海.txt")
        progress = self.root / "progress.json"
        progress.write_text(
            json.dumps(
                {
                    "current_id": "雾海.txt",
                    "timer_min": 15,
                    "books": {
                        "雾海.txt": {"offset": 2, "updated_at": 1},
                        "没了.txt": {"offset": 9, "updated_at": 1},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.books.save_upload("雾海.txt", "一二".encode())
        kept = json.loads(progress.read_text(encoding="utf-8"))
        self.assertEqual(kept["books"]["雾海.txt"]["offset"], 2)
        self.assertNotIn("没了.txt", kept["books"])
        self.assertEqual(kept["timer_min"], 15)

    def test_parentheses_name_is_saved(self) -> None:
        name = "我合成了全世界(王自律).txt"
        saved = self.books.save_upload(name, "第一章 雾\n\n海雾来了。".encode())
        self.assertEqual(saved["id"], name)
        self.assertTrue((self.root / name).is_file())

    def test_save_and_open_write_log_lines(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            self.books.save_upload("雾海.txt", "第一章 雾\n\n海雾来了。".encode())
            self.books.open_book("雾海.txt")
        text = buf.getvalue()
        self.assertIn("stage=book_save", text)
        self.assertIn("stage=book_open", text)
        self.assertIn("id=雾海.txt", text)

    def test_rejects_oversize_upload(self) -> None:
        with self.assertRaises(HubError) as caught:
            self.books.save_upload("大.txt", b"x" * (MAX_BOOK_BYTES + 1))
        self.assertEqual(caught.exception.status, 400)
        self.assertFalse((self.root / "大.txt").exists())

    def test_broken_progress_is_default(self) -> None:
        (self.root / "progress.json").write_text("{", encoding="utf-8")
        body = load_progress(self.root / "progress.json")
        self.assertEqual(body, {"current_id": None, "timer_min": 30, "books": {}})


class SplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_plain_text_is_one_chapter(self) -> None:
        raw = "只有正文。没有标题。".encode()
        doc = split_document(raw, "随笔")
        self.assertEqual(len(doc.chapters), 1)
        self.assertEqual(doc.chapters[0].title, "随笔")
        self.assertIn("只有正文", doc.text)
        self.assertEqual(doc.chapters[0].start, 0)
        self.assertEqual(doc.chapters[0].end, len(doc.text))

    def test_marked_chapters_join_without_extra_chars(self) -> None:
        raw = "第一章 雾\n\n海雾来了。\n\n第二章 岸\n\n灯还亮着。\n".encode()
        doc = split_document(raw, "雾海")
        self.assertGreaterEqual(len(doc.chapters), 2)
        self.assertEqual(doc.text, "".join(ch.text for ch in doc.chapters))
        self.assertEqual(doc.chapters[0].start, 0)
        self.assertEqual(doc.chapters[1].start, doc.chapters[0].end)
        self.assertIn("海雾来了", doc.text)
        self.assertIn("灯还亮着", doc.text)

    def test_splitter_exception_becomes_one_chapter(self) -> None:
        def boom(_text: str):
            raise RuntimeError("split failed")

        doc = split_document("整篇都在。".encode(), "雾海", split_fn=boom)
        self.assertEqual(len(doc.chapters), 1)
        self.assertEqual(doc.chapters[0].title, "雾海")
        self.assertIn("整篇都在", doc.text)

    def test_open_returns_toc_without_body(self) -> None:
        (self.root / "雾海.txt").write_bytes("第一章 雾\n\n海雾来了。\n\n第二章 岸\n\n灯还亮着。\n".encode())
        books = Books(self.root)
        opened = books.open_book("雾海.txt")
        self.assertNotIn("text", opened)
        self.assertGreaterEqual(len(opened["chapters"]), 2)
        self.assertEqual(opened["bookmark"], 0)
        chapter = books.chapter("雾海.txt", 0)
        self.assertIn("海雾", chapter["text"])
        with self.assertRaises(HubError) as caught:
            books.chapter("雾海.txt", 99)
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(HubError) as missing:
            books.open_book("没有.txt")
        self.assertEqual(missing.exception.status, 404)

    def test_overlong_bookmark_clamps_to_document_end(self) -> None:
        (self.root / "雾海.txt").write_bytes("海雾来了。".encode())
        (self.root / "progress.json").write_text(
            json.dumps(
                {
                    "current_id": "雾海.txt",
                    "timer_min": 30,
                    "books": {"雾海.txt": {"offset": 9999, "updated_at": 1}},
                }
            ),
            encoding="utf-8",
        )
        books = Books(self.root)
        opened = books.open_book("雾海.txt")
        self.assertEqual(opened["bookmark"], opened["chars"])
        disk = json.loads((self.root / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(disk["books"]["雾海.txt"]["offset"], opened["chars"])

    def test_undecodable_book_raises(self) -> None:
        (self.root / "坏.txt").write_bytes(b"\xff\xfe\x00\x00\xff")
        books = Books(self.root)
        with self.assertRaises(HubError) as caught:
            books.open_book("坏.txt")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("读不出", caught.exception.message)


class SliceTest(unittest.TestCase):
    def test_reads_a_contiguous_sentence(self) -> None:
        # 第一句长过打包上限，才不会和下一句合成一段。
        text = ("海雾来了" * 12) + "。岸边有灯。"
        start, piece = next_slice(text, 0)
        self.assertEqual(text[start : start + len(piece)], piece)
        self.assertTrue(piece.startswith("海雾来了"))
        nxt = next_slice(text, start + len(piece))
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertIn("岸边有灯", nxt[1])

    def test_skips_leading_punctuation(self) -> None:
        text = "。。海雾来了。"
        start, piece = next_slice(text, 0)
        self.assertFalse(piece.startswith("。"))
        self.assertEqual(text[start : start + len(piece)], piece)

    def test_done_at_end(self) -> None:
        self.assertIsNone(next_slice("海雾来了。", 5))
        self.assertIsNone(next_slice("。。", 0))

    def test_limit_stops_before_the_next_chapter(self) -> None:
        text = "岸边有灯。灯还亮着。"
        start, piece = next_slice(text, 0, limit=len("岸边有灯。"))
        self.assertEqual(piece, "岸边有灯。")
        self.assertNotIn("灯还亮着", piece)
        self.assertEqual(text[start : start + len(piece)], piece)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waiting = threading.Event()
        self._release = threading.Event()

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> int:
        return 1_700_000_000

    def wait(self, seconds: float, interrupt: threading.Event, deadline: float | None) -> str:
        self.waiting.set()
        while not interrupt.is_set():
            if self._release.wait(timeout=0.05):
                self.now += seconds
                self._release.clear()
                self.waiting.clear()
                if deadline is not None and self.now >= deadline:
                    return "deadline"
                return "elapsed"
        self.waiting.clear()
        return "stopped"

    def release(self) -> None:
        self._release.set()

    def arrived(self, timeout: float = 1) -> bool:
        ok = self.waiting.wait(timeout)
        self.waiting.clear()
        return ok


class FakeSpeaker:
    def __init__(self) -> None:
        self.cued: list[str] = []
        self.stops = 0
        self.cancelled = 0
        self.fail = False

    def cue(self, text: str) -> bool:
        if self.fail:
            return False
        self.cued.append(text)
        return True

    def stop(self) -> None:
        self.stops += 1

    def cancel_short_reads(self) -> None:
        self.cancelled += 1


class PlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "雾海.txt").write_bytes(
            (
                "第一章 雾\n\n"
                + ("海雾来了" * 12)
                + "。岸边有灯。\n\n第二章 岸\n\n灯还亮着。\n"
            ).encode()
        )
        self.clock = FakeClock()
        self.speaker = FakeSpeaker()
        self.books = Books(self.root, speaker=self.speaker, clock=self.clock)
        self.addCleanup(self.books.pause)

    def test_cue_stays_inside_the_chapter(self) -> None:
        (self.root / "短章.txt").write_bytes("第一章 雾\n\n岸边有灯。\n\n第二章 岸\n\n灯还亮着。\n".encode())
        self.books.play("短章.txt")
        self.assertTrue(self.clock.arrived())
        self.assertTrue(self.speaker.cued)
        self.assertIn("岸边有灯", self.speaker.cued[0])
        self.assertNotIn("灯还亮着", self.speaker.cued[0])
        self.books.pause()

    def test_pause_keeps_sentence_start(self) -> None:
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        self.assertGreaterEqual(self.speaker.cancelled, 1)
        story = self.books.pause()
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "user")
        self.assertEqual(story["offset"], story["sentence_start"])
        self.assertEqual(self.speaker.stops, 1)
        disk = json.loads((self.root / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(disk["books"]["雾海.txt"]["offset"], story["sentence_start"])

    def test_play_and_pause_write_log_lines(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            self.books.play("雾海.txt")
            self.assertTrue(self.clock.arrived())
            self.books.pause()
        text = buf.getvalue()
        self.assertIn("stage=story_play", text)
        self.assertIn("stage=story_chapter", text)
        self.assertIn("stage=story_pause", text)

    def test_voice_speed_shortens_the_wait(self) -> None:
        self.books.voice_speed = lambda: 2.0
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        first = self.speaker.cued[0]
        self.clock.release()
        self.assertTrue(self.clock.arrived())
        self.assertEqual(self.clock.now, estimate_read_seconds(first) / 2)
        self.books.pause()

    def test_next_sentence_is_cued_while_this_one_plays(self) -> None:
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        self.assertGreaterEqual(len(self.speaker.cued), 2)
        story = self.books.story()
        self.assertEqual(story["sentence"], self.speaker.cued[0])
        self.assertNotEqual(story["sentence"], self.speaker.cued[1])
        self.assertEqual(story["offset"], story["sentence_start"])
        self.books.pause()

    def test_second_slice_waits_estimate(self) -> None:
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        first = self.speaker.cued[0]
        self.clock.release()
        self.assertTrue(self.clock.arrived())
        self.assertGreaterEqual(len(self.speaker.cued), 2)
        self.assertEqual(self.clock.now, estimate_read_seconds(first))
        self.books.pause()

    def test_end_then_play_restarts_at_zero(self) -> None:
        self.books.play("雾海.txt")
        for _ in range(12):
            if not self.clock.arrived():
                break
            if not self.books.story()["playing"]:
                break
            self.clock.release()
        story = self.books.story()
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "end")
        self.assertEqual(story["offset"], story["chars"])
        again = self.books.play("雾海.txt")
        self.assertTrue(again["playing"])
        self.assertEqual(again["sentence_start"], 0)
        self.books.pause()

    def test_cue_failure_stops_with_error(self) -> None:
        self.speaker.fail = True
        story = self.books.play("雾海.txt")
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "error")
        self.assertTrue(story["error"])

    def test_bookmark_while_idle_does_not_speak(self) -> None:
        opened = self.books.open_book("雾海.txt")
        target = opened["chapters"][1]["start"]
        story = self.books.bookmark("雾海.txt", target)
        self.assertFalse(story["playing"])
        self.assertEqual(story["offset"], target)
        self.assertEqual(self.speaker.cued, [])
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        with self.assertRaises(HubError) as caught:
            self.books.bookmark("雾海.txt", 0)
        self.assertEqual(caught.exception.status, 400)
        self.books.pause()

    def test_empty_book_cannot_play(self) -> None:
        (self.root / "空.txt").write_bytes(b"")
        with self.assertRaises(HubError) as caught:
            self.books.play("空.txt")
        self.assertEqual(caught.exception.message, "没有可以读的字")


class TimerTest(PlayTest):
    def _wait_until_stopped(self) -> None:
        deadline = time.time() + 2
        while self.books.story()["playing"] and time.time() < deadline:
            time.sleep(0.01)

    def test_deadline_during_wait_pauses_and_keeps_preference(self) -> None:
        self.books.set_timer(15)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        start = self.books.story()["sentence_start"]
        self.clock.now += 15 * 60
        self.clock.release()
        self._wait_until_stopped()
        story = self.books.story()
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "timer")
        self.assertEqual(story["offset"], start)
        self.assertEqual(story["timer_min"], 15)
        self.assertIsNone(story["timer_remaining_sec"])
        self.assertGreaterEqual(self.speaker.stops, 1)

    def test_pause_freezes_remaining(self) -> None:
        self.books.set_timer(30)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        self.clock.now += 60
        story = self.books.pause()
        self.assertEqual(story["timer_remaining_sec"], 30 * 60 - 60)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        again = self.books.story()
        self.assertLess(again["timer_remaining_sec"], 30 * 60)
        self.assertGreater(again["timer_remaining_sec"], 30 * 60 - 120)
        self.books.pause()

    def test_jump_does_not_refill(self) -> None:
        opened = self.books.open_book("雾海.txt")
        self.books.set_timer(30)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        self.clock.now += 60
        before = self.books.story()["timer_remaining_sec"]
        self.books.play("雾海.txt", offset=opened["chapters"][-1]["start"])
        self.assertTrue(self.clock.arrived())
        after = self.books.story()["timer_remaining_sec"]
        self.assertLessEqual(after, before)
        self.books.pause()

    def test_change_minutes_and_off(self) -> None:
        self.books.set_timer(30)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        changed = self.books.set_timer(15)
        self.assertEqual(changed["timer_remaining_sec"], 15 * 60)
        off = self.books.set_timer(0)
        self.assertIsNone(off["timer_remaining_sec"])
        self.assertTrue(off["playing"])
        with self.assertRaises(HubError):
            self.books.set_timer(10)
        self.books.pause()

    def test_next_play_after_timer_is_full_again(self) -> None:
        self.books.set_timer(15)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.arrived())
        self.clock.now += 15 * 60
        self.clock.release()
        self._wait_until_stopped()
        story = self.books.play("雾海.txt")
        self.assertEqual(story["timer_remaining_sec"], 15 * 60)
        self.books.pause()


class HttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "devices": [],
                "companion": {
                    "enabled": True,
                    "tts": {"base_url": "http://127.0.0.1:9", "timeout_sec": 2},
                },
            }
        )
        self.hub = DockHub(cfg)
        self.clock = FakeClock()
        self.speaker = FakeSpeaker()
        self.hub.books = Books(root / "books", speaker=self.speaker, clock=self.clock)
        self.hub.companion.set_story_books(self.hub.books)
        (root / "books").mkdir(parents=True, exist_ok=True)
        (root / "books" / "已有.txt").write_bytes("海雾来了。".encode())
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.hub.books.pause)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _request(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = None
        headers = {"Accept": "application/json"}
        if body is not None:
            raw = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode())
        conn.close()
        return resp.status, payload

    def test_lists_without_body(self) -> None:
        status, body = self._request("GET", "/v1/companion/books")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["id"], "已有.txt")
        self.assertNotIn("text", body["items"][0])

    def test_upload_play_pause_and_bad_timer(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/v1/companion/books",
            body="第一章 雾\n\n海雾来了。".encode(),
            headers={
                "X-Filename": "%E9%9B%BE%E6%B5%B7.txt",
                "Content-Type": "application/octet-stream",
            },
        )
        resp = conn.getresponse()
        uploaded = json.loads(resp.read().decode())
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(uploaded["id"], "雾海.txt")
        status, _playing = self._request(
            "POST", "/v1/companion/books/" + quote("雾海.txt") + "/play", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(self.clock.arrived())
        _status, story = self._request("GET", "/v1/companion/story")
        self.assertTrue(story["playing"])
        self.assertEqual(list(self.hub.companion.reads.jobs), [])
        _status, paused = self._request(
            "POST", "/v1/companion/books/" + quote("雾海.txt") + "/pause", {}
        )
        self.assertFalse(paused["playing"])
        self.assertEqual(paused["stopped_by"], "user")
        status, _bad = self._request("POST", "/v1/companion/story/timer", {"minutes": 10})
        self.assertEqual(status, 400)

    def test_short_read_pauses_story_and_keeps_bookmark(self) -> None:
        self.hub.books.save_upload("雾海.txt", "第一章 雾\n\n海雾来了。".encode())
        self._request("POST", "/v1/companion/books/" + quote("雾海.txt") + "/play", {})
        self.assertTrue(self.clock.arrived())
        start = self.hub.books.story()["sentence_start"]
        self._request("POST", "/v1/companion/read", {"text": "短稿。", "mode": "play"})
        story = self.hub.books.story()
        self.assertFalse(story["playing"])
        self.assertEqual(story["offset"], start)

