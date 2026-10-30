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
- **Guides:** [BIOS and wake](BIOS-AND-WAKE.md), and an optional [remote desktop](REMOTE-DESKTOP.md) guide.

## Next
1. **Any small computer.** The controller side is Python + the system's own `ssh`, so Linux works as-is.
   Windows (10/11 ship OpenSSH) needs an `install-windows.ps1` twin of `install-mac.sh`.
   - Done when the setup steps pass on Windows and Linux.
2. **Any big computer:**
   - **Linux + NVIDIA:** a `runner.sh` twin of `pc/runner.ps1` (a systemd service under its own user) and `setup-linux.sh`.
     ComfyUI, llama.cpp and Whisper all run there.
   - **Apple Silicon Mac as the big computer:** ComfyUI and llama.cpp use Metal; launchd runs the runner.
   - **AMD cards:** ROCm on Linux, DirectML/ZLUDA on Windows. Speed and model support vary, so `check.py` rates them separately.
   - Each one is done only when it has passed on real hardware.
3. **Live prices:** where a free public feed exists, pick the cheapest hours each day instead of fixed windows.
   Examples are a US utility's hourly price feed or a UK agile tariff.
4. **Tidy-up for an old PC (opt-in, never automatic):** after `check.py`, offer the safe Windows clean-ups:
   - Storage Sense / Disk Cleanup categories
   - old update files, temp files
   Each one shows the size first and runs only after a yes. Personal files are never touched.
5. **More than one big computer (a home AI cluster):**
   - a list of machines in `config.json`
   - each job goes to the one that's awake, free and has enough graphics memory
   - every machine is set up by its owner, with the same safety rules
