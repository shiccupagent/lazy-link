#!/usr/bin/env python3
"""bumpmaps.py - bump maps for a surface picture (floor, wall, ground...), the default style ("MoGe-2 shapes + a touch of
fine detail", picked in a look test): MoGe-2's real shapes (brick faces, mortar, plank seams; run on the PC by
`imagegen.py make maps`) plus a touch of the picture's own fine detail (grain, pits).
Needs numpy, scipy and Pillow on the computer that runs it (the Mac side; the PC only runs MoGe-2).
  classic(rgb)        -> height, normal from the picture's light/dark detail (alone it turns colour specks into fake bumps)
  moge_detail(normal) -> MoGe's normal map with any overall tilt removed (MoGe reads a texture as a camera photo)
  combo(moge, classic)-> the picked style
Normal maps are OpenGL (+Y up), as three.js and glTF expect; load them with colorSpace = NoColorSpace.
`imagegen.py bump PICTURE... --out DIR` runs the whole thing: DIR/<name>_normal.png (+ _height.png) per picture."""
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, sobel

MOGE_DEPTH = 1.6     # MoGe's shapes made this much deeper
FINE = 0.45          # share of the picture's fine detail added on top
CLASSIC_SLOPE = 0.8  # gentle: a stronger slope turns colour specks into fake bumps


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), np.float64) / 255


def save(a, p):
    Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)).save(p)


def unit(n):
    return n / np.linalg.norm(n, axis=2, keepdims=True)


def high_pass(h, sigma):
    h = h - gaussian_filter(h, sigma, mode="wrap")
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


def moge_detail(n):
    n = n - np.dstack([gaussian_filter(n[..., i], 32, mode="nearest") for i in range(3)]) + [0, 0, 1]
    return unit(n)


def combo(moge_n, classic_n):
    return unit(moge_n * [MOGE_DEPTH, MOGE_DEPTH, 1] + (classic_n - [0, 0, 1]) * [FINE, FINE, 0])


def finish(picture, moge_normal_png, moge_depth_png, out_dir, name):
    """Writes out_dir/<name>_normal.png (the picked style) and <name>_height.png (MoGe depth, detail only)."""
    _, cn = classic(load(picture))
    save(combo(moge_detail(load(moge_normal_png) * 2 - 1), cn) * 0.5 + 0.5, out_dir / f"{name}_normal.png")
    save(np.dstack([high_pass(load(moge_depth_png)[..., 0], 32)] * 3), out_dir / f"{name}_height.png")
