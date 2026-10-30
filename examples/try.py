#!/usr/bin/env python3
"""try.py - the "try these first" examples: each runs the real commands on your PC and ends in something to look at
or listen to, in out/try/<example>/.

  ./examples/try.py              the list, and which ones your PC has the models for
  ./examples/try.py NAME         run one (spin, pictures, sounds, room, 3d)
  ./examples/try.py all          run them all, in order

  spin      an animated page made into a video (no AI model; needs Node, Chrome and ffmpeg on the PC)  -> spin.mp4
  pictures  floor, wall and brick surfaces plus a lantern, then a comparison sheet           -> sheet.png
  sounds    six game sounds, checked for harshness, then a soundboard                         -> soundboard.mp3
  room      bump maps for the surfaces, then a lit room: flat vs bumps (reuses `pictures`)    -> room.mp4
  3d        the lantern as a 3D model, a phone-size copy, then a turntable (reuses `pictures`) -> turntable.mp4

Each step prints the command it runs, so you can run it yourself on your own pictures and sounds later.
Needs on this computer: ffmpeg, plus `pip3 install numpy scipy pillow` (sheet, bump, shrink, sound check).
"""
import json, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "out" / "try"
MODEL = "krea2"   # the picture model these asks were tuned on; another one works too: edit here

EXAMPLES = {   # name: (what it shows, what it needs installed, the file to open)
    "spin": ("an animated page made into a video", "nothing (Node, Chrome, ffmpeg on the PC)", "spin.mp4"),
    "pictures": ("four pictures and a comparison sheet", "images", "sheet.png"),
    "sounds": ("six game sounds and a soundboard", "sounds", "soundboard.mp3"),
    "room": ("bump maps in a lit room: flat vs bumps", "images, maps", "room.mp4"),
    "3d": ("a picture turned into a 3D model, on a turntable", "images, 3d", "turntable.mp4"),
}


def sh(*cmd):
    print("\n$ " + " ".join(str(c) if " " not in str(c) else f'"{c}"' for c in cmd), flush=True)
    if subprocess.run([str(c) for c in cmd], cwd=REPO).returncode:
        sys.exit(f"try: that step failed; see above. Is the model installed? ./check.py --setup --want ...")


def py(script, *args):
    sh(sys.executable, REPO / script, *args)


def pictures():
    d = OUT / "pictures"
    py("imagegen/imagegen.py", "run", HERE / "pictures.json", "--out", d, "--models", MODEL)
    py("imagegen/sheet.py", "grid", d / "sheet.png", HERE / "pictures.json", d, MODEL)
    return d / "sheet.png"


def picture(name):
    """A picture from the `pictures` example, made first if it isn't there yet."""
    f = OUT / "pictures" / f"{name}__{MODEL}.png"
    if not f.exists():
        pictures()
    return f


def room():
    d = OUT / "room"
    page = d / "page"
    shutil.rmtree(page, ignore_errors=True)
    shutil.copytree(HERE / "room", page)
    shutil.copytree(HERE / "lib", page / "lib")
    for n in ("floor", "wall", "side"):
        shutil.copy(picture(n), page / f"{n}.png")
    py("imagegen/imagegen.py", "bump", *[page / f"{n}.png" for n in ("floor", "wall", "side")], "--out", page)
    py("video/webvideo.py", page / "index.html", "--out", d, "--name", "room", "--seconds", 8)
    return d / "room.mp4"


def model3d():
    d = OUT / "3d"
    d.mkdir(parents=True, exist_ok=True)
    cut = OUT / "pictures" / f"lantern__{MODEL}-cutout.png"
    if not cut.exists():
        picture("lantern")
    shutil.copy(cut, d / "lantern.png")
    (d / "mesh.json").write_text(json.dumps([{"name": "lantern", "image": "lantern.png",
                                              "params": {"faces": 40000, "texture": 2048, "seed": 7}}], indent=1))
    py("imagegen/imagegen.py", "make", "mesh", d / "mesh.json", "--out", d)
    glb = d / "lantern__mesh.glb"
    py("imagegen/imagegen.py", "shrink", glb, "--out", d)
    page = d / "page"
    shutil.rmtree(page, ignore_errors=True)
    shutil.copytree(HERE / "viewer", page)
    shutil.copytree(HERE / "lib", page / "lib")
    shutil.copy(glb, page / "model.glb")
    shutil.copy(d / "lantern__mesh-small.glb", page / "model-small.glb")
    mb = lambda f: f"{f.stat().st_size / 1e6:.1f}"
    py("video/webvideo.py", page / "index.html", "--out", d, "--name", "turntable", "--seconds", 8,
       "--query", f"big={mb(page / 'model.glb')}&small={mb(page / 'model-small.glb')}")
    return d / "turntable.mp4"


def sounds():
    d = OUT / "sounds"
    py("imagegen/imagegen.py", "make", "sfx", HERE / "sounds.json", "--out", d)
    py("imagegen/audio_check.py", d, "--frequent", "coin,jump,click", "--fix", d / "game-ready")
    return d / "soundboard.mp3"


def spin():
    d = OUT / "spin"
    py("video/webvideo.py", REPO / "video" / "examples" / "spin.html", "--out", d, "--name", "spin", "--seconds", 6)
    return d / "spin.mp4"


RUN = {"spin": spin, "pictures": pictures, "sounds": sounds, "room": room, "3d": model3d}


def show_list():
    print("Try these first (./examples/try.py NAME). Each ends in a file to open:\n")
    for k, (what, needs, f) in EXAMPLES.items():
        print(f"  {k:9} {what:52} -> out/try/{k}/{f}   (installed: {needs})")
    print("\nRun one with ./examples/try.py NAME, or ./examples/try.py all.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "list"):
        print(__doc__ if len(sys.argv) > 1 and sys.argv[1] != "list" else "", end="")
        return show_list()
    names = list(RUN) if sys.argv[1] == "all" else sys.argv[1:]
    bad = [n for n in names if n not in RUN]
    if bad:
        sys.exit(f"try: no example {', '.join(bad)}; there are: {', '.join(RUN)}")
    done = [(n, RUN[n]()) for n in names]
    print("\nOpen these:")
    for n, f in done:
        print(f"  {n:9} {f}")


if __name__ == "__main__":
    main()
