# job.ps1 for `voice/clone.py --setup`: Chatterbox in its own venv + its model files (about 3.2 GB, plus 2.5 GB of torch).
# Runs as a pcjob under lazylink (no admin), everything under the PC's work folder. Rerun-safe: what is there is kept.
$ErrorActionPreference = 'Continue'
$c = Get-Content chatterbox.json -Raw | ConvertFrom-Json
$Tools = Join-Path $env:RENDER_ROOT 'tools'
$uv = Join-Path $Tools 'uv\uv.exe'
if (-not (Test-Path $uv)) {   # uv comes with `imagegen.py setup`; fetch it here otherwise
    $u = (Get-Content models.json -Raw | ConvertFrom-Json).uv
    $zip = Join-Path $Tools 'dl\uv.zip'; New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
    & curl.exe -L -s -S --fail -o $zip $u.url
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $u.sha256) { 'uv sha256 mismatch'; exit 2 }
    Expand-Archive -Force $zip (Join-Path $Tools 'uv')
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools 'python'; $env:UV_CACHE_DIR = Join-Path $env:RENDER_ROOT 'tmp\uv-cache'
$v = Join-Path $env:RENDER_ROOT $c.venv
$py = Join-Path $v 'Scripts\python.exe'
$have = $false; if (Test-Path $py) { & $py -c "import chatterbox" 2>$null; $have = ($LASTEXITCODE -eq 0) }
if (-not $have) {
    if (-not (Test-Path $py)) { & $uv venv --python $c.python $v 2>&1 | Select-Object -Last 1 }
    "torch (about 2.5 GB the first time)"
    & $uv pip install --python $py @($c.torch) --index-url $c.torch_index 2>&1 | Select-Object -Last 2
    if ($LASTEXITCODE) { 'torch install failed'; exit 2 }
    & $uv pip install --python $py @($c.pip) 2>&1 | Select-Object -Last 2
    if ($LASTEXITCODE) { 'pip failed'; exit 2 }
}
& $py -c "import torch, importlib.metadata as m; print('chatterbox', m.version('chatterbox-tts'), 'torch', torch.__version__, 'cuda', torch.cuda.is_available())"

$dir = Join-Path $env:RENDER_ROOT $c.dir
New-Item -ItemType Directory -Force $dir | Out-Null
# Already fetched once through Hugging Face's own cache? Its blobs are named by their sha256: copy instead of downloading.
$blobs = Join-Path $env:RENDER_ROOT ('models\hf\hub\models--' + ($c.repo -replace '/', '--') + '\blobs')
foreach ($p in $c.files.PSObject.Properties) {
    $f = $p.Name; $want = $p.Value; $dst = Join-Path $dir $f
    $ok = { param($x) (Test-Path $x) -and (Get-Item $x).Length -eq $want.size -and (Get-FileHash $x -Algorithm SHA256).Hash.ToLower() -eq $want.sha256 }
    if (& $ok $dst) { "have $f"; continue }
    $blob = Join-Path $blobs $want.sha256
    if (& $ok $blob) { Copy-Item $blob $dst -Force; "copied $f from the Hugging Face cache"; continue }
    $url = "https://huggingface.co/$($c.repo)/resolve/$($c.revision)/$f"
    for ($i = 1; $i -le 5 -and -not (& $ok "$dst.part"); $i++) { & curl.exe -L -s -S --retry 5 -C - -o "$dst.part" $url }
    if (-not (& $ok "$dst.part")) { "download failed or wrong sha256: $f"; exit 2 }
    Move-Item -Force "$dst.part" $dst; "got $f"
}
"model in $dir"
