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

M = json.load(open("models.json", encoding="utf-8"))
ASKS = json.load(open("asks.json", encoding="utf-8"))
WANT = json.load(open("want.json", encoding="utf-8"))
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
    if ask.get("init"):  # repaint a picture: start from it (VAE-encoded) and, with a mask, only where the mask is white
        g["init"] = {"class_type": "LoadImage", "inputs": {"image": upload(ask["init"])}}
        g["latent"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["init", 0], "vae": ["vae", 0]}}
        if ask.get("mask"):
            g["mask"] = {"class_type": "LoadImageMask", "inputs": {"image": upload(ask["mask"]), "channel": "red"}}
            g["noisemask"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["init_latent", 0], "mask": ["mask", 0]}}
            g["init_latent"], g["latent"] = g["latent"], g["noisemask"]
            # outside the mask the saved picture keeps the original pixels exactly (the VAE round trip would soften them)
            g["keep"] = {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["init", 0], "source": ["decode", 0],
                                                                        "x": 0, "y": 0, "resize_source": False, "mask": ["mask", 0]}}
            g["save"]["inputs"]["images"] = ["keep", 0]
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
            "scheduler": m["scheduler"], "denoise": ask.get("denoise", 1)}}
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
    base = json.load(open(Path("recipes") / f"{r['template']}.api.json", encoding="utf-8"))
    for i, ask in enumerate(ASKS):
        g = json.loads(json.dumps(base))
        for name, value in {**r.get("fixed", {}), **ask.get("params", {})}.items():
            node, inp = r["params"][name] if name in r["params"] else name.split(".", 1)
            g[node]["inputs"][inp] = value
        if "image" in ask and "start_image" in r:   # video: the picture becomes the first frame (the template leaves it unwired)
            node, inp = r["start_image"]
            g["start"] = {"class_type": "LoadImage", "inputs": {"image": upload(Path("inputs") / ask["image"])}}
            g[node]["inputs"][inp] = ["start", 0]
        elif "image" in ask:
            node, inp = r["image"]
            g[node]["inputs"][inp] = upload(Path("inputs") / ask["image"])
        if ask.get("cutout") is True and "frames" in r:   # video: BiRefNet on every frame -> RGBA frames, made into a .webm below
            g["bgm"] = {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": resolve("cutout", M["cutout"]["model"])}}
            g["fg"] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["bgm", 0], "image": r["frames"]}}
            g["bgmask"] = {"class_type": "InvertMask", "inputs": {"mask": ["fg", 0]}}
            g["rgba"] = {"class_type": "JoinImageWithAlpha", "inputs": {"image": r["frames"], "alpha": ["bgmask", 0]}}
            g["save_cut"] = {"class_type": "SaveImage", "inputs": {"images": ["rgba", 0], "filename_prefix": "cutframes/f"}}
        secs, files = run_one(g)
        if "frames" in r:   # a video recipe: loop and cutout are done here, after ComfyUI
            cut = sorted((f for f in files if f.get("subfolder", "").startswith("cutframes")), key=lambda f: f["filename"])
            files = [f for f in files if f not in cut]
            t1 = time.time()
            finish_video(ask, key, [SCRATCH / "out" / f.get("subfolder", "") / f["filename"] for f in files],
                         [SCRATCH / "out" / f.get("subfolder", "") / f["filename"] for f in cut])
            secs += time.time() - t1
            files = []
        for n, f in enumerate(sorted(files, key=lambda f: f["filename"])):
            ext = Path(f["filename"]).suffix
            dest = OUT / f"{ask['name']}__{key}{'' if n == 0 else '-' + str(n)}{ext}"
            shutil.move(str(SCRATCH / "out" / f.get("subfolder", "") / f["filename"]), dest)
        row = {"model": key, "ask": ask["name"], "seconds": round(secs, 2), "cold": i == 0,
               "files": len(files) or len(list(OUT.glob(f"{ask['name']}__{key}*"))), "vram_mb": vram_used_mb()}
        timings["runs"].append(row)
        print(json.dumps(row), flush=True)


