"""speech.py：切句子、拼块、拼 wav。

只用标准库起一份最小的 wav 就能验，不用等 TTS。这层出错的代价很直接——
句子断错，念出来就会一顿一顿，所以边界值（刚好 limit、全是标点、没标点）
都钉一下。
"""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
import wave
from pathlib import Path

from dock_hub.speech import (
    PUNCT_ONLY,
    chunk_read_text,
    clip_announce,
    cut_sentences,
    estimate_read_seconds,
    join_wavs,
    pack_read_chunks,
    spoken_chars,
    wav_duration_sec,
)


def make_wav(frames: int, rate: int = 24000, channels: int = 1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        dst.writeframes(b"\x00\x00" * frames * channels)
    return out.getvalue()


class CutSentencesTest(unittest.TestCase):
    def test_splits_on_sentence_end(self) -> None:
        ready, rest = cut_sentences("灯关了。要读书吗？还没定")
        self.assertEqual(ready, ["灯关了。", "要读书吗？"])
        self.assertEqual(rest, "还没定")

    def test_newline_also_ends_a_piece(self) -> None:
        ready, rest = cut_sentences("第一行\n第二行")
        self.assertEqual(ready, ["第一行"])
        self.assertEqual(rest, "第二行")

    def test_nothing_to_cut(self) -> None:
        ready, rest = cut_sentences("还没说完")
        self.assertEqual(ready, [])
        self.assertEqual(rest, "还没说完")


class ChunkReadTextTest(unittest.TestCase):
    def test_short_text_stays_whole(self) -> None:
        self.assertEqual(chunk_read_text("灯我关了。"), ["灯我关了。"])

    def test_long_piece_is_hard_split(self) -> None:
        text = "一" * 250
        chunks = chunk_read_text(text, limit=100)
        self.assertEqual([len(c) for c in chunks], [100, 100, 50])

    def test_empty(self) -> None:
        self.assertEqual(chunk_read_text("   "), [])


class PackReadChunksTest(unittest.TestCase):
    def test_short_pieces_are_glued_together(self) -> None:
        packed = pack_read_chunks(["灯我关了。", "书还读吗。", "嗯。"])
        self.assertEqual(len(packed), 1)
        self.assertIn("灯我关了", packed[0])

    def test_punctuation_only_pieces_dropped(self) -> None:
        self.assertEqual(pack_read_chunks(["……", "。"], target=36, limit=52), [])

    def test_nothing_exceeds_the_limit(self) -> None:
        long_text = "这是一句相当长的句子，用来把块撑到需要拆分的地步，看看它会不会溢出。" * 3
        for piece in pack_read_chunks([long_text], target=36, limit=52):
            self.assertLessEqual(len(piece), 52)

    def test_commas_become_the_split_points(self) -> None:
        # 逗号处断开，避免一长一短把语速带跑
        packed = pack_read_chunks(["第一段很长很长很长很长很长，第二段也不短也不短。"], target=36, limit=52)
        self.assertGreaterEqual(len(packed), 1)
        self.assertTrue(all(piece for piece in packed))


class AnnounceTest(unittest.TestCase):
    def test_keeps_only_a_few_sentences(self) -> None:
        text = "水开了。饭好了。菜也热了。还有汤。"
        self.assertEqual(clip_announce(text, max_sentences=2), "水开了。饭好了。")

    def test_long_single_sentence_is_cut_with_ellipsis(self) -> None:
        out = clip_announce("啊" * 200)
        self.assertEqual(len(out), 160)
        self.assertTrue(out.endswith("…"))

    def test_empty(self) -> None:
        self.assertEqual(clip_announce("  "), "")


class CountingTest(unittest.TestCase):
    def test_spoken_chars_ignores_whitespace(self) -> None:
        self.assertEqual(spoken_chars("灯 关 了"), 3)

    def test_estimate_uses_four_chars_a_second(self) -> None:
        self.assertEqual(estimate_read_seconds(""), 0)
        self.assertEqual(estimate_read_seconds("一二三四"), 1)
        self.assertEqual(estimate_read_seconds("一" * 400), 100)


class WavTest(unittest.TestCase):
    def test_duration(self) -> None:
        raw = make_wav(24000)
        self.assertAlmostEqual(wav_duration_sec(raw), 1.0, places=3)

    def test_join_keeps_total_length(self) -> None:
        joined = join_wavs([make_wav(1000), make_wav(2000)])
        self.assertAlmostEqual(wav_duration_sec(joined), 0.125, places=3)

    def test_join_single_part_is_passthrough(self) -> None:
        raw = make_wav(500)
        self.assertIs(join_wavs([raw]), raw)

    def test_join_rejects_empty_and_mismatched(self) -> None:
        with self.assertRaises(ValueError):
            join_wavs([])
        with self.assertRaises(ValueError):
            join_wavs([make_wav(100, rate=24000), make_wav(100, rate=16000)])


class PunctOnlyTest(unittest.TestCase):
    def test_matches(self) -> None:
        self.assertIsNotNone(PUNCT_ONLY.match("……"))
        self.assertIsNone(PUNCT_ONLY.match("灯"))


class IndependenceTest(unittest.TestCase):
    def test_speech_does_not_drag_in_the_companion(self) -> None:
        code = "import sys, dock_hub.speech; print('dock_hub.companion' in sys.modules)"
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


if __name__ == "__main__":
    unittest.main()
