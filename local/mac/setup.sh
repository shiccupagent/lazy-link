# job.sh for `local.py setup` on an Apple Silicon Mac: llama.cpp (Metal GPU), two open chat models (gpt-oss-20b,
# Qwen3-30B-A3B) and faster-whisper large-v3 on the CPU (CTranslate2 has no Metal backend). Runs as a job under the
# Mac's work account, everything under $RENDER_ROOT. Rerun-safe: a finished step is skipped. Every step lands in
# $RENDER_OUT/local-ai.json, so a failure in one never hides the others (like local/linux/setup.sh).
# LOCAL_STEPS="llama gptoss qwen bench whisper-venv whisper-model whisper-test" limits the steps (default: all).
# Needs python3, curl, tar, shasum and find on the Mac (all come with macOS).
set -u -o pipefail   # no -e: a failed step is recorded and the next one still runs

die() { echo "local setup: $*" >&2; exit 1; }
[ -n "${RENDER_ROOT:-}" ] && [ -n "${RENDER_OUT:-}" ] || die "RENDER_ROOT and RENDER_OUT must be set (run this as a job)"
JOB=$(pwd)                                   # the job folder: models.json (from imagegen) and pc_job.py live here
ROOT=$RENDER_ROOT; TOOLS=$ROOT/tools; MODELS=$ROOT/models
REPORT=$RENDER_OUT/local-ai.json
STEPS_TSV=$(mktemp)
trap 'rm -f "$STEPS_TSV"' EXIT

# macOS tools: stat has no -c, sha256sum is shasum
file_size() { wc -c < "$1" | tr -d ' '; }
sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

[ "$(uname -s)" = Darwin ] || die "this script is for macOS"
ARCH=$(uname -m)
case $ARCH in arm64) ;; *) die "unsupported CPU $ARCH (Apple Silicon arm64 only)" ;; esac
METAL=1   # llama.cpp's macOS build runs on the Apple GPU (Metal) by default; there is no CUDA on a Mac
echo "PC: Apple Silicon ($ARCH), Metal GPU for llama.cpp; speech-to-text on the CPU"

want() {   # LOCAL_STEPS (testing) picks steps by id; unset = all
    [ -z "${LOCAL_STEPS:-}" ] && return 0
    case " $LOCAL_STEPS " in *" $1 "*) return 0 ;; esac
    return 1
}

# one row per finished step: id, ok, seconds, last lines of output
add_step() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(printf '%s' "$4" | tr '\t' ' ')" >> "$STEPS_TSV"; }
write_report() {
    python3 - "$STEPS_TSV" "$REPORT" <<'PY'
import json, sys
out = {}
for line in open(sys.argv[1], encoding="utf-8"):
    name, ok, secs, text = line.rstrip("\n").split("\t", 3)
    out[name] = {"ok": ok == "true", "seconds": int(secs), ("result" if ok == "true" else "error"): text}
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
PY
}
step() {   # id, function: runs it, shows its output in the job log, records the result
    local id=$1 fn=$2 t0 rc tmp last secs
    want "$id" || { echo "skip  $id"; return 0; }
    echo "== $id"
    tmp=$(mktemp); t0=$(date +%s)
    "$fn" 2>&1 | tee "$tmp"; rc=${PIPESTATUS[0]}
    secs=$(( $(date +%s) - t0 ))
    last=$(tail -n 3 "$tmp" | tr '\n' ' '); rm -f "$tmp"
    if [ "$rc" -eq 0 ]; then echo "ok    $id ($secs s)"; add_step "$id" true "$secs" "$last"
    else echo "FAIL  $id (exit $rc)"; add_step "$id" false "$secs" "$last"; fi
    write_report
}

head_len() {   # size of a URL after redirects (empty when the server does not say)
    curl -sIL "$1" | tr -d '\r' | awk 'tolower($1) == "content-length:" {n = $2} END {print n}'
}
fetch_file() {   # url dest size [sha256]: resumable; checked before it gets its real name; kept if already there
    local url=$1 dest=$2 size=$3 sha=${4:-} part tries=0 got
    part="$dest.part"; mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && [ "$(file_size "$dest")" = "$size" ]; then
        if [ -z "$sha" ] || [ "$(sha256_of "$dest")" = "$sha" ]; then echo "have  $dest"; return 0; fi
    fi
    until curl -L --fail --silent --show-error --retry 5 --retry-delay 10 -C - -o "$part" "$url"; do
        tries=$((tries + 1)); [ "$tries" -ge 4 ] && { echo "download failed: $url"; return 1; }
        echo "  curl failed, resuming (try $((tries + 1)))"; sleep 15
    done
    [ "$(file_size "$part")" = "$size" ] || { echo "$dest is $(file_size "$part") bytes, expected $size"; return 1; }
    if [ -n "$sha" ]; then
        got=$(sha256_of "$part")
        [ "$got" = "$sha" ] || { rm -f "$part"; echo "sha256 mismatch for $dest"; return 1; }
    fi
    mv -f "$part" "$dest"; echo "got   $dest"
}

# llama.cpp b11986 macOS arm64 archive (official GitHub release). Size and sha256 match the release's asset metadata
# (GitHub API: size 16464104, digest sha256:a266fcce...), checked with `gh api` before pinning.
REL=https://github.com/ggml-org/llama.cpp/releases/download/b11986
ASSETS=("llama-b11986-bin-macos-arm64.tar.gz 16464104 a266fcce72860e3acafed69ebb50b19f4572f3bd37d4b0056997880229579133")
# Linux CUDA/CPU builds are in local/linux/setup.sh. Other llama.cpp Mac options (e.g. a newer tag) must pin the same way.

