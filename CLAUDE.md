# render-link: notes for Claude Code

This repo lets you (Claude, on the Mac) run AI jobs on the user's Windows PC GPU. Setup steps: README.md.

## Commands
- `./imagegen/imagegen.py one "prompt" --out DIR [--model krea2|klein|zimage] [--size WxH] [--cutout]`
- `./imagegen/imagegen.py run ASKS.json --out DIR`: many images in one job (format in the script's docstring).
- `./imagegen/imagegen.py make audio|sfx|mesh ASKS.json --out DIR`: sounds/music, or picture to 3D.
- `./imagegen/imagegen.py preview DIR`: turntable pictures of every .glb (needs Blender on the PC).
- `./local/local.py ask ASKS.jsonl --out DIR` / `ask-one "..."` / `transcribe FILE... --out DIR`.
- `./mac/pcjob run DIR --fetch OUT`: any folder with a job.ps1 (PowerShell; write results to $env:RENDER_OUT).
- `./mac/pcjob ping | list | status ID | log ID | cancel ID | wake | sleep`.

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
  (generated files: rerun it, never hand-edit recipes/). convert needs Node 22 and Chrome on the PC.
- Before anything leaves the machine: `tools/privacy-scan.sh` (repo) and `tools/frame-scan.sh VIDEO` (videos).
