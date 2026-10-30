#!/usr/bin/env python3
"""Tests for pbr.py and the `imagegen.py pbr` command. Run from the repo root:
  python3 -m unittest discover -s imagegen -p 'test_pbr.py'
The seam test imports tiles.py (needs scipy); without scipy that one test is skipped and the rest still run."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import pbr

HERE = Path(__file__).resolve().parent
IMAGEGEN = HERE / "imagegen.py"


def periodic_picture(n=128, seed=0):
    """A random colour picture that repeats exactly, edge to edge, like a seamless tile (0-1 floats)."""
    rng = np.random.default_rng(seed)
    f = np.fft.fft2(rng.random((n, n, 3)), axes=(0, 1))
    fy, fx = np.meshgrid(np.fft.fftfreq(n), np.fft.fftfreq(n), indexing="ij")
    keep = np.exp(-(fx * fx + fy * fy) / (2 * 0.05 ** 2))[..., None]
    a = np.real(np.fft.ifft2(f * keep, axes=(0, 1)))
    a -= a.min()
    return a / a.max()


def write_image(path, arr):
    """0-1 float array to an 8-bit PNG: grey if 2-D, colour if 3-D."""
    Image.fromarray(np.round(np.clip(arr, 0, 1) * 255).astype(np.uint8)).save(path)


def read_gray(path):
    return np.asarray(Image.open(path).convert("L"))


def bump(yy, xx, cy, cx, s):
    return np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))


class BlurAndHelpers(unittest.TestCase):
    def test_blur_keeps_flat_and_wraps(self):
        flat = np.full((20, 24), 0.3)
        for wrap in (False, True):
            np.testing.assert_allclose(pbr.blur(flat, 3, wrap), 0.3, atol=1e-12)
        a = np.random.default_rng(1).random((32, 40))
        b = np.roll(a, (3, 9), axis=(0, 1))
        np.testing.assert_allclose(pbr.blur(b, 4, True), np.roll(pbr.blur(a, 4, True), (3, 9), axis=(0, 1)), atol=1e-12)

    def test_unit_maps_flat_to_zero(self):
        np.testing.assert_array_equal(pbr.unit(np.full((8, 8), 0.3)), 0)


class RoughnessMath(unittest.TestCase):
    def test_flat_dark_albedo_stays_near_constant(self):
        rgb = 0.08 + 0.001 * np.random.default_rng(2).standard_normal((64, 64, 3))
        r = pbr.heuristic_roughness(rgb, None, None, 0.5, 0.2, wrap=False)
        self.assertLess(r.std(), 0.002)
        self.assertAlmostEqual(r.mean(), 0.5, delta=0.002)

    def test_dark_spot_is_rougher_than_its_surroundings(self):
        rgb = np.full((64, 64, 3), 0.6)
        rgb[30:34, 30:34] = 0.2
        r = pbr.heuristic_roughness(rgb, None, None, 0.4, 0.2, wrap=True)
        self.assertGreater(r[31, 31], r[5, 5] + 0.03)

    def test_ao_cavity_raises_roughness(self):
        rgb = np.full((64, 64, 3), 0.5)
        ao = np.ones((64, 64))
        ao[20:40, 20:40] = 0.3
        r = pbr.heuristic_roughness(rgb, None, ao, 0.4, 0.2, wrap=False)
        self.assertAlmostEqual(r[30, 30], 0.4 + 0.2 * 0.7, places=6)
        self.assertAlmostEqual(r[5, 5], 0.4, places=6)

    def test_height_hollow_is_rougher_than_ridge(self):
        yy, xx = np.mgrid[0:96, 0:96]
        h = 0.5 + 0.4 * bump(yy, xx, 30, 30, 8) - 0.4 * bump(yy, xx, 65, 65, 8)
        rgb = np.full((96, 96, 3), 0.5)
        r = pbr.heuristic_roughness(rgb, h, None, 0.4, 0.3, wrap=False)
        self.assertGreater(r[65, 65], r[30, 30] + 0.05)     # pit floor vs ridge crest

    def test_values_stay_in_range(self):
        rgb = periodic_picture(64)
        hi = pbr.heuristic_roughness(rgb, None, None, 0.95, 0.5, wrap=False)
        self.assertLessEqual(hi.max(), 1.0)
        lo = pbr.heuristic_roughness(rgb, None, None, 0.0, 0.0, wrap=False)
        np.testing.assert_array_equal(lo, pbr.ROUGH_MIN)


class MetalMath(unittest.TestCase):
    def test_constant_metal(self):
        m0, _ = pbr.make_metal(0, None, (16, 16), False)
        m1, _ = pbr.make_metal(1, None, (16, 16), False)
        self.assertTrue((m0 == 0).all())
        self.assertTrue((m1 == 1).all())

    def test_edges_mark_ridges_not_pits(self):
        yy, xx = np.mgrid[0:96, 0:96]
        h = 0.5 + 0.4 * bump(yy, xx, 30, 30, 8) - 0.4 * bump(yy, xx, 65, 65, 8)
        m, note = pbr.make_metal("edges", h, h.shape, False)
        self.assertIsNone(note)
        self.assertGreater(m[30, 30], 0.9)     # ridge crest
        self.assertEqual(m[65, 65], 0.0)       # pit floor

    def test_edges_without_height_warns_and_is_zero(self):
        m, note = pbr.make_metal("edges", None, (8, 8), False)
        self.assertTrue((m == 0).all())
        self.assertIn("height", note)


class TileMode(unittest.TestCase):
    def test_wrap_is_translation_equivariant(self):
        """With wrap on, shifting the input shifts every map by the same amount: nothing sees a border."""
        rng = np.random.default_rng(4)
        rgb = rng.random((64, 80, 3))
        h = rng.random((64, 80))
        ao = rng.random((64, 80))
        shift = (7, -11)

        def roll(a):
            return np.roll(a, shift, axis=(0, 1))
        r0 = pbr.heuristic_roughness(rgb, h, ao, 0.4, 0.2, wrap=True)
        r1 = pbr.heuristic_roughness(roll(rgb), roll(h), roll(ao), 0.4, 0.2, wrap=True)
        np.testing.assert_allclose(r1, roll(r0), atol=1e-9)
        m0, _ = pbr.make_metal("edges", h, h.shape, True)
        m1, _ = pbr.make_metal("edges", roll(h), h.shape, True)
        np.testing.assert_allclose(m1, roll(m0), atol=1e-9)
        # the same check without wrap must fail, or the test proves nothing
        p0 = pbr.heuristic_roughness(rgb, h, ao, 0.4, 0.2, wrap=False)
        p1 = pbr.heuristic_roughness(roll(rgb), roll(h), roll(ao), 0.4, 0.2, wrap=False)
        self.assertFalse(np.allclose(p1, roll(p0)))

    def test_tile_output_seam_is_no_worse_than_an_ordinary_step(self):
        try:
            import tiles
        except ImportError:
            self.skipTest("scipy is missing, so tiles.py cannot load; the equivariance test still covers the wrap")
        rgb = periodic_picture(128, seed=7)
        r_tile = pbr.heuristic_roughness(rgb, None, None, 0.5, 0.2, wrap=True)
        r_plain = pbr.heuristic_roughness(rgb, None, None, 0.5, 0.2, wrap=False)
        s_tile = tiles.seam(np.dstack([r_tile] * 3))
        s_plain = tiles.seam(np.dstack([r_plain] * 3))
        self.assertLess(s_tile, 1.2)
        self.assertLess(s_tile, s_plain)


class Presets(unittest.TestCase):
    def test_presets_load(self):
        version, presets = pbr.load_presets()
        self.assertEqual(version, 2)
        self.assertEqual(len(presets), 18)
        self.assertEqual(presets["steel_painted"]["metal"], "edges")
        self.assertEqual(presets["chrome"]["metal"], 1)

    def test_preset_then_overrides(self):
        vals, ver = pbr.resolve("wood_lacquer")
        self.assertEqual(vals, {"rough": 0.35, "var": 0.15, "metal": 0})
        self.assertEqual(ver, 1)
        vals, _ = pbr.resolve("wood_lacquer", rough=0.5)
        self.assertEqual(vals, {"rough": 0.5, "var": 0.15, "metal": 0})
        vals, _ = pbr.resolve("chrome", rough_var=0.2, metal="edges")
        self.assertEqual(vals, {"rough": 0.08, "var": 0.2, "metal": "edges"})

    def test_rough_without_material(self):
        vals, ver = pbr.resolve(None, rough=0.3)
        self.assertEqual(vals, {"rough": 0.3, "var": 0.1, "metal": 0})
        self.assertIsNone(ver)

    def test_needs_material_or_rough(self):
        with self.assertRaisesRegex(ValueError, "--material"):
            pbr.resolve()

    def test_unknown_preset(self):
        with self.assertRaisesRegex(ValueError, "no preset 'nope'"):
            pbr.resolve("nope")

    def test_out_of_range_values_rejected(self):
        with self.assertRaises(ValueError):
            pbr.resolve(None, rough=1.5)
        with self.assertRaises(ValueError):
            pbr.resolve(None, rough=0.3, rough_var=-0.1)
        with self.assertRaises(ValueError):
            pbr.resolve(None, rough=0.3, metal=0.5)


class Companions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, *names):
        for n in names:
            (self.d / n).write_bytes(b"")

    def test_tile_name_finds_its_own_height_first(self):
        self.touch("x_tile.png", "x_tile_height.png", "x_height.png")
        hp, ap = pbr.companions(self.d / "x_tile.png")
        self.assertEqual(hp, self.d / "x_tile_height.png")
        self.assertIsNone(ap)

    def test_tile_name_never_borrows_untiled_companions(self):
        # tile cuts and resizes the picture, so the untiled picture's maps no longer line up with it
        self.touch("x_tile.png", "x_height.png", "x_ao.png")
        self.assertEqual(pbr.companions(self.d / "x_tile.png"), (None, None))

    def test_negative_detail_makes_lighter_marks_rougher(self):
        rgb = np.full((64, 64, 3), 0.8)
        rgb[30:34, :] = 0.95                       # a light scratch across flat ice
        pos = pbr.heuristic_roughness(rgb, None, None, 0.08, 0.05, wrap=True)
        neg = pbr.heuristic_roughness(rgb, None, None, 0.08, 0.05, wrap=True, detail_amount=-0.7)
        self.assertLess(pos[32].mean(), 0.08)      # default: lighter reads smoother
        self.assertGreater(neg[32].mean(), 0.2)    # ice: the scratch reads rough
        self.assertAlmostEqual(neg[5].mean(), 0.08, places=2)   # open ice stays at the base

    def test_ice_preset_and_command_line_detail(self):
        vals, ver = pbr.resolve("ice")
        self.assertEqual((vals["detail"], ver), (-0.7, 2))
        vals, _ = pbr.resolve("ice", detail=-0.3)
        self.assertEqual(vals["detail"], -0.3)
        with self.assertRaises(ValueError):
            pbr.resolve(None, rough=0.3, detail=2)

    def test_no_companions(self):
        self.touch("x.png")
        self.assertEqual(pbr.companions(self.d / "x.png"), (None, None))


class Pipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.pic = self.d / "pic.png"
        write_image(self.pic, periodic_picture(96, seed=3))
        yy, xx = np.mgrid[0:96, 0:96]
        write_image(self.d / "pic_height.png", 0.5 + 0.4 * bump(yy, xx, 30, 30, 8) - 0.4 * bump(yy, xx, 65, 65, 8))
        write_image(self.d / "pic_ao.png", periodic_picture(96, seed=5)[..., 0])
        self.out = self.d / "out"
        self.out.mkdir()
        self.vals = {"rough": 0.4, "var": 0.2, "metal": "edges"}

    def tearDown(self):
        self._tmp.cleanup()

    def test_orm_packs_ao_roughness_metal(self):
        pbr.make_pbr(self.pic, self.out, self.vals, True)
        orm = np.asarray(Image.open(self.out / "pic_orm.png").convert("RGB"))
        np.testing.assert_array_equal(orm[..., 0], read_gray(self.d / "pic_ao.png"))
        np.testing.assert_array_equal(orm[..., 1], read_gray(self.out / "pic_roughness.png"))
        np.testing.assert_array_equal(orm[..., 2], read_gray(self.out / "pic_metal.png"))

    def test_orm_red_is_white_without_ao(self):
        (self.d / "pic_ao.png").unlink()
        pbr.make_pbr(self.pic, self.out, self.vals, False)
        orm = np.asarray(Image.open(self.out / "pic_orm.png").convert("RGB"))
        self.assertTrue((orm[..., 0] == 255).all())

    def test_edges_come_from_the_height_file(self):
        pbr.make_pbr(self.pic, self.out, self.vals, False)
        metal = read_gray(self.out / "pic_metal.png")
        self.assertGreater(int(metal[30, 30]), 200)
        self.assertEqual(int(metal[65, 65]), 0)

    def test_stored_roughness_never_below_floor(self):
        pbr.make_pbr(self.pic, self.out, {"rough": 0.0, "var": 0.0, "metal": 0}, False)
        self.assertEqual(int(read_gray(self.out / "pic_roughness.png").min()), 11)

    def test_sidecar_says_heuristic_and_records_inputs(self):
        side = pbr.make_pbr(self.pic, self.out, {"rough": 0.35, "var": 0.15, "metal": 0}, False, "wood_lacquer", 1)
        on_disk = json.loads((self.out / "pic_pbr.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, side)
        self.assertEqual(on_disk["source"], "heuristic")
        self.assertEqual(on_disk["tool_version"], 1)
        self.assertEqual(on_disk["material"], "wood_lacquer")
        self.assertEqual(on_disk["preset_version"], 1)
        self.assertEqual(on_disk["inputs"], {"albedo": "pic.png", "height": "pic_height.png", "ao": "pic_ao.png"})
        self.assertEqual(on_disk["colorSpace"], "linear")
        self.assertEqual(on_disk["packing"], "ORM: R=ao G=roughness B=metal")

    def test_model_other_than_heuristic_writes_nothing(self):
        out = self.d / "never"
        out.mkdir()
        with self.assertRaisesRegex(ValueError, "only heuristic for now; pbrify comes in v2 on the PC"):
            pbr.make_pbr(self.pic, out, self.vals, False, model="pbrify")
        self.assertEqual(list(out.iterdir()), [])

    def cli(self, *args):
        return subprocess.run([sys.executable, str(IMAGEGEN), "pbr", *args], capture_output=True, text=True)

    def test_cli_material_run_writes_the_four_files(self):
        r = self.cli(str(self.pic), "--out", str(self.out), "--material", "steel_painted")
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("pic_roughness.png", "pic_metal.png", "pic_orm.png", "pic_pbr.json"):
            self.assertTrue((self.out / name).exists(), name)
        side = json.loads((self.out / "pic_pbr.json").read_text(encoding="utf-8"))
        self.assertEqual(side["material"], "steel_painted")
        self.assertEqual(side["params"]["metal"], "edges")

    def test_cli_chrome_is_all_metal(self):
        r = self.cli(str(self.pic), "--out", str(self.out), "--material", "chrome")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((read_gray(self.out / "pic_metal.png") == 255).all())

    def test_cli_flags_override_the_preset(self):
        r = self.cli(str(self.pic), "--out", str(self.out), "--material", "chrome", "--rough", "0.5", "--metal", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        side = json.loads((self.out / "pic_pbr.json").read_text(encoding="utf-8"))
        self.assertEqual(side["params"]["rough"], 0.5)
        self.assertEqual(side["params"]["metal"], 0)

    def test_cli_rejects_other_model_before_writing(self):
        out = self.d / "never"
        r = self.cli(str(self.pic), "--out", str(out), "--rough", "0.5", "--model", "pbrify")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only heuristic for now; pbrify comes in v2 on the PC", r.stderr)
        self.assertFalse(out.exists())

    def test_cli_needs_material_or_rough(self):
        r = self.cli(str(self.pic), "--out", str(self.out))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--material", r.stderr)

    def test_cli_metal_only_0_1_or_edges(self):
        r = self.cli(str(self.pic), "--out", str(self.out), "--rough", "0.5", "--metal", "0.5")
        self.assertNotEqual(r.returncode, 0)

    def test_cli_rejects_two_pictures_with_one_name(self):
        other = self.d / "sub"
        other.mkdir()
        (other / "pic.png").write_bytes(self.pic.read_bytes())
        r = self.cli(str(self.pic), str(other / "pic.png"), "--out", str(self.out), "--rough", "0.5")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("same name", r.stderr)


if __name__ == "__main__":
    unittest.main()
