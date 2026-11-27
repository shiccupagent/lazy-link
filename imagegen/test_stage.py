"""Job staging: files go into the job folder as hard links (no extra disk), a second name for the same file and a file
on another disk are copied, and the folder is gone before the wait for the job starts.
Run: python3 -m unittest test_stage"""
import os
import shutil
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import imagegen


class Stage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "clip.bin"
        self.src.write_bytes(b"x" * 4096)
        (self.tmp / "job").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_links_then_copies_a_second_name(self):
        seen = set()
        imagegen.stage(self.src, self.tmp / "job" / "a.bin", seen)
        imagegen.stage(self.src, self.tmp / "job" / "b.bin", seen)
        self.assertEqual(os.stat(self.tmp / "job" / "a.bin").st_ino, os.stat(self.src).st_ino)
        self.assertNotEqual(os.stat(self.tmp / "job" / "b.bin").st_ino, os.stat(self.src).st_ino)   # tar: no link entry
        self.assertEqual((self.tmp / "job" / "b.bin").read_bytes(), self.src.read_bytes())

    def test_copies_when_a_link_is_refused(self):
        with mock.patch("os.link", side_effect=OSError(18, "Cross-device link")):
            imagegen.stage(self.src, self.tmp / "job" / "a.bin", set())
        self.assertNotEqual(os.stat(self.tmp / "job" / "a.bin").st_ino, os.stat(self.src).st_ino)
        self.assertEqual((self.tmp / "job" / "a.bin").read_bytes(), self.src.read_bytes())

    def test_staging_is_gone_before_the_wait(self):
        seen = {}

        class PC:
            POSIX, CFG = False, {}

            def cmd_run(self, a):
                self.assertFalse(a.wait or a.fetch)
                seen["dir"] = Path(a.dir)
                seen["files"] = sorted(p.name for p in Path(a.dir).iterdir())
                a.timeout = a.timeout or 3600
                return "job-1"

            def finish(self, job_id, a):
                seen["exists_at_wait"] = seen["dir"].exists()
                seen["finish"] = (job_id, a.fetch, a.timeout)

            def cmd_rm(self, a):
                seen["rm"] = a.id

        pc = PC()
        pc.assertFalse = self.assertFalse
        imagegen._send(pc, [self.src, (self.src, "sub/clip.bin")], "ps1", [], "t", self.tmp / "out", None, None, False)
        self.assertIn("clip.bin", seen["files"])
        self.assertFalse(seen["exists_at_wait"])
        self.assertEqual(seen["finish"], ("job-1", str(self.tmp / "out"), 3600))
        self.assertEqual(seen["rm"], "job-1")


if __name__ == "__main__":
    unittest.main()
