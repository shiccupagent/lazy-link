# Lazy Link roadmap

The idea: use your big computer lazily from your small, nice one. The big one sleeps until a job comes,
does the heavy lifting with no windows, and goes back to sleep.

## Done
- **Mac → Windows PC with an NVIDIA card:** pictures, sound and music, 3D, chat, speech-to-text.
- **`./check.py`:** looks over a PC that sat unused for months:
  - updates, driver, disks, memory, wake settings
  - rates every model fast / slow / too big for that PC
  - installs what fits (`--setup`)
- **Cheap-electricity hours:**
  - `--cheap` / `LAZY_CHEAP=1` / `cheap_only` hold jobs until the off-peak hours.
  - `cheap_hours_auto` lets Claude look up your plan's hours.
  - **Live prices:** `cheap_feed` picks the cheapest hours each day from ComEd (Northern Illinois) or
    Octopus Agile (UK). Other free feeds can be added as one function each.
- **Old-PC tidy-up (Windows):** `./check.py --tidy` shows the sizes first and asks before deleting anything.
  It removes only Lazy Link's own files: its account's temp files, old finished jobs and old logs.
- **Linux as the big computer:** setup, undo, job runner, `check.py`, pictures, sound, chat and speech
  (ComfyUI, llama.cpp, Whisper). Tested on CPU in a virtual machine; see [LINUX-PC.md](LINUX-PC.md).
- **Apple Silicon Mac as the big computer:** setup, undo, job runner, `check.py`, and the picture, sound, chat and speech
  scripts (PyTorch on the GPU, llama.cpp with Metal, Whisper on the CPU). Tested on one Mac without a second machine;
  the real setup run, the models and wake from sleep still to test. See [MAC-PC.md](MAC-PC.md).
- **Guides:** [BIOS and wake](BIOS-AND-WAKE.md), and an optional [remote desktop](REMOTE-DESKTOP.md) guide.

## Next
Small fixes first:
- Mac big computer: if Remote Login was already on, keep the accounts that already use SSH working (add `lazylink` to the allowed group instead of `AllowUsers lazylink`).
- Windows `local/setup-local-ai.ps1`: pin PyAV below 17 like Linux (PyAV 19 breaks faster-whisper 1.2.1), then test on the PC.

1. **Any small computer.** Windows: `Install-Windows.cmd` (tested on Windows 11; pairing from Windows still to test).
   Linux: `install-mac.sh` works there too (to test).
2. **Any big computer:**
   - **Linux + NVIDIA:** a real Linux PC with an NVIDIA card: the GPU paths (ComfyUI and llama.cpp on CUDA,
     Whisper on the GPU), the 30 GB chat models and 3D. Until then it is tested on CPU only.
   - **AMD cards:** ROCm on Linux, DirectML/ZLUDA on Windows. Speed and model support vary, so `check.py` rates them separately.
   - Each one is done only when it has passed on real hardware.
3. **More than one big computer (a home AI cluster):**
   - a list of machines in `config.json`
   - each job goes to the one that's awake, free and has enough graphics memory
   - every machine is set up by its owner, with the same safety rules
