#!/usr/bin/env python3
"""pbr.py - roughness and metal maps (plus one packed ORM) from a surface picture, `imagegen.py pbr`. Mac only, no AI:
numpy and Pillow, nothing else (the blurs are written out here, no scipy).
  make_roughness()  base + var x cavities + detail x albedo detail, kept in [0.04, 1]. The one function a model-made
                    roughness would replace; metal, AO, the ORM packing and the sidecar stay the same.
  make_metal()      0 or 1 everywhere, or "edges": worn ridges from the height map's curvature (not its cavities).
  pack_orm()        R = ambient occlusion (white when there is none), G = roughness, B = metal.
Presets (rough base, variation, metal per material) are data in pbr_materials.json; command-line overrides win.
Inputs: the albedo picture. <name>_height.png and <name>_ao.png beside it are used when present (bumpmaps makes both).
With --tile every filter wraps, so the maps repeat with the picture. All outputs are linear 8-bit PNG (no colour profile):
load them with NoColorSpace in three.js.
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

PBR_VERSION = 1          # tool version, written into every <name>_pbr.json; bump when the maps change
MODELS = ("heuristic",)  # roughness makers; a model-made one (pbrify, v2 on the PC) is added here
MODEL_REJECT = "only heuristic for now; pbrify comes in v2 on the PC"
PRESETS = Path(__file__).resolve().parent / "pbr_materials.json"
DEFAULT_VAR = 0.1        # variation when --rough is given without a material
ROUGH_MIN = 0.04         # roughness never goes below this (the lowest value is stored as 11/255 = 0.043)
LUMA = np.array([0.2126, 0.7152, 0.0722])
CAVITY_STRENGTH = 1.6    # same as bumpmaps.AO_STRENGTH, so a cavity here is as dark as the AO bump makes it
CAVITY_RADII = (6, 24)   # px: the same two radii as bumpmaps' AO; the wide one counts half
DETAIL_RADIUS = 6        # px: albedo detail is luminance minus its blur
DETAIL_GAIN = 4.0        # a luminance step of 1/4 counts as full detail
DETAIL_WEIGHT = 0.5      # default detail = var x this: albedo detail moves roughness half as much as a cavity, darker
                         # than its blur = rougher. A preset's own "detail" (roughness units, signed) replaces it:
                         # negative = LIGHTER marks are rougher (scratches on ice are whiter than the ice around them)
EDGE_RADIUS = 2.0        # px: blur before the curvature, so pixel noise is not a worn edge
EDGE_LO, EDGE_HI = 0.4, 0.7   # curvature (share of the strongest) where worn-edge metal starts, and where it is full


def blur(a, sigma, wrap):
    """Gaussian blur over rows and columns of a 2-D or 3-D array. wrap: the picture repeats, so the edges wrap;
    otherwise the edge pixels are repeated outward."""
    a = np.asarray(a, np.float64)
    r = int(math.ceil(3 * sigma))
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-x * x / (2 * sigma * sigma))
    k /= k.sum()
    mode = "wrap" if wrap else "edge"
    for axis in (0, 1):
        p = np.pad(a, [(r, r) if i == axis else (0, 0) for i in range(a.ndim)], mode=mode)
        n = a.shape[axis]
        out = np.zeros_like(a)
        for i, w in enumerate(k):
            sl = [slice(None)] * a.ndim
            sl[axis] = slice(i, i + n)
            out += w * p[tuple(sl)]
        a = out
    return a


def unit(a):
    """Robust 0-1: the 1st and 99th percentiles become 0 and 1. A flat map stays 0."""
    lo, hi = np.percentile(a, [1, 99])
    return np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)


def laplacian(a, wrap):
    if wrap:
        return np.roll(a, 1, 0) + np.roll(a, -1, 0) + np.roll(a, 1, 1) + np.roll(a, -1, 1) - 4 * a
    p = np.pad(a, 1, mode="edge")
    return p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4 * a


def smoothstep(lo, hi, x):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def cavity_from_height(height, wrap):
    """How far each spot sits below its surroundings: 0 on flat ground and on ridges, up to 1 in a hollow."""
    h = unit(height)
    dip = (np.clip(blur(h, CAVITY_RADII[0], wrap) - h, 0, None)
           + 0.5 * np.clip(blur(h, CAVITY_RADII[1], wrap) - h, 0, None))
    return np.clip(CAVITY_STRENGTH * dip, 0, 1)


def heuristic_roughness(rgb, height, ao, base, var, wrap, detail_amount=None):
    if ao is not None:
        cavity = np.clip(1 - ao, 0, 1)            # darker AO = deeper cavity
    elif height is not None:
        cavity = cavity_from_height(height, wrap)
    else:
        cavity = 0.0
    lum = rgb @ LUMA
    # only the part that differs from its blur: a dark but even albedo has none, so it does not read rough
    detail = np.clip(-(lum - blur(lum, DETAIL_RADIUS, wrap)) * DETAIL_GAIN, -1, 1)
    if detail_amount is None:
        detail_amount = var * DETAIL_WEIGHT
    return np.clip(base + var * cavity + detail_amount * detail, ROUGH_MIN, 1)


def check_model(model):
    if model not in MODELS:
        raise ValueError(MODEL_REJECT)


def make_roughness(model, rgb, height, ao, base, var, wrap=False, detail_amount=None):
    """Roughness, float 0-1, same shape as the picture. The one place a model-made roughness would plug in."""
    check_model(model)
    return heuristic_roughness(rgb, height, ao, base, var, wrap, detail_amount)


def make_metal(mode, height, shape, wrap):
    """(metal map 0-1, note or None). mode: 0, 1, or "edges" (ridges and edges from the height map's curvature)."""
    if mode in (0, 1):
        return np.full(shape, float(mode)), None
    if height is None:
        return np.zeros(shape), 'warning: metal "edges" needs a height map (<name>_height.png beside the picture); used 0'
    # raw height, not unit(): unit() clips the top 1%, which flattens a bump's peak and moves its curvature onto a ring
    convex = np.clip(-laplacian(blur(height, EDGE_RADIUS, wrap), wrap), 0, None)   # > 0 on ridges and edges
    peak = np.percentile(convex, 99)
    return smoothstep(EDGE_LO, EDGE_HI, convex / max(peak, 1e-9)), None


def to_u8(a):
    return np.round(np.clip(a, 0, 1) * 255).astype(np.uint8)


def pack_orm(ao_u8, rough_u8, metal_u8):
    """ORM: R = ambient occlusion (255 when there is none), G = roughness, B = metal. Takes the 8-bit maps, so each
    channel equals its own PNG exactly."""
    r = np.full(rough_u8.shape, 255, np.uint8) if ao_u8 is None else ao_u8
    return np.dstack([r, rough_u8, metal_u8])


def check_values(where, rough, var, metal, detail=None):
    if detail is not None and not (-1 <= detail <= 1):
        raise ValueError(f"{where}: detail must be -1 to 1, got {detail}")
    if not (0 <= rough <= 1):
        raise ValueError(f"{where}: rough must be 0 to 1, got {rough}")
    if var < 0:
        raise ValueError(f"{where}: var must be 0 or more, got {var}")
    if metal not in (0, 1, "edges"):
        raise ValueError(f'{where}: metal must be 0, 1 or "edges", got {metal!r}')


def load_presets(path=PRESETS):
    """(file version, {name: preset}). Every value is checked here, so a typo fails before any map is made."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for name, p in data["presets"].items():
        check_values(f"preset {name}", p["rough"], p["var"], p["metal"], p.get("detail"))
    return data.get("version"), data["presets"]


def resolve(material=None, rough=None, rough_var=None, metal=None, detail=None):
    """(values used, preset version or None). The preset gives the base; any value given on the command line wins.
    "detail" is in the values only when a preset or the command line sets it (else var x DETAIL_WEIGHT)."""
    if material is not None:
        _, presets = load_presets()
        if material not in presets:
            raise ValueError(f"no preset {material!r}; known: {', '.join(sorted(presets))}")
        p = presets[material]
        vals = {"rough": p["rough"], "var": p["var"], "metal": p["metal"]}
        if "detail" in p:
            vals["detail"] = p["detail"]
        preset_version = p.get("version")
    else:
        if rough is None:
            raise ValueError("pick a --material (names in imagegen/pbr_materials.json) or give --rough R")
        vals = {"rough": rough, "var": DEFAULT_VAR, "metal": 0}
        preset_version = None
    for key, value in (("rough", rough), ("var", rough_var), ("metal", metal), ("detail", detail)):
        if value is not None:
            vals[key] = value
    check_values("settings", vals["rough"], vals["var"], vals["metal"], vals.get("detail"))
    return vals, preset_version


def load_rgb(path):
    return np.asarray(Image.open(path).convert("RGB"), np.float64) / 255


def load_gray(path, shape, notes):
    im = Image.open(path).convert("L")
    if im.size != (shape[1], shape[0]):
        im = im.resize((shape[1], shape[0]), Image.BILINEAR)
        notes.append(f"{path.name} resized to the picture's size")
    return np.asarray(im, np.float64) / 255


def companions(picture):
    """(height file, AO file), each or None, beside the picture: <stem>_height.png and <stem>_ao.png only. A tiled
    picture (<name>_tile.png) never borrows the untiled <name>_height: tile cuts and resizes the picture, so those maps
    no longer line up with it (run bump --tile on the tiled picture)."""
    def find(kind):
        q = picture.with_name(f"{picture.stem}_{kind}.png")
        return q if q.exists() else None
    return find("height"), find("ao")


def make_pbr(picture, out_dir, vals, tile, material=None, preset_version=None, model="heuristic"):
    """Writes <stem>_roughness.png, <stem>_metal.png, <stem>_orm.png and <stem>_pbr.json into out_dir.
    Returns the sidecar dict."""
    check_model(model)
    picture = Path(picture)
    stem = picture.stem
    rgb = load_rgb(picture)
    shape = rgb.shape[:2]
    notes = []
    hp, ap = companions(picture)
    height = load_gray(hp, shape, notes) if hp else None
    ao = load_gray(ap, shape, notes) if ap else None
    detail = vals.get("detail", vals["var"] * DETAIL_WEIGHT)
    rough = make_roughness(model, rgb, height, ao, vals["rough"], vals["var"], tile, detail)
    metal, note = make_metal(vals["metal"], height, shape, tile)
    if note:
        notes.append(note)
    # the floor is stored as 11/255 (0.043), so no stored value is under ROUGH_MIN
    rough_u8 = np.maximum(to_u8(rough), math.ceil(ROUGH_MIN * 255))
    metal_u8 = to_u8(metal)
    ao_u8 = None if ao is None else to_u8(ao)
    Image.fromarray(rough_u8).save(out_dir / f"{stem}_roughness.png")
    Image.fromarray(metal_u8).save(out_dir / f"{stem}_metal.png")
    Image.fromarray(pack_orm(ao_u8, rough_u8, metal_u8)).save(out_dir / f"{stem}_orm.png")
    side = {
        "material": material,
        "params": {"rough": vals["rough"], "rough_var": vals["var"], "detail": round(detail, 4), "metal": vals["metal"], "tile": tile},
        "preset_version": preset_version,
        "tool_version": PBR_VERSION,
        "source": model,
        "inputs": {"albedo": picture.name, "height": hp.name if hp else None, "ao": ap.name if ap else None},
        "colorSpace": "linear",
        "packing": "ORM: R=ao G=roughness B=metal",
        "notes": notes,
    }
    (out_dir / f"{stem}_pbr.json").write_text(json.dumps(side, indent=2) + "\n", encoding="utf-8")
    for n in notes:
        print(f"pbr: {picture.name}: {n}")
    print(f"pbr: {out_dir / (stem + '_roughness.png')} roughness {rough.min():.2f}-{rough.max():.2f} "
          f"(mean {rough.mean():.2f}), metal {metal.min():.0f}-{metal.max():.0f}  (+ _metal, _orm, _pbr.json)")
    return side
