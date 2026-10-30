"""Runs on the PC (ComfyUI's own venv python) as part of an imagegen job.

Starts our own ComfyUI headless on 127.0.0.1 only (its own port, no custom nodes, its own
user/temp folders), queues every ask in asks.json
for every model in want.json, saves <ask>__<model>.png and timings.json to $RENDER_OUT, then
stops ComfyUI. The graphs follow ComfyUI's own templates; settings are data in models.json.

want.json can also be {"recipe": KEY}: each ask then fills a converted template (recipes/<template>.api.json,
inputs named in models.json "recipes") and every file it saves (audio, 3D model, image) comes back as
<ask>__<KEY>.<ext>. {"convert": [template, ...]} turns ComfyUI templates into those .api.json files.
"""
import json, mimetypes, os, shutil, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

M = json.load(open("models.json"))
ASKS = json.load(open("asks.json"))
WANT = json.load(open("want.json"))
OUT = Path(os.environ["RENDER_OUT"])
BASE = Path(os.environ["RENDER_ROOT"])
ROOT = BASE / M["comfy"]["dir"]
WINDOWS = os.name == "nt"
PYTHON = BASE / M["comfy"]["venv"] / ("Scripts/python.exe" if WINDOWS else "bin/python")
PORT = M["comfy"]["port"]
URL = f"http://127.0.0.1:{PORT}"
SCRATCH = BASE / "tmp" / "comfy"


def api(path, body=None, timeout=30):
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def vram_used_mb():
    try:
        d = api("/system_stats")["devices"][0]
        return round((d["vram_total"] - d["vram_free"]) / 2**20)
    except Exception:
        return None


def resolve(kind, name):
    """ComfyUI lists files by path inside its model folders (e.g. FLUX1\\ae.safetensors)."""
    node = {"unet": ("UNETLoader", "unet_name"), "clip": ("CLIPLoader", "clip_name"), "vae": ("VAELoader", "vae_name"),
            "cutout": ("LoadBackgroundRemovalModel", "bg_removal_name")}[kind]
    spec = api(f"/object_info/{node[0]}")[node[0]]["input"]["required"][node[1]]
    choices = spec[1]["options"] if spec[0] == "COMBO" else spec[0]  # newer nodes: ["COMBO", {"options": [...]}]
    hits = [c for c in choices if c.replace("\\", "/").split("/")[-1] == name]
    if not hits:
        sys.exit(f"batch: {name} not found by ComfyUI ({node[0]}); run: imagegen.py fetch --models ...")
    return hits[0]


def graph(m, files, ask, prefix):
    w, h, seed = ask.get("w", 1024), ask.get("h", 1024), ask.get("seed", 1)
    g = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": files["unet"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": m["clip_type"], "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": ask["prompt"], "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": ask.get("negative", ""), "clip": ["clip", 0]}}
        if m.get("negative") == "empty" else  # real guidance (cfg > 1) wants an encoded negative
        {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}},
        "latent": {"class_type": m["latent"], "inputs": {"width": w, "height": h, "batch_size": 1}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": prefix}},
    }
    if ask.get("cutout"):  # + a transparent copy: BiRefNet foreground mask -> alpha
        g["bgm"] = {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": files["cutout"]}}
        g["fg"] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["bgm", 0], "image": ["decode", 0]}}
        g["bgmask"] = {"class_type": "InvertMask", "inputs": {"mask": ["fg", 0]}}  # JoinImageWithAlpha wants 1 = clear
        g["rgba"] = {"class_type": "JoinImageWithAlpha", "inputs": {"image": ["decode", 0], "alpha": ["bgmask", 0]}}
        g["save_cut"] = {"class_type": "SaveImage", "inputs": {"images": ["rgba", 0], "filename_prefix": prefix + "-cutout"}}
    model = ["unet", 0]
    if m.get("shift"):
        g["shift"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": model, "shift": m["shift"]}}
        model = ["shift", 0]
    if m["graph"] == "ksampler":
        g["sample"] = {"class_type": "KSampler", "inputs": {
            "model": model, "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0],
            "seed": seed, "steps": m["steps"], "cfg": m["cfg"], "sampler_name": m["sampler"],
            "scheduler": m["scheduler"], "denoise": 1}}
    else:  # flux2: custom sampler with the Flux2 schedule, as in image_flux2_klein_text_to_image
        g["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        g["sampler"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": m["sampler"]}}
        g["sigmas"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": m["steps"], "width": w, "height": h}}
        g["guider"] = {"class_type": "CFGGuider", "inputs": {"model": model, "positive": ["pos", 0], "negative": ["neg", 0], "cfg": m["cfg"]}}
        g["sample"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
            "sigmas": ["sigmas", 0], "latent_image": ["latent", 0]}}
    return g


