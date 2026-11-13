"""pcjob release(): after a finished job the put-down never fails the caller; it leaves the PC on when someone is
using it or another job is queued or running. Run: python3 -m unittest test_pcjob_release"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_pcjob():
    os.environ["LAZY_LINK_CONFIG"] = str(REPO / "no-such-config.json")   # never the personal one
    loader = importlib.machinery.SourceFileLoader("pcjob_under_test", str(REPO / "mac" / "pcjob"))
    spec = importlib.util.spec_from_loader("pcjob_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Release(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pc = load_pcjob()

    def setUp(self):
        self.saved = {k: getattr(self.pc, k) for k in ("in_use", "on_pc", "cmd_sleep")}
        self.slept = []
        self.pc.in_use = lambda: False
        self.pc.on_pc = lambda op, *a, **k: "0"
        self.pc.cmd_sleep = lambda a: self.slept.append(a)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(self.pc, k, v)

    def run_release(self, woke=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.pc.release(woke)   # must not raise SystemExit
        return out.getvalue() + err.getvalue()

    def test_not_woken_does_nothing(self):
        self.pc.in_use = lambda: self.fail("touched the PC")
        self.run_release(woke=False)
        self.assertEqual(self.slept, [])

    def test_idle_pc_goes_down(self):
        self.run_release()
        self.assertEqual(len(self.slept), 1)

    def test_in_use_stays_on(self):
        self.pc.in_use = lambda: True
        self.assertIn("someone is signed in", self.run_release())
        self.assertEqual(self.slept, [])

    def test_queued_job_stays_on_without_failing(self):
        self.pc.on_pc = lambda op, *a, **k: "2" if op is self.pc.op_busy_jobs else "0"
        self.assertIn("2 job(s) queued or running", self.run_release())
        self.assertEqual(self.slept, [])

    def test_sleep_refusal_is_a_note_not_an_exit(self):
        def refuse(a):
            raise SystemExit("pcjob: 1 job(s) queued or running; not putting the PC to sleep")
        self.pc.cmd_sleep = refuse
        self.assertIn("leaving the PC on", self.run_release())


if __name__ == "__main__":
    unittest.main()
