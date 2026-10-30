#!/usr/bin/env python3
"""bake - light maps and ambient occlusion for a .glb, baked on the PC's NVIDIA GPU with Blender (Cycles).
Windows PC only for now: other PCs are refused before anything is woken or sent.
The Mac side sends the job with its own _submit, not imagegen.send: it waits in the PC's queue, fetches the
results, and dequeues or cancels the job if it stops early. pc/bake_blender.py does the work on the PC.

  from bake import bake
  summary = bake("MODEL.glb", "out/")            # glb=None bakes the built-in test room

  python3 imagegen/bake.py MODEL.glb --out DIR [--mode {full,hybrid}] [--make-test-scene] [--ceiling] [--reunwrap]
                  [--allow-dark NAME[,NAME]] [--opt key=value ...] [--timeout S] [--wait S]

Modes (--mode, default hybrid; the sidecar says which one was baked). Which one for which scene:
  full    direct + indirect light, already shadowed. For props on their own, static backdrops, and any
          room with no realtime lights. No aoMap.
  hybrid  bounce only. For scenes that keep realtime lights and apply aoMap. A prop on its own has almost no
          bounce, so hybrid fails the dark check on it: use full.

A mesh whose light map is black, whose AO is black, or whose AO is flat (all 1) fails the job: no GLB is
written, and summary.json says "status": "failed" with the reasons. --allow-dark NAME[,NAME] lets a genuinely
unlit mesh through; it is listed as dark_allowed in summary.json. A material's alpha counts only when a
texture drives it: a factor alpha is baked as opaque (see pc/bake_blender.py).

DIR gets <mesh>_lightmap.png (sRGB), <mesh>_lightmap.json ({mode, intensity, colorSpace, aoMap}),
<mesh>_ao.png (linear, R; hybrid only), manifest.json, <stem>_baked.glb (the bake layer is TEXCOORD_1),
summary.json and blender.log. Options and their defaults: the header of pc/bake_blender.py.
Unknown options are refused here, before anything is sent.
A job that waits in the PC's queue is normal: bake waits up to --wait seconds (default 3 h, queue
included), prints the queue position while it waits, then fetches the results.
"""
import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TIMEOUT = 1800       # PC-side Blender limit for one bake
WAIT_LIMIT = 3 * 3600        # seconds bake waits for the PC (queue + run) before it gives up
WAIT_POLL = 20               # seconds between PC status checks
MODES = ("full", "hybrid")
DEFAULT_MODE = "hybrid"
OPTIONS = {"stem", "make_test_scene", "ceiling", "device", "budget", "margin", "samples", "lights",
           "sun_strength", "sky_strength", "gain", "ao_distance", "denoise", "min_size", "max_size",
           "reunwrap", "mode", "allow_dark"}

# Blender writes its own logging to stdout; keep the lines that matter, and always keep the log.
PS1 = ("$ErrorActionPreference = 'Continue'\n"
       "if (-not $env:BLENDER) { [Console]::Error.WriteLine('bake: Blender is not installed on this PC'); exit 3 }\n"
       "& $env:BLENDER -b --factory-startup --python-exit-code 1 -P bake_blender.py -- "
       "--options options.json --out $env:RENDER_OUT 2>&1 "
       "| Tee-Object -FilePath (Join-Path $env:RENDER_OUT 'blender.log') "
       "| ForEach-Object { \"$_\" } | Select-String 'BAKE|Error|Traceback'\n"
       "exit $LASTEXITCODE\n")


