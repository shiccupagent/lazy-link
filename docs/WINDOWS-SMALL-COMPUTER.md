# Using a Windows PC as the small computer

Lazy Link has two computers. The **big PC** (with the NVIDIA graphics card) does the work. The **small computer** sends it jobs. The small computer can be a Mac, or a Windows 10 or 11 PC. This page is for a Windows small computer.

## What you need on the small computer

- Windows 10 or Windows 11.
- Python 3.9 or newer. The easiest way: open Command Prompt and type `winget install Python.Python.3.12`, then close and reopen the window.
- The OpenSSH Client. Most Windows PCs already have it. If it is missing: Settings, System, Optional features, Add a feature, OpenSSH Client.

No administrator rights are needed on the small computer.

## Steps

1. **Get this folder onto the small computer.** Download the repository as a zip and unzip it, or copy the folder over.
2. **Double-click `Install-Windows.cmd`.** It makes this computer's private key for the link to the big PC and copies the public half to `pc\mac-key.pub`. It changes nothing else, and it is safe to run again. The last screen shows the next command.
3. **On the big PC,** copy the whole folder (including `pc\mac-key.pub`), then double-click `pc\Setup-PC.cmd` and answer Yes to the administrator prompt. Setup prints a line of text at the end. You can also paste the line the small computer's installer printed.
4. **Pair the two.** Open Command Prompt in this folder (click the address bar in File Explorer, type `cmd`, press Enter). Type:

   `python mac\pcjob pair 192.168.1.50`

   (Use `py` instead of `python` if the installer's last screen says so.) Compare the fingerprint it shows with the one on the big PC's screen. Type `y` only if they match.
5. **Look over the big PC:** `python check.py`. It lists what the PC can run and what to fix first.
6. **Install what it recommends:** `python check.py --setup`. This downloads tens of gigabytes, so read the size in step 5 first.

## What's tested
- On a real Windows 11 PC: the installer (makes the key, safe to run again, clear message when Python or the
  OpenSSH client is missing), `check.py`, and `pcjob cheap`.
- Not yet tested from a Windows small computer: `pair` and sending real jobs. They use the same OpenSSH as the
  Mac, so they should work. If something breaks, docs/TROUBLESHOOTING.md and an issue on GitHub help.
