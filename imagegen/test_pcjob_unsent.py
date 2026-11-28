"""pcjob job states on a Linux/Mac PC (the sh half of each op, run here with bash): a folder with no 'ready' file was
half sent and lists as 'unsent', never 'queued' (2026-11-27: a half-sent job listed as queued kept a PC awake all
night). prune lists an unsent folder once nothing in it changed for an hour, and removes it with --yes.
Run: python3 -m unittest test_pcjob_unsent"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_pcjob_release import load_pcjob


class Unsent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pc = load_pcjob()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = {k: getattr(self.pc, k) for k in ("ROOT", "on_pc", "status")}
        self.pc.ROOT = str(self.tmp)
        jobs = self.tmp / "jobs"
        made = {
            "j1-done": {"ready": "x", "status.json": json.dumps({"state": "done"})},
            "j2-running": {"ready": "x", "status.json": json.dumps({"state": "running"})},
            "j3-queued": {"ready": "x"},
            "j4-cancelling": {"ready": "x", "cancel": "x"},
            "j5-unsent": {"in.tar": "half"},
            "j6-unsent-old": {"in.tar": "half"},
            "j7-lane": {"ready": "x", "lane": "test\n"},
        }
        for job, files in made.items():
            (jobs / job).mkdir(parents=True)
            for name, text in files.items():
                (jobs / job / name).write_text(text)
        old = time.time() - 2 * 3600
        for p in [jobs / "j6-unsent-old" / "in.tar", jobs / "j6-unsent-old"]:
            os.utime(p, (old, old))

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(self.pc, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sh(self, op, *args):
        return subprocess.run(["bash", "-c", op(*args)[1]], capture_output=True, text=True, check=True).stdout

    def test_list_labels(self):
        rows = dict(reversed(line.split()) for line in self.sh(self.pc.op_list_jobs, "").splitlines())
        self.assertEqual(rows, {"j1-done": "done", "j2-running": "running", "j3-queued": "queued",
                                "j4-cancelling": "cancelling", "j5-unsent": "unsent", "j6-unsent-old": "unsent"})
        self.assertEqual(self.sh(self.pc.op_list_jobs, "test").split(), ["queued", "j7-lane"])

    def test_list_matches_status_and_busy(self):
        for job, want in [("j3-queued", "queued"), ("j5-unsent", "missing"), ("j4-cancelling", "cancelling")]:
            self.assertEqual(json.loads(self.sh(self.pc.op_status, job))["state"], want)
        self.assertEqual(self.sh(self.pc.op_busy_jobs).strip(), "3")   # running, queued, the lane job; never unsent

    def test_prune_list_columns(self):
        rows = {p[0]: p[1:] for p in (line.split("\t") for line in self.sh(self.pc.op_prune_list).splitlines())}
        self.assertEqual(rows["j5-unsent"][0], "unsent")
        self.assertEqual(rows["j5-unsent"][4], "1")       # changed in the last hour: may still be mid-send
        self.assertEqual(rows["j6-unsent-old"][4], "0")
        self.assertEqual(rows["j3-queued"][0], "queued")
        self.assertEqual(rows["j4-cancelling"][0], "cancelling")
        self.assertTrue(all(len(r) == 5 for r in rows.values()))

    def test_prune_removes_only_quiet_unsent(self):
        lines = self.sh(self.pc.op_prune_list)
        gone = []
        self.pc.status = lambda job: {"state": "missing" if "unsent" in job else "queued"}
        self.pc.on_pc = lambda op, *a, **k: lines if op is self.pc.op_prune_list else gone.append(a[0])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.pc.cmd_prune(argparse.Namespace(older_than="7d", legacy=False, yes=True))
        self.assertEqual(gone, ["j6-unsent-old"])
        self.assertIn("no send", out.getvalue())


if __name__ == "__main__":
    unittest.main()
