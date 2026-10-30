# Lazy Link: questions and answers

Lazy Link is your own private AI cloud at home: the big PC does the AI work, your small computer (and any AI agent
on it) sends the jobs. These are short answers to what people ask most. The safety rules are in the README under
[Safety, in plain words](../README.md#safety-in-plain-words).

## What can Lazy Link reach on my PC?

Every job runs as `lazylink`, a hidden standard (non-admin) Windows account. A job, or an AI agent sending jobs,
can reach only what that account can.

**It can:**
- read and change the work folder `C:\lazylink` (tools, models, jobs and results);
- read, and maybe change, your extra model folders (`extra_model_folders` in config.json). They stay open on
  purpose so ComfyUI can load the models in them;
- read what any standard Windows account can read: Windows, Program Files, ProgramData.

**It can't:**
- open your own user folder (Documents, Desktop, Downloads): Windows keeps each account's folder private;
- reach your other drives (D:, E:, ...) or the folders straight under `C:\`, once Setup-PC's drive lock has run;
- change Windows settings, install drivers or do anything that needs an administrator.

**Leftover risks:**
- Jobs can read, and maybe change, files in an extra model folder. Keep only models there.
- Some apps leave folders inside Program Files or ProgramData open to every account. Those stay readable.
- Jobs can use the internet (that is how models download). A job you didn't write could send out what it can
  read, so run only jobs you trust.
- Whoever holds your small computer's key can run jobs. Treat that computer like the PC's front door.
- A drive you add after setup isn't locked until you run Setup-PC again.

## How do I check what it can reach?

Run `./check.py` on your small computer. It runs a check job as `lazylink` and lists every place outside the work
folder that the account can still read or change. To test "change", it makes one empty, uniquely named file and
deletes it again; it never touches your existing files.

## How does the drive lock work?

Windows often lets every account read, or even change, other drives and some folders straight under `C:\`.
Setup-PC adds a **DENY** entry for `lazylink` alone on each of those places. A DENY always wins over any "allow"
the account gets through a group, and it changes nobody else's access, yours included. Setup-PC writes what it
locked to `C:\ProgramData\ssh\lazy-link-drive-locks.json` (only admins can change that file), and Undo-PC takes
off exactly those entries.

Left open on purpose: `C:\lazylink`, your extra model folders and the folders that hold them, what the job runner
needs (Windows, Program Files, ProgramData, Users) and every folder on the system PATH (so tools installed there
keep working). `pc/reach.ps1` has the list.

`Setup-PC.cmd -DryRun` shows what it would lock without changing anything. `Setup-PC.cmd -NoDriveLock` skips the lock.

## I added an extra model folder. What now?

Run `./imagegen/imagegen.py setup` on your small computer (it tells ComfyUI about the folder), then run Setup-PC
again on the PC. It reads the folder from ComfyUI's list and keeps it open. Running Setup-PC again is safe.

## Can my AI organize my whole PC?

Yes, if you want that. It means giving `lazylink` access to more of your PC, so think of it as handing your AI
the keys to those folders. Open an **admin PowerShell** on the PC (Start, type PowerShell, right-click,
Run as administrator) and run the lines you need.

Let jobs read and change one folder (here `D:\Photos`):
```powershell
icacls "D:\Photos" /remove:d lazylink
icacls "D:\Photos" /grant "lazylink:(OI)(CI)M"
```
The first line takes off Setup-PC's lock, if there was one. The second gives `lazylink` "modify": read, write and
delete, but not change permissions. Use `RX` instead of `M` for read-only.

Keep it open when you run Setup-PC again: `Setup-PC.cmd -OpenFolder D:\Photos`.

Take it back:
```powershell
icacls "D:\Photos" /remove:g lazylink
```
Then run Setup-PC again to put the lock back.

Your own user folder works the same way (`C:\Users\<you>\Documents`), but think twice: that is where passwords,
browser data and tax papers usually live. A safer pattern is a folder just for the AI, for example `D:\ForAI`.

## Which AI agents work with it?

Any agent that runs terminal commands on your small computer: Claude Code, Codex, Gemini CLI, Cursor, or you
typing the commands. `AGENTS.md` is the one instructions file; `CLAUDE.md` and `GEMINI.md` point to it.

## Does the PC send my files or prompts to the internet?

No. Your prompts and files stay on your own machines. The only internet traffic is downloading models from their
official sites and reading public model lists (`imagegen/watch.py`).

## Does `./check.py` change anything on the PC?

The report only reads the PC (graphics card, memory, disks, updates, wake settings, what `lazylink` can reach),
apart from the empty test file above. Two flags change things: `./check.py --setup` installs what the report
recommends, so it downloads models, and `./check.py --tidy` deletes Lazy Link's own old files after asking you.

## How do I take it all back?

Double-click `pc\Undo-PC.cmd`. It removes the drive locks it added, the `lazylink` account, the firewall rule and
the job runner. Add `-RemoveWorkFolder -RemoveOpenSSH` to remove the work folder and OpenSSH Server too.
