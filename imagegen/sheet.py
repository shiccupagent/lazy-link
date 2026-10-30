#!/usr/bin/env python3
"""sheet.py - side-by-side comparison: one row per ask, one column per source.

  sheet.py OUT.png ASKS.json COL=DIR[:label] ...    a column's images are DIR/<ask>.png or DIR/<ask>__<model>.png
  e.g. sheet.py cmp.png asks.json krea2=out/krea2:"Krea 2" zimage=out/zimage:"Z-Image Turbo"

  sheet.py grid OUT.png ASKS.json DIR MODEL [--cols N]   one model's results as a grid: each ask, its
      cutout (if any) on a checkerboard, and asks named tile-* repeated 2x2 so seams show.

A timings.json in the column's DIR adds the seconds per image under each picture.
ASKS.json is the same list imagegen.py run takes; "w" and "h" (optional) set each row's shape.

Needs Pillow on this computer: pip3 install pillow. Nothing else.
"""
import json, sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # --help works without Pillow; the real run checks in main()
    Image = None

CELL_H = 420
PAD = 16
FONT_FILES = [  # first one found wins; Pillow's built-in font is the last resort
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",     # macOS
    "/Library/Fonts/Arial Bold.ttf",                          # macOS, older
    "C:/Windows/Fonts/arialbd.ttf",                           # Windows
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Linux
]


def font(size):
    for f in FONT_FILES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1
        return ImageFont.load_default()


def checker(size, n=24):
    im = Image.new("RGB", size, (235, 235, 235))
    d = ImageDraw.Draw(im)
    for y in range(0, size[1], n):
        for x in range(0, size[0], n):
            if (x // n + y // n) % 2:
                d.rectangle((x, y, x + n - 1, y + n - 1), fill=(200, 200, 200))
    return im


def grid(out, asks, folder, key, ncols=4):
    folder = Path(folder)
    secs = {r["ask"]: r for r in json.load(open(folder / "timings.json"))["runs"] if r["model"] == key} \
        if (folder / "timings.json").exists() else {}
    cells = []  # (label, image)
    for a in asks:
        p = folder / f"{a['name']}__{key}.png"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB")
        t = f"  {secs[a['name']]['seconds']:.1f} s" if a["name"] in secs else ""
        if a["name"].startswith("tile-"):
            w, h = im.size
            tiled = Image.new("RGB", (w * 2, h * 2))
            for dx in (0, w):
                for dy in (0, h):
                    tiled.paste(im, (dx, dy))
            cells.append((f"{a['name']} (2x2 repeat){t}", tiled))
        else:
            cells.append((f"{a['name']}{t}", im))
        c = folder / f"{a['name']}__{key}-cutout.png"
        if c.exists():
            rgba = Image.open(c).convert("RGBA")
            bg = checker(rgba.size)
            bg.paste(rgba, (0, 0), rgba)
            cells.append((f"{a['name']} cutout", bg))
    cw, ch, lab = 480, 360, 34
    rows = (len(cells) + ncols - 1) // ncols
    sheet = Image.new("RGB", (ncols * (cw + PAD) + PAD, rows * (ch + lab + PAD) + PAD), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    small = font(18)
    for i, (label, im) in enumerate(cells):
        x, y = PAD + (i % ncols) * (cw + PAD), PAD + (i // ncols) * (ch + lab + PAD)
        im = im.copy()
        im.thumbnail((cw, ch), Image.LANCZOS)
        sheet.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
        d.text((x + cw // 2, y + ch + lab // 2), label, font=small, fill=(200, 200, 200), anchor="mm")
    sheet.save(out, optimize=True)
    print(out, sheet.size, len(cells), "cells")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    if Image is None:
        sys.exit("sheet.py needs Pillow on this computer: pip3 install pillow")
    if sys.argv[1] == "grid":
        a = sys.argv[2:]
        ncols = int(a[a.index("--cols") + 1]) if "--cols" in a else 4
        return grid(a[0], json.load(open(a[1])), a[2], a[3], ncols)
    out, asks = sys.argv[1], json.load(open(sys.argv[2]))
    cols = []
    for spec in sys.argv[3:]:
        key, rest = spec.split("=", 1)
        d, _, label = rest.partition(":")
        d = Path(d)
        secs = {}
        if (d / "timings.json").exists():
            for r in json.load(open(d / "timings.json"))["runs"]:
                if r["model"] == key:
                    secs[r["ask"]] = r
        cols.append((key, d, label or key, secs))
    widths = {a["name"]: round(CELL_H * a.get("w", 1024) / a.get("h", 1024)) for a in asks}
    col_w = max(widths.values())
    head, foot, left = 60, 30, 170
    W = left + len(cols) * (col_w + PAD) + PAD
    H = head + len(asks) * (CELL_H + foot + PAD) + PAD
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    big, small = font(28), font(18)
    for c, (key, folder, label, secs) in enumerate(cols):
        x = left + PAD + c * (col_w + PAD)
        d.text((x + col_w // 2, head // 2), label, font=big, fill=(240, 240, 240), anchor="mm")
    for r, a in enumerate(asks):
        y = head + r * (CELL_H + foot + PAD)
        d.text((PAD, y + CELL_H // 2), a["name"].replace("-", "\n"), font=big, fill=(200, 200, 200), anchor="lm")
        for c, (key, folder, label, secs) in enumerate(cols):
            x = left + PAD + c * (col_w + PAD)
            p = next((p for p in (folder / f"{a['name']}__{key}.png", folder / f"{a['name']}.png") if p.exists()), None)
            if p is None:
                d.rectangle((x, y, x + col_w, y + CELL_H), outline=(90, 90, 90))
                d.text((x + col_w // 2, y + CELL_H // 2), "missing", font=small, fill=(150, 150, 150), anchor="mm")
                continue
            im = Image.open(p).convert("RGB")
            im.thumbnail((col_w, CELL_H), Image.LANCZOS)
            sheet.paste(im, (x + (col_w - im.width) // 2, y + (CELL_H - im.height) // 2))
            src = Image.open(p).size
            note = f"{src[0]}x{src[1]}"
            if a["name"] in secs:
                s = secs[a["name"]]
                note += f"  ·  {s['seconds']:.1f} s" + (" (incl. model load)" if s["cold"] else "")
            d.text((x + col_w // 2, y + CELL_H + foot // 2), note, font=small, fill=(170, 170, 170), anchor="mm")
    sheet.save(out, optimize=True)
    print(out, sheet.size)


if __name__ == "__main__":
    main()
