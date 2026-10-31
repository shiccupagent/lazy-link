#!/usr/bin/env python3
"""env - equirectangular HDR environment maps for three.js lighting and reflections, rendered by Blender on the PC.

  python3 imagegen/env.py VENUE.glb --at=x,y,z --out DIR [--lights three] [--sky --sun=AZ,EL [--turbidity T]] [--name NAME]
  python3 imagegen/env.py --sky --sun=AZ,EL --out DIR [--turbidity T | --ground R,G,B] [--name NAME]
                  [--clamp 20] [--size 512] [--also 256,1024] [--timeout S] [--wait S]

Use the = form for negative values (--at=-1,2,3, --sun=-90,20): argparse reads a bare -1 as an option.

A venue is lit by its own lights (suns, lamps, emissive materials) and seen from --at, in the .glb's own units.
--sky adds a Sky Texture; with no venue the camera sits at the origin. Both together light the venue with the sky too.
A venue with no light and no --sky is refused before anything is sent.
--lights standard (the default) reads the venue's lights in glTF's physical units, as Blender's importer does: a sun
comes in at I / 683. --lights three reads them as three.js intensities, the units a three.js scene is lit in and what
GLTFExporter writes: a sun's I is a sun strength of I, and a point or spot's candela I is a power of 4 * pi * I, so it
gives the same light as three.js. Emissive materials are the same either way. Needs a venue (see the venue section of
README.md, "Venues exported from three.js").
A venue node's extras "env" (userData.env in three.js): "drop" removes the object and its children before the render;
"light-only" keeps it lighting the scene but not in view. Counts go to the sidecar's "extras".
--ground R,G,B (linear albedo, 0 to 1, e.g. 0.10,0.22,0.06 for grass; needs --sky, not --turbidity) puts a 100 km
diffuse plane under the sky: 1.6 m below the camera with no venue, just under the venue's lowest point with one. The
sky's sun lights it, so its brightness follows the sky; the map itself still shows no sun disc. Without it the lower
half is the sky model's own dark ground, which tints bounce light blue.

DIR gets <name>_env.hdr (Radiance RGBE, linear float: the --size map), <name>_env.png (preview, Reinhard then sRGB,
top is up), <name>_env.json (the sidecar: how it was made, the checks, suggestedEnvMapIntensity) and
<name>_env_<W>.hdr for each --also width. summary.json and blender.log stay beside them. The raw render is removed on
success. The job fails when the render is nearly black, flat, or its left and right edges do not meet.

Colour: view transform Standard (no look, exposure 0, gamma 1), float output, so the values stay scene-linear.
Sky: Multiple Scattering (sun disc off) where this Blender has it; a turbidity forces Hosek-Wilkie.
Camera: equirectangular panorama, three.js's own layout: u 0.25 faces -Z, the centre +X, u 0.75 +Z, both edges -X;
row 0 is up. Sun AZ 0 = -Z, 90 = +X (both checked on Blender 5.1.2).
Intensity: suggestedEnvMapIntensity = round(0.5 / meanLuminance, 3). Scale the map by it in three.js: a white albedo-1
diffuse under a uniform map then reads 0.5. That suits a venue. For a sky, the game's DirectionalLight is the sun, so
keep the sky-to-sun ratio instead: with --ground and no venue the sidecar's sunIrradiance.normal is the sun's
irradiance at right angles, measured off the ground, and envMapIntensity = directionalLight.intensity / that keeps the
map and the game's sun at the rendered ratio, whatever units the game's light uses.
Windows PC only for now (like bake). Not in v1: UltraHDR, pre-filtered cubemaps.
"""
import argparse
import datetime as dt
import json
import math
import re
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from pcsubmit import load_imagegen, submit

