# Troubleshooting: what went wrong for us, and the fix

**`check.py --tidy`, imagegen or local say the work folder isn't set.** If your PC runs an older runner, run `pc\Setup-PC.cmd` again; it upgrades the runner and keeps your models.

**The Mac can't connect.**
- The PC's network must be *Private* (Settings > Network & internet > your network > Private).
  On *Public*, the firewall rule doesn't apply. Setup warns you if it sees this.
- `ssh-keyscan` gets no answer: is `sshd` running? (Setup starts it and sets it to start at boot.)

**"WARNING: connection is not using a post-quantum key exchange algorithm."**
Newer Macs say this because Windows' built-in SSH server is an older OpenSSH. The link is still
encrypted and key-only; the warning goes away when Windows updates its OpenSSH.

**`pcjob` says the runner is DOWN.**
- Run `Setup-PC.cmd` again; it re-registers the task with a fresh password.
- The task needs the *Log on as a batch job* right; setup grants it. Some company-managed PCs block that.

**A job fails with an error that is only a warning.**
PowerShell turns anything a program prints to stderr into an error when `$ErrorActionPreference` is
`Stop` and you capture with `2>&1`. Judge native programs by their exit code (`$LASTEXITCODE`), and
use `'Continue'` around chatty tools like Blender.

**A command works in a job but fails when run straight over SSH** ("untrusted mount point",
os error 448, or "uv trampoline failed to spawn Python"). Windows treats an SSH login as a network
logon and won't follow the folder links that uv's Python install uses. Send work as a job
(`pcjob run`); the runner is a scheduled task, a normal logon, so it works there.

**Quoting PowerShell over SSH breaks.**
Don't fight it: `pcjob` sends every command as base64 with `-EncodedCommand`.

**PowerShell variable names ignore case.** `$m` and `$M` are the same variable. One overwrote the other in our setup script.

**`render` can't use some things.** A standard account can't use WMI, `taskkill`, the event log,
or programs installed only for another user. That's on purpose. Install tools into `C:\render\tools`
instead (the scripts here do).

**Wake-on-LAN doesn't wake the PC.**
Cable, not Wi-Fi. BIOS: *Resume by PCI-E device* Enabled, *ErP* Disabled. Sleep, not shutdown.
Check `config.json` has the PC's wired network card address (`pcjob pair` fills it in).

**"The PC went quiet" doesn't mean it's off.** We once thought a remote hibernate had worked
because SSH stopped answering. The PC was still on, with the network just gone quiet. Remote
hibernate is unreliable (a check that arrives while it writes memory to disk cancels it), so
`pcjob` uses sleep.

**Downloads.** `fetch` resumes broken downloads and checks size + sha256 before using a file.
Some models on Hugging Face are *gated* (you must sign in and accept terms); those aren't in
`models.json`.

**Model quirks we hit.**
- Fast ("distilled") image models barely change between seeds: change the prompt instead.
- Generated textures have seams; they need a make-tileable step before tiling.
- New sound effects can come out bright or clicky: check them before use.
- Picture-to-3D handles solid objects well; see-through glass and drawn cartoon faces come out badly.
