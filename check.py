#!/usr/bin/env python3
"""check.py - look over the PC (wakes it if needed): its graphics card, memory, disks and how ready it is
after months or years switched off, then say which AI models it can run and the exact commands to get them.

  ./check.py              the report, plus what to install
  ./check.py --setup      the report, then install what it recommends
  ./check.py --from FILE  the report from an earlier check.json (no PC needed)
  ./check.py --tidy       sizes of what can be cleaned on the PC; asks before deleting Lazy Link's own files (--yes skips the question)

It only reads, except --tidy, which deletes nothing without your yes. Fixes that need you (Windows Update, the graphics
driver, BIOS settings) are listed in plain words.
"""
import argparse, json, os, shlex, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "imagegen"))
import imagegen  # noqa: E402  (send(): wake the PC, run the job, fetch, sleep it again if we woke it)

MODELS = imagegen.MODELS
GB = 1e9
WORK_ROOM = 2.0   # GB of graphics memory a picture/sound job needs on top of its largest model file
# The local chat and speech models (downloads and settings live in local/setup-local-ai.ps1).
LOCAL = {"qwen": ("Qwen3-30B-A3B chat", 18.6, 20.0), "gptoss": ("gpt-oss-20b chat", 12.1, 13.5),
         "whisper": ("Whisper large-v3 speech-to-text", 3.1, 5.0)}   # name, download GB, graphics memory GB to run fast
DRIVER_MIN, DRIVER_GOOD = 527.41, 551.61   # CUDA 12 runs from 527.41; our CUDA 12.4 builds want 551.61+ on Windows


def size(f):
    return MODELS["files"][f]["size"] / GB


def image_need(m):
    return max(size(m["unet"]), size(m["clip"])) + size(m["vae"]) + WORK_ROOM


def recipe_need(r):
    return r.get("min_vram_gb") or max(size(f) for f in r["files"]) + sum(size(f) for f in r["files"] if size(f) < 1.5) + WORK_ROOM


def rate(need, vram, ram):
    """fast: fits the graphics card. slow: works by borrowing system memory. no: too big."""
    if need <= vram:
        return "fast"
    return "slow" if vram >= 6 and need <= vram + ram * 0.5 else "no"


def plan(c):
    gpu = max(c.get("gpus") or [{}], key=lambda g: g.get("vram_gb", 0))
    ram = c.get("ram_gb") or 0
    # A Mac chip shares its memory with the system: about 70% of the RAM can hold a model, and none of it is spare on top.
    if gpu.get("unified_memory"):
        vram, spare = round(0.7 * ram, 1), 0
    else:
        vram, spare = gpu.get("vram_gb", 0), ram
    rows, images, recipes, local = [], [], [], False
    for k, m in MODELS["models"].items():
        rows.append((m["label"], "images", image_need(m), rate(image_need(m), vram, spare), k))
    for k, r in MODELS["recipes"].items():
        if isinstance(r, dict):
            rows.append((r["label"], "sound" if k != "mesh" else "3D", recipe_need(r), rate(recipe_need(r), vram, spare), k))
    for k, (label, _, need) in LOCAL.items():
        rows.append((label, "text", need, rate(need, vram, spare), k))
    ok = {r[4]: r[3] for r in rows}
    # Pictures: the best model that runs fast, else the lightest that runs at all.
    for k in ("krea2", "zimage", "klein"):
        if ok[k] == "fast":
            images = [k]; break
    if not images and ok["klein"] == "slow":
        images = ["klein"]
    recipes = [k for k in ("sfx", "audio", "mesh") if ok.get(k) == "fast"] or ([] if ok.get("sfx") == "no" else ["sfx"])
    local = ok["whisper"] != "no" and (ok["qwen"] != "no" or ok["gptoss"] != "no")
    files = set()
    for k in images:
        files |= {MODELS["models"][k][p] for p in ("unet", "clip", "vae")}
    for k in recipes:
        files |= set(MODELS["recipes"][k]["files"])
    files.add(MODELS["cutout"]["model"])
    download = sum(size(f) for f in files) + (sum(v[1] for v in LOCAL.values()) + 0.7 if local else 0) + (5 if images or recipes else 0)
    cmds = []
    if images or recipes:
        cmds += [["./imagegen/imagegen.py", "setup"],
                 ["./imagegen/imagegen.py", "fetch"] + ["--models", ",".join(images)] +   # "" = no picture models (the default is krea2)
                 
                 (["--recipes", ",".join(recipes)] if recipes else [])]
    if local:
        cmds.append(["./local/local.py", "setup"])
    return dict(gpu=gpu, vram=vram, ram=ram, rows=rows, download=download, cmds=cmds)


