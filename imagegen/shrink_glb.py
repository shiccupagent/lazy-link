#!/usr/bin/env python3
"""shrink_glb.py IN.glb OUT.glb [--px 512] [--q 88] - game-size copy of a generated model: every texture resized so its
longest side is at most PX and stored as JPEG (core glTF, no extension needed) instead of the generator's lossless PNG.
Geometry is untouched. Needs Pillow on this computer (pip3 install pillow); nothing else.
Caveat: textures are saved as JPEG, so any transparency inside a texture is lost (TRELLIS output is opaque).
Also used by `imagegen.py shrink GLB... --out DIR`."""
import argparse, io, json, struct
from PIL import Image


def shrink(src, dst, px=512, q=88):
    """Writes the shrunk copy to dst and returns its size in MB."""
    with open(src, "rb") as f:
        b = f.read()
    jl = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jl])
    bin0 = 20 + jl + 8
    binary = b[bin0:bin0 + struct.unpack("<I", b[20 + jl:24 + jl])[0]]
    imgviews = {i["bufferView"]: n for n, i in enumerate(j.get("images", []))}
    out, views = bytearray(), []
    for v_i, v in enumerate(j["bufferViews"]):
        data = binary[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        if v_i in imgviews:
            im = Image.open(io.BytesIO(data)).convert("RGB")
            if max(im.size) > px:  # longest side = px, whichever way the picture is turned
                s = px / max(im.size)
                im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            j["images"][imgviews[v_i]]["mimeType"] = "image/jpeg"
        while len(out) % 4:
            out.append(0)
        nv = {k: x for k, x in v.items() if k != "byteOffset"}
        nv["byteOffset"] = len(out)
        nv["byteLength"] = len(data)
        out += data
        views.append(nv)
    while len(out) % 4:
        out.append(0)
    j["bufferViews"] = views
    j["buffers"] = [{"byteLength": len(out)}]
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    glb = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(out))
           + struct.pack("<II", len(js), 0x4E4F534A) + js
           + struct.pack("<II", len(out), 0x004E4942) + out)
    with open(dst, "wb") as f:
        f.write(glb)
    return len(glb) / 1e6


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("src"); p.add_argument("dst")
    p.add_argument("--px", type=int, default=512); p.add_argument("--q", type=int, default=88)
    a = p.parse_args()
    print(f"{a.dst}: {shrink(a.src, a.dst, a.px, a.q):.2f} MB")


if __name__ == "__main__":
    main()
