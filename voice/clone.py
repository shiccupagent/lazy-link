#!/usr/bin/env python3
"""clone.py - copy your voice with AI, on your own PC. After you record (voice/install.py puts the recorder on the PC):
bring the takes here, pick about 10 s of the cleanest lines per voice as that voice's reference (Chatterbox listens to
the first 10 s), have Chatterbox on the PC say each voice's "try" line, and put your real recording and the AI copy
side by side so you can hear how close it is.

  clone.py --setup              install Chatterbox on the PC (once: about 3.2 GB of model files plus 2.5 GB of torch)
  clone.py [--out DIR]          fetch the takes, make the references and AI copies (default reports/voice-copies/<today>)
  clone.py --src DIR --out DIR  use takes already on this computer (a test)
  --script FILE                 the script you recorded with, if not voice/scripts.json

Your recordings are personal: they stay on the PC and this computer (voice/recordings and voice/refs, both kept out of
git), and the PC runs Chatterbox offline. Needs ffmpeg here. Windows PC only for now."""
import argparse, datetime, json, shutil, subprocess, sys, tempfile, wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402

FF = "ffmpeg"
REF_SECS = (10.0, 14.0)      # enough to fill Chatterbox's 10 s, short enough to stay on the best lines
GOOD_PEAK = (-20.0, -1.0)    # takes outside this were too quiet or clipped
TRIM = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse," \
       "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse"

FETCH_PS1 = r"""$ErrorActionPreference = 'Continue'
$src = Join-Path $env:RENDER_ROOT 'voice\recordings'
if (-not (Get-ChildItem $src -Recurse -Filter '*.wav' -ErrorAction SilentlyContinue)) { 'no recordings yet'; exit 3 }
Copy-Item $src "$env:RENDER_OUT\recordings" -Recurse -Force
Get-ChildItem "$env:RENDER_OUT\recordings" -Recurse -Filter '*.wav' | Measure-Object | ForEach-Object { "takes: $($_.Count)" }
"""
CLONE_PS1 = r"""$ErrorActionPreference = 'Continue'
$c = Get-Content chatterbox.json -Raw | ConvertFrom-Json
$py = Join-Path $env:RENDER_ROOT ($c.venv + '\Scripts\python.exe')
if (-not (Test-Path $py)) { 'Chatterbox is not installed: run voice/clone.py --setup'; exit 3 }
$env:HF_HUB_OFFLINE = '1'; $env:HF_HUB_DISABLE_TELEMETRY = '1'
& $py -u clone_pc.py 2>&1 | ForEach-Object { "$_" }
exit $LASTEXITCODE
"""