llama_libs_check() {   # the macOS archive keeps its dylibs next to llama-server (@rpath): nothing to install like on Linux
    return 0
}
llama_step() {
    local line name size sha exe
    exe=$(find "$TOOLS/llama" -name llama-server -type f 2>/dev/null | head -n 1)
    if [ -n "$exe" ]; then echo "have  $exe"; llama_libs_check "$exe" || return 1; echo "$exe"; return 0; fi
    [ "${#ASSETS[@]}" -gt 0 ] || { echo "no llama.cpp build for $ARCH"; return 1; }
    for line in "${ASSETS[@]}"; do
        read -r name size sha <<< "$line"
        [ -n "$sha" ] || { echo "no sha256 pinned for $name: refusing to download it unchecked"; return 1; }
        fetch_file "$REL/$name" "$TOOLS/dl/$name" "$size" "$sha" || return 1
        mkdir -p "$TOOLS/llama"; tar -xzf "$TOOLS/dl/$name" -C "$TOOLS/llama" || return 1
    done
    rm -rf "$TOOLS/dl"
    exe=$(find "$TOOLS/llama" -name llama-server -type f | head -n 1)
    [ -n "$exe" ] || { echo "llama-server is not in the archives"; return 1; }
    llama_libs_check "$exe" || return 1
    echo "$exe"
}

LLM_DIR=$MODELS/llm
GPTOSS=gpt-oss-20b-MXFP4.gguf
QWEN=Qwen3-30B-A3B-Q4_K_M.gguf
llm_step() {   # url file: size from the server, like the Windows script
    local size
    size=$(head_len "$1"); [ -n "$size" ] || { echo "no size from $1"; return 1; }
    fetch_file "$1" "$LLM_DIR/$2" "$size"
}
bench_step() {   # llama-bench on the first model that is there: prompt (pp512) and writing (tg128) speed
    local bench f ngl=0
    [ "$METAL" = 1 ] && ngl=99
    bench=$(find "$TOOLS/llama" -name llama-bench -type f 2>/dev/null | head -n 1)
    [ -n "$bench" ] || { echo "no llama-bench"; return 1; }
    f=$LLM_DIR/$QWEN; [ -f "$f" ] || f=$LLM_DIR/$GPTOSS
    [ -f "$f" ] || { echo "no chat model to bench"; return 1; }
    "$bench" -m "$f" -ngl "$ngl" -p 512 -n 128 2>&1 | grep '|' | tail -n 3
}

uv_bin() {   # uv from models.json (same release as the Linux and Windows pins), fetched once
    local uv=$TOOLS/uv/uv url sha size
    if [ ! -x "$uv" ]; then
        read -r url sha size < <(python3 -c 'import json, sys; u = json.load(open("models.json"))["uv"]["mac"][sys.argv[1]]; print(u["url"], u["sha256"], u["size"])' "$ARCH") || return 1
        fetch_file "$url" "$TOOLS/dl/uv.tar.gz" "$size" "$sha" >&2 || return 1
        mkdir -p "$TOOLS/uv" && tar -xzf "$TOOLS/dl/uv.tar.gz" -C "$TOOLS/uv" --strip-components=1 || return 1
    fi
    echo "$uv"
}
whisper_venv_step() {
    # av 19 dropped the metadata_errors argument that faster-whisper 1.2.1 passes to av.open (tested on Linux: transcribe fails)
    # CPU only: CTranslate2 has no Metal backend, so no CUDA packages here
    local uv v=$TOOLS/whisper-venv pkgs=(faster-whisper "av>=11,<17")
    uv=$(uv_bin) || return 1
    export UV_PYTHON_INSTALL_DIR=$TOOLS/python UV_CACHE_DIR=$ROOT/tmp/uv-cache
    [ -x "$v/bin/python" ] || "$uv" venv --python 3.12 "$v" || return 1
    "$uv" pip install --python "$v/bin/python" "${pkgs[@]}" || return 1
    "$v/bin/python" -c "import faster_whisper; print('faster-whisper', faster_whisper.__version__)"
}
whisper_model_step() {
    local base=https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main dir=$MODELS/whisper-large-v3 f len
    for f in config.json preprocessor_config.json tokenizer.json vocabulary.json model.bin; do
        len=$(head_len "$base/$f"); [ -n "$len" ] || { echo "no size for $f"; return 1; }
        fetch_file "$base/$f" "$dir/$f" "$len" || return 1
    done
    echo "model in $dir"
}
whisper_test_step() {   # pc_job.py transcribes three seconds of silence: loads the model on the real device
    local t=$RENDER_OUT/whisper-test
    rm -rf "$t"; mkdir -p "$t/in/inputs" "$t/out"
    "$TOOLS/whisper-venv/bin/python" - "$t/in/inputs/silence.wav" <<'PY' || return 1
import sys, wave, struct
with wave.open(sys.argv[1], "wb") as f:
    f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(struct.pack("<h", 0) * 16000 * 3)
PY
    echo '{"transcribe": true}' > "$t/in/want.json"
    (cd "$t/in" && RENDER_OUT="$t/out" "$TOOLS/whisper-venv/bin/python" -u "$JOB/pc_job.py") || { rm -rf "$t"; return 1; }
    rm -rf "$t"
}

llm_gptoss_step() { llm_step "https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/$GPTOSS" "$GPTOSS"; }
llm_qwen_step() { llm_step "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF/resolve/main/$QWEN" "$QWEN"; }

step llama llama_step
step gptoss llm_gptoss_step
step qwen llm_qwen_step
step bench bench_step
step whisper-venv whisper_venv_step
step whisper-model whisper_model_step
step whisper-test whisper_test_step
write_report
echo "report: $REPORT"
