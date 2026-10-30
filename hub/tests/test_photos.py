"""回忆相框（F37）：清单、取图、路径穿越一律拒，目录没开就安静。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dock_hub.config import hub_config_to_raw, parse_config
from dock_hub.photos import list_photos, mime_for, photo_bytes, resolve_photos_dir


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


class PhotosDirTest(unittest.TestCase):
    """resolve_photos_dir：false 一票否决，显式路径其次，旁目录活检测。"""

    def test_off_wins(self) -> None:
        self.assertIsNone(resolve_photos_dir(Path("/tmp/pics"), True, Path("/x/hub.yaml")))

    def test_explicit_path(self) -> None:
        self.assertEqual(resolve_photos_dir(Path("/tmp/pics"), False, None), Path("/tmp/pics"))

    def test_no_config_path_no_album(self) -> None:
        self.assertIsNone(resolve_photos_dir(None, False, None))

    def test_sibling_detected_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(resolve_photos_dir(None, False, root / "hub.yaml"))
            (root / "photos").mkdir()
            self.assertEqual(
                resolve_photos_dir(None, False, root / "hub.yaml"), root / "photos"
            )

    def test_sibling_missing_stays_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_photos_dir(None, False, Path(tmp) / "hub.yaml"))


class PhotosConfigTest(unittest.TestCase):
    def _parse(self, photos, *, with_dir: Path | None = None):
        raw = {"name": "study", "token": "secret-token-value", "devices": []}
        if photos is not ...:
            raw["photos"] = photos
        return parse_config(raw, with_dir)

    def test_unset_stays_auto(self) -> None:
        cfg = self._parse(...)
        self.assertIsNone(cfg.photos)
        self.assertFalse(cfg.photos_off)

    def test_explicit_false_off(self) -> None:
        cfg = self._parse(False)
        self.assertIsNone(cfg.photos)
        self.assertTrue(cfg.photos_off)

    def test_explicit_path(self) -> None:
        self.assertEqual(self._parse("/tmp/pics").photos, Path("/tmp/pics"))

    def test_bad_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._parse(42)

    def test_roundtrip_keeps_photos(self) -> None:
        cfg = self._parse("/tmp/pics")
        again = parse_config(hub_config_to_raw(cfg))
        self.assertEqual(again.photos, Path("/tmp/pics"))

    def test_roundtrip_keeps_off(self) -> None:
        again = parse_config(hub_config_to_raw(self._parse(False)))
        self.assertTrue(again.photos_off)


if __name__ == "__main__":
    unittest.main()
