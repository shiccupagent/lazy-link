"""clone_pc.py - runs on the PC in Chatterbox's venv (voice/clone.py sends it). For each refs/<voice>.wav (about 10 s
of your own recording; Chatterbox listens to the first 10 s) it speaks that voice's "try" line from asks.json and
writes $RENDER_OUT/<voice>.wav. Offline: the model loads from the work folder and HF_HUB_OFFLINE is set, so nothing
leaves the PC."""
import json, os, re, time
import numpy as np, soundfile as sf, torch
from chatterbox.tts import ChatterboxTTS

out = os.environ["RENDER_OUT"]
asks = json.load(open("asks.json"))
cfg = json.load(open("chatterbox.json"))
dev = "cuda" if torch.cuda.is_available() else "cpu"
model = ChatterboxTTS.from_local(os.path.join(os.environ["RENDER_ROOT"], cfg["dir"]), dev)
gap = np.zeros(int(model.sr * 0.3), dtype=np.float32)
for v in asks:
    torch.manual_seed(7)
    t0, parts = time.time(), []
    for s in [s for s in re.split(r"(?<=[.?!])\s+", v["text"]) if s]:
        wav = model.generate(s, audio_prompt_path=os.path.join("refs", v["id"] + ".wav"),
                             exaggeration=v["exaggeration"], cfg_weight=v["cfg"])
        parts += [wav.squeeze(0).cpu().numpy().astype(np.float32), gap]
    audio = np.concatenate(parts)
    sf.write(os.path.join(out, v["id"] + ".wav"), audio, model.sr)
    print(f"{v['id']}: {len(audio) / model.sr:.1f} s of speech in {time.time() - t0:.1f} s on {dev}", flush=True)
