# Lazy Link: your own private AI cloud at home

**Use your big gaming PC lazily, from your small nice computer.** It wakes up, does the heavy lifting,
and goes back to sleep.

Lazy Link turns the gaming PC you already own into a private AI cloud for your home. Your AI agent does the setup, the model research and the running. You get the results.

- **All the AI:** pictures, sound effects and music, 3D models, a chat model, and speech-to-text.
- **Rendering:** 3D scenes, and web pages turned into video through headless Chrome.
- **Compute:** heavy jobs run headless on your gaming PC's NVIDIA graphics card, started from your small computer.

You don't need to do the work or the research. `check.py` picks the models that fit your PC, `imagegen/watch.py` finds new open image models, and your AI agent runs the jobs. Any AI agent that can run terminal commands works: Claude Code, Codex, Gemini CLI, Cursor and others, or you typing the commands yourself. Tested with Claude Code; other agents should work the same way.

No hosted service: your prompts and files stay on your own machines and your home network. The only internet traffic is downloading models from their official sites and reading the public model list. The locked-down design is why it is safe; see [Safety, in plain words](#safety-in-plain-words).

How it fits together: your small computer sends jobs over your home network, and the gaming PC does the heavy work and sends the files back.

```
Mac (you or your AI agent)                          Windows PC (NVIDIA GPU)
  imagegen.py / local.py                              hidden standard account "lazylink"
  pcjob run JOB  ── SSH, your key only ───────▶       job runner (scheduled task), one job at a time
                 ◀── results folder ─────────         ComfyUI (images, sound, 3D) · llama.cpp · Whisper
  pcjob wake (Wake-on-LAN) / pcjob sleep              everything lives in C:\lazylink
```

Speeds we measured on an RTX 3090: a 1-megapixel image in 3-15 s, a sound effect in about 2 s,
a textured 3D model in 2.5-8.5 min, and a chat model writing about 200 tokens (roughly 150 words) per second.

## What you need
- A Mac with an AI agent such as Claude Code, Codex, Gemini CLI or Cursor, or any Mac/Linux terminal with `python3` 3.9+ and `ssh` (you can type the commands yourself).
- A Windows 10/11 PC with an NVIDIA GPU (12 GB of video memory or more is comfortable; 24 GB runs
  everything here), an up-to-date NVIDIA driver, and 40-100 GB free disk for models.
- Both on the same home network. A network **cable** on the PC if you want the Mac to wake it.

## Setup (about 15 minutes, plus downloads)
1. **Mac:** download this repo, open Terminal in its folder, run `./install-mac.sh`.
   It makes a key that lets this Mac (and only this Mac) into the PC.
2. **PC:** copy the same folder over (USB stick, or download the repo again there), then
   double-click **`pc\Setup-PC.cmd`** and click *Yes* on the admin prompt. It is the only step
   that needs admin. Want to see first what it changes? Run `Setup-PC.cmd -DryRun`.
3. **Mac:** run the line the PC shows at the end, e.g. `./mac/pcjob pair 192.168.1.50`.
   Check that the fingerprint matches the one on the PC's screen and answer `y`.
4. **Mac:** see what your PC can run, and what to fix first if it sat unused for months:
   ```bash
   ./check.py            # graphics card, memory, disks, updates, wake settings -> which models fit
   ./check.py --setup    # then install exactly what it recommends
   ./check.py --setup --want images,sounds   # or only the jobs you want (see "What gets installed")
   ./check.py --setup --kit game-assets      # or a ready-made set from kits.json
   ./check.py --tidy     # old PC: sizes first; asks before deleting, and only Lazy Link's own files
   ```
   Or install by hand (no admin needed, all into `C:\lazylink`):
   ```bash
   ./imagegen/imagegen.py setup          # ComfyUI + Python + PyTorch (about 5 GB)
   ./imagegen/imagegen.py sizes          # what each model downloads, and its licence
   ./imagegen/imagegen.py fetch --models krea2 --recipes audio
   ./local/local.py setup                # chat models + Whisper (about 35 GB)
   ./voice/clone.py --setup              # voice copying (about 6 GB)
   ```

### What gets installed
Everything goes into the PC's work folder (`C:\lazylink`); nothing needs admin, and `Undo-PC -RemoveWorkFolder`
takes it all away. Pick jobs with `--want` (comma-separated) or a kit from `kits.json` with `--kit`.
With neither, `--setup` installs every job that fits your card except `maps` and `voice`.

| Job | What you can do | What it installs | Download |
|---|---|---|---|
| `images` | pictures, transparent cutouts | ComfyUI + PyTorch, the best picture model that fits your card (Krea 2 Turbo, Z-Image or FLUX.2 klein), BiRefNet | 5 GB + 16-21 GB |
| `sounds` | sound effects, music | Stable Audio 3 (small and/or medium) | 4-14 GB |
| `3d` | picture to 3D model (.glb) | Pixal3D + TRELLIS.2 | about 15 GB |
| `maps` | bump maps for game surfaces | MoGe-2 | under 1 GB |
| `text` | chat, speech-to-text | llama.cpp, Qwen3-30B-A3B, gpt-oss-20b, Whisper large-v3 | about 35 GB |
| `voice` | copy your own voice | Chatterbox + its own PyTorch, the "Record voices" icon | about 6 GB |

Kits: `pictures` (images), `game-assets` (images, sounds, 3d, maps), `everything`. `./imagegen/imagegen.py sizes`
lists every file, its size and its licence. `./check.py` prints the exact total for your card before anything downloads.

## Other computers
The main path above is a **Mac controlling a Windows PC**. Other mixes:

| Small computer → big computer | How | State |
|---|---|---|
| Mac → Windows | the steps above | tested end to end |
| Windows → Windows | [docs/WINDOWS-SMALL-COMPUTER.md](docs/WINDOWS-SMALL-COMPUTER.md) | installer tested on Windows 11; pairing not yet |
| Linux → Windows | `./install-mac.sh` works on Linux too (it only uses `ssh-keygen`) | not yet tested |
| Any → Linux | [docs/LINUX-PC.md](docs/LINUX-PC.md) | pictures, sound, chat and speech now run there; tested on CPU in a virtual machine; GPU not yet tested on real hardware |
| Any → Mac (Apple Silicon) | [docs/MAC-PC.md](docs/MAC-PC.md) | setup, undo, job runner, check and the picture, sound, chat and speech scripts written; tested on one Mac without a second machine; the real setup run and the models not yet |

## Try these first
Each one runs the real commands on your PC and ends in a file to open, in `out/try/<name>/`:
```bash
./examples/try.py            # the list, with what each one needs installed
./examples/try.py spin       # an animated web page made into a video (no AI model)    -> spin.mp4
./examples/try.py pictures   # floor, wall and brick surfaces + a lantern, side by side -> sheet.png
./examples/try.py sounds     # six game sounds, checked for harshness                  -> soundboard.mp3
./examples/try.py room       # AI bump maps on those surfaces, in a lit room            -> room.mp4
./examples/try.py 3d         # the lantern as a 3D model, on a turntable               -> turntable.mp4
```
Every step prints its command, so you can run the same thing on your own pictures and sounds afterwards.
They need ffmpeg and `pip3 install numpy scipy pillow` on your small computer.

## Use it
```bash
./imagegen/imagegen.py one "a red ceramic mug, product photo" --out out/ --cutout
./imagegen/imagegen.py run imagegen/examples/images.json --out out/          # many at once
./imagegen/imagegen.py make audio imagegen/examples/sounds.json --out out/   # sounds / music
./imagegen/imagegen.py make mesh imagegen/examples/mesh.json --out out/      # picture -> 3D (.glb)
./imagegen/imagegen.py preview out/                                          # 3D turntable pictures (needs Blender on the PC)
./imagegen/imagegen.py bump floor.png --out out/                             # bump maps: a normal map + a height map, so a flat picture looks raised in games and 3D
./imagegen/imagegen.py shrink out/red-mug__mesh.glb --out out/               # game-size .glb: 512 px JPEG textures instead of PNG
./local/local.py ask-one "Explain Wake-on-LAN in two sentences"
./local/local.py ask local/examples-asks.jsonl --out out/
./local/local.py transcribe talk.m4a --out out/                              # .txt, .srt, word timings
./mac/pcjob run my-job-folder --fetch out/                                   # anything else: a folder with a job.ps1
./video/webvideo.py video/examples/spin.html --out out/ --seconds 6          # animated web page -> .mp4 + .png (docs/WEB-TO-VIDEO.md)
python3 imagegen/audio_check.py out/sounds/ --fix out/game/                 # is a sound harsh? soundboard to listen to; --fix writes game-ready copies
python3 imagegen/pick_sounds.py out/takes/ --ref liked.wav --dest out/picks/  # keep the best takes that sound like your reference clips
python3 imagegen/sheet.py cmp.png asks.json krea2=out/krea2:"Krea 2"         # compare pictures from several models side by side
python3 imagegen/watch.py                                                    # new open image models worth a test (public Hugging Face pages)
```
`bump` asks the PC's MoGe-2 model for the shapes (the same model as `make mesh`), then does the rest on the Mac.
It and `shrink` need Python libraries on the Mac: `pip3 install numpy scipy pillow` (`shrink` needs only Pillow).
`audio_check` and `pick_sounds` need ffmpeg (`brew install ffmpeg`) and numpy (`audio_check`) or numpy and scipy (`pick_sounds`);
`sheet` needs Pillow; `watch` needs nothing extra. One install covers all: `pip3 install numpy scipy pillow`.
`bump` and `shrink` are new; the PC step of `bump` has not yet run from this repo.
Or just ask your AI agent in this folder: *"make me 10 coin sounds"*. [AGENTS.md](AGENTS.md) tells it how.

### Copy your own voice (Windows PC)
```bash
./voice/install.py          # puts a "Record voices" icon on the PC's desktop
./voice/clone.py --setup    # once: installs Chatterbox on the PC (about 6 GB with its own torch)
./voice/clone.py            # after you record: your recording and the AI copy, side by side
```
Double-click **Record voices** on the PC, put on your headset and read the lines (about 20 minutes, one line at a
time, Space to record). The page records the plain microphone (no noise filters, which the copy would learn too)
and warns when a take is too loud, too quiet or the room is noisy. Then `clone.py` brings the takes back, picks
about 10 s of the cleanest lines and has [Chatterbox](https://huggingface.co/ResembleAI/chatterbox) (MIT) say a new
line in your voice, on the PC, offline. Your recordings are personal: they stay on the PC and your small computer
(`voice/recordings` and `voice/refs`, never committed) and never go anywhere else. Change the lines in
`voice/scripts.json`; `voice/example-voices.json` has two made-up character voices. Only copy a voice you have the
right to use. Everything Chatterbox makes carries an inaudible watermark that marks it as AI speech.

**Batch your asks.** Each job spends 15-55 s starting ComfyUI or loading a model; after that, every
extra image or answer is quick. One job with 50 asks beats 50 jobs with one.

## Run big jobs when electricity is cheap (optional)
If your power company charges less at night, put the cheap hours in `config.json`:
`"cheap_hours": ["23:00-07:00"]`. Then `LAZY_CHEAP=1 ./imagegen/imagegen.py run ...` (or `pcjob run --cheap`)
waits until then, and `"cheap_only": true` makes every job wait. Don't know your hours? Set
`"cheap_hours_auto": true` and your AI agent looks up your plan's off-peak hours and keeps them current.
`./mac/pcjob cheap` shows the hours and whether they're on now.
In Northern Illinois (ComEd) or on the UK's Octopus Agile tariff, `"cheap_feed": {"source": "comed"}` or
`{"source": "octopus", "region": "C"}` picks the cheapest 6 hours of each day from the utility's free live
price feed instead. Keep `cheap_hours` set too: it is used if the feed is down.

Installed before 2026-10-30? Re-run Setup-PC; it renames `render` to `lazylink` and keeps your models. Then pair again from the Mac.

## Safety, in plain words
- **A separate, hidden, standard account** (`lazylink`) runs every job. It is not an administrator,
  can't sign in at the keyboard, and its password is random and never shown.
- **Key only.** SSH accepts only your Mac's key, only for `lazylink`. Passwords are switched off.
- **Home network only.** The firewall opens port 22 only on networks Windows calls *Private*, and
  only to addresses on your own home network. ComfyUI and the chat model listen on 127.0.0.1 (the
  PC itself), never the network.
- **Your PC, your rules.** `pcjob sleep` refuses while someone is signed in and using the PC.
  A job that woke the PC puts it back to sleep afterwards, and only then.
- **Undo:** double-click `pc\Undo-PC.cmd` (add `-RemoveWorkFolder -RemoveOpenSSH` to remove everything).

**What a job can reach on the PC.** Every job runs as `lazylink`, so a job (or an AI agent sending jobs) can reach only what that account can:
- **Can:** the work folder `C:\lazylink` (tools, models, jobs), your extra model folders (`extra_model_folders`
  in config.json; open on purpose so ComfyUI can read them), and what any standard account can read: Windows,
  Program Files, ProgramData.
- **Can't:** your own user folder (Windows keeps each account's folder private), other drives and the folders straight under `C:\`
  once Setup-PC's drive lock has run (it adds a DENY for `lazylink` alone; your own access doesn't change), and admin
  settings.
- **Check it yourself:** `./check.py` lists every place outside the work folder that `lazylink` can still read or change.

**Leftover risks, honestly:**
- Anything in an extra model folder can be read, and maybe changed, by jobs. Keep only models there.
- Folders other accounts left open to "Users" or "Everyone" inside Program Files or ProgramData stay readable (some apps do this).
- Jobs can use the internet (to download models). A job you didn't write could send out what it can read, so run only jobs you trust.
- Whoever holds your Mac's key can run jobs. Treat that computer like the PC's front door.
- A drive you plug in after setup isn't locked until you run Setup-PC again.

More: [docs/FAQ.md](docs/FAQ.md) (for example, how to let your AI organise your whole PC, and how to take that back).

## Licences of the models (check before you sell what you make)
| Use | Model | Licence |
|---|---|---|
| Images (default) | Krea 2 Turbo | Krea 2 Community: free while yearly revenue < $1M |
| Images, quick / any use | FLUX.2 klein 4B, Z-Image Turbo | Apache-2.0 |
| Transparent cutouts | BiRefNet | MIT |
| Sounds and music | Stable Audio 3 (small / medium) | Stability AI Community: free while yearly revenue < $1M |
| Picture to 3D | Pixal3D + TRELLIS.2 | MIT |
| Chat | Qwen3-30B-A3B, gpt-oss-20b | Apache-2.0 |
| Speech-to-text | Whisper large-v3 (faster-whisper) | MIT |

Every file comes from the maker's official repository (or ComfyUI's official repack) and is checked
against its size and sha256 before use. This repo's own code is MIT.

## Support this project

Lazy Link is free and stays free (MIT). If it saves you money or time, you can chip in:
- [GitHub Sponsors](https://github.com/sponsors/shiccupagent): once or monthly, any amount.
  Personal sponsorships go 100% to the project.
- [Any amount, once](https://buy.stripe.com/cNieVf6aIbMz1dJ0jUdZ601) (card, Apple Pay and more, through Stripe).
- [Monthly, through Stripe](https://buy.stripe.com/5kQdRb42AdUH1dJaYydZ600): $1 a month for each one you pick.
  [Cancel any time here](https://billing.stripe.com/p/login/5kQdRb42AdUH1dJaYydZ600).

Thank you!

## More
- [docs/FAQ.md](docs/FAQ.md): what jobs can reach, letting your AI reach more, and other questions.
- [docs/BIOS-AND-WAKE.md](docs/BIOS-AND-WAKE.md): make the PC wake from the network (BIOS and Windows settings).
- [docs/MAC-PC.md](docs/MAC-PC.md): an Apple Silicon Mac as the big computer: setup, what it changes, what is tested.
- [docs/ROADMAP.md](docs/ROADMAP.md): what's done and what's next (every operating system, live power prices, more machines).
- [docs/REMOTE-DESKTOP.md](docs/REMOTE-DESKTOP.md): optional, see and use your computers from anywhere, safely.
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md): why each piece is built the way it is.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): the gotchas we hit, and their fixes.
