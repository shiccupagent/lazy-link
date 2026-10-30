# job.sh for `imagegen.py fetch` on a Linux PC: download the model files listed in want.json that the PC
# does not have yet. Official URL from models.json, resumable download to .part, size + sha256 checked
# before the file gets its real name. A file already in models/comfy or in one of your extra model
# folders (same name, same size) is skipped, so nothing is downloaded twice.
set -eu -o pipefail

die() { echo "fetch: $*" >&2; exit 1; }
[ -n "${RENDER_ROOT:-}" ] || die "RENDER_ROOT is not set (run this as a job)"
[ -n "${RENDER_OUT:-}" ] || die "RENDER_OUT is not set (run this as a job)"
[ -f models.json ] && [ -f want.json ] || die "models.json and want.json must be in the job folder"

# One line per file: name, size, sha256, url, destination, and an existing copy that already passes (or empty).
PLAN=$(python3 - <<'PY'
import json, os, sys
m = json.load(open("models.json"))
want = json.load(open("want.json"))
extra = (json.load(open("link.json")).get("extra_model_folders") or {}) if os.path.exists("link.json") else {}
lib = os.path.join(os.environ["RENDER_ROOT"], m["library"])
for name in want:
    f = m["files"].get(name)
    if not f:
        sys.exit(f"unknown file {name}")
    dest = os.path.join(lib, f["dir"], name)
    cands = [dest] + [os.path.join(d, name) for d in (extra.get(f["dir"]) or [])]
    have = next((c for c in cands if os.path.isfile(c) and os.path.getsize(c) == f["size"]), "")
    print("\t".join([name, str(f["size"]), f["sha256"], f["url"], dest, have]))
PY
)

need=0; n=0
while IFS=$'\t' read -r name size sha url dest have; do
    [ -z "$name" ] && continue
    n=$((n + 1))
    [ -n "$have" ] || need=$((need + size))
done <<< "$PLAN"
free=$(df -P -B1 "$RENDER_ROOT" | awk 'NR==2 {print $4}')
echo "$n file(s), $(awk -v b=$need 'BEGIN {printf "%.1f", b/1e9}') GB if none are there yet; $(awk -v b=$free 'BEGIN {printf "%.0f", b/1e9}') GB free"
[ "$need" -le "$free" ] || die "not enough free space in $RENDER_ROOT"

report=()
while IFS=$'\t' read -r name size sha url dest have; do
    [ -z "$name" ] && continue
    if [ -n "$have" ]; then
        echo "have  $name"
        report+=("{\"file\": \"$name\", \"state\": \"had\", \"seconds\": 0}")
        continue
    fi
    mkdir -p "$(dirname "$dest")"
    part="$dest.part"
    echo "get   $name ($(awk -v b=$size 'BEGIN {printf "%.2f", b/1e9}') GB) from $url"
    t0=$(date +%s); tries=0
    until curl -L --fail --silent --show-error --retry 5 --retry-delay 10 -C - -o "$part" "$url"; do
        tries=$((tries + 1))
        [ "$tries" -ge 4 ] && die "download failed for $name"
        echo "  curl failed, resuming (try $((tries + 1)))"
        sleep 15
    done
    len=$(stat -c %s "$part")
    [ "$len" = "$size" ] || die "$name is $len bytes, expected $size"
    got=$(sha256sum "$part" | cut -d' ' -f1)
    [ "$got" = "$sha" ] || { rm -f "$part"; die "$name sha256 mismatch ($got)"; }
    mv -f "$part" "$dest"
    s=$(( $(date +%s) - t0 ))
    echo "done  $name in $s s, sha256 ok"
    report+=("{\"file\": \"$name\", \"state\": \"downloaded\", \"seconds\": $s}")
done <<< "$PLAN"

{
    echo "["
    for i in "${!report[@]}"; do
        sep=","; [ "$i" -eq $((${#report[@]} - 1)) ] && sep=""
        echo "  ${report[$i]}$sep"
    done
    echo "]"
} > "$RENDER_OUT/fetch.json"
echo "fetch done"
