#!/usr/bin/env python3
"""imagegen - make images, sounds and 3D models on your PC's NVIDIA GPU with open models (ComfyUI,
headless) and bring the files back to the Mac. Models, licences and settings: models.json.

  imagegen.py setup                          install our own ComfyUI on the PC (pinned in models.json)
  imagegen.py sizes                          what each model / recipe downloads, and its licence

  imagegen.py run ASKS.json --out DIR [--models zimage,klein,krea2] [--timeout S]
  imagegen.py one "prompt" --out DIR [--model krea2] [--size 1024x1024] [--seed N] [--name N]
  imagegen.py fetch [--models krea2] [--recipes audio,mesh]   download missing model files (official repos, sha256-checked)
  imagegen.py make RECIPE ASKS.json --out DIR   sound (sfx, audio) or 3D (mesh) from a converted template
  imagegen.py preview DIR                    four turntable renders of every .glb in DIR (Blender on the PC) -> DIR/preview/
  imagegen.py convert TEMPLATE ...           ComfyUI template -> recipes/TEMPLATE.api.json (generated; rerun, never hand-edit)

For make, ASKS.json is a list of {"name": "...", "params": {"prompt": "...", ...}, "image": "in.png"}:
params are the recipe's named inputs (models.json "recipes"), image (3D) is a local file.
ASKS.json is a list of {"name": "...", "prompt": "...", "w": 1024, "h": 1024, "seed": 1, "cutout": false}.
Each result is DIR/<name>__<model>.png (+ <name>__<model>-cutout.png with a transparent
background when "cutout" is true), plus DIR/timings.json and DIR/comfy.log.
"""
import argparse, importlib.machinery, importlib.util, json, shutil, sys, tempfile, time
from argparse import Namespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MODELS = json.load(open(HERE / "models.json"))
DEFAULT = "krea2"   # best of the three in our side-by-side tests (see docs/HOW-IT-WORKS.md)

RUN_PS1 = r"""$ErrorActionPreference = 'Stop'
$py = Join-Path $env:RENDER_ROOT ((Get-Content models.json -Raw | ConvertFrom-Json).comfy.venv + '\Scripts\python.exe')
& $py -u batch.py
exit $LASTEXITCODE
"""


