# job.sh for `imagegen.py setup` on an Apple Silicon Mac: our own ComfyUI at the release pinned in models.json,
# in the Mac's work folder (tools/ComfyUI + tools/comfy-venv). Runs as the job's own account: no sudo.
# Python comes from uv (no system packages, no admin). Models are read from models/comfy plus any extra
# folders in the owner's config.json (extra_model_folders), through ComfyUI's extra_model_paths.yaml.
# Torch comes from PyPI (the macOS arm64 wheels include the Metal backend, MPS): comfy.torch_index is a CUDA index.
# Safe to rerun: an existing install stays, packages are only added.
set -eu -o pipefail

die() { echo "setup: $*" >&2; exit 1; }
[ -n "${RENDER_ROOT:-}" ] || die "RENDER_ROOT is not set (run this as a job)"
[ -f models.json ] || die "models.json missing: run this from the job folder"

mj() { python3 -c "import json,sys; m=json.load(open('models.json')); print($1)"; }
sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

[ "$(uname -s)" = Darwin ] || die "this script is for macOS"
ARCH=$(uname -m)
case $ARCH in arm64) ;; *) die "no uv build pinned for $ARCH (Apple Silicon arm64 only)" ;; esac

ROOT=$RENDER_ROOT
TOOLS=$ROOT/tools
DL=$TOOLS/dl
mkdir -p "$DL" "$ROOT/tmp/uv-cache" "$ROOT/models"
export UV_PYTHON_INSTALL_DIR=$TOOLS/python UV_CACHE_DIR=$ROOT/tmp/uv-cache

echo "== macOS $ARCH (Apple Silicon); work folder $ROOT"
echo "GPU: Apple Metal through PyTorch MPS (no CUDA on a Mac)"

# 1. uv (makes Python without admin rights)
UV=$TOOLS/uv/uv
if [ ! -x "$UV" ]; then
    url=$(mj "m['uv']['mac']['$ARCH']['url']"); want=$(mj "m['uv']['mac']['$ARCH']['sha256']")
    echo "get uv from $url"
    curl -L -s -S --fail --retry 5 -o "$DL/uv.tar.gz" "$url"
    got=$(sha256_of "$DL/uv.tar.gz")
    [ "$got" = "$want" ] || die "uv sha256 mismatch ($got)"
    mkdir -p "$TOOLS/uv"
    tar -xzf "$DL/uv.tar.gz" -C "$TOOLS/uv" --strip-components=1
    [ -x "$UV" ] || die "uv not found in the archive"
fi

# 2. ComfyUI source at the pinned tag (a zip from GitHub, unpacked with Python: no unzip needed)
COMFY_DIR=$(mj "m['comfy']['dir']"); COMFY_VER=$(mj "m['comfy']['version']")
COMFY=$ROOT/$COMFY_DIR
STAMP=$COMFY/.lazy-link-version
if [ ! -f "$STAMP" ] || [ "$(tr -d '[:space:]' < "$STAMP")" != "$COMFY_VER" ]; then
    echo "get ComfyUI $COMFY_VER"
    curl -L -s -S --fail --retry 5 -o "$DL/ComfyUI-$COMFY_VER.zip" \
        "https://github.com/Comfy-Org/ComfyUI/archive/refs/tags/$COMFY_VER.zip"
    rm -rf "$DL/comfy-unpack" "$COMFY"
    mkdir -p "$DL/comfy-unpack"
    python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
        "$DL/ComfyUI-$COMFY_VER.zip" "$DL/comfy-unpack"   # source only: models live outside it
    top=$(find "$DL/comfy-unpack" -mindepth 1 -maxdepth 1 -type d | head -1)
    [ -n "$top" ] || die "ComfyUI archive is empty"
    mkdir -p "$(dirname "$COMFY")"
    mv "$top" "$COMFY"
    echo "$COMFY_VER" > "$STAMP"
fi

# 3. Python + packages
VENV=$ROOT/$(mj "m['comfy']['venv']")
PY=$VENV/bin/python
if [ ! -x "$PY" ]; then
    echo "make Python $(mj "m['comfy']['python']") venv"
    "$UV" venv --python "$(mj "m['comfy']['python']")" "$VENV"
fi
echo "torch from PyPI (several GB the first time)"
"$UV" pip install --python "$PY" torch torchvision torchaudio
echo "ComfyUI requirements"
"$UV" pip install --python "$PY" -r "$COMFY/requirements.txt"

# 4. Where ComfyUI looks for models (same file format as the Windows and Linux setups)
LIB=$ROOT/$(mj "m['library']")
python3 - "$LIB" "$COMFY" <<'PY'
import json, os, sys
lib, comfy = sys.argv[1], sys.argv[2]
m = json.load(open("models.json"))
extra = (json.load(open("link.json")).get("extra_model_folders") or {}) if os.path.exists("link.json") else {}
yaml = "lazy_link:\n"
for kind in m["model_kinds"]:
    path = os.path.join(lib, kind)
    os.makedirs(path, exist_ok=True)
    yaml += f"  {kind}: |-\n    {path}\n"
    for d in extra.get(kind) or []:
        yaml += f"    {d}\n"
with open(os.path.join(comfy, "extra_model_paths.yaml"), "w") as f:
    f.write(yaml)
print("model folders:", lib)
PY

# ComfyUI picks MPS by itself when it is there (batch.py adds --cpu only when neither CUDA nor MPS is)
"$PY" -c "import torch, sys; print('python', sys.version.split()[0], 'torch', torch.__version__, 'mps', torch.backends.mps.is_available())"
echo "ComfyUI $COMFY_VER ready in $COMFY"
