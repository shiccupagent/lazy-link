#!/usr/bin/env python3
"""local.py - the PC's local AI from the Mac, in batches (saves Claude tokens on bulk text; Claude still checks).

  local.py setup                                   install llama.cpp, both chat models and Whisper (~35 GB, once)
  local.py ask ASKS.jsonl --out DIR [--model qwen|gptoss] [--max-tokens 800]
      ASKS.jsonl: one {"id": "...", "prompt": "...", "system": "..."} per line -> DIR/answers.jsonl
  local.py ask-one "prompt" [--model ...]          prints the answer
  local.py transcribe FILE... --out DIR            speech -> DIR/<name>.txt + .srt + .words.json (Whisper large-v3)

Models (Apache-2.0 / MIT): qwen = Qwen3-30B-A3B (default), gptoss = gpt-oss-20b.
Both write about 200 tokens/s on an RTX 3090; each job adds 10-20 s to load the model, so batch.
"""
import argparse, json, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "imagegen"))
import imagegen  # noqa: E402  (send(): wake the PC, run the job, fetch, sleep it again if we woke it)

PS1 = r"""$ErrorActionPreference = 'Continue'
if (-not $env:RENDER_ROOT) { 'RENDER_ROOT is not set: the PC runs an old runner (pcjob push-runner)'; exit 3 }
$py = Join-Path $env:RENDER_ROOT 'tools\whisper-venv\Scripts\python.exe'   # plain Python is enough for the chat side too
& $py -u pc_job.py
exit $LASTEXITCODE
"""


def run(files, want, out, name):
    out = Path(out).expanduser().resolve()
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "want.json"
        w.write_text(json.dumps(want))
        imagegen.send([HERE / "pc_job.py", w] + files, PS1, want, name, out, 7200, sh=(HERE / "linux" / "run.sh").read_text())
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp1252
    p = argparse.ArgumentParser(prog="local", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask"); a.add_argument("asks"); a.add_argument("--out", required=True)
    o = sub.add_parser("ask-one"); o.add_argument("prompt")
    for x in (a, o):
        x.add_argument("--model", default="qwen", choices=["qwen", "gptoss"]); x.add_argument("--max-tokens", type=int, default=800)
    t = sub.add_parser("transcribe"); t.add_argument("files", nargs="+"); t.add_argument("--out", required=True)
    sub.add_parser("setup")
    args = p.parse_args()
    if args.cmd == "setup":
        out = HERE.parent / "reports" / ("local-ai-" + __import__("time").strftime("%Y%m%d-%H%M%S"))
        imagegen.send([HERE.parent / "imagegen" / "models.json", HERE / "pc_job.py"], (HERE / "setup-local-ai.ps1").read_text(), {},
                      "local-setup", out, 4 * 3600, sh=(HERE / "linux" / "setup.sh").read_text())
        print((out / "local-ai.json").read_text(encoding="utf-8-sig", errors="replace"))
        return
    if args.cmd == "transcribe":
        out = run([(f, f"inputs/{Path(f).name}") for f in args.files], {"transcribe": True}, args.out, "local-transcribe")
        print(f"transcripts in {out}")
        return
    want = {"ask": args.model, "max_tokens": args.max_tokens}
    if args.cmd == "ask":
        src = Path(args.asks)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "asks.jsonl"
            f.write_bytes(src.read_bytes())
            out = run([f], want, args.out, "local-ask")
        print(f"answers in {out / 'answers.jsonl'}")
    else:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "asks.jsonl"
            f.write_text(json.dumps({"id": "one", "prompt": args.prompt}) + "\n")
            out = run([f], want, Path(td) / "out", "local-ask")
            ans = out / "answers.jsonl"
            if not ans.exists():
                sys.exit("no answer came back; the job log above says why (pcjob log ID shows it again)")
            print(json.loads(ans.read_text(encoding="utf-8").splitlines()[0])["answer"])


if __name__ == "__main__":
    main()