def health(c, p):
    """(mark, text) lines: ✔ fine, ! worth fixing, ✘ blocks Lazy Link."""
    out = []
    os_, gpu = c.get("os") or {}, p["gpu"]
    out.append(("✔", f"{os_.get('name')} {os_.get('version') or ''}".strip()))
    if "Windows 10" in (os_.get("name") or ""):
        out.append(("!", "Windows 10 stopped getting free security updates in October 2025. Upgrade to Windows 11 if the PC "
                         "allows it, or sign up for Microsoft's Extended Security Updates."))
    lu = c.get("last_update")
    pc = imagegen.load_pcjob()
    linux, mac = pc.LINUX, pc.MAC
    if not lu:
        out.append(("·", "Couldn't read the system update history (/var/log/apt/history.log). If the PC sat unused for a while, "
                         "install its updates (sudo apt update && sudo apt upgrade) until there is nothing left to install."
                     if linux else
                     "Couldn't read the macOS update history. If the Mac sat unused for a while, run Software Update "
                     "(System Settings > General > Software Update) until it says your Mac is up to date."
                     if mac else
                     "Couldn't read the Windows Update history. If the PC sat unused for a while, run Windows Update "
                     "(Settings > Windows Update) until it says you're up to date."))
    else:
        age = (time.time() - time.mktime(time.strptime(lu, "%Y-%m-%d"))) / 86400
        label = "Last system update" if linux else "Last macOS update" if mac else "Last Windows update"
        fix = ("Install its updates first (sudo apt update && sudo apt upgrade), restart, and run it again until there is nothing left to install."
               if linux else
               "Install its updates first (System Settings > General > Software Update), restart, and run it again until your Mac is up to date."
               if mac else
               "Run Windows Update first (Settings > Windows Update), restart, and run it again until it says you're up to date.")
        out.append(("✔" if age < 45 else "!", f"{label}: {lu}" + ("" if age < 45 else f" ({int(age)} days ago). {fix}")))
    if mac:
        if gpu.get("name"):
            out.append(("✔", f"{gpu['name']}: shares {c.get('ram_gb') or 0:g} GB memory with the system; about {p['vram']:g} GB of it can hold models"))
        else:
            out.append(("✘", "Couldn't read the Mac's chip (system_profiler SPDisplaysDataType)."))
    elif not gpu.get("name"):
        out.append(("✘", "No NVIDIA graphics card found (or its driver isn't installed). Lazy Link needs one for now: "
                         "install the driver from nvidia.com/drivers, then check again."))
    else:
        drv = (gpu.get("driver") or "").split(".")   # "566.36" (Windows) or "550.54.14" (Linux): major.minor
        d = float(".".join(drv[:2])) if drv[0].isdigit() and (len(drv) == 1 or drv[1].isdigit()) else 0
        mark = "✔" if d >= DRIVER_GOOD else ("!" if d >= DRIVER_MIN else "✘")
        out.append((mark, f"{gpu['name']}, {gpu['vram_gb']:g} GB graphics memory, driver {gpu.get('driver')}" + (
            "" if mark == "✔" else ". Update the driver: NVIDIA App, or nvidia.com/drivers (Game Ready or Studio both work).")))
    ram = c.get("ram_gb") or 0
    out.append(("✔" if ram >= 16 else "!", f"{ram:g} GB memory" + ("" if ram >= 16 else " (16 GB or more makes big models much smoother)")))
    root = pc.ROOT or "C:/lazylink"
    disks = {d["drive"].upper(): d for d in c.get("disks") or []}
    if root.startswith("/"):   # Linux: the disk is the longest mount point that holds the work folder
        disks = {d["drive"]: d for d in c.get("disks") or []}
        holds = [m for m in disks if root == m or root.startswith(m.rstrip("/") + "/")]
        root = max(holds, key=len, default=root)
    else:
        root = root[:2].upper()
    free = disks.get(root, {}).get("free_gb", 0)
    if free >= p["download"] + 20:
        out.append(("✔", f"{free} GB free on {root} (the recommended models need about {p['download']:.0f} GB)"))
    else:
        roomy = [f"{k} ({d['free_gb']} GB free)" for k, d in disks.items() if d["free_gb"] >= p["download"] + 20]
        out.append(("!", f"Only {free} GB free on {root}; the recommended models need about {p['download']:.0f} GB plus room to work. "
                         + (f"Models can live on {', '.join(roomy)}: see extra_model_folders in config.example.json."
                            if roomy else "Free some space (System Settings > General > Storage on a Mac; Settings > System > Storage on Windows).")))
    nets = c.get("network") or []
    cable = any(n["kind"] == "cable" and n["connected"] for n in nets)
    out.append(("✔" if cable else "!", "Network cable connected" if cable else
                "No network cable connected. Lazy Link works over Wi-Fi, but only a cable can wake the PC from sleep."))
    if c.get("fast_startup"):
        out.append(("!", "Fast Startup is on, so a shut-down PC can't be woken from the network. Turn it off: "
                         "Control Panel > Power Options > Choose what the power buttons do (docs/BIOS-AND-WAKE.md)."))
    w = c.get("wake_on_magic_packet")
    if w is not None:
        if mac:
            out.append(("✔" if w else "!", "Wake for network access is on" if w else
                        "Wake for network access is off. Run macpc/setup-mac.sh again (it asks for an admin password) to turn it on."))
        else:
            out.append(("✔" if w else "!", "Network card can wake the PC" if w else
                        "The network card's \"Wake on Magic Packet\" is off (docs/BIOS-AND-WAKE.md shows where to turn it on)."))
    s = c.get("sleep_after_min")
    if s == 0:
        out.append(("·", "The PC never sleeps on its own. Lazy Link puts it back to sleep after jobs it woke it for."))
    return out