TOOL_VERSION = "1.3.0"
LIGHTS = ("standard", "three")      # --lights: how the venue's lights are read (env_blender.py LIGHT_MODES)
PC_SCRIPT = HERE / "pc" / "env_blender.py"
RENDER_FILE = "render.hdr"          # the raw render's name inside the job's output folder (options "render")
DEFAULT_SIZE = 512
DEFAULT_CLAMP = 20.0
MIN_SIZE, MAX_SIZE = 64, 8192
TURBIDITY_RANGE = (1.0, 10.0)
DEFAULT_TIMEOUT = 1800              # PC-side Blender limit for one render
WAIT_LIMIT = 3 * 3600               # seconds the job waits in the PC's queue before it gives up
REC709 = np.array([0.2126, 0.7152, 0.0722])
DARK_MEAN = 0.005                   # mean luminance at or below this: the lights do not reach the camera (bake's DARK_MEAN)
FLAT_SPREAD = 0.02                  # luminance std / mean below this: one uniform colour, nothing to light from
SEAM_WARN = 2.0                     # wrap step / median step beside it (about 1 = seamless)
SEAM_FAIL = 4.0                     # calibrated 2026-10-30: gym and clear-sky renders read 0.1 to 1.1 at seven inner
                                    # columns; the gym's wrap read 3.0 before the overscan (a 4% denoiser bump)
SEAM_BAND = 4                       # columns on each side of the wrap that wrap_seam compares against
SEAM_FLOOR = 0.01                   # a step under 1% of the brightness beside the wrap counts as ordinary (a flat,
                                    # denoised wall has steps near 0, which made a 0.3% offset read 3.8)
CAM_ROT_DEG = [90.0, 0.0, -90.0]    # must match CAM_ROT in pc/env_blender.py (test_env checks it)

# Blender writes its own logging to stdout; keep the ENV lines that matter, and always keep the log.
PS1 = ("$ErrorActionPreference = 'Continue'\n"
       "if (-not $env:BLENDER) { [Console]::Error.WriteLine('env: Blender is not installed on this PC'); exit 3 }\n"
       "& $env:BLENDER -b --factory-startup --python-exit-code 1 -P env_blender.py -- "
       "--options options.json --out $env:RENDER_OUT 2>&1 "
       "| Tee-Object -FilePath (Join-Path $env:RENDER_OUT 'blender.log') "
       "| ForEach-Object { \"$_\" } | Select-String 'ENV|Error|Traceback'\n"
       "exit $LASTEXITCODE\n")


# ---------- options ----------

