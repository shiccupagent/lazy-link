#!/usr/bin/env python3
"""install.py [--script FILE] [--no-test] [--out DIR] - put the walk-up voice recorder on the PC (Windows), through a job:
<work folder>\\voice gets server.py, the script (voice/scripts.json, or --script FILE) and page/; recordings/ is never
touched. A "Record voices" icon goes on the PC's desktop for everyone. Double-click it, read the lines, done.
Then, unless --no-test, it starts a second copy on another port and drives the page in headless Chrome with a fake
mic (test.mjs, needs Node and Chrome on the PC); those test takes go to the job's output only.
Run it again after changing the script. Proof (shot.png and the log) lands in --out (default reports/voice-install).
Recordings stay on the PC and your small computer; they never go anywhere else."""
import argparse, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402

PS1 = r"""$ErrorActionPreference = 'Continue'
$dst = Join-Path $env:RENDER_ROOT 'voice'
New-Item -ItemType Directory -Force "$dst\page", "$dst\recordings" | Out-Null
Copy-Item server.py, scripts.json $dst -Force
Copy-Item page\* "$dst\page" -Force
# The person at the PC runs the recorder as themselves, so their account must be able to save takes here.
icacls "$dst\recordings" /grant "*S-1-5-32-545:(OI)(CI)M" | Out-Null
# Lazy Link's own Python (uv-managed, under <work folder>\tools\python), which every account can run
$py = Get-ChildItem (Join-Path $env:RENDER_ROOT 'tools\python') -Directory -Filter 'cpython-3.1*' -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { 'no Python under <work folder>\tools\python yet: run ./imagegen/imagegen.py setup first'; exit 2 }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
$sh = New-Object -ComObject WScript.Shell
$lnk = Join-Path $dst 'Record voices.lnk'
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pyw; $s.Arguments = "`"$dst\server.py`""; $s.WorkingDirectory = $dst
$s.IconLocation = "$env:SystemRoot\System32\SndVol.exe,0"; $s.Description = 'Record your voice for Lazy Link'; $s.Save()
$pub = 'C:\Users\Public\Desktop\Record voices.lnk'
try { Copy-Item $lnk $pub -Force -ErrorAction Stop; "desktop icon: yes" }
catch { "desktop icon: NO (this PC doesn't let the job account write to the desktop). Paste in an admin PowerShell: Copy-Item '$lnk' 'C:\Users\Public\Desktop\'" }
"recorded so far: $((Get-ChildItem "$dst\recordings" -Recurse -Filter '??.wav' -ErrorAction SilentlyContinue).Count) takes"
if (Test-Path test.mjs) {
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) { 'test skipped: Node.js is not on the PC'; exit 0 }
  $p = Start-Process $py -ArgumentList "`"$dst\server.py`"", '--no-browser', '--port', '8798', '--root', "`"$env:RENDER_OUT\testrec`"" -PassThru -WindowStyle Hidden
  Start-Sleep 2
  & node test.mjs 'C:\Program Files\Google\Chrome\Application\chrome.exe' 'http://127.0.0.1:8798/' (Resolve-Path fake.wav).Path "$env:RENDER_OUT\shot.png" 2>&1 | ForEach-Object { "$_" }
  $rc = $LASTEXITCODE
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  "test exit $rc"
  exit $rc
}
"""


def fake_voice(td):
    """A spoken test line for the fake mic: the computer's own text-to-speech (test input only, never kept)."""
    line = "Hi, this is a test line for the voice recorder. One, two, three."
    wav = Path(td) / "fake.wav"
    if sys.platform == "darwin":
        subprocess.run(["say", "-o", f"{td}/fake.aiff", line], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"{td}/fake.aiff", "-ar", "48000", "-ac", "1", str(wav)], check=True)
    elif shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-w", f"{td}/fake0.wav", line], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"{td}/fake0.wav", "-ar", "48000", "-ac", "1", str(wav)], check=True)
    else:   # a plain tone still exercises the meter and the save path
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-ar", "48000",
                        "-ac", "1", str(wav)], check=True)
    return wav


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--script", default=str(HERE / "scripts.json"))
    ap.add_argument("--no-test", action="store_true")
    ap.add_argument("--out", default=str(HERE.parent / "reports" / "voice-install"))
    a = ap.parse_args()
    if imagegen.load_pcjob().POSIX:
        sys.exit("voice: the recorder installs on a Windows PC for now")
    out = Path(a.out).expanduser().resolve()
    files = [HERE / "server.py", (Path(a.script), "scripts.json"), (HERE / "page" / "index.html", "page/index.html"),
             (HERE / "page" / "capture.js", "page/capture.js")]
    with tempfile.TemporaryDirectory() as td:
        if not a.no_test:
            files += [HERE / "test.mjs", fake_voice(td)]
        imagegen.send(files, PS1, [], "voice-install", out, 900)
    print((out / "_log.txt").read_text(errors="replace") if (out / "_log.txt").exists() else "")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