def run_one(g):
    t0 = time.time()
    try:
        pid = api("/prompt", {"prompt": g, "client_id": "imagegen"})["prompt_id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"batch: ComfyUI refused the graph: {e.read().decode()[:2000]}")
    while True:
        h = api(f"/history/{pid}")
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                sys.exit(f"batch: ComfyUI error: {json.dumps(msgs)[:2000]}")
            files = [f for o in h[pid]["outputs"].values() for v in o.values() if isinstance(v, list)
                     for f in v if isinstance(f, dict) and "filename" in f and f.get("type") == "output"]
            return time.time() - t0, files
        if time.time() - t0 > 1800:
            sys.exit("batch: one ask took over 30 min; giving up")
        time.sleep(0.25)


def upload(path):
    """Put a local input image into ComfyUI's input folder; returns the name LoadImage wants."""
    b = uuid.uuid4().hex
    data = Path(path).read_bytes()
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{Path(path).name}\"\r\n"
            f"Content-Type: {mimetypes.guess_type(path)[0] or 'image/png'}\r\n\r\n").encode() + data + \
           f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(URL + "/upload/image", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["name"]


def run_recipe(key, timings):
    r = M["recipes"][key]
    base = json.load(open(Path("recipes") / f"{r['template']}.api.json"))
    for i, ask in enumerate(ASKS):
        g = json.loads(json.dumps(base))
        for name, value in {**r.get("fixed", {}), **ask.get("params", {})}.items():
            node, inp = r["params"][name] if name in r["params"] else name.split(".", 1)
            g[node]["inputs"][inp] = value
        if "image" in ask:
            node, inp = r["image"]
            g[node]["inputs"][inp] = upload(Path("inputs") / ask["image"])
        secs, files = run_one(g)
        for n, f in enumerate(sorted(files, key=lambda f: f["filename"])):
            ext = Path(f["filename"]).suffix
            dest = OUT / f"{ask['name']}__{key}{'' if n == 0 else '-' + str(n)}{ext}"
            shutil.move(str(SCRATCH / "out" / f.get("subfolder", "") / f["filename"]), dest)
        row = {"model": key, "ask": ask["name"], "seconds": round(secs, 2), "cold": i == 0,
               "files": len(files), "vram_mb": vram_used_mb()}
        timings["runs"].append(row)
        print(json.dumps(row), flush=True)


def site_packages():
    venv = PYTHON.parent.parent
    if WINDOWS:
        return venv / "Lib" / "site-packages"
    return next(venv.glob("lib/python*/site-packages"))


def convert(names):
    site = site_packages()
    dirs = [site / "comfyui_workflow_templates_json" / "templates", ROOT / "blueprints"]
    paths = []
    for n in names:
        hit = next((d / f"{n}.json" for d in dirs if (d / f"{n}.json").exists()), None)
        if hit is None:
            sys.exit(f"batch: no template {n}")
        paths.append(str(hit))
    if WINDOWS:
        node = os.environ.get("NODE", r"C:\Program Files\nodejs\node.exe")
    else:
        node = os.environ.get("NODE") or shutil.which("node") or "node"
    env = dict(os.environ)
    if not WINDOWS and not env.get("CHROME"):   # to_api.mjs drives Chrome; find a Linux one
        chrome = next((shutil.which(c) for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(c)), None)
        if chrome is None:
            sys.exit("batch: convert needs Google Chrome or Chromium on this PC (install one, or set CHROME)")
        env["CHROME"] = chrome
    r = subprocess.run([node, "to_api.mjs", URL, str(OUT), *paths], capture_output=True, text=True, timeout=600, env=env)
    print(r.stdout, r.stderr)
    if r.returncode:
        sys.exit("batch: template conversion failed")


def main():
    for d in ("user", "temp", "out"):
        (SCRATCH / d).mkdir(parents=True, exist_ok=True)
    log = open(OUT / "comfy.log", "w")
    t_start = time.time()
    # a PC without an NVIDIA GPU (or a CPU-only torch) needs --cpu, or ComfyUI stops at import; a Mac's Apple GPU is MPS
    cuda = subprocess.run([str(PYTHON), "-c", "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"]).returncode == 0
    mps = not cuda and subprocess.run([str(PYTHON), "-c", "import torch, sys; sys.exit(0 if torch.backends.mps.is_available() else 1)"]).returncode == 0
    comfy = subprocess.Popen(
        [str(PYTHON), "-u", "main.py", "--listen", "127.0.0.1", "--port", str(PORT),
         "--disable-all-custom-nodes", "--disable-auto-launch", "--output-directory", str(SCRATCH / "out"),
         "--temp-directory", str(SCRATCH / "temp"), "--user-directory", str(SCRATCH / "user")] + ([] if cuda or mps else ["--cpu"]),
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    print("ComfyUI on " + ("the GPU" if cuda else "the Apple GPU (MPS)" if mps else "the CPU (no CUDA torch: slow)"), flush=True)
    try:
        for _ in range(240):
            if comfy.poll() is not None:
                sys.exit(f"batch: ComfyUI exited early ({comfy.returncode}); see comfy.log")
            try:
                api("/system_stats", timeout=3)
                break
            except Exception:
                time.sleep(1)
        else:
            sys.exit("batch: ComfyUI did not come up in 4 min")
        boot = time.time() - t_start
        print(f"ComfyUI up in {boot:.1f} s, VRAM in use {vram_used_mb()} MB", flush=True)
        timings = {"comfy_boot_s": round(boot, 1), "runs": []}
        if isinstance(WANT, dict) and "convert" in WANT:
            return convert(WANT["convert"])
        if isinstance(WANT, dict) and "recipe" in WANT:
            run_recipe(WANT["recipe"], timings)
            json.dump(timings, open(OUT / "timings.json", "w"), indent=1)
            return
        for key in WANT:
            m = M["models"][key]
            files = {k: resolve(k, m[k]) for k in ("unet", "clip", "vae")}
            if any(a.get("cutout") for a in ASKS):
                files["cutout"] = resolve("cutout", M["cutout"]["model"])
            for i, ask in enumerate(ASKS):
                prefix = f"{ask['name']}__{key}"
                secs, imgs = run_one(graph(m, files, ask, prefix))
                for img in imgs:  # SaveImage names files <prefix>_00001_.png
                    tag = "-cutout" if img["filename"].startswith(prefix + "-cutout") else ""
                    shutil.move(str(SCRATCH / "out" / img.get("subfolder", "") / img["filename"]), OUT / f"{prefix}{tag}.png")
                row = {"model": key, "ask": ask["name"], "seconds": round(secs, 2), "cold": i == 0,
                       "w": ask.get("w", 1024), "h": ask.get("h", 1024), "vram_mb": vram_used_mb()}
                timings["runs"].append(row)
                print(json.dumps(row), flush=True)
            api("/free", {"unload_models": True, "free_memory": True})
            time.sleep(2)
        json.dump(timings, open(OUT / "timings.json", "w"), indent=1)
    finally:
        comfy.terminate()
        try:
            comfy.wait(20)
        except subprocess.TimeoutExpired:
            comfy.kill()


if __name__ == "__main__":
    main()
