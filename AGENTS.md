# Lazy Link: instructions for AI agents

This repo lets you run AI jobs on the user's big PC's GPU from their small computer (Mac, or Windows/Linux).
You can be any AI coding agent that runs terminal commands (Claude Code, Codex, Gemini CLI, Cursor, ...),
or a person typing the commands. Tested with Claude Code; other agents should work the same way.
The main path is Mac → Windows. A Linux big PC (`"pc_os": "linux"`) runs `job.sh` jobs, check.py, imagegen and local
(tested on CPU in a VM; the CUDA path is untested until a real Linux GPU PC). An Apple Silicon Mac (`"pc_os": "mac"`,
macpc/) runs the same jobs with Metal/MPS; setup is untested on real hardware (docs/MAC-PC.md). `check.py --tidy` is Windows-only. Setup steps: README.md; other mixes: README "Other computers".

## Commands
- `./check.py [--setup]`: what the PC has, what to fix, which models fit (only reads the PC).
- `./imagegen/imagegen.py one "prompt" --out DIR [--model klein3|krea2|klein|zimage] (default klein3 = FLUX 3 klein) [--size WxH] [--cutout]`
  An empty cutout (mask under 2% of the picture) is retried once with plain BiRefNet; still empty = no `-cutout.png`,
  `"cutout": {"status": "failed"}` on its timings.json row, listed in timings.json `cutout_failed`, and a CUTOUT FAILED line.
