"""PC side of voice/describe.py: says each line of lines.json with Ming-3.0-TTS in the voice its description asks for.
Runs inside the Ming venv from the Ming code folder (describe.py's job script puts it there). Writes RENDER_OUT/<id>.wav
per line and RENDER_OUT/report.json (seconds to make, seconds of speech, graphics memory)."""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, os.getcwd())   # the job script runs from the Ming code folder (describe.py's job script cds there)
import soundfile as sf
import torch
from huggingface_hub import snapshot_download
from ming_tts import MingTTS

JOB = Path(sys.argv[1])
OUT = Path(os.environ["RENDER_OUT"])
cfg = json.loads((JOB / "ming.json").read_text(encoding="utf-8"))
lines = json.loads((JOB / "lines.json").read_text(encoding="utf-8"))
st = cfg["settings"]

t = time.time()
path = snapshot_download(cfg["model_repo"], revision=cfg["model_sha"])
torch.cuda.reset_peak_memory_stats()
model = MingTTS(path, text_normalization=st["text_normalization"])
load_s = time.time() - t
print(f"Ming loaded in {load_s:.1f} s", flush=True)

report = []
for ln in lines:
    f = OUT / f"{ln['id']}.wav"
    torch.cuda.synchronize(); t = time.time()
    model.synthesize(task=st["task"], text=ln["text"], instruction=ln["voice"], use_zero_spk_emb=st["use_zero_spk_emb"],
                     output_path=str(f))
    torch.cuda.synchronize(); dt = time.time() - t
    r = {"id": ln["id"], "text": ln["text"], "voice": ln["voice"], "seconds": round(dt, 2),
         "audio_s": round(sf.info(str(f)).duration, 2), "ming_version": cfg["version"]}
    report.append(r)
    print(json.dumps(r), flush=True)
(OUT / "report.json").write_text(json.dumps({"load_s": round(load_s, 1), "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                                             "lines": report}, indent=1), encoding="utf-8")
