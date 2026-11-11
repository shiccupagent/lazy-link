#!/usr/bin/env python3
"""describe.py LINES.json --out DIR - say lines in voices made from a written description, on the PC, with
Ming-3.0-TTS (voice/ming.json: version, licence, pinned code and weights). No recording needed; for a copy of a real
voice use clone.py + speak.py instead. Installs Ming on the PC on first use (a few minutes, about 4 GB).

LINES.json is a list of {"id", "text", "voice"}, where "voice" is a description ("a bright cheerful young woman,
upbeat, like a game show host"), or {"voices": {name: description}, "lines": [{"id", "text", "voice": name}]}.
Write numbers as words. Writes DIR/<id>.wav per line and DIR/report.json. Windows PC only for now, like speak.py.

    python3 voice/describe.py voice/examples/describe.json --out out/lines"""
import argparse, json, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402

PS1 = r"""$ErrorActionPreference = 'Continue'
$job = (Get-Location).Path
$c = Get-Content ming.json -Raw | ConvertFrom-Json
$tools = Join-Path $env:RENDER_ROOT 'tools'; $uv = "$tools\uv\uv.exe"
$env:UV_PYTHON_INSTALL_DIR = "$tools\python"; $env:UV_CACHE_DIR = "$env:RENDER_ROOT\tmp\uv-cache"
$env:HF_HOME = "$env:RENDER_ROOT\models\hf"; $env:HF_HUB_DISABLE_TELEMETRY = '1'
if (-not (Test-Path $uv)) { 'uv is not installed on the PC: run imagegen.py setup first'; exit 3 }
$src = "$tools\ming-tts-$($c.code_sha)"
if (-not (Test-Path "$src\requirements.txt")) {
  'installing the Ming code (pinned revision)'
  $zip = "$env:RENDER_ROOT\tmp\ming-tts.zip"; $tmp = "$tools\ming-tmp"
  & curl.exe -L -s -S --fail -o $zip "https://codeload.github.com/$($c.code_repo)/zip/$($c.code_sha)"
  if ($LASTEXITCODE) { 'Ming code download failed'; exit 2 }
  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
  Expand-Archive -Force $zip $tmp; Move-Item (Get-ChildItem $tmp)[0].FullName $src; Remove-Item -Recurse $tmp, $zip
}
$v = Join-Path $env:RENDER_ROOT $c.venv; $py = "$v\Scripts\python.exe"
if (-not (Test-Path $py)) {
  'installing the Ming venv (torch + requirements)'
  & $uv venv --python $c.python $v 2>&1 | Out-Null
  & $uv pip install --python $py $c.torch.Split(' ') --index-url $c.torch_index 2>&1 | Select-Object -Last 2
  & $uv pip install --python $py -r "$src\requirements.txt" 2>&1 | Select-Object -Last 3
  if ($LASTEXITCODE) { 'Ming install failed'; Remove-Item -Recurse -Force $v; exit 2 }
}
Push-Location $src
& $py -u "$job\describe_pc.py" $job 2>&1 | ForEach-Object { "$_" }
$rc = $LASTEXITCODE
Pop-Location
exit $rc
"""


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    voices, lines = (data.get("voices", {}), data["lines"]) if isinstance(data, dict) else ({}, data)
    out = []
    for ln in lines:
        if not ln.get("id") or not ln.get("text") or not ln.get("voice"):
            sys.exit(f"describe: every line needs id, text and voice: {ln}")
        out.append({"id": ln["id"], "text": ln["text"], "voice": voices.get(ln["voice"], ln["voice"])})
    ids = [ln["id"] for ln in out]
    if len(set(ids)) != len(ids):
        sys.exit("describe: line ids must be unique")
    return out


def main():
    ap = argparse.ArgumentParser(description="say lines in described voices with Ming-3.0-TTS on the PC")
    ap.add_argument("lines")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=2 * 3600)
    a = ap.parse_args()
    lines = load(a.lines)
    if imagegen.load_pcjob().POSIX:
        sys.exit("describe: Windows PC only for now")
    print(f"{len(lines)} lines, {len({ln['voice'] for ln in lines})} voices")
    out = Path(a.out).expanduser().resolve()
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "lines.json").write_text(json.dumps(lines, indent=1), encoding="utf-8")
        imagegen.send([HERE / "pc" / "describe_pc.py", HERE / "ming.json", Path(td) / "lines.json"], PS1, [],
                      "voice-describe", out, a.timeout)
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    print(f"{len(rep['lines'])} lines in {out}; Ming loaded in {rep['load_s']} s, "
          f"{sum(r['seconds'] for r in rep['lines']) / max(1, len(rep['lines'])):.1f} s per line, {rep['peak_vram_gb']} GB")


if __name__ == "__main__":
    main()
