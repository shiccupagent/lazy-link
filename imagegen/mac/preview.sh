# job.sh for `imagegen.py preview DIR` on an Apple Silicon Mac: four turntable renders of every .glb in glb/, using
# Blender (background). Needs Blender on the Mac: the runner sets BLENDER when it finds it, else the app's default
# location is used (install from blender.org into /Applications).
set -u

die() { echo "preview: $*" >&2; exit 1; }
[ -n "${RENDER_OUT:-}" ] || die "RENDER_OUT is not set (run this as a job)"
BLENDER=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
[ -x "$BLENDER" ] || die "Blender is not installed on this Mac (install it from blender.org, then run the preview again)"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
"$BLENDER" -b --factory-startup -P render_glb.py -- glb "$RENDER_OUT" > "$tmp" 2>&1
rc=$?
grep -E 'rendered|Error|Traceback' "$tmp" || true
exit $rc