def _numbers(text, count, what):
    """'a,b,c' to a list of finite floats; raises ValueError with the option's name in the message."""
    parts = [p for p in re.split(r"[,\s]+", str(text).strip()) if p]
    if len(parts) != count:
        raise ValueError(f"{what} needs {count} numbers separated by commas, got {text!r}")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"{what} needs numbers, got {text!r}") from None
    if not all(math.isfinite(v) for v in vals):
        raise ValueError(f"{what} needs finite numbers, got {text!r}")
    return vals


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Equirectangular HDR environment map for three.js (Windows PC, Blender).")
    ap.add_argument("venue", nargs="?", help="venue .glb, lit by its own lights and seen from --at")
    ap.add_argument("--at", metavar="x,y,z", help="camera position in the .glb's units (needs a venue)")
    ap.add_argument("--sky", action="store_true", help="add a sky (needs --sun)")
    ap.add_argument("--lights", choices=LIGHTS, default="standard",
                    help="how the venue's lights are read: standard (glTF units) or three (three.js intensities)")
    ap.add_argument("--sun", metavar="AZ,EL",
                    help="sun direction in degrees: AZ 0 = glTF -Z, 90 = +X; EL 0 = horizon, 90 = zenith")
    ap.add_argument("--turbidity", type=float, help="sky haziness, 1 to 10 (forces the Hosek-Wilkie sky)")
    ap.add_argument("--ground", metavar="R,G,B",
                    help="linear albedo (0 to 1 each) of a sunlit ground plane under the sky; needs --sky")
    ap.add_argument("--clamp", type=float, default=DEFAULT_CLAMP,
                    help="Cycles indirect light clamp; 0 = no clamp (default 20)")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE,
                    help="width of <name>_env.hdr in pixels, even, 64 to 8192 (height is half); default 512")
    ap.add_argument("--also", action="append", default=[], metavar="W[,W...]",
                    help="more widths to write as <name>_env_<W>.hdr; each must divide the largest width")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--name", help="file name stem (default: the venue's file name, or 'sky')")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="PC-side limit for the render, seconds")
    ap.add_argument("--wait", type=int, default=WAIT_LIMIT,
                    help="how long to wait in the PC's queue, seconds (default 3 h)")
    return normalize(ap, ap.parse_args(argv))


def normalize(ap, args):
    """Refuses bad option combinations (argparse error, exit 2) and fills in the derived values."""
    refuse = ap.error
    if not args.venue and not args.sky:
        refuse("give a venue .glb with --at, or --sky with --sun (or both)")
    try:
        if args.venue:
            if args.at is None:
                refuse("a venue needs --at x,y,z (the camera position, in the .glb's units)")
            venue = Path(args.venue).expanduser().resolve()
            if venue.suffix.lower() != ".glb" or not venue.is_file():
                refuse(f"{args.venue} is not a .glb file")
            args.venue = venue
            args.at = _numbers(args.at, 3, "--at")
        else:
            if args.at is not None:
                refuse("--at needs a venue .glb")
            if args.lights == "three":
                refuse("--lights three needs a venue .glb (it sets how the venue's lights are read)")
            args.venue = None
            args.at = None
        if args.sky:
            if args.sun is None:
                refuse("--sky needs --sun AZ,EL")
            args.sun = _numbers(args.sun, 2, "--sun")
            if not -90.0 <= args.sun[1] <= 90.0:
                refuse("--sun EL (the second number) must be between -90 and 90")
            if args.turbidity is not None:
                lo, hi = TURBIDITY_RANGE
                if not (math.isfinite(args.turbidity) and lo <= args.turbidity <= hi):
                    refuse(f"--turbidity must be from {lo:g} to {hi:g}, got {args.turbidity}")
            if args.ground is not None:
                if args.turbidity is not None:
                    refuse("--ground needs the sun disc of the default sky; the --turbidity sky (Hosek-Wilkie) has none")
                args.ground = _numbers(args.ground, 3, "--ground")
                if not all(0.0 <= v <= 1.0 for v in args.ground):
                    refuse(f"--ground values are linear albedo from 0 to 1, got {args.ground}")
        else:
            if args.sun is not None:
                refuse("--sun needs --sky")
            if args.turbidity is not None:
                refuse("--turbidity needs --sky")
            if args.ground is not None:
                refuse("--ground needs --sky (the sky's sun lights it)")
    except ValueError as e:
        refuse(str(e))
    if not (math.isfinite(args.clamp) and args.clamp >= 0):
        refuse(f"--clamp must be 0 or more, got {args.clamp}")
    extra = []
    for chunk in args.also:
        for part in str(chunk).split(","):
            if part.strip():
                try:
                    extra.append(int(part))
                except ValueError:
                    refuse(f"--also takes widths such as 256,1024, got {part!r}")
    sizes = [args.size] + extra
    for s in sizes:
        if s % 2 or not MIN_SIZE <= s <= MAX_SIZE:
            refuse(f"widths must be even and from {MIN_SIZE} to {MAX_SIZE}, got {s}")
    if len(set(sizes)) != len(sizes):
        refuse("--also repeats --size or another width")
    largest = max(sizes)
    for s in sizes:
        if largest % s:
            refuse(f"each width must divide the largest ({largest}); {s} does not")
    args.also = extra
    args.render_width = largest
    if args.name is None:
        args.name = (re.sub(r"[^A-Za-z0-9_-]+", "_", args.venue.stem).strip("_") or "venue") if args.venue else "sky"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.name):
        refuse(f"--name must be letters, digits, _ or - (1 to 64 characters), got {args.name!r}")
    return args


# ---------- the venue's light, read on the Mac before anything is sent ----------

