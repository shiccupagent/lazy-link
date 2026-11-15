"""One job never touches another's ComfyUI output: per-job output folder, leftovers stopped and removed at the start,
and a default timeout that grows with the batch. Run: python3 -m unittest test_batch_jobs"""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from test_cutout import load_batch

import imagegen


class JobFolders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.b = load_batch(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.real_api = self.b.api

    def tearDown(self):
        self.b.api = self.real_api

    def test_every_save_node_gets_the_job_folder(self):
        g = {"save": {"class_type": "SaveAudioMP3", "inputs": {"filename_prefix": "audio/audio"}},
             "img": {"class_type": "SaveImage", "inputs": {"filename_prefix": "thing__klein3"}},
             "other": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        self.b.own_prefixes(g)
        fp = g["save"]["inputs"]["filename_prefix"]
        self.b.own_prefixes(g)   # twice is the same as once
        self.assertEqual(g["save"]["inputs"]["filename_prefix"], fp)
        self.assertTrue(fp.startswith(self.b.JOB + "-") and fp.endswith("/audio/audio"), fp)
        self.assertEqual(g["img"]["inputs"]["filename_prefix"].split("/")[0], fp.split("/")[0])
        self.assertNotIn("filename_prefix", g["other"]["inputs"])

    def test_same_graph_twice_saves_to_two_folders(self):
        """Two identical asks must not be the same ComfyUI graph, or the second comes back from its cache."""
        mk = lambda: {"save": {"class_type": "SaveAudioMP3", "inputs": {"filename_prefix": "audio/audio"}}}
        a, b = self.b.own_prefixes(mk()), self.b.own_prefixes(mk())
        self.assertNotEqual(a, b)

    def test_two_jobs_never_share_a_folder(self):
        other = load_batch(Path(tempfile.mkdtemp(dir=self.tmp)))
        self.assertNotEqual(other.JOB, self.b.JOB)

    def test_sub_parts_drops_the_job_folder(self):
        j = self.b.JOB
        self.assertEqual(self.b.sub_parts({"subfolder": f"{j}-3\\cutframes"}), ["cutframes"])
        self.assertEqual(self.b.sub_parts({"subfolder": f"{j}-12/audio"}), ["audio"])
        self.assertEqual(self.b.sub_parts({"subfolder": f"{j}-1"}), [])
        self.assertEqual(self.b.sub_parts({}), [])

    def fake_api(self, running, pending):
        state = {"running": list(running), "pending": list(pending), "calls": []}

        def api(path, body=None, timeout=30):
            state["calls"].append((path, body))
            if path == "/queue" and body is None:
                return {"queue_running": state["running"], "queue_pending": state["pending"]}
            if path == "/queue" and body == {"clear": True}:
                state["pending"] = []
            if path == "/interrupt":
                state["running"] = []
            return None
        self.b.api = api
        return state

    def test_leftovers_stopped_and_removed(self):
        out = self.b.SCRATCH / "out"
        (out / "audio").mkdir(parents=True, exist_ok=True)
        (out / "audio" / "audio_00001.mp3").write_bytes(b"x")
        (out / "old.png").write_bytes(b"x")
        st = self.fake_api(running=[["p1"]], pending=[["p2"], ["p3"]])
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.b.clear_leftovers()
        self.assertIn(("/queue", {"clear": True}), st["calls"])
        self.assertIn(("/interrupt", {}), st["calls"])
        self.assertIn("stopped 3 prompt(s)", log.getvalue())
        self.assertEqual(list(out.iterdir()), [])

    def test_clean_start_touches_nothing(self):
        st = self.fake_api(running=[], pending=[])
        with contextlib.redirect_stdout(io.StringIO()):
            self.b.clear_leftovers()
        self.assertEqual([c for c in st["calls"] if c[1] is not None], [])


class AskCheck(unittest.TestCase):
    def errs(self, asks):
        return imagegen.ask_errors("music3", imagegen.MODELS["recipes"]["music3"], asks)

    def test_flat_prompt_is_refused(self):
        e = self.errs([{"name": "a", "prompt": "night ambient"}])
        self.assertTrue(any("'prompt' belongs inside" in x for x in e), e)

    def test_same_take_twice_is_refused(self):
        p = {"prompt": "night ambient", "seconds": 60}
        e = self.errs([{"name": "a", "params": p}, {"name": "b", "params": dict(p)}])
        self.assertTrue(any("same params as a" in x for x in e), e)

    def test_seeds_make_them_different(self):
        e = self.errs([{"name": "a", "params": {"prompt": "x", "seed": 1}}, {"name": "b", "params": {"prompt": "x", "seed": 2}}])
        self.assertEqual(e, [])


class Timeout(unittest.TestCase):
    def quiet(self, *a):
        with contextlib.redirect_stdout(io.StringIO()):
            return imagegen.job_timeout(*a)

    def test_given_timeout_wins(self):
        self.assertEqual(self.quiet(9000, 36, 300, "asks"), 9000)

    def test_small_batch_keeps_the_hour(self):
        self.assertEqual(self.quiet(None, 5, 300, "asks"), 3600)
        self.assertEqual(self.quiet(None, 50, 0, "asks"), 3600)

    def test_big_music_batch_grows(self):
        t = self.quiet(None, 36, imagegen.MODELS["recipes"]["music3"]["ask_s"], "asks")
        self.assertGreaterEqual(t, 36 * 295)


if __name__ == "__main__":
    unittest.main()
