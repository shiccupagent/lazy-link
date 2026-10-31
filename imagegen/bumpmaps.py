#!/usr/bin/env python3
"""bumpmaps.py - bump maps for a surface picture (floor, wall, ground...), the default style ("MoGe-2 shapes + a touch of
fine detail"): MoGe-2's real shapes (brick faces, mortar, plank seams; run on the PC by
`imagegen.py make maps`) plus a touch of the picture's own fine detail (grain, pits).
Needs numpy, scipy and Pillow on the computer that runs it (the Mac side; the PC only runs MoGe-2).
  classic(rgb)        -> height, normal from the picture's light/dark detail (alone it turns colour specks into fake bumps)
  moge_detail(normal) -> MoGe's normal map with any overall tilt removed (MoGe reads a texture as a camera photo)
  combo(moge, classic)-> the default style
  height_from_normal(n) -> the height map, integrated from the finished normal map (so height, AO and normals agree)
  ambient_occlusion(h) -> darkens the hollows of the height map (cavity), for three.js aoMap
A tiled texture (imagegen.py tile) gets maps that wrap too: finish_tiled() runs on MoGe's maps of the picture and of
the picture moved by half across, down and both (SHIFTS), using each where its own edges are far away.
Objects and whole scenes (a car sprite, a room painting) use prep_object() + finish_object() (`bump --object`): there the
broad tilt IS the shape, so MoGe's whole normal is kept.
Normal maps are OpenGL (+Y up), as three.js and glTF expect; load them (and AO) with colorSpace = NoColorSpace.
`imagegen.py bump PICTURE... --out DIR [--tile] [--slope S] [--highpass R]` runs the whole thing: DIR/<name>_normal.png, _height.png and _ao.png."""
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, sobel

VERSION = 3          # bump when the style below changes; models.json "maps" version follows it
                     # 3: height and AO from the normal map, not MoGe's depth (2026-10-30)
MOGE_DEPTH = 1.6     # MoGe's shapes made this much deeper
FINE = 0.45          # share of the picture's fine detail added on top
CLASSIC_SLOPE = 0.8  # gentle: a stronger slope turns colour specks into fake bumps
AO_STRENGTH = 1.6   # how dark the deepest hollows get (AO = 1 - this x depth below the local average height)
AO_FLOOR = 0.35     # never darker than this, so baked shading can't fight the game's own lights
SLOPE_LOW = 0.15    # mean slope (|xy|/z) under this: the game's light shows almost no relief (flat-painted stone and
                    # planks came out ~0.05 in Swish Craft's pilot, rubble 0.40); bump warns, --slope steepens
SLOPE_GAIN_MAX = 8  # --slope never steepens more than this (a map that flat is mostly noise)


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), np.float64) / 255


def save(a, p):
    Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)).save(p)


def unit(n):
    return n / np.linalg.norm(n, axis=2, keepdims=True)


def high_pass(h, sigma, mode="wrap"):
    h = h - gaussian_filter(h, sigma, mode=mode)
    lo, hi = np.percentile(h, [1, 99])
    return np.clip((h - lo) / (hi - lo + 1e-9), 0, 1)


def normal_from_height(h, strength):
    # OpenGL: +X right, +Y up the image (rows grow downward, hence the sign on dy)
    dx = sobel(h, axis=1, mode="wrap") / 8 * strength * h.shape[1] / 64
    dy = -sobel(h, axis=0, mode="wrap") / 8 * strength * h.shape[0] / 64
    return unit(np.dstack([-dx, -dy, np.ones_like(h)]))


def classic(rgb):
    h = high_pass(gaussian_filter(rgb @ [0.2126, 0.7152, 0.0722], 0.7), 24)
    return h, normal_from_height(h, CLASSIC_SLOPE)


def moge_detail(n, mode="nearest"):
    n = n - np.dstack([gaussian_filter(n[..., i], 32, mode=mode) for i in range(3)]) + [0, 0, 1]
    return unit(n)


def combo(moge_n, classic_n):
    return unit(moge_n * [MOGE_DEPTH, MOGE_DEPTH, 1] + (classic_n - [0, 0, 1]) * [FINE, FINE, 0])


