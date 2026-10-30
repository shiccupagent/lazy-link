# job.ps1 for `imagegen.py setup`: our own ComfyUI at the release pinned in models.json, in the PC's
# work folder (tools\ComfyUI + tools\comfy-venv). Runs as the standard 'lazylink' account: no admin.
# Python comes from uv (no installer, no admin). Models are read from models\comfy plus any extra
# folders in the Mac's config.json (extra_model_folders), through ComfyUI's extra_model_paths.yaml.
# Safe to rerun: an existing install moves to the pinned release, packages are only added.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$m = Get-Content models.json -Raw | ConvertFrom-Json
$c = $m.comfy
$root = $env:RENDER_ROOT
$tools = Join-Path $root 'tools'
$dl = Join-Path $tools 'dl'
New-Item -ItemType Directory -Force $dl | Out-Null

# Native tools write progress to stderr; judge them by exit code only.
function Run { $ErrorActionPreference = 'Continue'; $exe, $rest = $args; & $exe @rest 2>&1 | ForEach-Object { "  $_" }; if ($LASTEXITCODE) { throw "$exe failed ($LASTEXITCODE)" } }

# 1. uv (makes Python without admin rights)
$uv = Join-Path $tools 'uv\uv.exe'
if (-not (Test-Path $uv)) {
    "get uv from $($m.uv.url)"
    $zip = Join-Path $dl 'uv.zip'
    & curl.exe -L -s -S --fail -o $zip $m.uv.url
    if ($LASTEXITCODE) { throw 'uv download failed' }
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $m.uv.sha256) { throw 'uv sha256 mismatch' }
    Expand-Archive -Force $zip (Join-Path $tools 'uv')
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'
$env:UV_CACHE_DIR = Join-Path $root 'tmp\uv-cache'

# 2. ComfyUI source at the pinned tag (a zip from GitHub: no git needed)
$comfy = Join-Path $root $c.dir
$stamp = Join-Path $comfy '.lazy-link-version'
$oldStamp = Join-Path $comfy '.render-link-version'   # the name before 2026-10-29
if ((Test-Path $oldStamp) -and -not (Test-Path $stamp)) { Move-Item $oldStamp $stamp }
if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $c.version) {
    "get ComfyUI $($c.version)"
    $zip = Join-Path $dl "ComfyUI-$($c.version).zip"
    & curl.exe -L -s -S --fail -o $zip "https://github.com/Comfy-Org/ComfyUI/archive/refs/tags/$($c.version).zip"
    if ($LASTEXITCODE) { throw 'ComfyUI download failed' }
    $unpack = Join-Path $dl 'comfy-unpack'
    if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
    New-Item -ItemType Directory $unpack | Out-Null
    Run "$env:WINDIR\System32\tar.exe" -xf $zip -C $unpack
    if (Test-Path $comfy) { Remove-Item $comfy -Recurse -Force }   # source only: models live outside it
    Move-Item (Get-ChildItem $unpack -Directory | Select-Object -First 1).FullName $comfy
    Set-Content $stamp $c.version
}

# 3. Python + packages
$venv = Join-Path $root $c.venv
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) { "make Python $($c.python) venv"; Run $uv venv --python $c.python $venv }
"torch from $($c.torch_index) (about 3 GB the first time)"
Run $uv pip install --python $py torch torchvision torchaudio --index-url $c.torch_index
"ComfyUI requirements"
Run $uv pip install --python $py -r (Join-Path $comfy 'requirements.txt')

# 4. Where ComfyUI looks for models
$lib = Join-Path $root $m.library
$extra = if (Test-Path link.json) { (Get-Content link.json -Raw | ConvertFrom-Json).extra_model_folders } else { $null }
$yaml = "lazy_link:`n"
foreach ($kind in $m.model_kinds) {
    New-Item -ItemType Directory -Force (Join-Path $lib $kind) | Out-Null
    $yaml += "  ${kind}: |-`n    $(Join-Path $lib $kind)`n"
    if ($extra -and $extra.$kind) { foreach ($d in $extra.$kind) { $yaml += "    $d`n" } }
}
Set-Content (Join-Path $comfy 'extra_model_paths.yaml') $yaml

& $py -c "import torch, sys; print('python', sys.version.split()[0], 'torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-')"
"ComfyUI $($c.version) ready in $comfy"
