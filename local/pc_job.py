# Runs on the PC (pcjob): local chat model or speech-to-text over a batch. want.json says which.
#   {"ask": "qwen"|"gptoss", "max_tokens": 800}  asks.jsonl lines {"id", "prompt", "system"?} -> answers.jsonl
#   {"transcribe": true}                         inputs/* audio/video -> <name>.txt + <name>.srt
import ctypes, glob, json, os, subprocess, sys, time, urllib.request

OUT = os.environ["RENDER_OUT"]
WANT = json.load(open("want.json"))
ROOT = os.environ["RENDER_ROOT"]
MODELS = {"qwen": os.path.join(ROOT, "models", "llm", "Qwen3-30B-A3B-Q4_K_M.gguf"),
          "gptoss": os.path.join(ROOT, "models", "llm", "gpt-oss-20b-MXFP4.gguf")}
PORT = 8191
WINDOWS = os.name == "nt"
LLAMA_EXE = "llama-server.exe" if WINDOWS else "llama-server"


def ask():
    exe = glob.glob(os.path.join(ROOT, "tools", "llama", "**", LLAMA_EXE), recursive=True)[0]
    log = open(os.path.join(OUT, "llama.log"), "w")
    # on Linux the llama.cpp archive's shared libraries sit next to llama-server
    env = os.environ if WINDOWS else dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(exe) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", ""))
    srv = subprocess.Popen([exe, "-m", MODELS[WANT["ask"]], "-ngl", "99", "-c", "16384", "--host", "127.0.0.1", "--port", str(PORT)],
                           stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(240):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).status == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            sys.exit("llama-server did not come up")
        with open(os.path.join(OUT, "answers.jsonl"), "w", encoding="utf-8") as out:
            for line in open("asks.jsonl", encoding="utf-8"):
                if not line.strip():
                    continue
                a = json.loads(line)
                msgs = ([{"role": "system", "content": a["system"]}] if a.get("system") else []) + [{"role": "user", "content": a["prompt"]}]
                body = json.dumps({"messages": msgs, "max_tokens": WANT.get("max_tokens", 800), "temperature": a.get("temperature", 0.3)}).encode()
                t = time.time()
                req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", body, {"Content-Type": "application/json"})
                r = json.load(urllib.request.urlopen(req, timeout=600))
                text = r["choices"][0]["message"]["content"]
                out.write(json.dumps({"id": a.get("id"), "answer": text, "seconds": round(time.time() - t, 1)}, ensure_ascii=False) + "\n")
                out.flush()
                print(f"{a.get('id')}: {time.time() - t:.1f} s")
    finally:
        srv.terminate()


def preload_cuda_libs():
    """Linux: load the pip-installed CUDA libraries (cuBLAS, cuDNN) by full path. The dynamic loader reads
    LD_LIBRARY_PATH only at start, so setting it here would be too late. A library may need another one
    first, so keep going while something new loads."""
    pending = sorted(glob.glob(os.path.join(sys.prefix, "lib", "python*", "site-packages", "nvidia", "*", "lib", "lib*.so*")))
    progress = True
    while pending and progress:
        progress, left = False, []
        for p in pending:
            try:
                ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
                progress = True
            except OSError:
                left.append(p)
        pending = left


def transcribe():
    if WINDOWS:
        for p in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
            os.add_dll_directory(p)
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]
        device, compute = "cuda", "float16"
    else:
        preload_cuda_libs()
        import ctranslate2
        device, compute = ("cuda", "float16") if ctranslate2.get_cuda_device_count() > 0 else ("cpu", "int8")
    print(f"whisper on {device} ({compute})", flush=True)
    from faster_whisper import WhisperModel
    m = WhisperModel(os.path.join(ROOT, "models", "whisper-large-v3"), device=device, compute_type=compute)
    stamp = lambda s: f"{int(s // 3600):02}:{int(s % 3600 // 60):02}:{int(s % 60):02},{int(s * 1000 % 1000):03}"
    for f in sorted(glob.glob("inputs/*")):
        t = time.time()
        segs, info = m.transcribe(f, vad_filter=True, word_timestamps=True)
        segs = list(segs)
        words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)} for s in segs for w in (s.words or [])]
        name = os.path.splitext(os.path.basename(f))[0]
        open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8").write(" ".join(s.text.strip() for s in segs) + "\n")
        open(os.path.join(OUT, name + ".srt"), "w", encoding="utf-8").write(
            "".join(f"{i}\n{stamp(s.start)} --> {stamp(s.end)}\n{s.text.strip()}\n\n" for i, s in enumerate(segs, 1)))
        json.dump(words, open(os.path.join(OUT, name + ".words.json"), "w", encoding="utf-8"), ensure_ascii=False)
        print(f"{name}: {info.duration:.0f} s of audio in {time.time() - t:.1f} s ({info.language})")


ask() if "ask" in WANT else transcribe()
