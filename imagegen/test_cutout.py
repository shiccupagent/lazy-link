"""The empty-cutout guard in pc/batch.py: a cutout whose snapped mask keeps under min_cover of the picture is retried
once with the fallback model and, if still empty, removed (the raw picture stays) and reported as failed.
Run: python3 -m unittest test_cutout (needs numpy and Pillow)."""
import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent


def load_batch(tmp):
    """Import pc/batch.py as the PC would see it (models.json, asks.json, want.json in the working folder)."""
    shutil.copy(HERE / "models.json", tmp / "models.json")
    (tmp / "asks.json").write_text("[]")
    (tmp / "want.json").write_text("{}")
    os.environ.update(RENDER_OUT=str(tmp / "out"), RENDER_ROOT=str(tmp))
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location("batch_under_test", HERE / "pc" / "batch.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
    return mod


def rgba(path, alpha):
    a = np.asarray(alpha, dtype=np.uint8)
    im = np.dstack([np.full(a.shape + (3,), 200, np.uint8), a])
    Image.fromarray(im, "RGBA").save(path)


def blob(size=64, cover=0.3):
    """Alpha with a centred square keeping about `cover` of the picture."""
    a = np.zeros((size, size), np.uint8)
    side = int(round(size * cover ** 0.5))
    o = (size - side) // 2
    a[o:o + side, o:o + side] = 255
    return a


class CutoutGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.b = load_batch(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=self.tmp))
        self.pic = self.dir / "thing__klein3.png"
        Image.new("RGB", (64, 64), (227, 227, 227)).save(self.pic)
        self.cut = self.dir / "thing__klein3-cutout.png"
        self.real_retry = self.b.retry_cutout

    def tearDown(self):
        self.b.retry_cutout = self.real_retry

    def test_settings(self):
        c = self.b.M["cutout"]
        self.assertEqual(c["min_cover"], 0.02)
        self.assertIn(c["fallback"], self.b.M["files"], "the fallback cutout model must be a listed file")

    def test_cover(self):
        rgba(self.cut, blob(cover=0.25))
        self.assertAlmostEqual(self.b.cutout_cover(self.cut), 0.25, places=2)
        rgba(self.cut, np.zeros((64, 64)))
        self.assertEqual(self.b.cutout_cover(self.cut), 0.0)

    def test_verdict(self):
        v = self.b.cutout_verdict
        self.assertEqual(v(0.30, None, 0.02), "ok")
        self.assertEqual(v(0.02, None, 0.02), "ok")
        self.assertEqual(v(0.0, 0.25, 0.02), "retried")
        self.assertEqual(v(0.0, 0.01, 0.02), "failed")
        self.assertEqual(v(0.0, None, 0.02), "failed")

    def test_faint_mask_snaps_to_empty(self):
        """A mask whose highest value is under the snap floor (seen: max 7) is empty after the snap."""
        a = np.zeros((64, 64), np.uint8)
        a[20:40, 20:40] = 5
        rgba(self.cut, a)
        self.b.snap_png(self.cut)
        self.assertEqual(self.b.cutout_cover(self.cut), 0.0)

    def test_good_cutout_untouched(self):
        rgba(self.cut, blob(cover=0.3))
        r = self.b.check_cutout(self.pic, self.cut, {"cutout_fallback": "fallback.safetensors"}, "thing__klein3")
        self.assertEqual(r["status"], "ok")
        self.assertTrue(self.cut.exists())

    def test_empty_without_fallback_fails_loudly(self):
        rgba(self.cut, np.zeros((64, 64)))
        err = io.StringIO()
        with redirect_stderr(err):
            r = self.b.check_cutout(self.pic, self.cut, {"cutout_fallback": None}, "thing__klein3")
        self.assertEqual(r["status"], "failed")
        self.assertFalse(self.cut.exists(), "a failed cutout must not ship")
        self.assertTrue(self.pic.exists(), "the raw picture stays")
        self.assertIn("CUTOUT FAILED", err.getvalue())

    def test_empty_retry_rescues(self):
        rgba(self.cut, np.zeros((64, 64)))
        self.b.retry_cutout = lambda picture, dst, model, prefix: rgba(dst, blob(cover=0.4))
        r = self.b.check_cutout(self.pic, self.cut, {"cutout_fallback": "fallback.safetensors"}, "thing__klein3")
        self.assertEqual(r["status"], "retried")
        self.assertAlmostEqual(r["cover"], 0.4, places=1)
        self.assertAlmostEqual(self.b.cutout_cover(self.cut), 0.4, places=1)
        self.assertEqual(list(self.dir.glob("*-retry.png")), [])

    def test_empty_retry_also_empty_fails(self):
        rgba(self.cut, np.zeros((64, 64)))
        self.b.retry_cutout = lambda picture, dst, model, prefix: rgba(dst, np.zeros((64, 64)))
        with redirect_stderr(io.StringIO()):
            r = self.b.check_cutout(self.pic, self.cut, {"cutout_fallback": "fallback.safetensors"}, "thing__klein3")
        self.assertEqual(r["status"], "failed")
        self.assertFalse(self.cut.exists())
        self.assertEqual(list(self.dir.glob("*-retry.png")), [])


if __name__ == "__main__":
    unittest.main()
