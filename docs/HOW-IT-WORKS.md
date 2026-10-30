# How it works, and why it is built this way

## 1. Why a spare PC at all
A gaming GPU is an AI machine that sits idle most of the day. Cloud image tools charge per image or
cap you per day; a local model costs only electricity and makes as many as you want. On an RTX 3090
we measured 3-15 s per 1-megapixel image, about 2 s per sound effect, and a chat model that writes
about 200 tokens per second. The Mac (where your AI agent or you work) stays light: it only sends prompts
and receives files.

## 2. The safe link
The goal is to let the Mac in and nobody else, without giving it the keys to the PC.
- **A separate account, `lazylink`.** Standard rights (not an administrator), hidden from the sign-in
  screen, random password nobody knows. If something went wrong in a job, it can only touch what
  `lazylink` can touch: its own work folder, `C:\lazylink`.
- **OpenSSH, key only.** Windows has an SSH server built in. Setup turns off passwords and allows
  exactly one account with exactly one key: the one `install-mac.sh` made on your Mac.
- **Firewall: home network only.** One rule: port 22, only on *Private* networks, only from your
  own subnet. If the PC joins a café Wi-Fi (a *Public* network), the door stays shut.
- **Pairing checks a fingerprint.** `pcjob pair` shows the PC's SSH fingerprint and you compare it
  with the PC's screen, so the Mac knows it is talking to your PC and not an impostor. After that,
  the Mac refuses to connect if the PC's identity ever changes.
- **One admin step.** Everything that needs admin is in `Setup-PC.cmd`, and `Undo-PC.cmd` reverses
  it. Everything after that (ComfyUI, models, chat models) installs as `lazylink`, without admin.

## 3. The job runner
The obvious way, running a long render straight over SSH, fails on Windows: when an SSH session
closes, Windows' OpenSSH ends every process that session started. A Wi-Fi blip or the Mac going
to sleep would kill a 20-minute job.

So the Mac doesn't run jobs; it **drops them off**. A job is a folder with a `job.ps1` script.
`pcjob run` packs it, copies it to `C:\lazylink\jobs\<id>\`, and writes a `ready` marker last.
A scheduled task (the *runner*) runs as `lazylink` from boot, whether or not anyone is signed in, and:
- runs one job at a time, oldest first;
- keeps the PC awake while a job runs;
- puts each job in a Windows *job object*, so a timeout or `pcjob cancel` ends the job and
  everything it started (no orphan holding the GPU);
- writes `status.json` (queued, running, done, failed, timeout, cancelled) and packs the results.
The Mac checks the status every 10 s and fetches the results when it's done.

## 4. Wake-on-LAN and sleep
The Mac can wake a sleeping PC by sending a *magic packet*: a broadcast on your home network that
the PC's network card listens for, even while asleep. It needs:
- a network **cable** (Wi-Fi cards don't listen while asleep);
- the BIOS setting usually called *Resume by PCI-E device* / *Wake on LAN* = Enabled, and
  *ErP* = Disabled (ErP cuts standby power to the network card);
- sleep, not a full shutdown (with Windows' *Fast Startup* on, a shut-down PC can't be woken).
`pcjob run` wakes the PC by itself, about 20 s from sleep. When the job is done it puts the PC back
to sleep **only if this job woke it and nobody is signed in and using it**.

## 5. ComfyUI, headless
ComfyUI is the open-source engine behind most local image tools. We install our own pinned copy in
`C:\lazylink\tools` (any ComfyUI you already have is left alone) and never open its web page:
each job starts it on 127.0.0.1 (the PC only), sends work through its HTTP API, collects the files
and stops it. Starting takes 13-55 s, which is why you batch.
Recipes for sound and 3D are ComfyUI's own templates, converted to the API format by ComfyUI's own
frontend (`imagegen.py convert`), so they stay right as ComfyUI changes.

## 6. Picking models: licence first
Before a model is even tried, check its licence for commercial use. Several popular models are
non-commercial only (we rejected FLUX.1 dev, Qwen-Image 2.1, MMAudio and AudioLDM for that).
Then a side-by-side test on your real use, judged by eye and ear, not by a benchmark.
`models.json` is the one list: file, official URL, size, sha256, licence and settings.

## 7. The local chat model and Whisper
llama.cpp runs open chat models on the GPU; Qwen3-30B-A3B and gpt-oss-20b both write about 200
tokens per second on a 3090. They're good for bulk jobs (naming 300 sound files, summarising logs)
so your AI agent spends its own effort on the parts that need it, then checks the results.
Whisper large-v3 turns speech into text with word timings: captions, transcripts, and lip-sync.

## 8. Quality gates
Fast generation makes it easy to ship junk. So: look at every image (models drift toward famous
characters), measure sounds for harsh peaks before listening, and render 3D models as turntable
pictures before they go into a game.