def height_from_normal(n, wrap):
    """Height 0-1 integrated from a normal map: the surface whose slopes best match the normals (Frankot-Chellappa,
    solved in Fourier space). Exact for a seamless picture; otherwise the picture is mirrored to 2 x 2 first so its
    edges meet. Why not MoGe's own depth: on flat-painted and scanned surfaces it is mostly slow drift, and its fine
    detail barely matches its own normals (machining chat 2026-10-30: 88-95% drift, correlation 0.00-0.15), so the
    height and AO came out near blank while the normal map was good."""
    nz = np.maximum(n[..., 2], 0.2)
    p = -n[..., 0] / nz          # height change along a row (OpenGL: +X right)
    q = n[..., 1] / nz           # height change down a column (+Y is up the picture, rows run down)
    rows, cols = p.shape
    if not wrap:                 # mirror: a slope flips sign in the mirrored copy along its own axis
        p = np.block([[p, -p[:, ::-1]], [p[::-1], -p[::-1, ::-1]]])
        q = np.block([[q, q[:, ::-1]], [-q[::-1], -q[::-1, ::-1]]])
    u = 2j * np.pi * np.fft.fftfreq(p.shape[1])[None, :]
    v = 2j * np.pi * np.fft.fftfreq(p.shape[0])[:, None]
    denom = np.abs(u) ** 2 + np.abs(v) ** 2
    denom[0, 0] = 1
    z = (np.conj(u) * np.fft.fft2(p) + np.conj(v) * np.fft.fft2(q)) / denom
    z[0, 0] = 0
    return np.fft.ifft2(z).real[:rows, :cols]


def ambient_occlusion(h, mode="nearest"):
    cavity = np.clip(gaussian_filter(h, 6, mode=mode) - h, 0, None) + 0.5 * np.clip(gaussian_filter(h, 24, mode=mode) - h, 0, None)
    return np.clip(1 - AO_STRENGTH * cavity, AO_FLOOR, 1)


def mean_slope(n):
    """how tilted a normal map is on average, |xy| / z: 0 = flat"""
    return float(np.mean(np.hypot(n[..., 0], n[..., 1]) / np.clip(n[..., 2], 1e-3, None)))


def highpass_normal(n, radius, mode="nearest"):
    """n with the waves wider than `radius` px taken out (bump --highpass): its slopes (x/z, y/z) minus their Gaussian
    blur (sigma = radius), made back into unit normals. Slopes rather than raw xy, because a slope is the height's own
    derivative: this is exactly a high-pass of the surface, so the height and AO integrated from it agree with it.
    Grain, seams and edges stay; broad MoGe relief (a varnished floor reading as crumpled foil) goes."""
    nz = np.maximum(n[..., 2], 0.2)
    p, q = n[..., 0] / nz, n[..., 1] / nz
    p = p - gaussian_filter(p, radius, mode=mode)
    q = q - gaussian_filter(q, radius, mode=mode)
    return unit(np.dstack([p, q, np.ones_like(p)]))


def steepen(n, target):
    """n with its xy scaled so the mean slope reaches target (raised only, gain at most SLOPE_GAIN_MAX). Per pixel,
    so a wrapping map still wraps."""
    s = mean_slope(n)
    g = min(SLOPE_GAIN_MAX, target / max(s, 1e-6))
    return n if g <= 1 else unit(n * [g, g, 1])


def write(cn, moge_n, out_dir, name, mode, slope=None, highpass=None):
    """Returns the normal map's mean slope (before, after --slope). highpass: bump --highpass radius in px, applied
    first, so the height and AO come from the high-passed normal."""
    n = combo(moge_detail(moge_n, mode), cn)
    if highpass:
        n = highpass_normal(n, highpass, mode)
    before = mean_slope(n)
    height = high_pass(height_from_normal(n, mode == "wrap"), 32, mode)   # before --slope: the same shape either way
    if slope:
        n = steepen(n, slope)
    save(n * 0.5 + 0.5, out_dir / f"{name}_normal.png")
    save(np.dstack([height] * 3), out_dir / f"{name}_height.png")
    save(np.dstack([ambient_occlusion(height, mode)] * 3), out_dir / f"{name}_ao.png")
    return before, mean_slope(n)


