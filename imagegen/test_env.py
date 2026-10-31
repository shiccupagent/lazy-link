#!/usr/bin/env python3
"""Tests for imagegen/env.py and imagegen/pc/env_blender.py. Mac side, synthetic data only: no PC is reached.
The job goes through the real pcsubmit.submit, but the PC behind it is a fake (FakePC) that answers from memory,
and env_blender.py is only loaded for its pure helpers (bpy is not on the Mac). Run from the repo root:
  python3 -m unittest discover -s imagegen -p 'test_env.py'
"""
import contextlib
import importlib.util
import io
import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import env  # noqa: E402

PC_SCRIPT = HERE / "pc" / "env_blender.py"


def load_pc_script():
    """pc/env_blender.py loaded by path: it runs inside Blender, so only its pure helpers are used here."""
    spec = importlib.util.spec_from_file_location("env_blender_under_test", PC_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EB = load_pc_script()


# ---------- synthetic data ----------

def make_glb(path, doc, binary=b""):
    """A glTF 2 binary: JSON chunk (padded with spaces), optional BIN chunk (padded with zeros)."""
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js
    if binary:
        binary += b"\0" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)


def doc_with(lights=None, nodes=(), materials=(), meshes=()):
    doc = {"asset": {"version": "2.0"}, "nodes": list(nodes), "materials": list(materials), "meshes": list(meshes)}
    if lights is not None:
        doc["extensions"] = {"KHR_lights_punctual": {"lights": list(lights)}}
    return doc


def used_light(index):
    return {"extensions": {"KHR_lights_punctual": {"light": index}}}


def gray_rows(g):
    """(H, W) luminance-like array to a 3-channel linear image (all channels equal)."""
    g = np.asarray(g, dtype=np.float64)
    return np.repeat(g[..., None], 3, axis=2)


def periodic_render(h=32, w=64, mean=0.5):
    """A 2:1 linear map that wraps seamlessly in u, with a gradient in v, scaled so its mean luminance is `mean`."""
    u = np.arange(w) / w
    v = (np.arange(h) + 0.5) / h
    base = (0.6 + 0.4 * np.cos(2 * np.pi * u))[None, :] * (0.5 + 0.5 * v)[:, None]
    img = np.repeat(base[..., None], 3, axis=2)
    return img * (mean / env.mean_luminance(img))


def ok_summary(**over):
    """What the PC side writes to summary.json when the render succeeds (fields env.py reads back)."""
    s = {"status": "ok", "failures": [], "warnings": ["test warning"], "renderFile": "render.hdr",
         "viewTransform": "Standard", "look": "None", "exposure": 0.0, "gamma": 1.0, "samples": 256,
         "denoiser": "OPENIMAGEDENOISE", "device": "CPU", "deviceNames": ["CPU"], "blenderVersion": "5.1.2",
         "skyType": "MULTIPLE_SCATTERING", "skyTypesOffered": ["MULTIPLE_SCATTERING", "SINGLE_SCATTERING"],
         "sun": {"az": 90.0, "el": 35.0}, "turbidity": None,
         "census": {"suns": 0, "lamps": 0, "emissive": 0, "sky": True}}
    s.update(over)
    return s


# ---------- fake PC ----------

class FakePC:
    """Stands in for mac/pcjob (same functions pcsubmit calls). Records calls, answers from memory, sends nothing."""
    POSIX = False

    def __init__(self, state="done", summary=None, render=None, render_name="render.hdr"):
        self.state, self.summary, self.render, self.render_name = state, summary, render, render_name
        self.job_files = None     # what the job folder held when it was sent
        self.job_ps1 = None
        self.calls = []

    def wake(self):
        self.calls.append("wake")
        return False

    def release(self, woke):
        self.calls.append("release")

    def cmd_run(self, a):
        d = Path(a.dir)
        self.job_files = {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}
        self.job_ps1 = (d / "job.ps1").read_text()
        self.calls.append("cmd_run")
        return "job-1"

    def cmd_wait(self, a):
        self.calls.append("cmd_wait")
        return {"state": self.state}

    def cmd_fetch(self, a):
        self.calls.append("cmd_fetch")
        d = Path(a.dest)
        d.mkdir(parents=True, exist_ok=True)
        (d / "blender.log").write_text("ENV test log\n")
        if self.summary is not None:
            (d / "summary.json").write_text(json.dumps(self.summary))
        if self.render is not None:
            env.write_hdr(d / self.render_name, self.render)

    def cmd_rm(self, a):
        self.calls.append("cmd_rm")

    def cmd_cancel(self, a):
        self.calls.append("cmd_cancel")

    def status(self, job_id):
        return {"state": self.state}


