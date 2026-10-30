# Using a Linux PC as the big computer

Lazy Link can use a Linux PC instead of the Windows one. Everything you do from the Mac stays the same. What changes is the setup on the big computer and a few limits, listed below.

**Status:** the Linux path is tested in a virtual Linux machine on the Mac. It has not yet been tested on a real Linux PC with an NVIDIA card, so the GPU work (images, sounds, 3D, local AI) is not checked on Linux yet.

## What you need

- A PC running Ubuntu (other Debian-based systems probably work, but Ubuntu is what was tested).
- An NVIDIA graphics card, with its driver installed (`nvidia-smi` must print the card).
- A network cable. Wake-on-LAN, which lets the Mac wake the PC from sleep, works only over a cable.
- The PC on a private network, and the Mac on the same network.

## Setup (once)

1. On the small computer, run its installer (`./install-mac.sh`, or `Install-Windows.cmd`). It makes the key that lets it sign in to the PC and puts the public half in `linux/mac-key.pub` (never committed; it is in `.gitignore`).
2. Copy the whole `linux` folder to the PC.
3. On the PC, open a terminal in that folder and run:

   `sudo bash setup-linux.sh`

   Add `--dry-run` first to see every change without making it. The setup creates a hidden account called `lazy` with no password and no admin rights, lets only that account sign in with the key, and sets up the job runner, which starts on boot and restarts if it stops.
4. On the Mac, pair with the PC: `./mac/pcjob pair ADDRESS --os linux`. Check the fingerprint the PC shows matches the one the Mac shows.
5. On the Mac, run `./check.py` to see what the PC has.

## Undo

On the PC, run `sudo bash undo-linux.sh`. It reverses each step the setup made and then checks that each one is gone. Your downloaded models are kept unless you add `--remove-work-folder`. The `--dry-run` option shows what it would remove.

## What works on Linux now

- `./check.py`: reports the PC's graphics card, memory, disks, network and update state, and says which models would fit.
- Jobs that run a shell script (`job.sh`) on the PC, with results fetched back to the Mac. The job runner keeps the PC awake while a job runs, stops a job when you cancel it or when it runs past its time limit, and cleans up everything the job started.
- `./mac/pcjob sleep` puts the PC to sleep with `systemctl suspend`. It is not yet tested on real hardware.

## What does not work on Linux yet

- The image, sound, 3D and local AI commands (`imagegen.py` and `local.py`) exit with "not on Linux yet". They need the Windows scripts.
- Hibernate is Windows only. `pcjob sleep --hibernate` is refused on Linux.
- The Linux PC has no Windows-style "Fast Startup" or "Wake on Magic Packet" checks yet beyond what `ethtool` reports.

## Notes

- The setup sets up the firewall rule for SSH only for the network you name (`--subnet`). It does not turn the firewall on. Turn the firewall on yourself if you want it.
- Models live in `/srv/lazy-link/models`. The undo keeps them unless you ask it to remove them.