def finish_video(ask, key, saved, cut):
    """The clip ComfyUI saved -> OUT/<name>__<key>.mp4; with "cutout", also <name>__<key>-cutout.webm (VP9 with
    alpha, plays in Chrome/Android/Firefox) and, with "frames": true, the RGBA frames as <name>__<key>-cutout/NNN.png.
    "cutout": true = BiRefNet (solid things; it drops motion-blurred ones), "black" = a brightness key for glows,
    sparks and things falling on black (dark = clear, the colour un-darkened so it looks the same over anything).
    "loop": S crossfades the last S seconds into the first S, so the clip repeats with no jump (it gets S shorter)."""
    import av, numpy as np
    from PIL import Image
    src = next(p for p in saved if p.suffix.lower() in (".mp4", ".webm", ".mov"))
    with av.open(str(src)) as c:
        s = c.streams.video[0]
        fps = s.average_rate or 24
        rgb = [f.to_ndarray(format="rgb24") for f in c.decode(s)]
    alpha = None
    if cut:
        if len(cut) != len(rgb):
            sys.exit(f"batch: {ask['name']}: {len(cut)} cutout frames for {len(rgb)} video frames")
        alpha = [np.asarray(Image.open(p))[..., 3] for p in cut]
    elif ask.get("cutout") == "black":
        lo, hi = 10.0, 110.0   # max channel: below lo clear, above hi solid
        alpha = [(np.clip((f.max(axis=2).astype(np.float32) - lo) / (hi - lo), 0, 1) * 255).round().astype(np.uint8) for f in rgb]
    k = int(round(float(ask.get("loop", 0)) * float(fps)))
    if k:
        if k * 2 >= len(rgb):
            sys.exit(f"batch: {ask['name']}: loop {ask['loop']} s is too long for a {len(rgb) / float(fps):.1f} s clip")
        def fold(fr):   # frame i of the first k = the clip's tail fading into its head (equal weights sum to 1)
            n = len(fr)
            head = [(fr[n - k + i].astype(np.float32) * (1 - (i + 1) / (k + 1)) +
                     fr[i].astype(np.float32) * ((i + 1) / (k + 1))).round().astype(np.uint8) for i in range(k)]
            return head + fr[k:n - k]
        rgb = fold(rgb)
        alpha = fold(alpha) if alpha else None
    name = f"{ask['name']}__{key}"
    h, w = rgb[0].shape[:2]

    def write(path, codec, fmt, frames, opts, container=None):
        with av.open(str(path), "w", options=container or {}) as o:
            st = o.add_stream(codec, rate=fps)
            st.width, st.height, st.pix_fmt, st.options = w, h, fmt, opts
            for fr in frames:
                for p in st.encode(av.VideoFrame.from_ndarray(fr, format="rgba" if fr.shape[2] == 4 else "rgb24")):
                    o.mux(p)
            for p in st.encode():
                o.mux(p)

    write(OUT / f"{name}.mp4", "libx264", "yuv420p", rgb, {"crf": "16", "preset": "slow"}, {"movflags": "+faststart"})
    if alpha:
        if ask.get("cutout") == "black":   # the frame was the colour times alpha over black: divide it back out
            rgb = [np.clip(c.astype(np.float32) * 255 / np.maximum(a, 1)[..., None], 0, 255).astype(np.uint8)
                   for c, a in zip(rgb, alpha)]
        rgba = [np.dstack([c, a]) for c, a in zip(rgb, alpha)]
        write(OUT / f"{name}-cutout.webm", "libvpx-vp9", "yuva420p", rgba, {"crf": "24", "b": "0", "row-mt": "1", "auto-alt-ref": "0"})
        if ask.get("frames"):
            d = OUT / f"{name}-cutout"
            d.mkdir(exist_ok=True)
            for i, f in enumerate(rgba):
                Image.fromarray(f).save(d / f"{i:03d}.png", optimize=False)
    for p in [*saved, *cut]:
        p.unlink(missing_ok=True)


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