def show(cmd):
    """The command as you would type it on this computer: py on Windows, python3 elsewhere."""
    rel = Path(cmd[0])   # "./imagegen/imagegen.py" -> imagegen/imagegen.py
    if os.name == "nt":
        ok_py = shutil.which("py") and subprocess.run(["py", "-3", "-c", "pass"], capture_output=True).returncode == 0
        return subprocess.list2cmdline([("py" if ok_py else "python"), str(rel)] + cmd[1:])
    return shlex.join(["python3", rel.as_posix()] + cmd[1:])


def report(c):
    p = plan(c)
    print("\nYour PC")
    for mark, text in health(c, p):
        print(f"  {mark} {text}")
    print(f"\nWhat it can run (graphics memory needed; fast = fits the card, slow = borrows system memory)")
    say = {"fast": "fast", "slow": "slow", "no": "too big"}
    for label, kind, need, r, _ in p["rows"]:
        print(f"  {say[r]:8} {kind:7} {label:52} ~{need:4.0f} GB")
    print("  (estimates from model sizes; measured on a 24 GB RTX 3090)")
    if p["cmds"]:
        print(f"\nRecommended install (about {p['download']:.0f} GB of downloads):")
        for cmd in p["cmds"]:
            print("  " + show(cmd))
    else:
        print("\nNothing fits this PC yet; fix the ✘ lines above and check again.")
    return p


def fmt_bytes(n):
    n = int(n or 0)
    if n >= GB:
        return f"{n / GB:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    if n >= 1e3:
        return f"{n / 1e3:.0f} KB"
    return f"{n} B"


def tidy_report(t):
    """Print the tidy-up table from tidy.json. Returns the ids Lazy Link can clean (with something to clean)."""
    print("\nTidy-up (sizes only; nothing is deleted by looking)")
    print(f"  {'Size':<16}{'What':<52}Who can clean it")
    cleanable = []
    for c in t.get("categories", []):
        b = c.get("bytes", 0)
        if not c.get("exists"):
            size, who = "none", ""
        elif not c.get("readable", True):
            size, who = "-", f"not visible to the Lazy Link account. You can clean it: {c.get('how', '')}"
        else:
            size = ("at least " if c.get("partial") else "") + fmt_bytes(b)
            if c.get("action") == "lazy":
                who = "Lazy Link can clean this"
                if b > 0:
                    cleanable.append(c)
            else:
                who = f"you can clean this: {c.get('how', '')}"
        print(f"  {size:<16}{c['label']:<52}{who}")
    if cleanable:
        print(f"\nLazy Link can clean {len(cleanable)} item(s), {fmt_bytes(sum(c['bytes'] for c in cleanable))}.")
    else:
        print("\nNothing here for Lazy Link to clean.")
    print("Everything else: Settings > System > Storage > Cleanup recommendations, or run cleanmgr /sageset once, then cleanmgr.")
    return [c["id"] for c in cleanable]


