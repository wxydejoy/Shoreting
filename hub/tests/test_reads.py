"""reads.py：朗读任务的排队、推进、取消、落盘。

这一层以前埋在 companion.py 里、只能连着真 TTS 才验，所以从来没人验。
现在出声那侧由 host 提供，用一个假 host 就能把「念到一半被打断」「合成失败」
「进程重启后从目录里捞回旧稿」这些真正会出事的路径跑一遍。
"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from dock_hub.errors import HubError
from dock_hub.reads import MAX_READ_JOBS, ReadJobs, job_public, preview_of


def make_wav(frames: int = 240) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


class FakeHost:
    """最小实现：不出声，只记账。"""

    def __init__(self) -> None:
        self.cued: list[tuple[str, str]] = []
        self.synthesized: list[str] = []
        self.stopped = 0
        self.seq = 0
        self.speaking = False
        self.clips: dict[str, bytes] = {}
        self.notes: list[tuple[str, str]] = []
        self.fail_at: int | None = None
        self.barge_at: int | None = None
        self.hold: threading.Event | None = None
        self.entered = threading.Event()

    def stop_playback(self) -> None:
        self.stopped += 1

    def cue_sentence(self, text: str, ident: str, seq: int) -> bool:
        if self.barge_at is not None and len(self.cued) >= self.barge_at:
            self.seq += 1  # 模拟被新一轮对话打断
        if seq != self.seq:
            return False
        self.cued.append((ident, text))
        if self.hold is not None:
            self.entered.set()
            self.hold.wait(1.0)
        return True

    def synthesize(self, text: str, ident: str) -> bytes | None:
        if self.fail_at is not None and len(self.synthesized) >= self.fail_at:
            return None
        self.synthesized.append(text)
        return make_wav()

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def still_current(self, seq: int) -> bool:
        return seq == self.seq

    def set_speaking(self, speaking: bool) -> None:
        self.speaking = speaking

    def keep_clip(self, ident: str, wav: bytes) -> None:
        self.clips[ident] = wav

    def drop_clip(self, ident: str) -> None:
        self.clips.pop(ident, None)

    def note(self, turn: str, stage: str, **fields: object) -> None:
        self.notes.append((turn, stage))


class ReadJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.host = FakeHost()
        self.jobs = ReadJobs(self.host)
        self.jobs.set_dir(self.dir)

    def _submit(self, ident: str = "abc123", *, mode: str = "file", pieces=None):
        return self.jobs.submit(
            ident,
            mode=mode,
            text="第一句。第二句。",
            chars=8,
            estimate=3,
            pieces=pieces if pieces is not None else ["第一句。", "第二句。"],
        )

    def _wait(self, ident: str, status: str, limit: float = 2.0) -> dict:
        deadline = limit
        step = 0.005
        while deadline > 0:
            with self.jobs._lock:
                job = self.jobs.jobs.get(ident)
                if job is not None and job.get("status") == status:
                    return dict(job)
            time.sleep(step)
            deadline -= step
        self.fail(f"任务 {ident} 没有变成 {status}")

    # ---- 正常路径 -----------------------------------------------------------

    def test_file_mode_joins_and_stores_wav(self) -> None:
        self._submit()
        job = self._wait("abc123", "done")
        self.assertEqual(job["done"], 2)
        self.assertEqual(job["audio_id"], "abc123")
        self.assertEqual(job["filename"], "shorekeeper-abc123.wav")
        self.assertGreater(job["duration_sec"], 0)
        stored = self.dir / "abc123.wav"
        self.assertTrue(stored.is_file())
        self.assertEqual(self.host.clips["abc123"], stored.read_bytes())
        sidecar = json.loads((self.dir / "abc123.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["chunks"], 2)
        self.assertEqual(sidecar["text"], "第一句。第二句。")

    def test_play_mode_cues_without_storing(self) -> None:
        self._submit(mode="play")
        self._wait("abc123", "done")
        self.assertEqual([text for _, text in self.host.cued], ["第一句。", "第二句。"])
        self.assertGreaterEqual(self.host.stopped, 1)
        self.assertFalse((self.dir / "abc123.wav").exists())
        self.assertFalse(self.host.speaking, "念完要把字幕状态放回去")

    def test_job_public_hides_internals(self) -> None:
        job = self._submit()
        shape = job_public(job)
        self.assertNotIn("_text", shape)
        self.assertNotIn("_chunks", shape)
        self.assertEqual(shape["id"], shape["job_id"])
        self.assertEqual(shape["format"], "audio/wav")

    def test_list_sorts_newest_first(self) -> None:
        self._submit("aaa111", pieces=["一。"])
        self._wait("aaa111", "done")
        self._submit("bbb222", pieces=["二。"])
        self._wait("bbb222", "done")
        with self.jobs._lock:
            self.jobs.jobs["aaa111"]["created_at"] = 100
            self.jobs.jobs["bbb222"]["created_at"] = 200
        items = self.jobs.list()["items"]
        self.assertEqual([item["id"] for item in items], ["bbb222", "aaa111"])

    def test_preview_is_clipped(self) -> None:
        self.assertTrue(preview_of("字" * 40).endswith("…"))
        self.assertEqual(len(preview_of("字" * 40)), 36)

    # ---- 取消与打断 ---------------------------------------------------------

    def test_barge_in_while_playing_marks_cancelled(self) -> None:
        self.host.barge_at = 1  # 念完第一块就被新一轮对话顶掉
        self._submit(mode="play")
        job = self._wait("abc123", "cancelled")
        self.assertIsNone(job["error"], "被打断不算出错")
        self.assertGreaterEqual(self.host.stopped, 1)
        self.assertFalse(self.host.speaking)

    def test_cancel_running_job_stops_playback(self) -> None:
        self.host.hold = threading.Event()  # 卡在第一块，模拟正在念
        self._submit(mode="play")
        self.assertTrue(self.host.entered.wait(1.0), "第一块没开始念")
        self.jobs.cancel("abc123")
        self.host.hold.set()
        job = self._wait("abc123", "cancelled")
        self.assertIsNone(job["error"])
        self.assertGreaterEqual(self.host.stopped, 2, "取消正在跑的任务必须叫停喇叭")
        self.assertFalse(self.host.speaking)

    def test_cancel_queued_job_never_cues(self) -> None:
        self._submit("aaa111", mode="play", pieces=["一。"])
        self._submit("bbb222", mode="play", pieces=["二。"])
        self.jobs.cancel("bbb222")
        self._wait("aaa111", "done")
        self.assertEqual([ident for ident, _ in self.host.cued], ["aaa111"])

    def test_cancel_unknown_job_is_404(self) -> None:
        with self.assertRaises(HubError) as caught:
            self.jobs.cancel("nope123")
        self.assertEqual(caught.exception.code, "not_found")

    def test_cancel_done_job_is_noop(self) -> None:
        self._submit()
        self._wait("abc123", "done")
        again = self.jobs.cancel("abc123")
        self.assertEqual(again["status"], "done")

    def test_cancel_all_marks_every_pending(self) -> None:
        self._submit("aaa111", pieces=["一。"])
        self._submit("bbb222", pieces=["二。"])
        self.jobs.cancel_all()
        with self.jobs._lock:
            statuses = {ident: job["status"] for ident, job in self.jobs.jobs.items()}
        self.assertEqual(set(statuses.values()), {"cancelled"})

    # ---- 失败路径 -----------------------------------------------------------

    def test_synthesis_failure_marks_error(self) -> None:
        self.host.fail_at = 0
        self._submit()
        job = self._wait("abc123", "error")
        self.assertEqual(job["error"], "合成失败")
        self.assertFalse((self.dir / "abc123.wav").exists())

    # ---- 磁盘 ---------------------------------------------------------------

    def test_delete_removes_wav_json_and_clip(self) -> None:
        self._submit()
        self._wait("abc123", "done")
        self.jobs.delete("abc123")
        self.assertFalse((self.dir / "abc123.wav").exists())
        self.assertFalse((self.dir / "abc123.json").exists())
        self.assertNotIn("abc123", self.host.clips)
        self.assertNotIn("abc123", self.jobs.jobs)

    def test_disk_jobs_come_back_after_restart(self) -> None:
        self._submit()
        self._wait("abc123", "done")
        fresh = ReadJobs(self.host)
        fresh.set_dir(self.dir)
        items = fresh.list()["items"]
        self.assertEqual([item["id"] for item in items], ["abc123"])
        self.assertEqual(items[0]["status"], "done")
        self.assertEqual(fresh.text_of("abc123"), "第一句。第二句。")
        self.assertEqual(fresh.stored("abc123"), (self.dir / "abc123.wav").read_bytes())

    def test_stored_missing_returns_none(self) -> None:
        self.assertIsNone(self.jobs.stored("nope123"))

    def test_old_jobs_are_dropped(self) -> None:
        for i in range(MAX_READ_JOBS + 1):
            with self.jobs._lock:
                self.jobs.jobs[f"id{i:04d}"] = {"id": f"id{i:04d}", "status": "done"}
                while len(self.jobs.jobs) > MAX_READ_JOBS:
                    self.jobs.jobs.popitem(last=False)
        self.assertEqual(len(self.jobs.jobs), MAX_READ_JOBS)
        self.assertNotIn("id0000", self.jobs.jobs)


if __name__ == "__main__":
    unittest.main()