def finish(picture, moge_normal_png, out_dir, name, slope=None, highpass=None):
    """Writes out_dir/<name>_normal.png (the default style), _height.png (integrated from it, detail only) and _ao.png."""
    _, cn = classic(load(picture))
    return write(cn, load(moge_normal_png) * 2 - 1, out_dir, name, "nearest", slope, highpass)


SHIFTS = {"": (0, 0), "__sx": (0, 1), "__sy": (1, 0), "__sxy": (1, 1)}   # bump --tile: the picture moved by half (rows, columns)


def shifted(a, sy, sx, back=False):
    h, w = a.shape[:2]
    d = -1 if back else 1
    return np.roll(a, (d * sy * (h // 2), d * sx * (w // 2)), axis=(0, 1))


def finish_tiled(picture, maps, out_dir, name, slope=None, highpass=None):
    """Like finish() for a seamless texture. maps[key] = MoGe's normal png for the picture moved by half as
    SHIFTS[key] says: each run is wrong near its own edges, so every spot is taken from the runs where it sits far
    from an edge (weight sin^2 of the position per axis: 1 in the middle, 0 at the edges; the moved run gets cos^2,
    0 where its own edges are). Two runs are not enough: at the middle of the top edge the unmoved run is at its top
    edge and the run moved both ways at its left edge (a hard line through the brick's normal map)."""
    _, cn = classic(load(picture))
    n_sum = 0
    for key, (sy, sx) in SHIFTS.items():
        n = shifted(load(maps[key]) * 2 - 1, sy, sx, back=True)
        h, w = n.shape[:2]
        ay, ax = np.sin(np.pi * (np.arange(h) + 0.5) / h) ** 2, np.sin(np.pi * (np.arange(w) + 0.5) / w) ** 2
        k = np.outer(1 - ay if sy else ay, 1 - ax if sx else ax)
        n_sum = n_sum + k[..., None] * n
    return write(cn, unit(n_sum), out_dir, name, "wrap", slope, highpass)


OBJECT_FINE = 0.25   # objects: share of the classic fine detail on top of MoGe's whole shape (more turns paint grain into blotches)
OBJECT_MIN = 768     # objects smaller than this (longest side) are scaled up 2-4x first, so MoGe has pixels to read
GREY = 128           # sprites are laid on mid grey for MoGe; transparent pixels read as neither near nor far


def prep_object(picture, out_png, plain=False):
    """The picture as MoGe should see it: scaled up if small, laid on mid grey, and with plain=True its colours taken
    out (light and dark kept), so a red door and a white door read as the same shape. Returns the scale used."""
    im = Image.open(picture).convert("RGBA")
    k = int(min(4, max(1, round(OBJECT_MIN / max(im.size)))))
    if k > 1:
        im = im.resize((im.width * k, im.height * k), Image.LANCZOS)
    if plain:
        im = Image.merge("RGBA", (*[im.convert("L")] * 3, im.getchannel("A")))
    bg = Image.new("RGBA", im.size, (GREY, GREY, GREY, 255))
    bg.alpha_composite(im)
    bg.convert("RGB").save(out_png)
    return k


def finish_object(picture, prepped_png, moge_normal_png, out_dir, name):
    """Writes out_dir/<name>_normal.png at the picture's own size: MoGe's whole normal + OBJECT_FINE of the classic
    detail, with the picture's alpha when it has one. No height or AO: MoGe's depth is relative to each picture and
    can't tell sky from wall on a painting."""
    pic = load(prepped_png)
    moge = Image.open(moge_normal_png).convert("RGB").resize(pic.shape[1::-1], Image.LANCZOS)
    _, cn = classic(pic)
    n = unit(unit(np.asarray(moge, np.float64) / 255 * 2 - 1) + (cn - [0, 0, 1]) * [OBJECT_FINE, OBJECT_FINE, 0])
    src = Image.open(picture)
    img = Image.fromarray((np.clip(n * 0.5 + 0.5, 0, 1) * 255 + 0.5).astype(np.uint8)).resize(src.size, Image.LANCZOS)
    n = unit(np.asarray(img, np.float64) / 255 * 2 - 1) * 0.5 + 0.5
    out = (np.clip(n, 0, 1) * 255 + 0.5).astype(np.uint8)
    if "A" in src.getbands():
        out = np.dstack([out, np.asarray(src.convert("RGBA"))[..., 3]])
    Image.fromarray(out).save(out_dir / f"{name}_normal.png")
