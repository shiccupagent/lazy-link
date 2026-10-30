#!/usr/bin/env python3
"""tiles.py - make a surface picture seamless (repeat it and no seam shows), `imagegen.py tile`. Runs on the Mac, no AI.
The classic best-cut: the picture's last strip (OVERLAP of its width) is laid over its first strip, and the cut between
them follows the path where the two already look alike (mortar lines, grain), so the join hides in the texture. Done
across, then down (the down cut starts and ends at the same place, so it wraps across too). The picture loses OVERLAP
of its size and is scaled back up. FLATTEN evens out big light-to-dark drift first, which would otherwise show as a
checkerboard when the tile repeats.
  seamless(picture, out)  -> the seamless copy, same size
  repeat(png, out)        -> 2 x 2 copies side by side, to look at the joins
  seam(img)               -> how much the edges jump when the picture repeats, against an ordinary step inside it
                             (about 1 = no visible seam; a natural line that lands on the edge, like a gap
                             between floor planks, scores high too: look at the 2 x 2)
Tried first (brick): shift by half and have the picture model repaint a band over the old seams (masked
img2img, pc/batch.py `init`/`mask`). It scored well (3.9 -> 1.1) but looked worse: the band drew bigger, oranger
bricks that showed as a grid, and where the band met the picture's edges top and bottom were repainted apart."""
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

OVERLAP = 0.15   # share of the picture laid over itself to find the cut (more = more room for a good path, less picture)
FLATTEN = 0.25   # light drift wider than this share of the picture is evened out (0 = off)


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), np.float64) / 255


def save(a, p, size=None):
    img = Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
    (img.resize(size, Image.LANCZOS) if size else img).save(p)


def seam(a):
    """Mean step across the wrap edges, over the mean step between neighbouring pixels inside."""
    g = a.mean(axis=2)
    inside = (np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean()) / 2
    edge = (np.abs(g[0] - g[-1]).mean() + np.abs(g[:, 0] - g[:, -1]).mean()) / 2
    return edge / (inside + 1e-9)


def flatten(a, share=FLATTEN):
    """Even out light drift wider than `share` of the picture, keeping the overall colour."""
    if not share:
        return a
    big = gaussian_filter(a, (share * a.shape[0], share * a.shape[1], 0), mode="reflect")
    return a - big + big.mean(axis=(0, 1))


def cut_path(err):
    """Cheapest path down `err` (rows x overlap columns), moving at most one column per row, ending where it started."""
    rows, o = err.shape
    cost = np.full((o, o), np.inf)            # cost[start, x]: every start column at once
    cost[np.arange(o), np.arange(o)] = err[0]
    back = np.zeros((rows, o, o), np.int8)
    for i in range(1, rows):
        c = np.stack([np.roll(cost, 1, 1), cost, np.roll(cost, -1, 1)])   # arriving from x-1, x, x+1
        c[0][:, 0] = c[2][:, -1] = np.inf
        k = c.argmin(0)
        cost = np.take_along_axis(c, k[None], 0)[0] + err[i][None]
        back[i] = k - 1
    s = int(np.argmin(cost[np.arange(o), np.arange(o)]))
    path, x = np.zeros(rows, int), s
    for i in range(rows - 1, -1, -1):
        path[i] = x
        x += int(back[i, s, x])
    return path


def wrap_across(a, o, feather=2.0):
    """Seamless left to right: the last o columns laid over the first o, cut along the best path. Width - o."""
    w = a.shape[1]
    end, start = a[:, w - o:], a[:, :o]
    err = gaussian_filter(((end - start) ** 2).sum(axis=2), 1.0)
    err[:, :2] = err[:, -2:] = np.inf   # a cut on the outer columns would skip the overlap and leave the old seam
    p = cut_path(err)
    m = gaussian_filter((np.arange(o)[None, :] >= p[:, None]).astype(np.float64), (0, feather))[..., None]
    return np.concatenate([end * (1 - m) + start * m, a[:, o:w - o]], axis=1)


def seamless(picture, out, overlap=OVERLAP, flat=FLATTEN):
    a = flatten(load(picture), flat)
    o = int(min(a.shape[:2]) * overlap)
    t = wrap_across(a, o)
    t = wrap_across(t.transpose(1, 0, 2), o).transpose(1, 0, 2)
    save(t, out, (a.shape[1], a.shape[0]))
    return seam(load(picture)), seam(load(out))


def repeat(png, out):
    Image.fromarray(np.tile(np.asarray(Image.open(png).convert("RGB")), (2, 2, 1))).save(out)
