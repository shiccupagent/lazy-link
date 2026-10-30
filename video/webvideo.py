#!/usr/bin/env python3
"""webvideo - turn an animated web page into a video, rendered on your PC's graphics card.

  ./video/webvideo.py PAGE.html --out DIR [--query "a=1&b=2"] [--seconds 6] [--fps 30]
                      [--size 1920x1080] [--name N] [--timeout S]

  -> DIR/<name>.mp4 (H.264 video) and DIR/<name>.png (a still from the middle of the clip)

The PC opens the page in headless Chrome and draws it frame by frame (video/render.mjs), so the
real clock never matters. The page must expose two things (docs/WEB-TO-VIDEO.md):
  window.renderAt(t)   draws the frame at t seconds
  window.sceneReady    a Promise that resolves once everything the page loads has loaded
The PC needs Node 22 or newer, Chrome (or Chromium) and ffmpeg. The Windows job (job.ps1) and the
Linux/Mac job (job.sh) run the same steps; the Windows one is tested on an RTX 3090 PC (docs/WEB-TO-VIDEO.md).
"""
import argparse, math, re, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402  (the job sender: wake, upload, run, fetch and sleep live there)

PAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.html?")  # goes into the PC's script unquoted
MAX_FILES = 300   # the PC gets the whole page folder; a bigger folder is probably the wrong one

# The job on Windows (job.ps1) and on Linux/Mac (job.sh). The runner starts it in the job folder
# with RENDER_OUT set; the results it leaves in RENDER_OUT come back to the Mac.
# __X__ marks are filled in by fill() before the script is sent.
JOB_PS1 = r"""$ErrorActionPreference = 'Continue'
if (-not $env:FFMPEG) { Write-Output 'webvideo: ffmpeg not found on the PC (the job runner looks in C:\lazylink\tools\ffmpeg\bin and C:\Program Files\ffmpeg\bin)'; exit 3 }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Write-Output 'webvideo: Node.js 22 or newer is not on the PC (install it from nodejs.org)'; exit 3 }
$query = (Get-Content query.txt -Raw).Trim()
& node render.mjs page "$env:RENDER_OUT\work" __SECS__ __FPS__ __W__ __H__ __PAGE__ $query 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { Write-Output "webvideo: rendering failed (exit $LASTEXITCODE)"; exit 1 }
$enc = @('-c:v', 'h264_nvenc', '-preset', 'p6', '-cq', '19')
if (-not (& $env:FFMPEG -hide_banner -encoders 2>$null | Select-String -SimpleMatch 'h264_nvenc')) { $enc = @('-c:v', 'libx264', '-preset', 'medium', '-crf', '18') }
& $env:FFMPEG -v error -y -framerate __FPS__ -i "$env:RENDER_OUT\work\frames\f%05d.jpg" @enc -pix_fmt yuv420p -movflags +faststart "$env:RENDER_OUT\video.mp4"
if ($LASTEXITCODE -ne 0) { Write-Output "webvideo: encoding failed (exit $LASTEXITCODE)"; exit 1 }
& $env:FFMPEG -v error -y -i "$env:RENDER_OUT\work\frames\f__MID__.jpg" "$env:RENDER_OUT\still.png"
Remove-Item -Recurse -Force "$env:RENDER_OUT\work"
exit 0
"""

JOB_SH = r"""set -eu
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
fail() { echo "webvideo: $*" >&2; exit 3; }
[ -n "${RENDER_OUT:-}" ] || fail "RENDER_OUT is not set (run this as a job)"
command -v node >/dev/null 2>&1 || fail "Node.js 22 or newer is not on the PC"
FF="${FFMPEG:-$(command -v ffmpeg 2>/dev/null || true)}"
[ -n "$FF" ] || fail "ffmpeg is not on the PC (install it with the system's package manager)"
QUERY=$(cat query.txt)
node render.mjs page "$RENDER_OUT/work" __SECS__ __FPS__ __W__ __H__ __PAGE__ "$QUERY"
if "$FF" -hide_banner -encoders 2>/dev/null | grep -q h264_nvenc; then
  ENC="-c:v h264_nvenc -preset p6 -cq 19"
else
  ENC="-c:v libx264 -preset medium -crf 18"
fi
"$FF" -v error -y -framerate __FPS__ -i "$RENDER_OUT/work/frames/f%05d.jpg" $ENC -pix_fmt yuv420p -movflags +faststart "$RENDER_OUT/video.mp4"
"$FF" -v error -y -i "$RENDER_OUT/work/frames/f__MID__.jpg" "$RENDER_OUT/still.png"
rm -rf "$RENDER_OUT/work"
"""


