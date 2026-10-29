#!/bin/bash
# frame-scan.sh VIDEO [EVERY_SECONDS] - read the text in a video, one frame every N seconds (default 3),
# with macOS's own text recognition, then run privacy-scan.sh's checks on that text. Use it before
# a screen recording or tutorial leaves your machine. Needs ffmpeg and the Xcode command line tools.
set -euo pipefail
VIDEO="$1"; EVERY="${2:-3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
FF="$(command -v ffmpeg || echo /opt/homebrew/bin/ffmpeg)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
"$FF" -v error -i "$VIDEO" -vf "fps=1/$EVERY" "$WORK/f_%05d.png"
n=$(ls "$WORK"/*.png | wc -l | tr -d ' ')
swift "$HERE/ocr.swift" "$WORK"/*.png | sed "s#^$WORK/##" > "$WORK/text.txt"
mkdir -p "$WORK/scan" && cp "$WORK/text.txt" "$WORK/scan/frames-text.txt"
echo "read $n frames ($(wc -l < "$WORK/text.txt" | tr -d ' ') lines of text)"
"$HERE/privacy-scan.sh" "$WORK/scan" | grep -v '^privacy scan: clean' || true
# privacy-scan exits non-zero on a hit; repeat the check on the frame text alone for the verdict
if "$HERE/privacy-scan.sh" "$WORK/scan" >/dev/null 2>&1; then echo "frame scan: clean"; else echo "frame scan: FOUND personal data"; exit 1; fi
