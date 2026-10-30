#!/usr/bin/env python3
"""imagegen - make images, sounds and 3D models on your PC's NVIDIA GPU with open models (ComfyUI,
headless) and bring the files back to the Mac. Models, licences and settings: models.json.

  imagegen.py setup                          install our own ComfyUI on the PC (pinned in models.json)
  imagegen.py sizes                          what each model / recipe downloads, and its licence

  imagegen.py run ASKS.json --out DIR [--models zimage,klein,krea2] [--timeout S]
  imagegen.py one "prompt" --out DIR [--model krea2] [--size 1024x1024] [--seed N] [--name N]
  imagegen.py fetch [--models krea2] [--recipes audio,mesh]   download missing model files (official repos, sha256-checked)
  imagegen.py make RECIPE ASKS.json --out DIR   sound (sfx, audio), 3D (mesh) or maps (normal + depth) from a recipe
  imagegen.py tile PICTURE... --out DIR      seamless copy of each surface picture, on this computer, no AI (tiles.py)
  imagegen.py bump PICTURE... --out DIR [--tile]   bump maps for surface pictures: DIR/<name>_normal/_height/_ao.png (runs make maps);
                                             --tile for seamless pictures (the maps wrap too)
                                             --object [--plain] for sprites and whole scenes: DIR/<name>_normal.png only
  imagegen.py shrink GLB... --out DIR        phone-size copies of .glb files: DIR/<name>-small.glb (512 px JPEG textures)
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

# the same job on a POSIX PC (job.sh, Linux or Mac): ComfyUI's own venv python runs batch.py
RUN_SH = r"""set -eu -o pipefail
[ -n "${RENDER_ROOT:-}" ] || { echo "RENDER_ROOT is not set (run this as a job)" >&2; exit 3; }
PY="$RENDER_ROOT/$(python3 -c 'import json; print(json.load(open("models.json"))["comfy"]["venv"])')/bin/python"
[ -x "$PY" ] || { echo "no ComfyUI at $PY: run imagegen.py setup first" >&2; exit 3; }
exec "$PY" -u batch.py
"""

def load_pcjob():
    loader = importlib.machinery.SourceFileLoader("pcjob", str(REPO / "mac" / "pcjob"))
    spec = importlib.util.spec_from_loader("pcjob", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def posix_script(name, base=HERE):
    """job.sh text for a POSIX big computer: <base>/<OS_NAME>/<name> (linux, mac). None on Windows (it uses the .ps1)."""
    pc = load_pcjob()
    return (base / pc.OS_NAME / name).read_text() if pc.POSIX else None


def model_keys(s):
    keys = [k.strip() for k in s.split(",") if k.strip()]
    bad = [k for k in keys if k not in MODELS["models"]]
    if bad:
        sys.exit(f"imagegen: unknown model {bad}; known: {', '.join(MODELS['models'])}")
    return keys


def send(files, ps1, want, name, out, timeout, sh=None):
    """Run a job on the PC: ps1 is the Windows script, sh the POSIX job.sh (Linux or Mac). Every command passes both."""
    pc = load_pcjob()
    if pc.POSIX and sh is None:   # refuse before pc.wake(): nothing is woken for a command with no POSIX script
        sys.exit(f"imagegen: this command has no {pc.OS_NAME} script yet")
    woke = pc.wake()
    with tempfile.TemporaryDirectory() as td:
        job = Path(td) / "job"
        job.mkdir()
        for f in files:  # a path, or (path, place in the job folder)
            src, rel = (f, Path(f).name) if not isinstance(f, tuple) else f
            (job / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, job / rel)
        (job / ("job.sh" if pc.POSIX else "job.ps1")).write_text(sh if pc.POSIX else ps1)
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
        send([HERE / "models.json", HERE / "pc" / "batch.py", a], RUN_PS1, keys, "imagegen", out, timeout, sh=RUN_SH)
    t = json.loads((out / "timings.json").read_text(encoding="utf-8-sig"))
    print(f"ComfyUI start {t['comfy_boot_s']} s")
    for r in t["runs"]:
        print(f"{r['model']:7} {r['ask']:24} {r['seconds']:6.1f} s{'  (first: loads the model)' if r['cold'] else ''}")
    print(f"images in {out}")


def make(key, asks, out, timeout):
    r = MODELS["recipes"][key]
    out = Path(out).expanduser().resolve()
    base = Path(asks).parent
    asks = json.loads(Path(asks).read_text(encoding="utf-8-sig"))
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "asks.json"
        json.dump(asks, open(a, "w"), indent=1)
        files = [HERE / "models.json", HERE / "pc" / "batch.py", a,
                 (HERE / "recipes" / f"{r['template']}.api.json", f"recipes/{r['template']}.api.json")]
        files += [(base / x["image"], f"inputs/{x['image']}") for x in asks if "image" in x]
        send(files, RUN_PS1, {"recipe": key}, f"imagegen-{key}", out, timeout, sh=RUN_SH)
    t = json.loads((out / "timings.json").read_text(encoding="utf-8-sig"))
    print(f"ComfyUI start {t['comfy_boot_s']} s")
    for row in t["runs"]:
        print(f"{key:6} {row['ask']:24} {row['seconds']:7.1f} s  {row['files']} file(s){'  (first: loads the model)' if row['cold'] else ''}")
    print(f"results in {out}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp1252
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
    bp = sub.add_parser("bump", help="bump maps (normal, height, AO) for surface pictures or objects; needs numpy, scipy, Pillow here")
    bp.add_argument("pictures", nargs="+"); bp.add_argument("--out", required=True); bp.add_argument("--timeout", type=int, default=1800)
    bp.add_argument("--tile", action="store_true", help="the pictures are seamless: make maps that wrap too")
    bp.add_argument("--object", action="store_true", help="objects or whole scenes (sprites, paintings): keep the whole shape; normal only")
    bp.add_argument("--plain", action="store_true", help="with --object: take the colours out first, so paint colour reads as no shape")
    tl = sub.add_parser("tile", help="seamless copies of surface pictures (tiles.py); needs numpy, scipy and Pillow here")
    tl.add_argument("pictures", nargs="+"); tl.add_argument("--out", required=True)
    tl.add_argument("--overlap", type=float, help="share laid over itself to find the cut (tiles.OVERLAP)")
    tl.add_argument("--flatten", type=float, help="even out light drift wider than this share; 0 = off (tiles.FLATTEN)")
    sk = sub.add_parser("shrink", help="phone-size copies of .glb files (512 px JPEG textures); needs Pillow here")
    sk.add_argument("glbs", nargs="+"); sk.add_argument("--out", required=True)
    a = p.parse_args()
    if a.cmd == "run":
        run(json.loads(Path(a.asks).read_text(encoding="utf-8-sig")), model_keys(a.models), a.out, a.timeout)
    elif a.cmd == "one":
        w, h = (int(x) for x in a.size.lower().split("x"))
        run([{"name": a.name, "prompt": a.prompt, "w": w, "h": h, "seed": a.seed, "cutout": a.cutout}], model_keys(a.model), a.out, a.timeout)
    elif a.cmd == "make":
        make(a.recipe, Path(a.asks).resolve(), a.out, a.timeout)
    elif a.cmd == "tile":
        try:
            import tiles
        except ImportError as e:
            sys.exit(f"imagegen tile needs numpy, scipy and Pillow on this computer (pip3 install numpy scipy pillow): {e}")
        out = Path(a.out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
        for f in [Path(x).resolve() for x in a.pictures]:
            dst = out / f"{f.stem}_tile.png"
            before, after = tiles.seamless(f, dst, tiles.OVERLAP if a.overlap is None else a.overlap,
                                           tiles.FLATTEN if a.flatten is None else a.flatten)
            tiles.repeat(dst, out / f"{f.stem}_tile_2x2.png")   # 2 x 2 copies: look at the joins, the score can't see everything
            print(f"tile: {dst}  edges jump {before:.1f}x an ordinary step before, {after:.1f}x after")
    elif a.cmd == "bump":
        try:
            import bumpmaps, tiles
        except ImportError as e:
            sys.exit(f"imagegen bump needs numpy, scipy and Pillow on this computer (pip3 install numpy scipy pillow): {e}")
        out = Path(a.out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
        pics = [Path(x).resolve() for x in a.pictures]
        if len({f.stem for f in pics}) != len(pics):
            sys.exit("imagegen bump: two pictures have the same name (file name without extension); rename one")
        if a.tile and a.object:
            sys.exit("imagegen bump: --tile is for surfaces, --object for objects; pick one")
        with tempfile.TemporaryDirectory() as td:
            asks = Path(td) / "asks.json"
            names = []   # (name in asks.json, file in the job folder)
            for f in pics:
                if a.object:   # scaled up if small, on mid grey, maybe without colour: what MoGe reads
                    bumpmaps.prep_object(f, Path(td) / f"{f.stem}.png", a.plain)
                    names.append((f.stem, f"{f.stem}.png"))
                    continue
                shutil.copy(f, Path(td) / f.name)
                names.append((f.stem, f.name))
                if a.tile:   # also the picture moved by half across, down and both, so the maps can wrap
                    for key, (sy, sx) in bumpmaps.SHIFTS.items():
                        if key:
                            tiles.save(bumpmaps.shifted(tiles.load(f), sy, sx), Path(td) / f"{f.stem}{key}.png")
                            names.append((f"{f.stem}{key}", f"{f.stem}{key}.png"))
            asks.write_text(json.dumps([{"name": n, "image": i} for n, i in names]))
            make("maps", asks, Path(td) / "maps", a.timeout)
            m = Path(td) / "maps"   # make saves <name>__maps.png (normal) and <name>__maps-1.png (depth)
            for f in pics:
                own = (m / f"{f.stem}__maps.png", m / f"{f.stem}__maps-1.png")
                if a.object:
                    bumpmaps.finish_object(f, Path(td) / f"{f.stem}.png", own[0], out, f.stem)
                    print(f"bump: {out / (f.stem + '_normal.png')}")
                    continue
                if a.tile:
                    bumpmaps.finish_tiled(f, {k: (m / f"{f.stem}{k}__maps.png", m / f"{f.stem}{k}__maps-1.png") for k in bumpmaps.SHIFTS}, out, f.stem)
                else:
                    bumpmaps.finish(f, *own, out, f.stem)
                print(f"bump: {out / (f.stem + '_normal.png')} (+ _height, _ao)")
    elif a.cmd == "shrink":
        try:
            import shrink_glb
        except ImportError as e:
            sys.exit(f"imagegen shrink needs Pillow on this computer (pip3 install pillow): {e}")
        out = Path(a.out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
        for g in a.glbs:
            src = Path(g).resolve()
            print(f"shrink: {out / (src.stem + '-small.glb')} ({shrink_glb.shrink(src, out / (src.stem + '-small.glb')):.2f} MB)")
    elif a.cmd == "preview":
        d = Path(a.dir).resolve()
        glbs = sorted(d.glob("*.glb"))
        # Blender writes warnings to stderr; 'Continue' keeps PowerShell from treating them as failures
        ps1 = ("$ErrorActionPreference = 'Continue'\n& $env:BLENDER -b --factory-startup -P render_glb.py -- glb $env:RENDER_OUT 2>&1 "
               "| ForEach-Object { \"$_\" } | Select-String 'rendered|Error|Traceback'\nexit $LASTEXITCODE\n")
        send([HERE / "pc" / "render_glb.py"] + [(g, f"glb/{g.name}") for g in glbs], ps1, [], "imagegen-preview", d / "preview", 1800,
             sh=posix_script("preview.sh"))
    elif a.cmd == "convert":
        out = HERE / "recipes"
        out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "asks.json").write_text("[]")
            send([HERE / "models.json", HERE / "pc" / "batch.py", HERE / "pc" / "to_api.mjs", Path(td) / "asks.json"],
                 RUN_PS1, {"convert": a.templates}, "imagegen-convert", out, 900, sh=RUN_SH)
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
        send([HERE / "models.json"], (HERE / "pc" / "setup-comfy.ps1").read_text(), [], "imagegen-setup", out, 3600,
             sh=posix_script("setup.sh"))
        print((out / "_log.txt").read_text(encoding="utf-8-sig", errors="replace"))
    else:
        files = {MODELS["models"][k][part] for k in model_keys(a.models) for part in ("unet", "clip", "vae")} | {MODELS["cutout"]["model"]}
        files = sorted(files | {f for r in a.recipes.split(",") if r for f in MODELS["recipes"][r]["files"]})
        out = REPO / "reports" / ("fetch-" + time.strftime("%Y%m%d-%H%M%S"))
        send([HERE / "models.json"], (HERE / "pc" / "fetch.ps1").read_text(),
             files, "imagegen-fetch", out, 4 * 3600, sh=posix_script("fetch.sh"))
        print((out / "_log.txt").read_text(encoding="utf-8-sig", errors="replace"))


if __name__ == "__main__":
    main()