def _imagegen():
    """imagegen.py is loaded by path: this package folder is also named imagegen, so a plain import
    can resolve to the folder instead of the module."""
    spec = importlib.util.spec_from_file_location("imagegen_cli", HERE / "imagegen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _job_queue(pc, job_id):
    """One PC call: (state of job_id, jobs ahead of it). The PC runs one job at a time, oldest first, so
    'ahead' counts the queued or running jobs listed before it. A job with no status.json is queued."""
    ahead = 0
    for line in pc.on_pc(pc.op_list_jobs).splitlines():
        parts = line.split()   # op_list_jobs pads the state column, so split on any whitespace
        if len(parts) != 2:
            continue
        state, name = parts
        if name == job_id:
            return state, ahead
        if state in ("queued", "running"):
            ahead += 1
    return "missing", ahead


def _wait_for_pc(pc, job_id, limit):
    """Poll until the PC finishes job_id. The limit covers the queue and the run; the queue position is
    printed each time it changes. Returns the final state ('done', 'failed', ...)."""
    deadline = time.time() + limit
    last, missing = None, 0
    while True:
        state, ahead = _job_queue(pc, job_id)
        missing = missing + 1 if state == "missing" else 0
        if missing >= 3:   # a status read can come back empty while the PC writes; three in a row is real
            raise SystemExit(f"bake: job {job_id} is no longer on the PC")
        label = f"queued, {ahead} job(s) ahead on the PC" if state == "queued" else state
        if label != last:
            print(f"{job_id}: {label}", flush=True)
            last = label
        if state not in ("queued", "running", "missing"):
            return state
        if time.time() > deadline:
            raise SystemExit(f"bake: gave up after {limit} s; job {job_id} is still {state} on the PC")
        time.sleep(WAIT_POLL)


def _failure_reason(out):
    """The bake's own reason for failing, from summary.json (the PC side writes it before it fails), or None."""
    f = out / "summary.json"
    if not f.exists():
        return None
    try:
        s = json.loads(f.read_text())
    except ValueError:
        return None
    if s.get("status") != "failed":
        return None
    return (f"bake failed: {' | '.join(s.get('failures', []))} (no GLB written; "
            f"see {out / 'blender.log'})")


def _submit(ig, files, ps1, out, timeout, wait):
    """Send the bake job, wait for it (queue included, bounded by `wait` seconds), fetch the results into
    `out`, and remove the PC job folder. If bake stops early, the job is dequeued (or cancelled while it
    runs) so nothing is left behind. imagegen.send does not do that: its cmd_run stops waiting after
    timeout+300 s while the job is still queued."""
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
            job_id = pc.cmd_run(argparse.Namespace(dir=str(job), name="bake", timeout=timeout,
                                                   wait=False, fetch=None))
        state = _wait_for_pc(pc, job_id, wait)
        pc.cmd_fetch(argparse.Namespace(id=job_id, dest=str(out)))   # log and blender.log come back even on failure
        pc.cmd_rm(argparse.Namespace(id=job_id))
        job_id = None
        if state != "done":
            sys.exit(_failure_reason(out) or f"bake: the PC job ended as {state}; see {out / 'blender.log'}")
    finally:
        if job_id is not None:   # stopped early: take our own job off the queue, or cancel it if it runs
            state = _job_queue(pc, job_id)[0]
            if state == "queued":
                pc.cmd_rm(argparse.Namespace(id=job_id))
                print(f"bake: {job_id} removed from the PC queue", flush=True)
            elif state == "running":
                pc.cmd_cancel(argparse.Namespace(id=job_id))
                busy = True   # never sleep the PC under a job that is still running
                print(f"bake: cancel sent to running job {job_id}; the PC stays on", flush=True)
        if not busy:
            pc.release(woke)   # back to sleep if we woke it and nobody is on it


def bake(glb, out, timeout=DEFAULT_TIMEOUT, wait=WAIT_LIMIT, **opts):
    """Bake light maps and AO for one .glb (or the test room when glb is None). Returns the summary dict
    from DIR/summary.json. Raises SystemExit when the PC job fails, writes no summary, or reports a failed
    status (a black or flat map; allow_dark lets a genuinely unlit mesh through)."""
    ig = _imagegen()
    if ig.load_pcjob().POSIX:   # refused before anything is woken or sent
        raise SystemExit("bake: Windows PC only for now")
    unknown = set(opts) - OPTIONS
    if unknown:
        raise SystemExit(f"bake: unknown option(s) {sorted(unknown)}; known: {sorted(OPTIONS)}")
    out = Path(out).expanduser().resolve()
    cfg = dict(opts)
    cfg.setdefault("mode", DEFAULT_MODE)
    if cfg["mode"] not in MODES:
        raise SystemExit(f"bake: mode must be one of {list(MODES)}, got {cfg['mode']!r}")
    if "allow_dark" in cfg:
        names = cfg["allow_dark"].split(",") if isinstance(cfg["allow_dark"], str) else list(cfg["allow_dark"])
        cfg["allow_dark"] = [str(n).strip() for n in names if str(n).strip()]
    files = [(HERE / "pc" / "bake_blender.py", "bake_blender.py")]
    src = None
    if glb is None:
        cfg["make_test_scene"] = True
    else:
        src = Path(glb).expanduser().resolve()
        if not src.is_file() or src.suffix.lower() != ".glb":
            raise SystemExit(f"bake: {src} is not a .glb file")
        cfg["glb"] = "input.glb"
        cfg["make_test_scene"] = False
    (out / "summary.json").unlink(missing_ok=True)   # a summary from an earlier run is not this run's result
    with tempfile.TemporaryDirectory() as td:
        cfg_file = Path(td) / "options.json"
        cfg_file.write_text(json.dumps(cfg, indent=1))
        files.append((cfg_file, "options.json"))
        if src is not None:
            files.append((src, "input.glb"))
        _submit(ig, files, PS1, out, timeout, wait)
    summary_file = out / "summary.json"
    if not summary_file.exists():
        raise SystemExit(f"bake: no summary.json in {out}; see {out / 'blender.log'}")
    summary = json.loads(summary_file.read_text())
    if summary.get("status") == "failed":
        raise SystemExit(f"bake failed: {' | '.join(summary.get('failures', []))}; see {out / 'blender.log'}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Bake light maps and AO for a .glb on the PC (Windows PC only).")
    ap.add_argument("glb", nargs="?", help="input .glb (omit with --make-test-scene)")
    ap.add_argument("--out", required=True, help="output folder (fetched from the PC)")
    ap.add_argument("--make-test-scene", action="store_true", help="bake the built-in test room")
    ap.add_argument("--ceiling", action="store_true", help="test room with a ceiling")
    ap.add_argument("--reunwrap", action="store_true",
                    help="always re-unwrap UV2 with Smart UV (default: only when it overlaps or covers little)")
    ap.add_argument("--allow-dark", default=None, metavar="NAME[,NAME]",
                    help="meshes allowed to bake black or flat (a genuinely unlit mesh); listed in summary.json")
    ap.add_argument("--mode", choices=MODES, default=None,
                    help="hybrid (default): bounce-only light map, used with the game's realtime lights and aoMap; "
                         "full: direct + indirect light map, no realtime lights, no aoMap")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="PC-side limit for the bake, seconds")
    ap.add_argument("--wait", type=int, default=WAIT_LIMIT,
                    help="how long to wait for the PC, queue included, seconds (default 3 h)")
    ap.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE",
                    help="one bake option, e.g. --opt budget=2048 (JSON values; others are strings)")
    a = ap.parse_args()
    if not a.glb and not a.make_test_scene:
        ap.error("give a .glb or --make-test-scene")
    opts = {}
    for kv in a.opt:
        if "=" not in kv:
            ap.error(f"--opt needs KEY=VALUE, got {kv!r}")
        k, v = kv.split("=", 1)
        try:
            opts[k] = json.loads(v)
        except json.JSONDecodeError:
            opts[k] = v
    if a.ceiling:
        opts["ceiling"] = True
    if a.reunwrap:
        opts["reunwrap"] = True
    if a.allow_dark:
        opts["allow_dark"] = a.allow_dark.split(",")
    if a.mode:
        if "mode" in opts and opts["mode"] != a.mode:
            ap.error(f"--mode {a.mode} and --opt mode={opts['mode']} disagree")
        opts["mode"] = a.mode
    summary = bake(None if a.make_test_scene else a.glb, a.out, a.timeout, wait=a.wait, **opts)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    sys.exit(main())
