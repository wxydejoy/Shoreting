"""回忆相框（F37）：清单、取图、路径穿越一律拒，目录没开就安静。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dock_hub.config import parse_config
from dock_hub.photos import list_photos, mime_for, photo_bytes


class PhotosTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "b.jpg").write_bytes(b"jpg")
        (self.dir / "a.png").write_bytes(b"png")
        (self.dir / "notes.txt").write_bytes(b"nope")
        (self.dir / "sub").mkdir()

    def test_list_sorted_images_only(self) -> None:
        self.assertEqual(list_photos(self.dir), ["a.png", "b.jpg"])

    def test_list_none_or_missing_is_empty(self) -> None:
        self.assertEqual(list_photos(None), [])
        self.assertEqual(list_photos(Path(self.tmp.name) / "nope"), [])

    def test_photo_bytes_roundtrip(self) -> None:
        body, mime = photo_bytes(self.dir, "a.png")
        self.assertEqual(body, b"png")
        self.assertEqual(mime, "image/png")

    def test_traversal_rejected(self) -> None:
        for bad in ("../hub.yaml", "a/b.png", "..", ".", "sub", ""):
            with self.assertRaises(ValueError, msg=bad):
                photo_bytes(self.dir, bad)

    def test_unknown_or_wrong_ext_rejected(self) -> None:
        with self.assertRaises(ValueError):
            photo_bytes(self.dir, "notes.txt")
        with self.assertRaises(ValueError):
            photo_bytes(self.dir, "missing.jpg")

    def test_closed_album_rejects(self) -> None:
        with self.assertRaises(ValueError):
            photo_bytes(None, "a.png")

    def test_mime_for(self) -> None:
        self.assertEqual(mime_for("x.JPG"), "image/jpeg")
        self.assertEqual(mime_for("x.heic"), "image/heic")
        self.assertIsNone(mime_for("x.txt"))


if __name__ == "__main__":
    unittest.main()
