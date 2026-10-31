#!/usr/bin/env python3
"""lipsync.py - mouth shapes from a voice recording, for animating a talking character. Runs Rhubarb Lip Sync
(MIT, github.com/DanielSWolf/rhubarb-lip-sync) on the PC: it listens to each recording and says which mouth shape
to show when.

  lipsync.py --setup                               install Rhubarb on the PC (once; the official 87 MB zip is
                                                   downloaded here, sha256-checked, and sent over)
  lipsync.py VOICE.wav... --out DIR [--text T...]  DIR/<name>.json per recording: {"mouthCues": [{"start", "end", "value"}]}

--text gives each recording's words (a .txt file per recording, same order), or put <name>.txt beside <name>.wav.
The words make the shapes land on the right sounds; without them Rhubarb guesses from the sound alone. English only.

The shapes (Rhubarb's letters; the usual set for cartoon mouths):
  X rest   A M/B/P lips pressed   B K/S/T/EE teeth together   C EH/AE open   D AA wide open
  E AO/ER a little rounded   F UW/OW/W puckered   G F/V teeth on the lower lip   H L tongue up
Draw one mouth per letter, or map the letters to a 3D mouth's numbers (barnaby-studio's host_motion.SHAPES does:
jaw, width, O, teeth, tongue). Ease into each new shape over about 60 ms so it never snaps.
Needs ffmpeg here. Windows PC only for now (Rhubarb has Linux and Intel-Mac builds; not wired up yet)."""
import argparse, hashlib, shutil, subprocess, sys, tempfile, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402

VERSION = "1.14.0"
ZIP = f"Rhubarb-Lip-Sync-{VERSION}-Windows.zip"
URL = f"https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v{VERSION}/{ZIP}"
SHA256 = "62fa416a8d5e382a3828ee4bef358ce520d0b4cabdeaea75a7ac266d098d1fe3"

SETUP_PS1 = rf"""$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:RENDER_ROOT 'tools\rhubarb'
$tmp = Join-Path $env:RENDER_ROOT 'tools\rhubarb-unzip'
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path '{ZIP}' -DestinationPath $tmp -Force
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
Move-Item (Join-Path $tmp 'Rhubarb-Lip-Sync-{VERSION}-Windows') $dest
Remove-Item $tmp -Recurse -Force
& (Join-Path $dest 'rhubarb.exe') --version
"""

RUN_PS1 = r"""$ErrorActionPreference = 'Continue'
$rb = Join-Path $env:RENDER_ROOT 'tools\rhubarb\rhubarb.exe'
if (-not (Test-Path $rb)) { 'Rhubarb is not installed: run voice/lipsync.py --setup'; exit 3 }
$bad = 0
foreach ($w in Get-ChildItem 'in' -Filter '*.wav') {
    $out = Join-Path $env:RENDER_OUT ($w.BaseName + '.json')
    $txt = [IO.Path]::ChangeExtension($w.FullName, '.txt')
    $ra = @('-f', 'json', '-o', $out, '--extendedShapes', 'GHX', '-q')
    if (Test-Path $txt) { $ra += @('-d', $txt) }
    & $rb @ra $w.FullName
    if ($LASTEXITCODE -ne 0) { "failed: $($w.Name)"; $bad++ } else { "done: $($w.BaseName).json" }
}
if ($bad) { exit 3 }
"""


def setup():
    with tempfile.TemporaryDirectory() as td:
        z = Path(td) / ZIP
        print(f"downloading {URL}")
        urllib.request.urlretrieve(URL, z)
        got = hashlib.sha256(z.read_bytes()).hexdigest()
        if got != SHA256:
            sys.exit(f"lipsync: {ZIP} sha256 is {got}, expected {SHA256}; not installing it")
        out = Path(td) / "report"
        imagegen.send([z], SETUP_PS1, [], "lipsync-setup", out, 900)
        print((out / "_log.txt").read_text(errors="replace"))


def run(wavs, texts, out):
    if texts and len(texts) != len(wavs):
        sys.exit("lipsync: give one --text per recording, in the same order")
    with tempfile.TemporaryDirectory() as td:
        files = []
        for i, w in enumerate(wavs):
            w = Path(w).expanduser().resolve()
            # Rhubarb reads 16-bit PCM WAV; any recording ffmpeg can open becomes one.
            pcm = Path(td) / f"{w.stem}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(w), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(pcm)], check=True)
            files.append((pcm, f"in/{w.stem}.wav"))
            t = Path(texts[i]) if texts else w.with_suffix(".txt")
            if t.exists():
                files.append((t, f"in/{w.stem}.txt"))
        out = Path(out).expanduser().resolve()
        imagegen.send(files, RUN_PS1, [], "lipsync", out, 600 + 60 * len(wavs))
    for w in wavs:
        print(f"{out / (Path(w).stem + '.json')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wavs", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--text", nargs="*", default=[])
    ap.add_argument("--setup", action="store_true")
    a = ap.parse_args()
    if a.setup:
        return setup()
    if not a.wavs or not a.out:
        ap.error("give recordings and --out (or --setup)")
    if not shutil.which("ffmpeg"):
        sys.exit("lipsync: needs ffmpeg on this computer")
    run(a.wavs, a.text, a.out)


if __name__ == "__main__":
    main()
