# An Apple Silicon Mac as the big computer

Use a Mac with an M-series chip as the big computer. Your small computer (Windows, Linux or a Mac) sends
jobs over SSH; the big Mac runs them on its GPU (Metal) and sends the results back. The Mac sleeps when
it isn't working and wakes for a job.

## Status

| Part | State |
|---|---|
| Setup and undo | Dry runs only (they change nothing). The real run is not yet tested. |
| Job runner (LaunchDaemon) | Tested by hand in a scratch folder on macOS. Not yet started by launchd. |
| `check.py` on the Mac | Tested read-only on macOS 27, M4, 16 GB. |
| Pictures (ComfyUI on PyTorch MPS) | Written; not run. |
| Chat (llama.cpp with Metal) | Written; not run. |
| Speech (Whisper on the CPU) | Written; not run. |
| Sleep and wake from another computer | Not tested. |

Everything was tested on one Mac, with no second computer. Nothing here has been run as the real setup yet.

## What you need

- A Mac with Apple Silicon and macOS with the Command Line Tools installed. Setup checks this and stops with a
  message if they are missing (install them with `xcode-select --install`).
- An administrator account on that Mac. Setup asks for its password once.
- A wired Ethernet connection for wake from sleep. Apple Silicon wakes over the wired port only; Wi-Fi still
  works for jobs.
- Room for the models: tens of gigabytes.

## Setup

1. **Get the small computer's key.** If the small computer is a Mac, `install-mac.sh` writes its public key to
   `macpc/mac-key.pub`. Otherwise copy the one line from the public key file (ends in `.pub`) into a file.
2. **Dry run on the big Mac, no admin password needed:**
   `bash macpc/setup-mac.sh --dry-run`
   It prints every change it would make and changes nothing.
3. **Real run, on the big Mac:**
   `sudo bash macpc/setup-mac.sh`
   Pass `--key-file FILE` (or `--key "<the key line>"`) if the key isn't in `macpc/mac-key.pub`.
4. **Pair from the small computer:**
   `./mac/pcjob pair <the Mac's address> --os mac`
   This checks the Mac's fingerprint and fills in `config.json` (the work folder, the user `render`, the
   network card).
5. **Check it:** `./check.py`

## What setup changes

Each step is recorded in `/var/db/lazy-link/setup-state`, so undo can reverse it.

1. Creates the account `render`: a standard account (not an administrator), hidden from the login screen,
   with a random password that is never shown. The key is its only way in.
2. Adds a sign-in rule, `/etc/ssh/sshd_config.d/10-lazy-link.conf`: only `render` may sign in over SSH, with a key.
   Passwords are off for all SSH sign-ins.
3. Turns Remote Login (the SSH server) on, if it was off.
4. Makes the work folder `~render/lazy-link` (jobs, runner and models).
5. Adds a LaunchDaemon, `com.lazylink.runner`, that runs the job runner as `render` and restarts it if it stops.
6. Adds a sleep rule, `/etc/sudoers.d/lazy-link-sleep`: `render` may run `pmset sleepnow` as root and nothing else.
7. Turns Wake for network access on (`pmset womp 1`), if it was off.
8. Writes `pc-info.json` with the hardware summary that `pcjob pair` reads.

## Sign-in lockout

After setup, SSH accepts only the `render` account, with a key. Your own administrator account can no longer
sign in over SSH. Local sign-in, screen sharing and Chrome Remote Desktop are not affected. To get admin SSH
back, run the undo below.

## Sleep and wake

- **Sleep:** `pcjob sleep` runs `pmset sleepnow` through the sleep rule, and only when the Mac is not in use.
  "In use" means someone is signed in at the Mac and active in the last 15 minutes, or something is holding it
  awake. If the Mac can't say how idle it is, it counts as in use, so it stays awake.
- **Wake:** a magic packet over the wired port, with Wake for network access on (set by setup).

## Undo

1. Dry run, no admin password needed: `bash macpc/undo-mac.sh --dry-run`
2. Real run: `sudo bash macpc/undo-mac.sh`

Undo removes the account, the runner, the sleep rule and the sign-in rule. It turns Remote Login off only if
setup turned it on, and sets Wake for network access back to its old value. It keeps the models unless you add
`--remove-work-folder`. It leaves Command Line Tools and Homebrew alone.

## Not yet verified (open questions)

- `pmset sleepnow` as root through the sleep rule on macOS 27: not run yet.
- Whether the `render` account can read the idle time. If it can't, the Mac counts as in use and won't sleep
  (the safe side).
- Remote Login set through `launchctl` on macOS 27 may need Full Disk Access for Terminal.
- Wake from sleep needs the Ethernet port and has not been tried.
- `imagegen/mac/setup.sh` installs PyTorch without a version pin, so it gets whatever PyPI has that day. Pin it
  before relying on it.
- `local/mac/setup.sh` uses bash arrays and has not been run under macOS's bash (3.2). It may need a fix.
- The random `render` password appears briefly in the process list while setup creates the account. Only
  someone already on the Mac can see it, and only during setup.

## Files

- Setup and undo: `macpc/setup-mac.sh`, `macpc/undo-mac.sh`
- Job runner: `macpc/runner.sh`; hardware check: `macpc/check.sh`
- Pictures and sound: `imagegen/mac/` (setup, fetch, preview); chat and speech: `local/mac/`
- On the Mac: the work folder `~render/lazy-link`, the setup record `/var/db/lazy-link/setup-state`,
  and the runner log `~render/lazy-link/runner/launchd.log`