def fill(text, marks):
    for key, value in marks.items():
        text = text.replace(f"__{key}__", str(value))
    return text


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="webvideo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("page", type=Path, help="the .html page; its whole folder goes to the PC")
    p.add_argument("--out", required=True, help="folder for the results")
    p.add_argument("--query", default="", help='query string the page reads, e.g. "speed=2&theme=dark"')
    p.add_argument("--seconds", type=float, default=6.0, help="clip length in seconds (default 6)")
    p.add_argument("--fps", type=int, default=30, help="frames per second (default 30)")
    p.add_argument("--size", default="1920x1080", help="WIDTHxHEIGHT in pixels (default 1920x1080)")
    p.add_argument("--name", help="output name without extension (default: the page's name)")
    p.add_argument("--timeout", type=int, default=1800, help="give up on the PC after this many seconds (default 1800)")
    a = p.parse_args()

    page = a.page.expanduser().resolve()
    if not page.is_file():
        sys.exit(f"webvideo: no page at {page}")
    if not PAGE_NAME.fullmatch(page.name):
        sys.exit(f"webvideo: rename {page.name!r} to letters, digits, dot, dash or underscore")
    if not 0 < a.seconds <= 600:
        sys.exit("webvideo: --seconds must be more than 0 and at most 600")
    if not 1 <= a.fps <= 60:
        sys.exit("webvideo: --fps must be from 1 to 60")
    m = re.fullmatch(r"(\d+)x(\d+)", a.size)
    if not m or not all(16 <= int(v) <= 3840 for v in m.groups()):
        sys.exit("webvideo: --size must look like 1920x1080 (16 to 3840 pixels each side)")
    w, h = m.groups()
    frames = math.floor(a.seconds * a.fps + 0.5)   # the same rounding render.mjs uses
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", a.name or page.stem).strip("-")[:60] or "web-video"
    out = Path(a.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    # The page's folder goes along: its pictures, models and scripts. Hidden files and the output folder stay home.
    files = [(HERE / "render.mjs", "render.mjs")]
    skip = out if page.parent.resolve() in out.parents else None   # only an output folder INSIDE the page's folder
    for f in sorted(page.parent.rglob("*")):
        rel = f.relative_to(page.parent)
        if not f.is_file() or any(part.startswith(".") for part in rel.parts) or (skip and skip in f.resolve().parents):
            continue
        files.append((f, f"page/{rel.as_posix()}"))
    if len(files) - 1 > MAX_FILES:
        sys.exit(f"webvideo: {page.parent} holds {len(files) - 1} files; put the page and only its own assets in their own folder")
    size_mb = sum(src.stat().st_size for src, _ in files[1:]) / 1e6
    print(f"sending {len(files) - 1} page files ({size_mb:.1f} MB); rendering {frames} frames of {w}x{h}")

    with tempfile.TemporaryDirectory(prefix="webvideo-") as td:
        query = Path(td) / "query.txt"
        query.write_text(a.query.lstrip("?"), encoding="utf-8")
        files.append((query, "query.txt"))
        result = Path(td) / "result"
        result.mkdir()
        marks = dict(SECS=f"{a.seconds:g}", FPS=a.fps, W=w, H=h, PAGE=page.name, MID=f"{frames // 2:05d}")
        try:
            imagegen.send(files, fill(JOB_PS1, marks), [], "web-video", result, a.timeout, sh=fill(JOB_SH, marks))
        except SystemExit:
            for log in ("_err.txt", "_log.txt"):   # the runner's logs, fetched even when the job failed
                if (result / log).is_file():
                    print(f"--- {log[1:]}, last lines:")
                    print("\n".join((result / log).read_text(encoding="utf-8", errors="replace").splitlines()[-20:]))
            raise
        video, still = result / "video.mp4", result / "still.png"
        if not (video.is_file() and still.is_file()):
            sys.exit("webvideo: the PC finished without a video; the log above says why")
        shutil.copy2(video, out / f"{name}.mp4")
        shutil.copy2(still, out / f"{name}.png")
    print(f"{out / (name + '.mp4')}  {frames} frames, {a.seconds:g} s at {a.fps} fps, {w}x{h}")
    print(f"{out / (name + '.png')}  still (middle frame)")


if __name__ == "__main__":
    main()
