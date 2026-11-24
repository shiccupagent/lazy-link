"""Reference pictures ("refs"): sent with the job, each added to BOTH the positive and the negative as in ComfyUI's
Klein image-edit templates, and the result takes the first one's size by default. Run: python3 -m unittest test_refs
(needs Pillow)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from test_cutout import load_batch

import imagegen


class Refs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.b = load_batch(cls.tmp)
        cls.b.upload = lambda path: "up-" + Path(path).name
        Image.new("RGB", (510, 300), (255, 0, 255)).save(cls.tmp / "hero.png")
        Image.new("RGB", (64, 64)).save(cls.tmp / "pose.png")
        cls.files = {"unet": "u", "clip": "c", "vae": "v"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ask(self, **kw):
        return dict({"name": "x", "prompt": "p", "refs": [str(self.tmp / "hero.png"), str(self.tmp / "pose.png")]}, **kw)

    def test_each_ref_goes_into_positive_and_negative(self):
        g = self.b.graph(self.b.M["models"]["klein3"], self.files, self.ask(), "x__klein3")
        s = g["sample"]["inputs"]
        self.assertEqual(s["positive"], ["ref1_pos", 0])
        self.assertEqual(s["negative"], ["ref1_neg", 0])
        self.assertEqual(g["ref1_pos"]["inputs"]["conditioning"], ["ref0_pos", 0])
        self.assertEqual(g["ref0_pos"]["inputs"]["conditioning"], ["pos", 0])
        self.assertEqual(g["ref0_neg"]["inputs"]["conditioning"], ["neg", 0])
        self.assertEqual(g["ref0"]["inputs"]["image"], "up-hero.png")
        self.assertEqual(g["ref1_lat"]["class_type"], "VAEEncode")

    def test_size_follows_the_first_ref_unless_given(self):
        a = self.ask()
        g = self.b.graph(self.b.M["models"]["klein3"], self.files, a, "x__klein3")
        self.assertEqual((g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]), (512, 304))
        self.assertEqual((a["w"], a["h"]), (512, 304))   # the timings row reports the real size
        g = self.b.graph(self.b.M["models"]["klein3"], self.files, self.ask(w=1024, h=768), "x__klein3")
        self.assertEqual((g["latent"]["inputs"]["width"], g["latent"]["inputs"]["height"]), (1024, 768))

    def test_flux2_graph_too(self):
        g = self.b.graph(self.b.M["models"]["klein"], self.files, self.ask(w=1024, h=1024), "x__klein")
        self.assertEqual(g["guider"]["inputs"]["positive"], ["ref1_pos", 0])

    def test_no_refs_is_unchanged(self):
        g = self.b.graph(self.b.M["models"]["klein3"], self.files, {"name": "x", "prompt": "p"}, "x__klein3")
        self.assertEqual(g["sample"]["inputs"]["positive"], ["pos", 0])
        self.assertFalse(any(k.startswith("ref") for k in g))

    def test_model_without_reference_input_is_refused(self):
        with self.assertRaises(SystemExit):
            self.b.graph(self.b.M["models"]["zimage"], self.files, self.ask(w=1024, h=1024), "x__zimage")

    def test_asks_send_their_refs(self):
        d = self.tmp / "job"
        d.mkdir(exist_ok=True)
        shutil.copy(self.tmp / "pose.png", d / "pose.png")
        (d / "asks.json").write_text(json.dumps([{"name": "a", "prompt": "p", "refs": ["pose.png", str(self.tmp / "hero.png")]}]))
        asks, inputs = imagegen.with_pictures(d / "asks.json")
        self.assertEqual(asks[0]["refs"], ["inputs/pose.png", "inputs/hero.png"])
        self.assertEqual(sorted(dest for _, dest in inputs), ["inputs/hero.png", "inputs/pose.png"])
        (d / "asks.json").write_text(json.dumps([{"name": "a", "prompt": "p", "refs": "pose.png"}]))
        with self.assertRaises(SystemExit):
            imagegen.with_pictures(d / "asks.json")


if __name__ == "__main__":
    unittest.main()