def load_pcjob():
    loader = importlib.machinery.SourceFileLoader("pcjob", str(REPO / "mac" / "pcjob"))
    spec = importlib.util.spec_from_loader("pcjob", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def model_keys(s):
    keys = [k.strip() for k in s.split(",") if k.strip()]
    bad = [k for k in keys if k not in MODELS["models"]]
    if bad:
        sys.exit(f"imagegen: unknown model {bad}; known: {', '.join(MODELS['models'])}")
    return keys


def send(files, ps1, want, name, out, timeout):
    pc = load_pcjob()
    woke = pc.wake()
    with tempfile.TemporaryDirectory() as td:
        job = Path(td) / "job"
        job.mkdir()
        for f in files:  # a path, or (path, place in the job folder)
            src, rel = (f, Path(f).name) if not isinstance(f, tuple) else f
            (job / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, job / rel)
        (job / "job.ps1").write_text(ps1)
        json.dump(want, open(job / "want.json", "w"))
        # the PC-side scripts read your extra model folders from here (config.json, never anything else)
        json.dump({"extra_model_folders": pc.CFG.get("extra_model_folders", {})}, open(job / "link.json", "w"))
        job_id = pc.cmd_run(Namespace(dir=str(job), name=name, timeout=timeout, wait=True, fetch=str(out)))
    pc.cmd_rm(Namespace(id=job_id))
    pc.release(woke)   # back to sleep if we woke it and nobody is on it


def run(asks, keys, out, timeout):
    out = Path(out).expanduser().resolve()
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "asks.json"
        json.dump(asks, open(a, "w"), indent=1)
        send([HERE / "models.json", HERE / "pc" / "batch.py", a], RUN_PS1, keys, "imagegen", out, timeout)
    t = json.load(open(out / "timings.json"))
    print(f"ComfyUI start {t['comfy_boot_s']} s")
    for r in t["runs"]:
        print(f"{r['model']:7} {r['ask']:24} {r['seconds']:6.1f} s{'  (first: loads the model)' if r['cold'] else ''}")
    print(f"images in {out}")


def make(key, asks, out, timeout):
    r = MODELS["recipes"][key]
    out = Path(out).expanduser().resolve()
    base = Path(asks).parent
    asks = json.load(open(asks))
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "asks.json"
        json.dump(asks, open(a, "w"), indent=1)
        files = [HERE / "models.json", HERE / "pc" / "batch.py", a,
                 (HERE / "recipes" / f"{r['template']}.api.json", f"recipes/{r['template']}.api.json")]
        files += [(base / x["image"], f"inputs/{x['image']}") for x in asks if "image" in x]
        send(files, RUN_PS1, {"recipe": key}, f"imagegen-{key}", out, timeout)
    t = json.load(open(out / "timings.json"))
    print(f"ComfyUI start {t['comfy_boot_s']} s")
    for row in t["runs"]:
        print(f"{key:6} {row['ask']:24} {row['seconds']:7.1f} s  {row['files']} file(s){'  (first: loads the model)' if row['cold'] else ''}")
    print(f"results in {out}")


def main():
    p = argparse.ArgumentParser(prog="imagegen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("asks"); r.add_argument("--out", required=True)
    r.add_argument("--models", default=DEFAULT); r.add_argument("--timeout", type=int, default=3600)
    o = sub.add_parser("one"); o.add_argument("prompt"); o.add_argument("--out", required=True)
    o.add_argument("--model", default=DEFAULT); o.add_argument("--size", default="1024x1024")
    o.add_argument("--seed", type=int, default=1); o.add_argument("--name", default="image")
    o.add_argument("--cutout", action="store_true", help="also save a transparent-background copy")
    o.add_argument("--timeout", type=int, default=1800)
    f = sub.add_parser("fetch"); f.add_argument("--models", default=DEFAULT); f.add_argument("--recipes", default="")
    sub.add_parser("setup"); sub.add_parser("sizes")
    mk = sub.add_parser("make"); mk.add_argument("recipe", choices=[k for k in MODELS["recipes"] if not k.startswith("_")])
    mk.add_argument("asks"); mk.add_argument("--out", required=True); mk.add_argument("--timeout", type=int, default=3600)
    cv = sub.add_parser("convert"); cv.add_argument("templates", nargs="+")
    pv = sub.add_parser("preview"); pv.add_argument("dir")
    a = p.parse_args()
    if a.cmd == "run":
        run(json.load(open(a.asks)), model_keys(a.models), a.out, a.timeout)
    elif a.cmd == "one":
        w, h = (int(x) for x in a.size.lower().split("x"))
        run([{"name": a.name, "prompt": a.prompt, "w": w, "h": h, "seed": a.seed, "cutout": a.cutout}], model_keys(a.model), a.out, a.timeout)
    elif a.cmd == "make":
        make(a.recipe, Path(a.asks).resolve(), a.out, a.timeout)
    elif a.cmd == "preview":
        d = Path(a.dir).resolve()
        glbs = sorted(d.glob("*.glb"))
        # Blender writes warnings to stderr; 'Continue' keeps PowerShell from treating them as failures
        ps1 = ("$ErrorActionPreference = 'Continue'\n& $env:BLENDER -b --factory-startup -P render_glb.py -- glb $env:RENDER_OUT 2>&1 "
               "| ForEach-Object { \"$_\" } | Select-String 'rendered|Error|Traceback'\nexit $LASTEXITCODE\n")
        send([HERE / "pc" / "render_glb.py"] + [(g, f"glb/{g.name}") for g in glbs], ps1, [], "imagegen-preview", d / "preview", 1800)
    elif a.cmd == "convert":
        out = HERE / "recipes"
        out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "asks.json").write_text("[]")
            send([HERE / "models.json", HERE / "pc" / "batch.py", HERE / "pc" / "to_api.mjs", Path(td) / "asks.json"],
                 RUN_PS1, {"convert": a.templates}, "imagegen-convert", out, 900)
        for f in ("_log.txt", "_err.txt", "_status.json", "comfy.log", "timings.json"):
            (out / f).unlink(missing_ok=True)
    elif a.cmd == "sizes":
        gb = lambda names: sum(MODELS["files"][n]["size"] for n in set(names)) / 1e9
        for k, m in MODELS["models"].items():
            print(f"model  {k:9} {gb([m['unet'], m['clip'], m['vae']]):5.1f} GB  {m['label']}: {m['licence']}")
        for k, r in MODELS["recipes"].items():
            if not k.startswith("_"):
                print(f"recipe {k:9} {gb(r['files']):5.1f} GB  {r['label']}: {r['licence']}")
        print(f"cutout (any image with --cutout) +{gb([MODELS['cutout']['model']]):.1f} GB  BiRefNet: MIT")
        print("Shared files are counted once per line; fetch never downloads a file twice.")
    elif a.cmd == "setup":
        out = REPO / "reports" / ("setup-" + time.strftime("%Y%m%d-%H%M%S"))
        send([HERE / "models.json"], (HERE / "pc" / "setup-comfy.ps1").read_text(), [], "imagegen-setup", out, 3600)
        print((out / "_log.txt").read_text())
    else:
        files = {MODELS["models"][k][part] for k in model_keys(a.models) for part in ("unet", "clip", "vae")} | {MODELS["cutout"]["model"]}
        files = sorted(files | {f for r in a.recipes.split(",") if r for f in MODELS["recipes"][r]["files"]})
        out = REPO / "reports" / ("fetch-" + time.strftime("%Y%m%d-%H%M%S"))
        send([HERE / "models.json"], (HERE / "pc" / "fetch.ps1").read_text(),
             files, "imagegen-fetch", out, 4 * 3600)
        print((out / "_log.txt").read_text())


if __name__ == "__main__":
    main()