def freed_report(t):
    done = [c for c in t.get("categories", []) if c.get("freed", 0) > 0]
    print("\nFreed:")
    for c in done:
        print(f"  {fmt_bytes(c['freed']):<10}{c['label']}")
    if not done:
        print("  nothing (the items were already empty)")
    print(f"\nTotal freed: {fmt_bytes(t.get('freed_bytes', 0))}.")
    for c in t.get("categories", []):
        if "before" in c and c.get("bytes", 0) > 0:   # only the items this clean touched
            print(f"  still there: {fmt_bytes(c['bytes'])} in {c['label']} (in use, or newer than an hour)")
    if t.get("error"):
        print(f"  note: {t['error']}")


def tidy(a):
    if imagegen.load_pcjob().POSIX:
        sys.exit("./check.py --tidy works on the Windows PC for now.")
    script = (HERE / "pc" / "tidy.ps1").read_text(encoding="utf-8")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if a.src:
        t = json.loads(Path(a.src).read_text(encoding="utf-8-sig"))
        print("(from a saved file: nothing sent to the PC)")
    else:
        out = HERE / "reports" / ("tidy-" + stamp)
        imagegen.send([], script, {"mode": "scan"}, "tidy-scan", out, 600)
        t = json.loads((out / "tidy.json").read_text(encoding="utf-8-sig"))
        print(f"(saved in {out / 'tidy.json'})")
    todo = tidy_report(t)
    if a.src or not todo:
        return
    total = sum(c["bytes"] for c in t["categories"] if c["id"] in todo)
    if a.yes:
        go = True
    elif not sys.stdin.isatty():
        print("\n(not a terminal, so nothing is cleaned. Add --yes to clean without the question.)")
        return
    else:
        try:
            go = input(f"\nClean the {len(todo)} items Lazy Link can clean ({fmt_bytes(total)})? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            go = False
    if not go:
        print("Nothing cleaned.")
        return
    out = HERE / "reports" / ("tidy-clean-" + stamp)
    imagegen.send([], script, {"mode": "clean", "categories": todo}, "tidy-clean", out, 600)
    freed_report(json.loads((out / "tidy.json").read_text(encoding="utf-8-sig")))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # ✔/✘ marks; Windows pipes default to cp1252
    ap = argparse.ArgumentParser(prog="check", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", action="store_true", help="install what the report recommends")
    ap.add_argument("--tidy", action="store_true", help="sizes of what can be cleaned; asks before deleting Lazy Link's files")
    ap.add_argument("--yes", action="store_true", help="with --tidy: clean without asking (for scripts)")
    ap.add_argument("--from", dest="src", help="an earlier check.json (or tidy.json with --tidy)")
    a = ap.parse_args()
    if a.setup and a.tidy:
        sys.exit("use --setup or --tidy, not both")
    if a.yes and not a.tidy:
        sys.exit("--yes goes with --tidy")
    if a.tidy:
        return tidy(a)
    if a.src:
        c = json.loads(Path(a.src).read_text(encoding="utf-8-sig"))
    else:
        out = HERE / "reports" / ("check-" + time.strftime("%Y%m%d-%H%M%S"))
        pc = imagegen.load_pcjob()
        if pc.POSIX:   # a Linux or Mac PC runs the same check as a shell script from its own folder
            imagegen.send([], "", {}, "check", out, 600, sh=(HERE / pc.PC_DIR[pc.OS_NAME] / "check.sh").read_text())
        else:
            imagegen.send([], (HERE / "pc" / "check.ps1").read_text(), {}, "check", out, 600)
        c = json.loads((out / "check.json").read_text(encoding="utf-8-sig"))
        print(f"(saved in {out / 'check.json'})")
    p = report(c)
    if a.setup:
        if any(m == "✘" for m, _ in health(c, p)):
            sys.exit("\nNot installing: fix the ✘ lines first.")
        for cmd in p["cmds"]:
            print("\n$ " + show(cmd))
            if subprocess.run([sys.executable, str(HERE / cmd[0])] + cmd[1:], cwd=HERE).returncode:
                sys.exit(f"stopped: {show(cmd)} failed")


if __name__ == "__main__":
    main()
