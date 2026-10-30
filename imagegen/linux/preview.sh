# job.sh for `imagegen.py preview DIR` on a Linux PC: four turntable renders of every .glb in glb/, using
# Blender (background). Needs Blender on the PC (the runner sets BLENDER when it is on the PATH).
set -u

die() { echo "preview: $*" >&2; exit 1; }
[ -n "${RENDER_OUT:-}" ] || die "RENDER_OUT is not set (run this as a job)"
[ -n "${BLENDER:-}" ] && [ -x "$BLENDER" ] || die "Blender is not installed on this PC (install it, then run the preview again)"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
"$BLENDER" -b --factory-startup -P render_glb.py -- glb "$RENDER_OUT" > "$tmp" 2>&1
rc=$?
grep -E 'rendered|Error|Traceback' "$tmp" || true
exit $rc
