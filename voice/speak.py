#!/usr/bin/env python3
"""speak.py LINES.json --out DIR [--map speaker=voice,...] [--rounds 4] [--script FILE] - say many lines in your
copied voices (voice/refs/<voice>.wav, made by clone.py) on the PC, checked: Chatterbox says each sentence, Whisper listens, and any
sentence whose words don't match is said again with a new seed, up to --rounds times (the best attempt is kept).
Writes DIR/<id>.wav per line and DIR/report.json (per sentence: what Whisper heard, how close, which round).

LINES.json is a list, or {"sections": [...]}, of {"id", "text", and "voice" or "speaker"}; --map turns a speaker
into a voice id (e.g. --map host=you,guest=friend). A voice's Chatterbox settings are its "try" settings in the
script you recorded with (voice/scripts.json unless --script). Windows PC only for now, like clone.py."""
import argparse, json, re, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402

PS1 = r"""$ErrorActionPreference = 'Continue'
$env:HF_HUB_OFFLINE = '1'; $env:HF_HUB_DISABLE_TELEMETRY = '1'
$c = Get-Content chatterbox.json -Raw | ConvertFrom-Json
$cb = Join-Path $env:RENDER_ROOT ($c.venv + '\Scripts\python.exe'); $wh = Join-Path $env:RENDER_ROOT 'tools\whisper-venv\Scripts\python.exe'
if (-not (Test-Path $cb)) { 'Chatterbox is not installed: run voice/clone.py --setup'; exit 3 }
if (-not (Test-Path $wh)) { 'Whisper is not installed: run local/local.py setup'; exit 3 }
for ($r = 0; $r -lt ROUNDS; $r++) {
  & $cb -u speak_pc.py $r 2>&1 | ForEach-Object { "$_" }; if ($LASTEXITCODE) { exit $LASTEXITCODE }
  & $wh -u check_pc.py $r 2>&1 | ForEach-Object { "$_" }; if ($LASTEXITCODE) { exit $LASTEXITCODE }
  if ((Get-Content todo.json -Raw).Trim() -eq '[]') { break }
}
& $cb -u check_pc.py join 2>&1 | ForEach-Object { "$_" }
exit $LASTEXITCODE
"""


def sentences(text):
    """Chatterbox does best a sentence at a time; very long sentences are split at a comma near the middle."""
    out = []
    for s in [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]:
        while len(s) > 260:
            commas = [m.end() for m in re.finditer(r", ", s)]
            if not commas:
                break
            cut = min(commas, key=lambda c: abs(c - len(s) // 2))
            out.append(s[:cut].strip()); s = s[cut:].strip()
        out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lines")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", default="")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--script", default=str(HERE / "scripts.json"))
    a = ap.parse_args()
    data = json.loads(Path(a.lines).read_text())
    lines = data["sections"] if isinstance(data, dict) else data
    remap = dict(x.split("=") for x in a.map.split(",") if x)
    if imagegen.load_pcjob().POSIX:
        sys.exit("speak: Windows PC only for now")
    plain = {"exaggeration": 0.5, "cfg": 0.5}
    tries = {v["id"]: v.get("try", plain) for v in json.loads(Path(a.script).read_text(encoding="utf-8"))["voices"]}
    jobs = []
    for line in lines:
        voice = line.get("voice") or remap.get(line["speaker"], line["speaker"])
        if not (HERE / "refs" / f"{voice}.wav").is_file():
            sys.exit(f"speak: no reference for voice '{voice}' (run voice/clone.py first)")
        for i, s in enumerate(sentences(line["text"])):
            jobs.append({"key": f"{line['id']}__{i:02d}", "id": line["id"], "voice": voice, "text": s,
                         "exaggeration": tries.get(voice, plain)["exaggeration"], "cfg": tries.get(voice, plain)["cfg"]})
    voices = sorted({j["voice"] for j in jobs})
    print(f"{len(lines)} lines, {len(jobs)} sentences, voices {', '.join(voices)}")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "sentences.json").write_text(json.dumps(jobs, indent=1))
        files = [HERE / "pc" / "speak_pc.py", HERE / "pc" / "check_pc.py", HERE / "chatterbox.json", Path(td) / "sentences.json"] + \
                [(HERE / "refs" / f"{v}.wav", f"refs/{v}.wav") for v in voices]
        imagegen.send(files, PS1.replace("ROUNDS", str(a.rounds)), [], "voice-speak", Path(a.out).expanduser().resolve(), 4 * 3600)
    rep = json.loads((Path(a.out) / "report.json").read_text())
    low = [r for r in rep if not r.get("exact")]
    print(f"{len(rep) - len(low)} of {len(rep)} sentences heard word for word" + (":" if low else ""))
    for r in low:
        print(f"  {r['score']:.2f} {r['key']}: wanted {r['text']!r}, heard {r['said']!r}")


if __name__ == "__main__":
    main()
