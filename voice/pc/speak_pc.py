"""speak_pc.py ROUND - runs on the PC in Chatterbox's venv (voice/speak.py sends it). Says every sentence of
sentences.json still listed in todo.json (all of them in round 0) in its voice (refs/<voice>.wav), with a new seed each
round, into att/<key>_<round>.wav. check_pc.py then listens to them. Offline: the model loads from the PC's own
copy (voice/chatterbox.json "dir") and the job sets HF_HUB_OFFLINE."""
import json, os, sys
import soundfile as sf, torch
from chatterbox.tts import ChatterboxTTS

rnd = int(sys.argv[1])
jobs = json.load(open("sentences.json"))
todo = set(json.load(open("todo.json"))) if os.path.exists("todo.json") else {j["key"] for j in jobs}
cfg = json.load(open("chatterbox.json"))
model = ChatterboxTTS.from_local(os.path.join(os.environ["RENDER_ROOT"], cfg["dir"]), "cuda" if torch.cuda.is_available() else "cpu")
os.makedirs("att", exist_ok=True)
for j in jobs:
    if j["key"] in todo:
        torch.manual_seed(7 + 101 * rnd)
        wav = model.generate(j["text"], audio_prompt_path=os.path.join("refs", j["voice"] + ".wav"),
                             exaggeration=j["exaggeration"], cfg_weight=j["cfg"])
        sf.write(os.path.join("att", f"{j['key']}_{rnd}.wav"), wav.squeeze(0).cpu().numpy(), model.sr)
print(f"round {rnd}: said {len(todo)} sentences", flush=True)