class FakeIG:
    def __init__(self, pc):
        self.pc = pc

    def load_pcjob(self):
        return self.pc


# ---------- tests ----------

class EnvCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "out"
        self.lit = self.tmp / "lit.glb"          # a sun, so the venue is lit
        make_glb(self.lit, doc_with(lights=[{"type": "directional", "intensity": 3.0}],
                                    nodes=[used_light(0)]))
        self.dark = self.tmp / "dark.glb"        # no light at all
        make_glb(self.dark, doc_with())
        self.o = ["--out", str(self.out)]

    def fake_pc(self, **kw):
        pc = FakePC(**kw)
        patch = mock.patch.object(env, "load_imagegen", lambda: FakeIG(pc))
        patch.start()
        self.addCleanup(patch.stop)
        return pc

    def refused(self, *argv):
        """argv that parse_args must refuse: argparse exit 2 with a message on stderr. Returns the message."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            env.parse_args(list(argv))
        self.assertEqual(cm.exception.code, 2, f"{argv} should be refused")
        return err.getvalue()


class OptionTests(EnvCase):
    def test_refuses_no_venue_and_no_sky(self):
        self.assertIn("give a venue .glb", self.refused(*self.o))

    def test_refuses_venue_without_at(self):
        self.assertIn("a venue needs --at", self.refused(str(self.lit), *self.o))

    def test_refuses_at_without_venue(self):
        self.assertIn("--at needs a venue", self.refused("--sky", "--sun=0,20", "--at=1,2,3", *self.o))

    def test_refuses_sun_without_sky(self):
        self.assertIn("--sun needs --sky", self.refused(str(self.lit), "--at=0,0,0", "--sun=0,20", *self.o))

    def test_refuses_sky_without_sun(self):
        self.assertIn("--sky needs --sun", self.refused("--sky", *self.o))

    def test_refuses_turbidity_without_sky(self):
        self.assertIn("--turbidity needs --sky",
                      self.refused(str(self.lit), "--at=0,0,0", "--turbidity", "3", *self.o))

    def test_refuses_turbidity_out_of_range(self):
        self.assertIn("--turbidity must be from 1 to 10",
                      self.refused("--sky", "--sun=0,20", "--turbidity", "11", *self.o))

    def test_refuses_bad_sun(self):
        with self.subTest("elevation above the zenith"):
            self.assertIn("between -90 and 90", self.refused("--sky", "--sun=0,95", *self.o))
        with self.subTest("one number"):
            self.assertIn("--sun needs 2 numbers", self.refused("--sky", "--sun=0", *self.o))
        with self.subTest("not a number"):
            self.assertIn("--sun needs numbers", self.refused("--sky", "--sun=east,20", *self.o))

    def test_refuses_bad_at(self):
        self.assertIn("--at needs 3 numbers", self.refused(str(self.lit), "--at=1,2", *self.o))

    def test_refuses_bad_clamp(self):
        self.assertIn("--clamp must be 0 or more", self.refused("--sky", "--sun=0,20", "--clamp=-1", *self.o))

    def test_refuses_bad_sizes(self):
        for size in ("63", "65", "32"):
            with self.subTest(size=size):
                self.assertIn(f"got {size}", self.refused("--sky", "--sun=0,20", "--size", size, *self.o))

    def test_refuses_bad_also(self):
        base = ["--sky", "--sun=0,20", "--size", "64", *self.o]
        with self.subTest("does not divide the largest"):
            self.assertIn("64 does not", self.refused(*base, "--also", "100"))
        with self.subTest("duplicates the size"):
            self.assertIn("repeats", self.refused(*base, "--also", "64"))
        with self.subTest("not a number"):
            self.assertIn("--also takes widths", self.refused(*base, "--also", "128,abc"))

    def test_refuses_bad_name(self):
        self.assertIn("--name must be letters", self.refused("--sky", "--sun=0,20", "--name", "bad name", *self.o))

    def test_refuses_non_glb_venue(self):
        obj = self.tmp / "venue.obj"
        obj.write_text("o venue\n")
        self.assertIn("is not a .glb file", self.refused(str(obj), "--at=0,0,0", *self.o))
        self.assertIn("is not a .glb file", self.refused(str(self.tmp / "missing.glb"), "--at=0,0,0", *self.o))

    def test_accepts_sky_only_with_defaults(self):
        args = env.parse_args(["--sky", "--sun=-90,20", *self.o])
        self.assertEqual(args.sun, [-90.0, 20.0])
        self.assertEqual(args.render_width, 512)
        self.assertEqual(args.name, "sky")
        self.assertIsNone(args.venue)
        self.assertIsNone(args.at)

    def test_accepts_also_and_takes_the_largest_as_render_width(self):
        args = env.parse_args(["--sky", "--sun=-90,20", "--size", "512", "--also", "256,1024", *self.o])
        self.assertEqual(args.render_width, 1024)
        self.assertEqual(args.also, [256, 1024])

    def test_venue_name_defaults_to_a_safe_stem(self):
        lit_doc = doc_with(lights=[{"type": "directional", "intensity": 3.0}], nodes=[used_light(0)])
        spaced = self.tmp / "my venue (v2).glb"
        make_glb(spaced, lit_doc)
        self.assertEqual(env.parse_args([str(spaced), "--at=0,0,0", *self.o]).name, "my_venue_v2")
        odd = self.tmp / "((( .glb"                   # nothing safe left in the stem: falls back to 'venue'
        make_glb(odd, lit_doc)
        self.assertEqual(env.parse_args([str(odd), "--at=0,0,0", *self.o]).name, "venue")


class CensusTests(EnvCase):
    def census_of(self, doc):
        path = self.tmp / "census.glb"
        make_glb(path, doc)
        return env.gltf_census(env.read_glb_json(path))

    def test_a_used_directional_light_is_a_sun(self):
        doc = doc_with(lights=[{"type": "directional", "intensity": 3.0}], nodes=[used_light(0)])
        self.assertEqual(self.census_of(doc), {"suns": 1, "lamps": 0, "emissive": 0})

    def test_point_and_spot_lights_are_lamps(self):
        doc = doc_with(lights=[{"type": "point", "intensity": 10.0}, {"type": "spot", "intensity": 5.0}],
                       nodes=[used_light(0), used_light(1)])
        self.assertEqual(self.census_of(doc), {"suns": 0, "lamps": 2, "emissive": 0})

    def test_zero_intensity_and_unused_lights_do_not_count(self):
        doc = doc_with(lights=[{"type": "directional", "intensity": 0.0}, {"type": "point", "intensity": 10.0}],
                       nodes=[used_light(0)])
        self.assertEqual(self.census_of(doc), {"suns": 0, "lamps": 0, "emissive": 0})

    def test_emissive_materials_on_used_meshes(self):
        materials = [
            {"emissiveFactor": [1.0, 0.0, 0.0]},                                            # counts
            {"emissiveTexture": {"index": 0}},                                              # counts (texture)
            {"emissiveFactor": [1.0, 1.0, 1.0],
             "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 0.0}}},  # strength 0: no
            {"emissiveFactor": [0.2, 0.2, 0.2],
             "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 30.0}}},  # counts
            {"emissiveFactor": [1.0, 1.0, 1.0]},                                            # on an unused mesh
        ]
        meshes = [{"primitives": [{"material": i} for i in range(4)]}, {"primitives": [{"material": 4}]}]
        doc = doc_with(materials=materials, meshes=meshes, nodes=[{"mesh": 0}])
        self.assertEqual(self.census_of(doc), {"suns": 0, "lamps": 0, "emissive": 3})

    def test_zero_emissive_factor_is_not_light(self):
        doc = doc_with(materials=[{"emissiveFactor": [0.0, 0.0, 0.0]}],
                       meshes=[{"primitives": [{"material": 0}]}], nodes=[{"mesh": 0}])
        self.assertEqual(self.census_of(doc)["emissive"], 0)

    def test_unreadable_glb_is_refused(self):
        bad = self.tmp / "bad.glb"
        bad.write_bytes(b"not a glb file at all")
        with self.assertRaises(SystemExit):
            env.read_glb_json(bad)

    def test_check_light_refuses_a_dark_venue_without_sky(self):
        args = env.parse_args([str(self.dark), "--at=0,0,0", *self.o])
        with self.assertRaises(SystemExit) as cm:
            env.check_light(args)
        self.assertIn("has no light", str(cm.exception))

    def test_check_light_passes_a_dark_venue_with_sky(self):
        args = env.parse_args([str(self.dark), "--at=0,0,0", "--sky", "--sun=0,20", *self.o])
        self.assertEqual(env.check_light(args), {"suns": 0, "lamps": 0, "emissive": 0})


class JobTests(EnvCase):
    def test_run_refuses_a_lightless_venue_before_sending(self):
        pc = self.fake_pc()
        submit = mock.Mock(side_effect=AssertionError("submit must not be called"))
        with mock.patch.object(env, "submit", submit), self.assertRaises(SystemExit) as cm:
            env.run(env.parse_args([str(self.dark), "--at=0,0,0", *self.o]))
        self.assertIn("has no light", str(cm.exception))
        submit.assert_not_called()
        self.assertEqual(pc.calls, [])

    def test_job_folder_for_a_lit_venue(self):
        pc = self.fake_pc(state="done", summary=ok_summary(), render=periodic_render())
        env.run(env.parse_args([str(self.lit), "--at=0,0,0", "--size", "64", *self.o]))
        self.assertEqual(set(pc.job_files), {"env_blender.py", "options.json", "venue.glb", "job.ps1"})
        self.assertEqual(pc.job_files["env_blender.py"], PC_SCRIPT.read_bytes())
        self.assertEqual(pc.job_files["venue.glb"], self.lit.read_bytes())
        opts = json.loads(pc.job_files["options.json"])
        self.assertEqual(opts, {"venue": "venue.glb", "at": [0.0, 0.0, 0.0], "sky": None, "clamp": 20.0,
                                "width": 64, "render": "render.hdr"})
        self.assertIn("--options options.json --out $env:RENDER_OUT", pc.job_ps1)

    def test_job_folder_for_sky_only(self):
        pc = self.fake_pc(state="done", summary=ok_summary(), render=periodic_render())
        env.run(env.parse_args(["--sky", "--sun=-90,20", "--size", "64", *self.o]))
        self.assertEqual(set(pc.job_files), {"env_blender.py", "options.json", "job.ps1"})
        opts = json.loads(pc.job_files["options.json"])
        self.assertIsNone(opts["venue"])
        self.assertIsNone(opts["at"])
        self.assertEqual(opts["sky"], {"sun_az": -90.0, "sun_el": 20.0, "turbidity": None})

    def test_job_folder_carries_turbidity_and_a_dark_venue_with_sky_is_sent(self):
        pc = self.fake_pc(state="done", summary=ok_summary(), render=periodic_render())
        env.run(env.parse_args([str(self.dark), "--at=0,0,0", "--sky", "--sun=0,20", "--turbidity", "3",
                                "--size", "64", *self.o]))
        opts = json.loads(pc.job_files["options.json"])
        self.assertEqual(opts["sky"], {"sun_az": 0.0, "sun_el": 20.0, "turbidity": 3.0})
        self.assertIn("venue.glb", pc.job_files)


class RunOutcomeTests(EnvCase):
    def sky_args(self, *extra):
        return env.parse_args(["--sky", "--sun=90,35", "--size", "64", "--name", "t", *self.o, *extra])

    def test_failed_summary_from_a_done_job_is_reported(self):
        self.fake_pc(state="done", summary=ok_summary(status="failed", failures=["RuntimeError: no light"]))
        with self.assertRaises(SystemExit) as cm:
            env.run(self.sky_args())
        self.assertIn("RuntimeError: no light", str(cm.exception))

    def test_failed_job_reports_the_render_reason_and_no_hdr(self):
        self.fake_pc(state="failed", summary=ok_summary(status="failed", failures=["RuntimeError: boom"]))
        with self.assertRaises(SystemExit) as cm:
            env.run(self.sky_args())
        self.assertIn("RuntimeError: boom", str(cm.exception))
        self.assertIn("no HDR written", str(cm.exception))

    def test_a_stale_summary_from_an_earlier_run_is_removed_first(self):
        self.out.mkdir(parents=True)
        (self.out / "summary.json").write_text(json.dumps(ok_summary()))
        env.write_hdr(self.out / "render.hdr", periodic_render())
        self.fake_pc(state="done", summary=None, render=None)
        with self.assertRaises(SystemExit) as cm:
            env.run(self.sky_args())
        self.assertIn("no summary.json", str(cm.exception))
        self.assertFalse((self.out / "summary.json").exists())
        self.assertFalse((self.out / "render.hdr").exists())

    def test_a_nearly_black_render_fails_and_keeps_the_raw_render(self):
        self.fake_pc(state="done", summary=ok_summary(), render=np.zeros((32, 64, 3)))
        with self.assertRaises(SystemExit) as cm:
            env.run(self.sky_args())
        self.assertIn("nearly black", str(cm.exception))
        self.assertTrue((self.out / "render.hdr").is_file())
        self.assertFalse((self.out / "t_env.hdr").exists())


class OutputTests(EnvCase):
    def test_venue_outputs_and_sidecar(self):
        self.fake_pc(state="done", summary=ok_summary(), render=periodic_render(h=64, w=128))
        args = env.parse_args([str(self.lit), "--at=0,0,0", "--size", "128", "--also", "64", "--name", "t", *self.o])
        side = env.run(args)
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, ["blender.log", "summary.json", "t_env.hdr", "t_env.json", "t_env.png",
                                 "t_env_64.hdr"])
        self.assertEqual(env.read_hdr(self.out / "t_env.hdr").shape, (64, 128, 3))
        self.assertEqual(env.read_hdr(self.out / "t_env_64.hdr").shape, (32, 64, 3))
        self.assertEqual(Image.open(self.out / "t_env.png").size, (128, 64))
        on_disk = json.loads((self.out / "t_env.json").read_text())
        self.assertEqual(on_disk, side)
        self.assertEqual(side["venue"], "lit.glb")
        self.assertEqual(side["at"], [0.0, 0.0, 0.0])
        self.assertIsNone(side["sky"])
        self.assertEqual(side["census"], {"suns": 1, "lamps": 0, "emissive": 0})
        self.assertEqual(side["viewTransform"], "Standard")
        self.assertEqual(side["look"], "None")
        self.assertEqual(side["samples"], 256)
        self.assertEqual(side["denoiser"], "OPENIMAGEDENOISE")
        self.assertIn("scene-linear", side["colourSpace"])
        self.assertEqual(side["size"], {"width": 128, "height": 64})
        self.assertEqual(side["renderWidth"], 128)
        self.assertEqual(side["files"], {"hdr": "t_env.hdr", "png": "t_env.png", "json": "t_env.json",
                                         "extraHdr": ["t_env_64.hdr"]})
        self.assertEqual(side["warnings"], ["test warning"])
        self.assertEqual(side["intensityFormula"], "round(0.5 / meanLuminance, 3)")
        self.assertEqual(side["camera"]["rotationDeg"], env.CAM_ROT_DEG)
        self.assertEqual(side["clamp"], 20.0)
        for key in ("tool", "toolVersion", "name", "createdAt", "device", "deviceNames", "blenderVersion",
                    "meanLuminance", "suggestedEnvMapIntensity", "checks"):
            self.assertIn(key, side)

    def test_the_overscan_columns_are_cropped_off(self):
        inner = periodic_render(h=32, w=64)
        pad = 8
        wide = np.concatenate([np.full((32, pad, 3), 99.0), inner, np.full((32, pad, 3), 99.0)], axis=1)
        self.fake_pc(state="done", summary=ok_summary(overscan=pad), render=wide)
        side = env.run(env.parse_args(["--sky", "--sun=90,35", "--size", "64", "--name", "t", *self.o]))
        np.testing.assert_allclose(env.read_hdr(self.out / "t_env.hdr"), inner, rtol=0.02, atol=1e-3)
        self.assertLess(side["checks"]["seamRatio"], env.SEAM_WARN)

    def test_a_render_missing_its_overscan_is_refused(self):
        self.fake_pc(state="done", summary=ok_summary(overscan=8), render=periodic_render(h=32, w=64))
        with self.assertRaises(SystemExit) as cm:
            env.run(env.parse_args(["--sky", "--sun=90,35", "--size", "64", *self.o]))
        self.assertIn("expected 64 + 2 x 8", str(cm.exception))

    def test_sky_sidecar_records_the_sun_and_the_model(self):
        self.fake_pc(state="done", summary=ok_summary(), render=periodic_render())
        side = env.run(env.parse_args(["--sky", "--sun=90,35", "--size", "64", *self.o]))
        self.assertEqual(side["name"], "sky")
        self.assertIsNone(side["venue"])
        self.assertEqual(side["sky"], {"sunAz": 90.0, "sunEl": 35.0, "turbidity": None,
                                       "skyType": "MULTIPLE_SCATTERING",
                                       "skyTypesOffered": ["MULTIPLE_SCATTERING", "SINGLE_SCATTERING"]})
        self.assertTrue((self.out / "sky_env.hdr").is_file())

    def test_intensity_is_half_over_the_mean_luminance(self):
        for mean, expect in ((0.25, 2.0), (1.0, 0.5)):
            with self.subTest(mean=mean):
                self.fake_pc(state="done", summary=ok_summary(), render=periodic_render(mean=mean))
                side = env.run(self.sky_args_plain())
                self.assertAlmostEqual(side["suggestedEnvMapIntensity"], expect, delta=0.01)
                self.assertAlmostEqual(side["meanLuminance"], mean, delta=0.005)

    def sky_args_plain(self):
        return env.parse_args(["--sky", "--sun=90,35", "--size", "64", "--name", "t", *self.o])


class RgbeTests(EnvCase):
    def test_flat_width_round_trip_within_one_128th_of_the_pixel_max(self):
        rng = np.random.default_rng(1)
        img = rng.random((3, 6, 3)) * np.array([50.0, 1.0, 0.001])
        img[0, 0] = 0.0
        path = self.tmp / "flat.hdr"
        env.write_hdr(path, img)
        back = env.read_hdr(path)
        v = img.max(axis=2, keepdims=True)
        self.assertTrue(np.all(np.abs(back - img) <= v / 128 + 1e-9))

    def test_rle_round_trip_with_a_long_run_matches_the_encoder(self):
        rng = np.random.default_rng(3)
        img = (rng.random((2, 64, 3)) * 4.0).astype(np.float32)
        img[:, :20] = 0.75
        path = self.tmp / "rle.hdr"
        env.write_hdr(path, img)
        raw = path.read_bytes()
        self.assertEqual(raw.count(bytes([2, 2, 0, 64])), 2)              # both scanlines are new-style RLE
        back = env.read_hdr(path)
        np.testing.assert_array_equal(back, env.decode_rgbe(env.encode_rgbe(img)))
        v = img.max(axis=2, keepdims=True)
        self.assertTrue(np.all(np.abs(back - img) <= v / 128 + 1e-9))

    def test_zeros_stay_zero_and_tiny_values_encode_to_zero(self):
        self.assertTrue(np.all(env.encode_rgbe(np.zeros((2, 6, 3))) == 0))
        self.assertTrue(np.all(env.encode_rgbe(np.full((1, 1, 3), 1e-40)) == 0))
        path = self.tmp / "zero.hdr"
        env.write_hdr(path, np.zeros((2, 6, 3)))
        self.assertTrue(np.all(env.read_hdr(path) == 0))

    def test_header_and_first_scanline_is_the_top_row(self):
        img = np.zeros((2, 6, 3), np.float32)
        img[0] = 1.0
        img[1] = 0.1
        path = self.tmp / "top.hdr"
        env.write_hdr(path, img)
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"#?RADIANCE\n"))
        self.assertIn(b"FORMAT=32-bit_rle_rgbe\n", raw)
        self.assertIn(b"\n-Y 2 +X 6\n", raw)
        body = raw.split(b"-Y 2 +X 6\n", 1)[1]
        self.assertEqual(body[:6 * 4], env.encode_rgbe(img)[0].tobytes())
        back = env.read_hdr(path)
        np.testing.assert_allclose(back[0], 1.0, rtol=1 / 128)
        np.testing.assert_allclose(back[1], 0.1, rtol=1 / 128)

    def test_run_of_ten_is_one_pair_of_bytes(self):
        self.assertEqual(env._rle_channel(np.array([5] * 10, np.uint8)), bytes([138, 5]))

    def test_literals_are_counted_and_short_runs_stay_literal(self):
        self.assertEqual(env._rle_channel(np.array([1, 2, 3], np.uint8)), bytes([3, 1, 2, 3]))
        self.assertEqual(env._rle_channel(np.array([1, 1, 1, 2], np.uint8)), bytes([4, 1, 1, 1, 2]))

    def test_old_style_repeat_decodes_to_copies(self):
        header = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 8\n"
        path = self.tmp / "old.hdr"
        path.write_bytes(header + bytes([128, 64, 32, 129, 1, 1, 1, 7]))
        back = env.read_hdr(path)
        expect = env.decode_rgbe(np.array([[[128, 64, 32, 129]] * 8], np.uint8))
        np.testing.assert_array_equal(back, expect)

    def test_exposure_divides_the_values(self):
        px = env.encode_rgbe(np.full((1, 6, 3), 0.8))
        path = self.tmp / "exposure.hdr"
        path.write_bytes(b"#?RADIANCE\nEXPOSURE=2.0\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 6\n" + px[0].tobytes())
        np.testing.assert_allclose(env.read_hdr(path), env.decode_rgbe(px) / 2.0)

    def test_garbage_and_truncated_files_raise_value_error(self):
        garbage = self.tmp / "garbage.hdr"
        garbage.write_bytes(b"hello world, not an hdr")
        with self.assertRaises(ValueError):
            env.read_hdr(garbage)
        good = self.tmp / "good.hdr"
        env.write_hdr(good, periodic_render(h=8, w=64))
        cut = self.tmp / "cut.hdr"
        cut.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])
        with self.assertRaises(ValueError):
            env.read_hdr(cut)


class CheckTests(unittest.TestCase):
    def test_a_periodic_render_passes(self):
        checks, failures, warnings = env.check_render(periodic_render())
        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])
        self.assertLess(checks["seamRatio"], env.SEAM_WARN)

    def test_wrong_aspect_fails(self):
        _, failures, _ = env.check_render(periodic_render(h=64, w=64))
        self.assertTrue(any("not 2:1" in f for f in failures))

    def test_black_fails(self):
        _, failures, _ = env.check_render(np.zeros((32, 64, 3)))
        self.assertTrue(any("nearly black" in f for f in failures))

    def test_flat_fails(self):
        _, failures, _ = env.check_render(np.full((32, 64, 3), 0.5))
        self.assertTrue(any("is flat" in f for f in failures))

    def test_a_ramp_across_u_fails_the_seam(self):
        ramp = gray_rows(np.tile(np.linspace(0.0, 1.0, 64), (32, 1)))
        _, failures, _ = env.check_render(ramp)
        self.assertTrue(any("left and right edges do not meet" in f for f in failures))

    def test_a_loose_seam_between_two_and_four_warns(self):
        jump = 2.0                                    # wrap ratio 1 + J = 3
        g = np.tile(np.arange(64) % 2, (32, 1)).astype(float)
        g[:, 63] = (63 % 2) + jump
        _, failures, warnings = env.check_render(gray_rows(g))
        self.assertEqual(failures, [])
        self.assertTrue(any("loosely" in w for w in warnings))

    def test_a_seam_above_four_fails(self):
        g = np.tile(np.arange(64) % 2, (32, 1)).astype(float)
        g[:, 63] = 1.0 + 6.0
        _, failures, _ = env.check_render(gray_rows(g))
        self.assertTrue(any("left and right edges do not meet" in f for f in failures))


class SeamAndWeightTests(unittest.TestCase):
    def test_a_triangle_wave_wraps_exactly(self):
        x = np.arange(16)
        tri = np.tile(np.minimum(x, 16 - x).astype(float), (2, 1))
        self.assertAlmostEqual(env.wrap_seam(gray_rows(tri)), 1.0, places=6)

    def test_a_ramp_wraps_to_its_width_minus_one(self):
        ramp = np.tile(np.arange(16, dtype=float), (2, 1))
        self.assertAlmostEqual(env.wrap_seam(gray_rows(ramp)), 15.0, places=6)

    def test_the_ratio_grows_with_the_edge_jump(self):
        ratios = []
        for jump in (0.0, 1.0, 2.0, 4.0):
            g = np.tile(np.arange(64) % 2, (2, 1)).astype(float)
            g[:, 63] = 1.0 + jump
            r = env.wrap_seam(gray_rows(g))
            self.assertAlmostEqual(r, 1 + jump, places=6)
            ratios.append(r)
        self.assertEqual(ratios, sorted(ratios))
        self.assertLess(ratios[0], ratios[-1])

    def test_a_bright_sun_beside_the_edge_is_not_a_seam(self):
        # a clear sky with the sun 20 columns from the wrap: steep but smooth there, nearly flat everywhere else
        # (the whole-picture mean step made this 3.0 on a real render)
        x = np.arange(512)
        d = np.minimum(np.abs(x - 20), 512 - np.abs(x - 20))
        sky = np.tile(0.05 + 5.0 * np.exp(-(d / 12.0) ** 2), (8, 1))
        self.assertLess(env.wrap_seam(gray_rows(sky)), 1.5)

    def test_a_tiny_offset_on_a_flat_wall_is_not_a_seam(self):
        g = np.full((4, 64), 1.9)
        g[:, :32] = 1.905                             # 0.26% step at the wrap, perfectly flat beside it
        self.assertLess(env.wrap_seam(gray_rows(g)), 1.0)

    def test_a_level_step_at_the_wrap_is_a_seam(self):
        g = np.tile((np.arange(64) % 2).astype(float), (4, 1))
        g[:, :32] += 4.0                              # wrap step |0 + 4 - 1| = 3, steps beside it 1
        self.assertAlmostEqual(env.wrap_seam(gray_rows(g)), 3.0, places=6)

    def test_overscan_widens_the_camera_by_the_cropped_columns(self):
        for w in (256, 512, 1024, 2048):
            pad = EB.overscan_px(w)
            self.assertGreaterEqual(pad, 8)
            # the same angle per column inside and in the overscan
            self.assertAlmostEqual(2 * EB.longitude_span(w) / (w + 2 * pad), 2 * math.pi / w, places=12)

    def test_box_down_averages_two_by_two_blocks(self):
        img = np.zeros((4, 8, 3))
        for y in range(4):
            for x in range(8):
                img[y, x] = x + 10 * y
        out = env.box_down(img, 4)
        self.assertEqual(out.shape, (2, 4, 3))
        self.assertAlmostEqual(out[0, 0, 0], 0.5 + 5.0)
        self.assertAlmostEqual(out[0, 3, 0], 6.5 + 5.0)
        self.assertAlmostEqual(out[1, 0, 0], 0.5 + 25.0)
        with self.assertRaises(ValueError):
            env.box_down(img, 3)

    def test_mean_luminance_of_a_constant_is_one(self):
        self.assertAlmostEqual(env.mean_luminance(np.ones((4, 8, 3))), 1.0, places=9)

    def test_mean_luminance_of_the_top_row_alone_is_pole_weighted(self):
        img = np.zeros((4, 8, 3))
        img[0] = 1.0
        expect = math.cos(0.375 * math.pi) / (2 * (math.cos(0.375 * math.pi) + math.cos(0.125 * math.pi)))
        self.assertAlmostEqual(env.mean_luminance(img), expect, places=6)

    def test_rows_near_the_equator_weigh_more_than_the_poles(self):
        w = env.row_weights(8)
        self.assertLess(w[0], w[3])
        self.assertAlmostEqual(w[3], w[4])
        self.assertIn(int(np.argmax(w)), (3, 4))
        self.assertAlmostEqual(float(w.max()), float(w[3]))


class SkyAndCameraTests(unittest.TestCase):
    def test_sky_model_choice(self):
        pick = EB.pick_sky_type
        every = ["MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "HOSEK_WILKIE", "PREETHAM"]
        self.assertEqual(pick(every), "MULTIPLE_SCATTERING")
        self.assertEqual(pick(["SINGLE_SCATTERING", "HOSEK_WILKIE", "PREETHAM"]), "SINGLE_SCATTERING")
        self.assertEqual(pick(["HOSEK_WILKIE", "PREETHAM"]), "HOSEK_WILKIE")
        self.assertEqual(pick(["PREETHAM"]), "PREETHAM")
        self.assertEqual(pick(every, turbidity=3.0), "HOSEK_WILKIE")
        with self.assertRaises(RuntimeError):
            pick(["MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "PREETHAM"], turbidity=3.0)
        with self.assertRaises(RuntimeError):
            pick([])

    def test_gltf_to_blender_axes(self):
        self.assertEqual(EB.at_to_blender((1, 2, 3)), (1.0, -3.0, 2.0))
        self.assertEqual(EB.at_to_blender((0, 0, 10)), (0.0, -10.0, 0.0))

    def test_camera_rotation_matches_the_sidecar_constant(self):
        degrees = [math.degrees(v) for v in EB.CAM_ROT]
        for got, want in zip(degrees, env.CAM_ROT_DEG):
            self.assertAlmostEqual(got, want, places=9)

    def test_colour_and_sampling_constants(self):
        self.assertEqual(EB.VIEW_TRANSFORM, "Standard")
        self.assertEqual(EB.SAMPLES, 256)
        self.assertEqual(EB.DENOISER, "OPENIMAGEDENOISE")
        self.assertEqual(EB.SKY_ORDER[0], "MULTIPLE_SCATTERING")


if __name__ == "__main__":
    unittest.main()
