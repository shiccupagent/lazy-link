# Web page to video

`video/webvideo.py` turns an animated web page into an MP4 and a PNG. The PC draws the page in headless Chrome, one frame at a time, on its graphics card, then ffmpeg encodes the frames. Time comes from the page's `renderAt(t)`, not the real clock, so every run gives the same clip.

## Use it

```bash
./video/webvideo.py video/examples/spin.html --out out/ --seconds 6 --fps 30 --size 1920x1080
./video/webvideo.py my-scene/index.html --out out/ --query "speed=2&theme=dark" --name scene-fast
```

Results: `out/<name>.mp4` (H.264) and `out/<name>.png` (the middle frame). The name defaults to the page's file name.

| Option | Default | Notes |
|---|---|---|
| `--seconds` | 6 | up to 600 |
| `--fps` | 30 | 1 to 60 |
| `--size` | 1920x1080 | each side 16 to 3840 pixels |
| `--query` | none | the page reads it as `location.search` |
| `--timeout` | 1800 | seconds to wait for the PC |

The page's whole folder goes to the PC (files whose names start with a dot stay home), up to 300 files. Keep the page and only its own assets in that folder.

## What the page must do

Two things on `window`:

- `renderAt(t)` draws the frame at `t` seconds. Drive all motion from `t`. Do not use `Date.now()`, `performance.now()`, `requestAnimationFrame` or timers for motion: the renderer calls `renderAt` once per frame as fast as the PC can, so the clock would drift.
- `sceneReady` is a Promise that resolves once everything the page loads (images, models, sounds) has loaded. The renderer waits up to 60 s for it, then starts.

Gotcha (three.js): `LoadingManager` does not expose its item counts, so its `onLoad` can fire early or never. Wrap each texture's own load callback in a Promise and set `sceneReady = Promise.all(...)`.

Load everything from the page folder. The renderer serves only that folder, on localhost.

`video/examples/spin.html` is the smallest page that follows the contract.

## What the PC needs

- Node 22 or newer.
- Chrome, or Chromium on Linux. `CHROME=/path/to/chrome` overrides the search.
- ffmpeg. Encoding uses NVIDIA's `h264_nvenc` when ffmpeg has it, and libx264 otherwise.

The Windows job (`job.ps1`) and the Linux/Mac job (`job.sh`) run the same steps. The Windows script is the main target.

## How it runs

webvideo.py sends the page folder and `render.mjs` through the Lazy Link job sender (`imagegen.send`, the same path the image jobs use), so the PC is woken and put back to sleep as usual. The PC script runs `render.mjs`, which starts Chrome with a throwaway profile and saves one JPEG per frame, then ffmpeg encodes them. Chrome and the profile are removed when the job ends, including when it is stopped by SIGTERM or SIGINT.

Frame count is `round(seconds * fps)`.

## If it fails

- `ffmpeg not found` or `Node.js 22 or newer is not on the PC`: install it on the PC and rerun.
- `page never set window.sceneReady` or `sceneReady did not resolve within 60 s`: a load is stuck, or `sceneReady` is missing. Open the page in Chrome and read the console.
- `Chrome was not found`: install Chrome, or set `CHROME`.
- On failure, webvideo prints the last lines of the PC's logs.

## Status

- Mac, local headless Chrome: `video/examples/spin.html` renders (20 frames at 10 fps, 1280x720 and 640x360).
- Mac, POSIX job script: `job.sh` ran end to end with a stand-in for the PC (`sh job.sh` with `RENDER_OUT` set) and produced `video.mp4` and `still.png`.
- Tested on a Windows PC (RTX 3090, 2026-10-30): 2D canvas and three.js WebGL pages (`examples/try.py spin`, `room`, `3d`).
- With nobody signed in on Windows, Chrome gets no WebGL from the graphics card. render.mjs then draws on the CPU
  (SwiftShader) by itself, slower but the same pictures. If a page never gets ready, render.mjs prints the page's own errors.
- Not yet run on real hardware: NVENC, and the Linux and Mac job runners.
