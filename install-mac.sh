#!/bin/bash
# install-mac.sh - the Mac half of Lazy Link setup. Safe to run again.
# Makes the Mac's own SSH key for the PC link, puts the public half where the PC setup finds it,
# and tells you the next step. Nothing is sent anywhere.
set -euo pipefail
cd "$(dirname "$0")"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null || {
  echo "Needs python3 3.9 or newer. Run: xcode-select --install   (then run this again)"; exit 1; }

KEY="$HOME/.ssh/lazy-link"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
OLD="$HOME/.ssh/render-link"   # the name before 2026-10-29: keep using that key
if [ ! -f "$KEY" ] && [ -f "$OLD" ]; then mv "$OLD" "$KEY" && mv "$OLD.pub" "$KEY.pub"; fi
if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "lazy-link" -f "$KEY"
  echo "Made a new key for the PC link: $KEY (it never leaves this Mac)"
else
  echo "Key already there: $KEY"
fi
cp "$KEY.pub" pc/mac-key.pub      # a Windows big PC reads it here
cp "$KEY.pub" linux/mac-key.pub   # a Linux one here
chmod +x mac/pcjob imagegen/imagegen.py local/local.py
command -v pbcopy >/dev/null && pbcopy < "$KEY.pub" && copied=" (also copied to the clipboard)" || copied=""

cat <<MSG

Mac side ready. Next, on the Windows PC:
  1. Copy this whole folder to the PC (USB stick, or download the same repo there).
     pc/mac-key.pub is the Mac's PUBLIC key: safe to copy; setup reads it from there.
  2. Double-click pc\\Setup-PC.cmd and say Yes to the admin prompt.
     (No copy of the folder? Setup asks you to paste this line$copied:)

$(cat "$KEY.pub")

  3. Back here, run the command the PC shows at the end:  ./mac/pcjob pair <PC address>
MSG
