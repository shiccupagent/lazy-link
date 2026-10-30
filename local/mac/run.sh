# job.sh for `local.py ask` and `local.py transcribe` on an Apple Silicon Mac: runs pc_job.py with the whisper venv's
# Python. A Mac that never got the speech part (no whisper venv) runs it with plain python3: the chat side
# needs only the standard library.
set -eu -o pipefail

[ -n "${RENDER_ROOT:-}" ] && [ -n "${RENDER_OUT:-}" ] || { echo "RENDER_ROOT and RENDER_OUT must be set (run this as a job)" >&2; exit 3; }
PY="$RENDER_ROOT/tools/whisper-venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" -u pc_job.py