- `./imagegen/imagegen.py run ASKS.json --out DIR`: many images in one job (format in the script's docstring).
- `./imagegen/imagegen.py make audio|sfx|music3|mesh|maps ASKS.json --out DIR`: sounds/music (`audio` = Stable Audio 3 medium, the pick for game sounds and music; `sfx` = the small SFX model; `music3` = MiniMax Music 3, the owner's pick for game music: every take comes back exactly `seconds` long and loop-ready, see models.json; credit rule in models.json), picture to 3D, or picture to normal + depth maps (MoGe-2).
  mesh params: `faces` (triangles after decimation; 4000-5000 for phone props, the normal map keeps the detail),
  `texture` (map size in px, 256-4096; a number, never true/false), `seed`. `make` refuses asks that don't fit (models.json "limits").
- `./imagegen/imagegen.py make video ASKS.json --out DIR`: short clips from a prompt, or from a picture as the first frame (Wan 2.2
  TI2V 5B, Apache-2.0, 24 fps). Ask: `{"name", "params": {"prompt", "seconds", "width", "height", "seed"}, "image": "first.png",
  "loop": 0.5, "cutout": true, "frames": true}`; `loop` crossfades the end into the start so it repeats; `cutout` adds a
  transparent `.webm` (VP9 alpha), `frames` its PNG frames. `"cutout": true` = BiRefNet HR-matting (soft edges kept; drops motion-blurred
  ones), `"cutout": "black"` = brightness key for glows, sparks and things falling on black (prompt for a black background).
  Sizes in multiples of 32; a 3 s 768x768 clip takes about 2 min on a 3090. Look at every clip before you use it.
- `./imagegen/imagegen.py tile PICTURE... --out DIR`: seamless copy of each surface picture (`<name>_tile.png` + a 2x2 preview to check the seams); no AI, needs numpy, scipy, Pillow on this computer.
- `./imagegen/imagegen.py pbr PICTURE... --out DIR [--material NAME] [--rough R] [--rough-var V] [--detail D] [--metal 0|1|edges] [--tile] [--model heuristic]`: roughness, metal and packed ORM maps from a surface picture (`<name>_roughness.png`, `_metal.png`, `_orm.png`, `_pbr.json`; reads `<name>_height.png` and `<name>_ao.png` beside it); no AI, needs numpy and Pillow on this computer; presets in imagegen/pbr_materials.json.
- `./imagegen/imagegen.py bump PICTURE... --out DIR [--tile] [--slope S] [--highpass R] [--object [--plain]]`: bump maps (`<name>_normal.png` + `_height.png` + `_ao.png`) from a surface picture; runs `make maps` on the PC, then needs numpy, scipy, Pillow on this computer. `--tile` makes the maps wrap for seamless pictures; `--object` gives a normal map only for sprites and whole scenes (`--plain` takes the colour out first). `--highpass R` takes out waves wider than R px (height and AO follow); `--slope S` steepens a flat map.
- `./imagegen/imagegen.py shrink GLB... --out DIR`: phone-size copies of `make mesh` .glb files (512 px JPEG textures); needs Pillow on this computer.
- `python3 imagegen/bake.py MODEL.glb --out DIR [--mode full|hybrid] [--allow-dark NAME] [--opt key=value]`: light map + AO for a .glb, baked by Blender on the PC (Windows PC only). `full` for props and backdrops, `hybrid` for scenes with realtime lights; `--make-test-scene` bakes a built-in room. Check `summary.json` first: `"status": "failed"` means no GLB. Don't pass `--allow-dark` for a mesh that should be lit (README "Bake light maps").
- `python3 imagegen/env.py [VENUE.glb --at=X,Y,Z] [--sky --sun=AZ,EL [--turbidity 1-10 | --ground R,G,B]] --out DIR [--size W] [--also W,...] [--name NAME]`: linear HDR environment map for three.js (equirectangular `<name>_env.hdr`, a png preview, and a json sidecar with `suggestedEnvMapIntensity`), rendered by Blender on the PC (Windows PC only). A render that fails its checks writes no `.hdr`; read the sidecar's `checks` first (README "Make a lighting map") `--ground` (linear albedo) puts a sunlit ground plane under the sky, so bounce light is not the sky model's dark blue.
- `./imagegen/imagegen.py preview DIR`: turntable pictures of every .glb (needs Blender on the PC).
- `python3 imagegen/audio_check.py DIR [--frequent a,b] [--fix OUTDIR]`: the never-harsh gate for sounds (brightness and >4 kHz share), writes DIR/soundboard.mp3 to listen to; `--fix` writes game-ready .mp3 copies (`--loop a,b`: sounds that repeat get a seamless copy, no fades, the silence a model pads onto the ends cut;
  `--music a,b`: stereo tracks at -16 LUFS). Needs numpy and ffmpeg.
- `python3 imagegen/pick_sounds.py TAKES_DIR --ref FILE... --dest DIR [--keep 3] [--often a,b] [--tone-floor 2000 | --no-tone-match]`: finishes, tone-matches and scores takes against reference sounds; keeps the best per sound (the tone match never muffles a take below --tone-floor Hz). Needs numpy, scipy and ffmpeg.
- `python3 imagegen/sheet.py OUT.png ASKS.json COL=DIR[:label] ...` or `sheet.py grid OUT.png ASKS.json DIR MODEL`: side-by-side comparison sheet (Pillow).
- `python3 imagegen/watch.py [--mark]`: new open image models on Hugging Face (public API, no key).
- `./local/local.py ask ASKS.jsonl --out DIR` / `ask-one "..."` / `transcribe FILE... --out DIR`.
- `./mac/pcjob run DIR --fetch OUT`: any folder with a job.ps1 (PowerShell; write results to $env:RENDER_OUT).
  `--lane test` (Windows PC): a short CPU/browser job (page tests, shelf checks), at most 15 min, run by the PC's test
  lane runner beside the GPU job instead of behind it, one at a time. `./mac/pcjob lane test` says if it is free (exit 0
  free, 1 busy, 2 down, 75 quiet hold): run the test on this computer when it is not. `run --lane` exits 75 when the
  lane is down. `pcjob list` shows the main jobs plus a `test lane:` line; `list --lane test` shows that lane's jobs.
- `./video/webvideo.py PAGE.html --out DIR [--query "a=1"] [--seconds 6] [--fps 30] [--size 1920x1080]`: animated web page to .mp4 + .png, rendered on the PC (docs/WEB-TO-VIDEO.md).
- `./voice/install.py [--script FILE]`: the walk-up voice recorder on a Windows PC ("Record voices" desktop icon).
  `./voice/clone.py --setup` once, then `./voice/clone.py [--only ID,...]`: AI copies of the recorded voices (Chatterbox, offline
  on the PC; Whisper checks every take, misread ones never become the reference, and the lines with the least vocal fry go first).
  `./voice/speak.py LINES.json --out DIR [--map speaker=voice] [--script FILE]`: narration in those voices; Whisper listens to
  each sentence and anything not heard word for word is said again with a new seed (up to --rounds 4; report.json).
  Misread tech words: spoken spellings in voice/lexicon.json, tried on the retries (`--respell auto|always|never`).
  `./voice/lipsync.py --setup` once, then `./voice/lipsync.py VOICE.wav... --out DIR [--text T...]`: mouth shapes for a
  talking character (Rhubarb Lip Sync on the PC; `<name>.json` mouthCues A-H/X; words from --text or `<name>.txt` beside
  the recording make them land right). Windows PC only. Shape table in the script's docstring.
  `./voice/describe.py LINES.json --out DIR`: lines in voices made from a written description (Ming-3.0-TTS 0.5B, MIT; voice/ming.json),
  the default for game voice lines; installs itself on the PC on first use; about 11 s per line on a 3090. Write numbers as words.
- `python3 tools/model_watch.py --out DIR`: the week's trending new open models on Hugging Face per category (pictures, cutouts, audio, voice, 3D, video, depth) that
  are not ours, not copies, and not barred from commercial use; settings and ruled-out licences in tools/model_watch.json. Candidates for a side-by-side test, not picks.
  Voice recordings are personal: never commit, upload or paste them; they stay in voice/recordings and voice/refs.
- `./examples/try.py [spin|pictures|sounds|room|3d|all]`: end-to-end examples, each ending in a file to open (out/try/).
- `./mac/pcjob prune [--older-than 7d] [--legacy] [--yes]`: lists (removes, with `--yes`) finished jobs on the PC older than that
  whose results this Mac fetched (`fetch` leaves a `fetched` mark); `--legacy` also counts done/failed/timeout jobs from before
  2026-11-11. Never a running or cancelled job: a cancelled job's frames may be the only copy (`pcjob rm ID` once its owner agrees).
- `./mac/pcjob ping | list | status ID | log ID | cancel ID | wake | sleep | cheap` (`cancel` on a queued job ends it
  within seconds, without starting it). `run` and `wait` ride out a PC that stops answering for up to 10 min
  (`LAZY_LINK_WAIT_GRACE` s) before giving up; the job keeps running on the PC either way.
  `sleep` (and the put-down after a job that woke the PC) sleeps or hibernates as config.json `"rest"` says
  (`--sleep`/`--hibernate` override), then does not check: an ssh or ping to a sleeping PC can wake it (Setup-PC turns the
  card's pattern wake off, so only the magic packet wakes it); `pcjob quiet` keeps every other check off it.
- `./mac/pcshare setup | keychain | check | mount [DIR]`: a folder on the Windows PC (D:\MacShare) that the Mac opens over
  SMB; `setup` puts a one-click admin shortcut on the PC's desktop for the user, `keychain` moves the share's random
  password into the Mac's keychain (never print it). What it changes and -Undo: pc/setup-share.ps1.
- `./mac/pcjob secret put|drop|has NAME`: a secret for one job, taken from the environment variable NAME (set by a key
  manager: `keydrop run NAME -- ...`), sent over ssh stdin to the PC's `secrets\` (job account only). Gated Hugging
  Face files (`"gated": "HF_TOKEN"` in models.json) do this by themselves: `keydrop run HF_TOKEN -- ./imagegen/imagegen.py fetch ...`.
  Never echo, print or pass a secret on a command line; accepting a gated model's terms is the user's step.
- `./mac/pcjob quiet HH:MM | off`: leave the PC alone until then (an overnight hibernate: any ssh can wake it). `run` waits;
  every other PC command exits 75 without touching the network. Respect a hold someone else set; ask before `quiet off`.
- Cheap electricity: `--cheap` on `pcjob run`, or `LAZY_CHEAP=1` before any command, holds the job until
  config.json `cheap_hours` (`"cheap_only": true` = every job). Use it for big batches that aren't urgent.

## Rules
- **Never sleep, restart or shut down the PC while someone is using it.** `pcjob sleep` checks;
  never work around it. A job that woke the PC puts it back to sleep by itself.
- Batch: one job with many asks, not many jobs. On Windows the runner keeps ComfyUI up between jobs (`warm.json`,
  pc/runner.ps1), so a job right after another skips the ~20 s start, and on the same model the ~5 s load too;
  a first job, or one after Blender/voice/10 idle minutes, still pays 15-55 s. Test: `pc/test-runner-warm.ps1` on the PC.
- Long jobs: run them in the background and wait for them to finish; don't poll in a tight loop.
- Look at every image you make before you use it (models drift toward famous characters and logos).
  Listen to, or at least measure, sounds before you ship them (new sounds often come out harsh): `imagegen/audio_check.py` measures them.
- Check a model's licence (`imagegen.py sizes`) before its output goes into something sold.
- Anything that needs admin on the PC is one line for the user to paste into an admin PowerShell.
  Never widen the firewall rule or make `lazylink` an administrator.
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
