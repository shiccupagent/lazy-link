#!/usr/bin/env python3
"""pcsubmit - send one Blender job folder to the PC and bring its results back. Shared by bake.py and env.py.

  from pcsubmit import submit, load_imagegen
  ig = load_imagegen()                       # imagegen.py, loaded by path (this folder is also named imagegen)
  submit(ig, files, ps1, out, timeout, wait, name="bake", label="bake", on_fail=reason_fn)

files is a list of (source path, path inside the job folder); ps1 is the job.ps1 text. The job waits in the PC's
queue for up to `wait` seconds (pcjob's wait: the run's own `timeout` starts when the job starts), the results are
fetched into `out` even on failure, and the job folder on the PC is removed. If the caller stops early, the job is
dequeued (or cancelled while it runs) so nothing is left behind, and the PC goes back to sleep only when nobody is on it.
on_fail(out) returns the caller's own reason for a failed job (or None); label prefixes the fallback message.
"""
import argparse
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_imagegen():
    """imagegen.py is loaded by path: this package folder is also named imagegen, so a plain import
    can resolve to the folder instead of the module."""
    spec = importlib.util.spec_from_file_location("imagegen_cli", HERE / "imagegen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def submit(ig, files, ps1, out, timeout, wait, name, label, on_fail=None):
    pc = ig.load_pcjob()
    woke = pc.wake()
    job_id = None
    busy = False
    try:
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "job"
            job.mkdir()
            for src, rel in files:
                (job / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, job / rel)
            (job / "job.ps1").write_text(ps1)
            job_id = pc.cmd_run(argparse.Namespace(dir=str(job), name=name, timeout=timeout,
                                                   wait=False, fetch=None))
        state = pc.cmd_wait(argparse.Namespace(id=job_id, max=timeout + 300, queue_max=wait))["state"]
        pc.cmd_fetch(argparse.Namespace(id=job_id, dest=str(out)))   # log and blender.log come back even on failure
        pc.cmd_rm(argparse.Namespace(id=job_id))
        job_id = None
        if state != "done":
            reason = on_fail(out) if on_fail else None
            sys.exit(reason or f"{label}: the PC job ended as {state}; see {out / 'blender.log'}")
    finally:
        if job_id is not None:   # stopped early: take our own job off the queue, or cancel it if it runs
            state = pc.status(job_id)["state"]
            if state == "queued":
                pc.cmd_rm(argparse.Namespace(id=job_id))
                print(f"{label}: {job_id} removed from the PC queue", flush=True)
            elif state == "running":
                pc.cmd_cancel(argparse.Namespace(id=job_id))
                busy = True   # never sleep the PC under a job that is still running
                print(f"{label}: cancel sent to running job {job_id}; the PC stays on", flush=True)
        if not busy:
            pc.release(woke)   # back to sleep if we woke it and nobody is on it
