# Lazy Link: notes for Claude Code

This repo lets you (Claude, on the small computer: Mac, or Windows/Linux) run AI jobs on the user's big PC's GPU.
The main path is Mac → Windows. A Linux big PC (`"pc_os": "linux"`) runs `job.sh` jobs, check.py, imagegen and local
(tested on CPU in a VM; the CUDA path is untested until a real Linux GPU PC). An Apple Silicon Mac (`"pc_os": "mac"`,
macpc/) runs the same jobs with Metal/MPS; setup is untested on real hardware (docs/MAC-PC.md). `check.py --tidy` is Windows-only. Setup steps: README.md; other mixes: README "Other computers".

## Commands
- `./check.py [--setup]`: what the PC has, what to fix, which models fit (only reads the PC).
- `./imagegen/imagegen.py one "prompt" --out DIR [--model krea2|klein|zimage] [--size WxH] [--cutout]`
- `./imagegen/imagegen.py run ASKS.json --out DIR`: many images in one job (format in the script's docstring).
- `./imagegen/imagegen.py make audio|sfx|mesh|maps ASKS.json --out DIR`: sounds/music, picture to 3D, or picture to normal + depth maps (MoGe-2).
- `./imagegen/imagegen.py bump PICTURE... --out DIR`: bump maps (`<name>_normal.png` + `_height.png`) from a surface picture; runs `make maps` on the PC, then needs numpy, scipy, Pillow on this computer.
- `./imagegen/imagegen.py shrink GLB... --out DIR`: phone-size copies of `make mesh` .glb files (512 px JPEG textures); needs Pillow on this computer.
- `./imagegen/imagegen.py preview DIR`: turntable pictures of every .glb (needs Blender on the PC).
- `./local/local.py ask ASKS.jsonl --out DIR` / `ask-one "..."` / `transcribe FILE... --out DIR`.
- `./mac/pcjob run DIR --fetch OUT`: any folder with a job.ps1 (PowerShell; write results to $env:RENDER_OUT).
- `./mac/pcjob ping | list | status ID | log ID | cancel ID | wake | sleep | cheap`.
- Cheap electricity: `--cheap` on `pcjob run`, or `LAZY_CHEAP=1` before any command, holds the job until
  config.json `cheap_hours` (`"cheap_only": true` = every job). Use it for big batches that aren't urgent.

## Rules
- **Never sleep, restart or shut down the PC while someone is using it.** `pcjob sleep` checks;
  never work around it. A job that woke the PC puts it back to sleep by itself.
- Batch: one job with many asks, not many jobs. Starting ComfyUI or a model costs 15-55 s per job.
- Long jobs: run them in the background and wait for them to finish; don't poll in a tight loop.
- Look at every image you make before you use it (models drift toward famous characters and logos).
  Listen to, or at least measure, sounds before you ship them (new sounds often come out harsh).
- Check a model's licence (`imagegen.py sizes`) before its output goes into something sold.
- Anything that needs admin on the PC is one line for the user to paste into an admin PowerShell.
  Never widen the firewall rule or make `render` an administrator.
- config.json is personal (PC address, network card). Never commit it or paste it anywhere.
- New model: add it to imagegen/models.json (official URL, size and sha256 from the Hugging Face API,
  licence), then `imagegen.py fetch`. ComfyUI templates become recipes with `imagegen.py convert`
  (generated files: rerun it, never hand-edit recipes/, except moge_maps.api.json: hand-written, ComfyUI has no
  template for it; its note is in models.json "maps"). convert needs Node 22 and Chrome on the PC.
- Before anything leaves the machine: `tools/privacy-scan.sh` (repo) and `tools/frame-scan.sh VIDEO` (videos).
- `"cheap_hours_auto": true` in config.json: keep `cheap_hours` right for the user. Ask once which electricity
  company and plan they have, or their town (never guess; it stays in config.json only). Look up that plan's
  time-of-use or off-peak hours on the company's own site, then write `cheap_hours`, plus `cheap_hours_note`
  (plan name, source link, date checked). Check again when the note is over 90 days old or the season changes.
  With no time-of-use plan, say so: every hour costs the same, so off-peak only helps the grid.
  If the utility is ComEd (Northern Illinois) or the plan is Octopus Agile (UK), set `cheap_feed` instead of fixed hours.