def peak_db(path):
    out = subprocess.run([FF, "-v", "info", "-i", str(path), "-af", "astats=measure_overall=Peak_level:measure_perchannel=0",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    vals = [float(l.split(":")[-1]) for l in out.splitlines() if "Peak level dB" in l and "-inf" not in l]
    return vals[-1] if vals else -120.0


def secs(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def make_ref(voice, takes, dest):
    """The cleanest spoken lines, in script order, trimmed, until REF_SECS is filled -> one WAV."""
    picked, total = [], 0.0
    with tempfile.TemporaryDirectory() as td:
        for i, line in enumerate(voice["lines"], 1):
            src = takes / voice["id"] / f"{i:02d}.wav"
            if line.get("room") or not src.is_file() or not GOOD_PEAK[0] <= peak_db(src) <= GOOD_PEAK[1]:
                continue
            t = Path(td) / f"{i:02d}.wav"
            subprocess.run([FF, "-v", "error", "-y", "-i", str(src), "-af", TRIM, "-ar", "48000", "-ac", "1", str(t)], check=True)
            if secs(t) < 0.8:
                continue
            picked.append(t); total += secs(t)
            if total >= REF_SECS[0]:
                break
        if not picked:
            return None
        lst = Path(td) / "list.txt"
        gap = Path(td) / "gap.wav"
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-t", "0.25", "-i", "anullsrc=r=48000:cl=mono", str(gap)], check=True)
        lst.write_text("".join(f"file '{p}'\nfile '{gap}'\n" for p in picked))
        subprocess.run([FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-t", str(REF_SECS[1]),
                        "-af", "loudnorm=I=-20:TP=-2", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)], check=True)
    return round(total, 1)


def main():
    ap = argparse.ArgumentParser(description="copy your voice with AI, on your own PC")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--src")
    ap.add_argument("--out")
    ap.add_argument("--script", default=str(HERE / "scripts.json"))
    a = ap.parse_args()
    if imagegen.load_pcjob().POSIX:
        sys.exit("voice: Windows PC only for now")
    if a.setup:
        with tempfile.TemporaryDirectory() as td:
            imagegen.send([HERE / "chatterbox.json", HERE.parent / "imagegen" / "models.json"],
                          (HERE / "pc" / "setup-voice.ps1").read_text(), [], "voice-setup", td, 3 * 3600)
            print((Path(td) / "_log.txt").read_text(errors="replace"))
        return
    voices = json.loads(Path(a.script).read_text(encoding="utf-8"))["voices"]
    takes = Path(a.src).resolve() if a.src else HERE / "recordings"
    if not a.src:
        with tempfile.TemporaryDirectory() as td:
            imagegen.send([], FETCH_PS1, [], "voice-fetch", td, 600)
            if not (Path(td) / "recordings").is_dir():
                sys.exit("no recordings on the PC yet: double-click 'Record voices' on its desktop first")
            if takes.exists():
                shutil.rmtree(takes)
            shutil.copytree(Path(td) / "recordings", takes)
    refs = HERE / "refs"
    refs.mkdir(exist_ok=True)
    asks = []
    for v in voices:
        got = make_ref(v, takes, refs / f"{v['id']}.wav")
        print(f"{v['name']}: " + (f"{got} s of clean takes in the reference" if got else "no clean takes yet, skipped"))
        if got:
            asks.append({"id": v["id"], "text": v["try"]["text"], "exaggeration": v["try"]["exaggeration"], "cfg": v["try"]["cfg"]})
    if not asks:
        sys.exit("nothing recorded yet")
    dest = Path(a.out).expanduser().resolve() if a.out else HERE.parent / "reports" / "voice-copies" / str(datetime.date.today())
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "asks.json").write_text(json.dumps(asks, indent=1))
        files = [HERE / "pc" / "clone_pc.py", HERE / "chatterbox.json", Path(td) / "asks.json"] + \
                [(refs / f"{x['id']}.wav", f"refs/{x['id']}.wav") for x in asks]
        made = Path(td) / "made"
        imagegen.send(files, CLONE_PS1, [], "voice-clone", made, 1800)
        for n, v in enumerate(voices, 1):
            if (made / f"{v['id']}.wav").is_file():
                for src, label in ((refs / f"{v['id']}.wav", "your recording"), (made / f"{v['id']}.wav", "AI copy")):
                    subprocess.run([FF, "-v", "error", "-y", "-i", str(src), "-af", "loudnorm=I=-16:TP=-1.5", "-ar", "48000",
                                    "-b:a", "192k", str(dest / f"{n}. {v['name']} - {label}.mp3")], check=True)
            else:
                print(f"{v['name']}: no AI copy came back; the PC's log says why:\n" +
                      ((made / "_log.txt").read_text(errors="replace")[-1500:] if (made / "_log.txt").exists() else "(no log)"))
        (dest / "READ ME.txt").write_text(
            "Your voice, copied by AI on your own PC (Chatterbox, MIT licence, runs offline; nothing was uploaded).\n"
            "For each voice: 'your recording' is about 10 s picked from your takes, and 'AI copy' is the AI saying a new line in that voice.\n"
            "Chatterbox marks everything it makes with an inaudible watermark, so detectors can tell it is AI speech.\n"
            "If one sounds off, record a few lines again (the recorder keeps the rest), then run clone.py again.\n"
            "Only copy a voice you have the right to use: your own, or someone's who said yes.\n")
    print(f"-> {dest}")


if __name__ == "__main__":
    main()