def read_glb_json(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise SystemExit(f"env: {Path(path).name} is not a binary .glb file")
    version, _ = struct.unpack("<II", data[4:12])
    clen, ctype = struct.unpack("<II", data[12:20])
    if version != 2 or ctype != 0x4E4F534A:    # glTF 2, first chunk is JSON
        raise SystemExit(f"env: {Path(path).name} is not a glTF 2.0 .glb (first chunk must be JSON)")
    try:
        return json.loads(data[20:20 + clen])
    except ValueError as e:
        raise SystemExit(f"env: {Path(path).name} has unreadable JSON ({e})") from None


def material_emits(mat):
    """glTF material: emits when it has an emissive texture or a positive emissive colour (and strength)."""
    strength = float(((mat.get("extensions") or {}).get("KHR_materials_emissive_strength") or {})
                     .get("emissiveStrength", 1.0))
    if strength <= 0:
        return False
    return "emissiveTexture" in mat or any(float(v) > 0 for v in mat.get("emissiveFactor", [0.0, 0.0, 0.0]))


def gltf_census(doc):
    """Light in the .glb as the PC will see it: suns (directional), lamps (point, spot) above zero intensity that
    a node uses, and emissive materials on meshes a node uses. The PC's census after import is the one that counts."""
    light_defs = ((doc.get("extensions") or {}).get("KHR_lights_punctual") or {}).get("lights", [])
    suns = lamps = 0
    meshes_used = set()
    for node in doc.get("nodes", []):
        ext = (node.get("extensions") or {}).get("KHR_lights_punctual")
        if ext is not None:
            light = light_defs[int(ext["light"])]
            if float(light.get("intensity", 1.0)) > 0:
                if light.get("type") == "directional":
                    suns += 1
                else:
                    lamps += 1
        if "mesh" in node:
            meshes_used.add(int(node["mesh"]))
    materials = doc.get("materials", [])
    mats = set()
    all_meshes = doc.get("meshes", [])
    for mi in meshes_used:
        for prim in all_meshes[mi].get("primitives", []):
            if "material" in prim:
                mats.add(int(prim["material"]))
    emissive = sum(1 for mi in mats if material_emits(materials[mi]))
    return {"suns": suns, "lamps": lamps, "emissive": emissive}


def check_light(args):
    """Refuses a venue with no light when there is no sky. Returns the census (None without a venue)."""
    if args.venue is None:
        return None
    census = gltf_census(read_glb_json(args.venue))
    if not args.sky and census["suns"] == 0 and census["lamps"] == 0 and census["emissive"] == 0:
        raise SystemExit("env: the venue has no light (0 suns, 0 lamps, 0 emissive materials) and no --sky, so "
                         "nothing would light it. Add --sky --sun AZ,EL, or light the venue.")
    return census


# ---------- the job ----------

def build_job(args, workdir):
    """Writes options.json into workdir. Returns (files, options): files are (source, path in the job folder)."""
    opts = {
        "venue": "venue.glb" if args.venue is not None else None,
        "at": args.at,
        "sky": ({"sun_az": args.sun[0], "sun_el": args.sun[1], "turbidity": args.turbidity}
                if args.sky else None),
        "clamp": float(args.clamp),
        "width": int(args.render_width),
        "render": RENDER_FILE,
        "ground": args.ground,
        "lights": args.lights,
    }
    opts_path = Path(workdir) / "options.json"
    opts_path.write_text(json.dumps(opts, indent=1))
    files = [(PC_SCRIPT, "env_blender.py"), (opts_path, "options.json")]
    if args.venue is not None:
        files.append((args.venue, "venue.glb"))
    return files, opts


def _failure_reason(out):
    """The render's own reason for failing, from summary.json (the PC side writes it before it fails), or None."""
    f = out / "summary.json"
    if not f.exists():
        return None
    try:
        s = json.loads(f.read_text())
    except ValueError:
        return None
    if s.get("status") != "failed":
        return None
    return f"env failed: {' | '.join(s.get('failures', []))} (no HDR written; see {out / 'blender.log'})"


# ---------- Radiance RGBE (.hdr): pure numpy writer and reader ----------

HDR_HEADER = f"#?RADIANCE\nSOFTWARE=lazy-link env.py {TOOL_VERSION}\nFORMAT=32-bit_rle_rgbe\n\n".encode()


def encode_rgbe(img):
    """(H, W, 3) linear float to (H, W, 4) uint8 RGBE. Pixels that are zero (or too small to store) are (0, 0, 0, 0)."""
    img = np.clip(np.asarray(img, dtype=np.float64), 0.0, 1e30)
    v = img.max(axis=2)
    _, e = np.frexp(v)                              # v = m * 2**e, 0.5 <= m < 1; v == 0 gives e == 0
    e = e.astype(np.int64)
    live = (v > 0) & (e + 128 >= 1) & (e + 128 <= 255)
    scale = np.ldexp(1.0, 8 - e)                    # puts the largest channel into [128, 256)
    mant = np.where(live[..., None], np.clip(np.floor(img * scale[..., None]), 0, 255), 0)
    out = np.empty(v.shape + (4,), np.uint8)
    out[..., :3] = mant
    out[..., 3] = np.where(live, e + 128, 0)
    return out


def decode_rgbe(px):
    """(H, W, 4) uint8 RGBE to (H, W, 3) float32. Exponent 0 is zero."""
    px = np.asarray(px, np.uint8)
    E = px[..., 3].astype(np.int64)
    rgb = (px[..., :3].astype(np.float64) + 0.5) * np.ldexp(1.0, E - 136)[..., None]
    rgb[E == 0] = 0.0
    return rgb.astype(np.float32)


def _rle_channel(vals):
    """One channel of one scanline in Radiance's new-style RLE: runs of 4 or more equal values, literals otherwise.
    Literal chunks hold 1 to 128 values (count byte, then the values); runs hold 1 to 127 (128 + count, then the value)."""
    out = bytearray()
    lit_from = 0

    def literals(chunk):
        for i in range(0, len(chunk), 128):
            part = chunk[i:i + 128]
            out.append(len(part))
            out.extend(part.tobytes())

    starts = np.flatnonzero(np.r_[True, vals[1:] != vals[:-1]])
    ends = np.r_[starts[1:], len(vals)]
    for s, e in zip(starts.tolist(), ends.tolist()):
        if e - s >= 4:
            literals(vals[lit_from:s])
            while s < e:
                n = min(127, e - s)
                out.append(128 + n)
                out.append(int(vals[s]))
                s += n
            lit_from = e
    literals(vals[lit_from:])
    return bytes(out)


def write_hdr(path, img):
    """Writes (H, W, 3) linear float as Radiance RGBE, first scanline = top row. RLE for widths 8 to 32767."""
    img = np.asarray(img, dtype=np.float32)
    h, w = img.shape[:2]
    px = encode_rgbe(img)
    parts = [HDR_HEADER, f"-Y {h} +X {w}\n".encode()]
    for y in range(h):
        if 8 <= w <= 32767:
            parts.append(bytes([2, 2, w >> 8, w & 255]))
            for c in range(4):
                parts.append(_rle_channel(px[y, :, c]))
        else:
            parts.append(px[y].tobytes())
    Path(path).write_bytes(b"".join(parts))


def read_hdr(path):
    """Reads a Radiance RGBE file as (H, W, 3) float32 in file order (first scanline = first row). New-style RLE
    and flat scanlines, old-style repeats, EXPOSURE honoured. Raises ValueError with the reason when unreadable."""
    data = Path(path).read_bytes()
    try:
        return _parse_hdr(data)
    except (IndexError, ValueError) as e:
        raise ValueError(f"{Path(path).name} is not a readable Radiance RGBE file ({e})") from None


def _parse_hdr(data):
    pos = 0

    def line():
        nonlocal pos
        end = data.index(b"\n", pos)
        text = data[pos:end].decode("latin-1").strip()
        pos = end + 1
        return text

    if not line().startswith("#?"):
        raise ValueError("no #? header")
    exposure, fmt = 1.0, False
    while True:
        text = line()
        if not text:
            break
        if text == "FORMAT=32-bit_rle_rgbe":
            fmt = True
        elif text.startswith("EXPOSURE="):
            exposure *= float(text.split("=", 1)[1])
    if not fmt:
        raise ValueError("format is not 32-bit_rle_rgbe")
    parts = line().split()
    if len(parts) != 4 or parts[0] != "-Y" or parts[2] != "+X":
        raise ValueError(f"unsupported resolution line {' '.join(parts)!r}")
    h, w = int(parts[1]), int(parts[3])
    px = np.zeros((h, w, 4), np.uint8)
    for y in range(h):
        if 8 <= w <= 32767 and data[pos:pos + 4] == bytes([2, 2, w >> 8, w & 255]):
            pos += 4
            for c in range(4):
                x = 0
                while x < w:
                    count = data[pos]
                    pos += 1
                    if count > 128:
                        n = count - 128
                        if n == 0 or x + n > w:
                            raise ValueError("bad run length")
                        px[y, x:x + n, c] = data[pos]
                        pos += 1
                    else:
                        n = count
                        if n == 0 or x + n > w or pos + n > len(data):
                            raise ValueError("bad literal length")
                        px[y, x:x + n, c] = np.frombuffer(data, np.uint8, n, pos)
                        pos += n
                    x += n
        else:
            x = 0
            while x < w:
                rgbe = data[pos:pos + 4]
                pos += 4
                if len(rgbe) < 4:
                    raise ValueError("truncated pixel data")
                if rgbe[:3] == b"\x01\x01\x01" and x > 0:      # old-style run: repeat the pixel before
                    n = rgbe[3]
                    if x + n > w:
                        raise ValueError("bad repeat length")
                    px[y, x:x + n] = px[y, x - 1]
                    x += n
                else:
                    px[y, x] = np.frombuffer(rgbe, np.uint8)
                    x += 1
    return decode_rgbe(px) / exposure


# ---------- the render's checks, and the outputs ----------

def row_weights(h):
    """Solid angle of each row of an equirectangular map: cos(latitude), 1 at the equator, 0 at the poles."""
    lat = (0.5 - (np.arange(h) + 0.5) / h) * np.pi
    return np.cos(lat)


def mean_luminance(img):
    """Solid-angle weighted Rec.709 mean of a linear equirectangular map."""
    lum = img.astype(np.float64) @ REC709
    w = row_weights(img.shape[0])[:, None]
    return float((lum * w).sum() / (w.sum() * img.shape[1]))


def flat_spread(img):
    lum = img.astype(np.float64) @ REC709
    m = lum.mean()
    return float(lum.std() / m) if m > 0 else 0.0


def wrap_seam(img, band=SEAM_BAND):
    """How much the picture jumps where its left edge meets its right edge, against the ordinary steps beside the
    edge (about 1 = seamless): the median of the column steps within `band` columns of the wrap. Local, so a sun or
    lamp near the edge (steep but smooth) is not a seam; the whole picture's mean step was tiny for a clear sky and
    flagged one at 3.0. Steps beside the wrap smaller than SEAM_FLOOR of the band's mean brightness count as that floor.
    Only the horizontal wrap counts: the top and bottom rows are the poles, not neighbours."""
    g = img.astype(np.float64).mean(axis=2)
    k = max(1, min(int(band), g.shape[1] // 4))
    b = np.concatenate([g[:, -k:], g[:, :k]], axis=1)
    steps = np.abs(np.diff(b, axis=1)).mean(axis=0)
    beside = max(np.median(np.delete(steps, k - 1)), SEAM_FLOOR * np.abs(b).mean())
    return float(steps[k - 1] / (beside + 1e-9))


def check_render(img):
    """Returns (checks, failures, warnings) for a full-size linear render. Failures stop the job's outputs."""
    h, w = img.shape[:2]
    mean_l, spread, seam = mean_luminance(img), flat_spread(img), wrap_seam(img)
    checks = {"aspect": f"{w}:{h}", "meanLuminance": round(mean_l, 6), "flatSpread": round(spread, 6),
              "seamRatio": round(seam, 4), "seamWarnAt": SEAM_WARN, "seamFailAt": SEAM_FAIL,
              "darkAt": DARK_MEAN, "flatAt": FLAT_SPREAD}
    failures, warnings = [], []
    if w != 2 * h:
        failures.append(f"render is {w}x{h}, not 2:1")
    if mean_l <= DARK_MEAN:
        failures.append(f"render is nearly black (mean luminance {mean_l:.4f}): the lights do not reach the camera")
    elif spread < FLAT_SPREAD:
        failures.append(f"render is flat (spread {spread:.4f}): one uniform colour gives no direction to light from")
    if seam > SEAM_FAIL:
        failures.append(f"left and right edges do not meet (seam ratio {seam:.2f}): the map would show a line")
    elif seam > SEAM_WARN:
        warnings.append(f"left and right edges meet loosely (seam ratio {seam:.2f}); look at the PNG for a line")
    return checks, failures, warnings


def sun_irradiance(img, albedo, sun_el):
    """The sun's irradiance at right angles to its rays, from a sky-plus-ground render with no venue: the ground near
    the nadir is lit by the sky's upper hemisphere and the sun, L = albedo (E_sky + S sin EL) / pi, so
    S = (pi L / albedo - E_sky) / sin EL per channel. Returns {"rgb": [...], "normal": luminance} or None when the sun
    is at or under the horizon or a channel's albedo is 0."""
    a = np.asarray(albedo, dtype=np.float64)
    s = math.sin(math.radians(float(sun_el)))
    if s <= 0.02 or np.any(a <= 0):
        return None
    img = img.astype(np.float64)
    h, w = img.shape[:2]
    lat = (0.5 - (np.arange(h) + 0.5) / h) * np.pi
    up = lat > 0
    dw = np.cos(lat) * (np.pi / h) * (2 * np.pi / w)                      # solid angle per pixel of each row
    e_sky = (img[up] * (np.sin(lat[up]) * dw[up])[:, None, None]).sum(axis=(0, 1))
    ground = img[lat < math.radians(-60)].mean(axis=(0, 1))            # well under the horizon
    rgb = (math.pi * ground / a - e_sky) / s
    return {"rgb": [round(float(v), 3) for v in rgb], "normal": round(float(rgb @ REC709), 3)}


def box_down(img, w):
    """Box-average a linear map down to width w (the height follows: 2:1). Averages the linear values."""
    h_in, w_in = img.shape[:2]
    f = w_in // w
    if w_in % w or h_in % f:
        raise ValueError(f"cannot downscale {w_in} to {w}")
    return img.reshape(h_in // f, f, w, f, 3).astype(np.float64).mean(axis=(1, 3)).astype(np.float32)


def to_png(img, path):
    """Preview: Reinhard per channel (x / (1 + x)), then sRGB. Row 0 is the top, so the sky is at the top."""
    x = img.astype(np.float64)
    x = x / (1.0 + x)
    s = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.maximum(x, 0.0), 1 / 2.4) - 0.055)
    Image.fromarray(np.clip(np.floor(s * 255 + 0.5), 0, 255).astype(np.uint8)).save(path)


def finish(out, args, opts, census):
    """Reads the PC's render, runs the checks, writes the outputs, and returns the sidecar dict."""
    summary_file = out / "summary.json"
    if not summary_file.exists():
        raise SystemExit(f"env: no summary.json in {out}; see {out / 'blender.log'}")
    summary = json.loads(summary_file.read_text())
    if summary.get("status") != "ok":
        raise SystemExit(f"env failed: {' | '.join(summary.get('failures', []))}; see {out / 'blender.log'}")
    render = out / summary.get("renderFile", RENDER_FILE)
    if not render.is_file():
        raise SystemExit(f"env: the render {render.name} is missing in {out}")
    try:
        full = read_hdr(render)
    except ValueError as e:
        raise SystemExit(f"env: {e}") from None
    pad = int(summary.get("overscan", 0))                 # columns past 360 degrees on each side (env_blender.py)
    if full.shape[1] != args.render_width + 2 * pad:
        raise SystemExit(f"env: the render is {full.shape[1]} wide, expected {args.render_width} + 2 x {pad}")
    full = full[:, pad:full.shape[1] - pad]
    checks, failures, warnings = check_render(full)
    if failures:
        raise SystemExit("env: the render failed its checks: " + " | ".join(failures)
                         + f" (raw render kept at {render})")
    warnings = list(summary.get("warnings", [])) + warnings
    main_img = box_down(full, args.size)
    mean_l = mean_luminance(main_img)
    sun = (sun_irradiance(full, args.ground, args.sun[1])                 # a venue would shade and bounce the ground
           if args.ground is not None and args.venue is None else None)
    stem = args.name
    hdr = out / f"{stem}_env.hdr"
    write_hdr(hdr, main_img)
    png = out / f"{stem}_env.png"
    to_png(main_img, png)
    extras = []
    for width in args.also:
        extra = out / f"{stem}_env_{width}.hdr"
        write_hdr(extra, box_down(full, width))
        extras.append(extra.name)
    sidecar = {
        "tool": "imagegen/env.py",
        "toolVersion": TOOL_VERSION,
        "name": stem,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "venue": args.venue.name if args.venue is not None else None,
        "at": args.at,
        "sky": ({"sunAz": args.sun[0], "sunEl": args.sun[1], "turbidity": args.turbidity,
                 "skyType": summary.get("skyType"), "skyTypesOffered": summary.get("skyTypesOffered")}
                if args.sky else None),
        "census": census,
        "ground": summary.get("ground") if args.ground is not None else None,
        "viewTransform": summary.get("viewTransform"),
        "look": summary.get("look"),
        "exposure": summary.get("exposure"),
        "gamma": summary.get("gamma"),
        "colourSpace": "scene-linear, Rec.709 primaries, float (Radiance RGBE)",
        "samples": summary.get("samples"),
        "denoiser": summary.get("denoiser"),
        "clamp": float(args.clamp),
        "device": summary.get("device"),
        "deviceNames": summary.get("deviceNames"),
        "blenderVersion": summary.get("blenderVersion"),
        "camera": {"type": "panoramic equirectangular", "rotationDeg": CAM_ROT_DEG,
                   "columnFaces": {"u0": "-X", "u0.25": "-Z", "u0.5": "+X", "u0.75": "+Z", "u1": "-X"},
                   "rowZeroIs": "up"},
        "size": {"width": args.size, "height": args.size // 2},
        "renderWidth": args.render_width,
        "lights": args.lights,
        "lightingMode": summary.get("lightingMode"),
        "extras": summary.get("extras"),
        "files": {"hdr": hdr.name, "png": png.name, "json": f"{stem}_env.json", "extraHdr": extras},
        "meanLuminance": round(mean_l, 6),
        "suggestedEnvMapIntensity": round(0.5 / mean_l, 3),
        "intensityFormula": "round(0.5 / meanLuminance, 3)",
        "sunIrradiance": sun,
        "sunIntensityRule": ("envMapIntensity = directionalLight.intensity / sunIrradiance.normal" if sun else None),
        "checks": checks,
        "warnings": warnings,
    }
    (out / f"{stem}_env.json").write_text(json.dumps(sidecar, indent=1))
    render.unlink()
    return sidecar


# ---------- run ----------

def run(args):
    """Refuses a lightless venue, sends the job, and writes the outputs. Returns the sidecar dict."""
    census = check_light(args)
    ig = load_imagegen()
    if ig.load_pcjob().POSIX:
        raise SystemExit("env: Windows PC only for now")
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    for stale in (out / "summary.json", out / RENDER_FILE):
        stale.unlink(missing_ok=True)       # an earlier run's result is not this run's
    with tempfile.TemporaryDirectory() as td:
        files, opts = build_job(args, td)
        submit(ig, files, PS1, out, args.timeout, args.wait, name="env", label="env", on_fail=_failure_reason)
    return finish(out, args, opts, census)


def main(argv=None):
    sidecar = run(parse_args(argv))
    print(json.dumps(sidecar, indent=1))


if __name__ == "__main__":
    main()
