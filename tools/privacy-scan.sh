#!/bin/bash
# privacy-scan.sh - run before every commit/push. Fails if a file that git would publish contains
# something personal: IP or MAC addresses (other than the documented examples), emails, home-folder
# paths, keys or tokens, or any word in your own private list (one per line, outside the repo):
#   ~/.config/render-link/private-words.txt   e.g. your name, PC name, Windows account, Wi-Fi name
# Usage: tools/privacy-scan.sh [extra files or folders to scan, e.g. video frames' text]
set -uo pipefail
cd "$(dirname "$0")/.."
WORDS="${RENDER_LINK_PRIVATE_WORDS:-$HOME/.config/render-link/private-words.txt}"
files=$( { git ls-files; git ls-files --others --exclude-standard; } | sort -u)
[ $# -gt 0 ] && files="$files"$'\n'"$(find "$@" -type f)"
fail=0
check() {  # label, grep -E pattern, allowed pattern
  local hits
  hits=$(echo "$files" | grep -v '^$' | tr '\n' '\0' | xargs -0 grep -nIE "$2" 2>/dev/null | grep -v '^tools/privacy-scan.sh:' | grep -vE "$3" || true)
  if [ -n "$hits" ]; then echo "== $1"; echo "$hits" | cut -c1-200; fail=1; fi
}
NONE='^$^'
check "IPv4 address" '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' '192\.168\.1\.(50|255)|127\.0\.0\.1|255\.255\.255\.255|0\.0\.0\.0|\b[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+[-.](whl|zip)|version|v[0-9]|\b1\.0\.0\.|cu1[0-9]+'
check "MAC address" '\b([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}\b' '00-11-22-33-44-55'
check "email" '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$NONE"
check "home folder path" '/Users/[A-Za-z]|/home/[a-z]|C:\\\\Users\\\\|C:/Users/' "$NONE"
check "key or token" 'ssh-(ed25519|rsa) AAAA|BEGIN [A-Z ]*PRIVATE KEY|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}' "$NONE"
if [ -f "$WORDS" ]; then
  while IFS= read -r w; do
    [ -z "$w" ] || [[ "$w" == \#* ]] && continue
    hits=$(echo "$files" | grep -v '^$' | tr '\n' '\0' | xargs -0 grep -nIiF -- "$w" 2>/dev/null || true)
    names=$(echo "$files" | grep -iF -- "$w" || true)
    if [ -n "$hits$names" ]; then echo "== private word (line $(grep -nxF -- "$w" "$WORDS" | cut -d: -f1) of your list)"; echo "$hits$names" | cut -d: -f1,2 | sort -u; fail=1; fi
  done < "$WORDS"
else
  echo "note: no private word list at $WORDS (add your name, PC name, Windows account...)"
fi
[ $fail -eq 0 ] && echo "privacy scan: clean ($(echo "$files" | grep -vc '^$') files)" || { echo "privacy scan: FOUND personal data (above)"; exit 1; }
