# job.ps1 for `local.py setup`: llama.cpp (CUDA) + two open chat models, and faster-whisper speech-to-text.
# Runs as a pcjob under render (no admin), everything under the PC's work folder. Rerun-safe: a finished step is skipped.
# Downloads: llama.cpp 0.7 GB, gpt-oss-20b 12.1 GB, Qwen3-30B-A3B 18.6 GB, Whisper large-v3 3.1 GB (all MIT / Apache-2.0).
# Each step is in its own try/catch and lands in $env:RENDER_OUT\local-ai.json, so a failure in one never hides the others.
$ErrorActionPreference = 'Continue'
# (PowerShell names ignore case: never reuse a letter as $m/$M)
$Tools = Join-Path $env:RENDER_ROOT 'tools'; $Models = Join-Path $env:RENDER_ROOT 'models'
$report = [ordered]@{}
function Step($name, [scriptblock]$body) {
  $t0 = Get-Date
  try { $r = & $body; $report[$name] = @{ ok = $true; seconds = [int]((Get-Date) - $t0).TotalSeconds; result = "$r" } }
  catch { $report[$name] = @{ ok = $false; seconds = [int]((Get-Date) - $t0).TotalSeconds; error = "$_" } }
  "$name -> $($report[$name].ok)"
  $report | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $env:RENDER_OUT 'local-ai.json')
}
function Get-File($url, $dst, [long]$size) {
  New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
  if ((Test-Path $dst) -and (Get-Item $dst).Length -eq $size) { return "have $dst" }
  for ($i = 1; $i -le 5; $i++) {
    & curl.exe -L -s -S --retry 5 -C - -o "$dst.part" $url
    if ((Test-Path "$dst.part") -and (Get-Item "$dst.part").Length -eq $size) { Move-Item -Force "$dst.part" $dst; return "got $dst" }
  }
  throw "download failed or wrong size: $url"
}

Step 'llama.cpp' {
  $rel = 'https://github.com/ggml-org/llama.cpp/releases/download/b11986'
  foreach ($z in @(@('llama-b11986-bin-win-cuda-12.4-x64.zip', 0), @('cudart-llama-bin-win-cuda-12.4-x64.zip', 0))) {
    $zip = Join-Path $Tools "dl\$($z[0])"
    New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
    if (-not (Test-Path $zip)) { & curl.exe -L -s -S --retry 5 -o $zip "$rel/$($z[0])"; if ($LASTEXITCODE) { throw "curl $($z[0])" } }
    Expand-Archive -Force $zip (Join-Path $Tools 'llama')
  }
  (Get-ChildItem (Join-Path $Tools 'llama') -Recurse -Filter llama-bench.exe | Select-Object -First 1).FullName
}
$bench = (Get-ChildItem (Join-Path $Tools 'llama') -Recurse -Filter llama-bench.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName

$llms = @(
  @('gpt-oss-20b', 'https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-MXFP4.gguf', 'gpt-oss-20b-MXFP4.gguf'),
  @('qwen3-30b-a3b', 'https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf', 'Qwen3-30B-A3B-Q4_K_M.gguf')
)
$sizes = @{ 'gpt-oss-20b-MXFP4.gguf' = 0; 'Qwen3-30B-A3B-Q4_K_M.gguf' = 0 }
foreach ($llm in $llms) {
  Step "download $($llm[0])" {
    $dst = Join-Path $Models "llm\$($llm[2])"
    # size from the server (HEAD after redirects), so the check never relies on a typed number
    $len = [long]((& curl.exe -sIL $llm[1] | Select-String -Pattern '^content-length:\s*(\d+)' | Select-Object -Last 1).Matches[0].Groups[1].Value)
    Get-File $llm[1] $dst $len
  }
  Step "bench $($llm[0])" {
    if (-not $bench) { throw 'no llama-bench' }
    # prompt reading (pp512) and writing speed (tg128), all layers on the 3090
    (& $bench -m (Join-Path $Models "llm\$($llm[2])") -ngl 99 -p 512 -n 128 2>&1 | Select-String '\|' | ForEach-Object { "$_".Trim() }) -join "`n"
  }
}

Step 'whisper venv' {
  # Python from uv (installed by `imagegen.py setup`, or fetched here), so no admin and no system Python needed
  $uv = Join-Path $Tools 'uv\uv.exe'
  if (-not (Test-Path $uv)) {
    $u = (Get-Content models.json -Raw | ConvertFrom-Json).uv
    $zip = Join-Path $Tools 'dl\uv.zip'; New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
    & curl.exe -L -s -S --fail -o $zip $u.url
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $u.sha256) { throw 'uv sha256 mismatch' }
    Expand-Archive -Force $zip (Join-Path $Tools 'uv')
  }
  $env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools 'python'; $env:UV_CACHE_DIR = Join-Path $env:RENDER_ROOT 'tmp\uv-cache'
  $v = Join-Path $Tools 'whisper-venv'
  if (-not (Test-Path "$v\Scripts\python.exe")) { & $uv venv --python 3.12 $v 2>&1 | Out-Null }
  & $uv pip install --python "$v\Scripts\python.exe" faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12 2>&1 | Select-Object -Last 3
  if ($LASTEXITCODE) { throw 'pip failed' }
  & "$v\Scripts\python.exe" -c "import faster_whisper; print('faster-whisper', faster_whisper.__version__)"
}
Step 'whisper model' {
  $base = 'https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main'
  $dir = Join-Path $Models 'whisper-large-v3'
  foreach ($f in 'config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json', 'model.bin') {
    $len = [long]((& curl.exe -sIL "$base/$f" | Select-String -Pattern '^content-length:\s*(\d+)' | Select-Object -Last 1).Matches[0].Groups[1].Value)
    Get-File "$base/$f" (Join-Path $dir $f) $len | Out-Null
  }
  "model in $dir"
}
Step 'whisper test' {
  $v = Join-Path $Tools 'whisper-venv'
  $code = @'
import os, glob, sys, time, wave, struct
for p in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
    os.add_dll_directory(p); os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]
from faster_whisper import WhisperModel
w = os.path.join(os.environ["RENDER_OUT"], "silence.wav")
with wave.open(w, "wb") as f:
    f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(struct.pack("<h", 0) * 16000 * 3)
t = time.time(); m = WhisperModel(os.path.join(os.environ["RENDER_ROOT"], "models", "whisper-large-v3"), device="cuda", compute_type="float16")
load = time.time() - t; t = time.time(); segs, info = m.transcribe(w); list(segs)
print(f"loaded on cuda in {load:.1f} s, 3 s clip in {time.time() - t:.2f} s")
'@
  $code | & "$v\Scripts\python.exe" - 2>&1 | Select-Object -Last 3
  if ($LASTEXITCODE) { throw 'whisper test failed' }
}
$report | ConvertTo-Json -Depth 4
