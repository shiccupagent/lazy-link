"""Light-map and ambient-occlusion bake for one .glb. Runs inside Blender on the PC:

  blender -b --factory-startup --python-exit-code 1 -P bake_blender.py -- --options options.json --out OUT_DIR

options.json, every key optional:
  glb               input .glb (a path inside the job folder)
  stem              base name for the outputs (default: the input's file name)
  make_test_scene   build a test room with four props, write it as test_room.glb, and bake that
  ceiling           add a ceiling to the test room
  reunwrap          re-run Smart UV on the existing UV set 2. Without it, set 2 is re-run only when
                    it overlaps (over 0.1% of its square) or covers under 20% of the square
  device            "auto" (OPTIX, then CUDA, then CPU) or "cpu"
  mode              "hybrid" (default) or "full". What the light map holds:
                      full:   Cycles direct + indirect light (sun, sky and bounce, already shadowed). For
                              scenes with no realtime lights on that geometry. Use no aoMap with it.
                      hybrid: Cycles indirect light only (the bounce; the sun and sky seen directly are left
                              out). For game scenes that keep realtime lights: the game's own sun and
                              hemisphere stay on, aoMap darkens the corners, the bounce adds colour.
  budget            texel budget as a square side: the maps share budget**2 by surface area (default 4096)
  margin            island margin in pixels, never below 8 (default 8)
  samples           Cycles samples per bake (default 128)
  lights            "auto" (the file's own lights if it has any, else a default sun and sky),
                    "scene" (only the file's own lights and world), "default" (always sun and sky)
  sun_strength, sky_strength  default sun and sky levels when used (default 1.6 and 0.9/pi: the
                    sun and sky of a three.js realtime scene, so the two renders can be compared directly)
  gain              multiplier on the light map before encoding (default 1.0)
  ao_distance       AO ray distance in scene units (default 1.5)
  denoise           Cycles denoising (default true)
  min_size, max_size  per-map size limits in pixels (default 512 and 2048)
  allow_dark        mesh names allowed to bake black or flat (default none). Every other mesh whose AO is black or
                    flat (all 1) fails the job, and in full so does one whose light map is black: no GLB is written,
                    and summary.json says "status": "failed" with the reasons. In hybrid a black light map is a
                    warning (summary.json "warnings"): bounce-only light on a lone prop is near zero. Use allow_dark
                    only for a genuinely unlit mesh.

Materials: only a texture can make a material's alpha (a real cutout such as leaves). A factor alpha with no
texture (glTF baseColorFactor.a, e.g. a prop exported with alpha 0 and MASK) is ignored for the bake, so the mesh
bakes as opaque. The original material is put back before the GLB is written.
Meshes with no UVs: a placeholder UV layer "UVMap" is added first and becomes TEXCOORD_0, so the bake set
(Lightmap) lands in slot 1 (TEXCOORD_1).

OUT_DIR gets <mesh>_lightmap.png (sRGB; full: direct + indirect light, hybrid: indirect light only),
<mesh>_ao.png (linear, one channel; hybrid only, manifest says "do not apply with full"),
<mesh>_lightmap.json ({"mode", "intensity", "colorSpace", "aoMap"}: intensity is the three.js
lightMapIntensity, pi / gain, because three.js divides lightmaps by pi in its diffuse term and the
gain is already in the PNG; aoMap is true for hybrid, false for full), manifest.json,
<stem>_baked.glb (the bake layer is exported as TEXCOORD_1), summary.json ("status": "ok" or "failed",
with the failures, the warnings and the dark_allowed list), and one stdout line
that starts with BAKE_SUMMARY.
"""
import json
import math
import re
import struct
import sys
import time
import zlib
from pathlib import Path

import addon_utils
import bpy
import numpy as np
from mathutils import Vector

# The default lights are the sun and sky of a three.js realtime scene: a sun at three.js (3, 6, 4), which is Blender travel
# direction (-3, 4, -6), and a hemisphere sky of 0xdfe6ef at 0.9. A uniform sky of radiance C * S gives
# irradiance pi * S * C, so the world strength that matches irradiance 0.9 * C is 0.9 / pi.
SUN_TRAVEL = (-3.0, 4.0, -6.0)
SKY_SRGB = (0xDF, 0xE6, 0xEF)
DEFAULTS = dict(glb=None, stem=None, make_test_scene=False, ceiling=False, reunwrap=False, device="auto",
                budget=4096, margin=8, samples=128, lights="auto", sun_strength=1.6, sky_strength=0.9 / math.pi,
                gain=1.0, ao_distance=1.5, denoise=True, min_size=512, max_size=2048, mode="hybrid",
                allow_dark=[])
MODES = ("full", "hybrid")
# Cycles diffuse passes per mode: full keeps direct + indirect (already shadowed), hybrid keeps only the
# bounce, because the game's own realtime sun and hemisphere supply the direct light.
LIGHT_PASSES = {"full": ("DIRECT", "INDIRECT"), "hybrid": ("INDIRECT",)}
WELD_EPS = 1e-5          # metres: vertices this close are one vertex for the UV layout
BAKE_UV_NAME = "Lightmap"
DARK_MEAN = 0.005        # a whole-map mean under this is black: the bake lit nothing
FLAT_MEAN = 0.995        # covered AO texels this high with this little spread: no occlusion was baked
FLAT_SPREAD = 0.01


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


SKY_COLOR = tuple(srgb_to_linear(c) for c in SKY_SRGB) + (1.0,)


def log(*a):
    print("BAKE", *a, flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def value(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    out = Path(value("--out", "bake_out")).resolve()
    opts = dict(DEFAULTS)
    if value("--options"):
        with open(value("--options")) as f:
            opts.update(json.load(f))
    if value("--glb"):
        opts["glb"] = value("--glb")
    if "--make-test-scene" in argv:
        opts["make_test_scene"] = True
    if "--ceiling" in argv:
        opts["ceiling"] = True
    if "--reunwrap" in argv:
        opts["reunwrap"] = True
    if isinstance(opts["allow_dark"], str):
        opts["allow_dark"] = opts["allow_dark"].split(",")
    opts["allow_dark"] = [str(n).strip() for n in opts["allow_dark"] if str(n).strip()]
    unknown = set(opts) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"BAKE unknown option(s): {sorted(unknown)}")
    if opts["mode"] not in MODES:
        raise SystemExit(f"BAKE mode must be one of {list(MODES)}, got {opts['mode']!r}")
    if not opts["make_test_scene"] and not opts["glb"]:
        raise SystemExit("BAKE give --glb FILE or --make-test-scene")
    return out, opts


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


# ---------- scene setup ----------

def reset_scene(opts):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "cycles" not in bpy.context.preferences.addons:
        addon_utils.enable("cycles", default_set=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = int(opts["samples"])
    sc.cycles.use_denoising = bool(opts["denoise"])
    return sc


def pick_device(sc, want):
    """OPTIX, then CUDA, then CPU. Returns (kind, device names)."""
    if want != "cpu":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                hits = [d for d in prefs.devices if d.type == kind]
                if not hits:
                    log(f"device: no {kind} GPU found, trying the next one")
                    continue
                for d in prefs.devices:
                    d.use = d.type == kind
                sc.cycles.device = "GPU"
                names = [d.name for d in hits]
                log(f"device: {kind} GPU {names}")
                return kind, names
            except Exception as e:  # the enum is not registered on this machine
                log(f"device: {kind} unavailable ({e})")
    sc.cycles.device = "CPU"
    log("device: CPU")
    return "CPU", ["CPU"]


def set_denoiser(sc, kind):
    if not sc.cycles.use_denoising:
        return
    choices = ("OPTIX", "OPENIMAGEDENOISE") if kind == "OPTIX" else ("OPENIMAGEDENOISE",)
    for name in choices:
        try:
            sc.cycles.denoiser = name
            log(f"denoiser: {name}")
            return
        except Exception:
            pass
    log("denoiser: Blender default")


def select_only(o):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o


def is_round(o, tol=0.03):
    """True for a sphere: at least 64 vertices, all at one distance from their centre, and vertex normals
    pointing away from it. A box's corners are equidistant too, so the normals test is the one that
    rejects boxes and planes."""
    me = o.data
    if len(me.vertices) < 64:
        return False
    vs = [v.co for v in me.vertices]
    c = sum(vs, Vector()) / len(vs)
    r = [(v - c).length for v in vs]
    mean = sum(r) / len(r)
    if mean <= 0 or max(abs(x - mean) for x in r) > tol * mean:
        return False
    dots = [v.normal.dot((v.co - c).normalized()) for v in me.vertices]
    return sum(dots) / len(dots) >= 0.98


def unwrap(o, layer_name, island_margin, method="smart"):
    """Writes the named UV layer (created if missing). The mesh is welded first: a glTF import keeps one
    copy of each vertex per face, so Smart UV Project sees no shared edges and makes one island per
    face (the test ball: 512 islands covering 4.6% of the square). method "sphere" is Sphere Project."""
    me = o.data
    lay = me.uv_layers.get(layer_name)
    if lay is None:
        lay = me.uv_layers.new(name=layer_name)
    me.uv_layers.active = lay
    select_only(o)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=WELD_EPS)
    if method == "sphere":
        bpy.ops.uv.sphere_project()
    else:
        bpy.ops.uv.smart_project(island_margin=island_margin, angle_limit=math.radians(66), area_weight=0.0,
                                 correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return method


def raster_tri(cnt, a, b, c, n):
    """Adds 1 to each cell of an n x n grid whose centre lies strictly inside UV triangle abc."""
    lo = np.clip(np.floor(np.minimum(np.minimum(a, b), c) * n - 0.5), 0, n - 1).astype(int)
    hi = np.clip(np.ceil(np.maximum(np.maximum(a, b), c) * n + 0.5), 0, n - 1).astype(int)
    xs = (np.arange(lo[0], hi[0] + 1) + 0.5) / n
    ys = (np.arange(lo[1], hi[1] + 1) + 0.5) / n
    X, Y = np.meshgrid(xs, ys)
    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(d) < 1e-14:
        return
    w0 = ((b[1] - c[1]) * (X - c[0]) + (c[0] - b[0]) * (Y - c[1])) / d
    w1 = ((c[1] - a[1]) * (X - c[0]) + (a[0] - c[0]) * (Y - c[1])) / d
    inside = (w0 > 1e-6) & (w1 > 1e-6) & (1.0 - w0 - w1 > 1e-6)
    cnt[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1] += inside


def uv_quality(o, layer_name, n=256):
    """Coverage: % of the UV square under some triangle. Overlap: % of the covered cells under two or
    more. A good set 2 has high coverage and near-zero overlap."""
    me = o.data
    lay = me.uv_layers[layer_name]
    flat = np.empty(len(lay.data) * 2, dtype=np.float64)
    lay.data.foreach_get("uv", flat)
    uv = flat.reshape(-1, 2)
    cnt = np.zeros((n, n), dtype=np.int32)
    for p in me.polygons:
        s = p.loop_start
        for j in range(1, p.loop_total - 1):
            raster_tri(cnt, uv[s], uv[s + j], uv[s + j + 1], n)
    covered = cnt >= 1
    return dict(coverage_pct=round(100.0 * float(covered.mean()), 2),
                overlap_pct=round(100.0 * float((cnt >= 2).sum()) / max(int(covered.sum()), 1), 3))


def add_solid(name, kind, rgb, loc, dims, rot=(0.0, 0.0, 0.0)):
    if kind == "box":
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    elif kind == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=loc)
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=dims[0], depth=dims[1], location=loc)
    else:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=dims[0], location=loc)
    o = bpy.context.active_object
    o.name = name
    o.data.name = name
    if kind == "box":
        o.scale = dims
    elif kind == "plane":
        o.scale = (dims[0], dims[1], 1.0)
    o.rotation_euler = rot
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1.0)
    o.data.materials.append(mat)
    select_only(o)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return o


def build_test_room(ceiling):
    """Floor, three walls (front open), optional ceiling, four props. The cabinet gets a UV set 2
    up front so the bake takes the 'existing UV set 2' path; the others get one from Smart UV."""
    wall = (0.90, 0.90, 0.88)
    add_solid("floor", "plane", (0.78, 0.76, 0.70), (0, 0, 0), (6.0, 6.0))
    add_solid("wall_back", "box", wall, (0, 3.0, 1.4), (6.0, 0.1, 2.8))
    add_solid("wall_left", "box", wall, (-3.0, 0, 1.4), (0.1, 6.0, 2.8))
    add_solid("wall_right", "box", wall, (3.0, 0, 1.4), (0.1, 6.0, 2.8))
    if ceiling:
        add_solid("ceiling", "plane", (0.95, 0.95, 0.95), (0, 0, 2.8), (6.0, 6.0), rot=(math.pi, 0, 0))
    add_solid("table", "box", (0.55, 0.36, 0.20), (0.8, 0.9, 0.375), (1.2, 0.7, 0.75))
    add_solid("stool", "cylinder", (0.20, 0.40, 0.70), (-0.9, -0.6, 0.3), (0.25, 0.6))
    add_solid("ball", "sphere", (0.80, 0.20, 0.20), (1.6, -1.0, 0.3), (0.3,))
    cab = add_solid("cabinet", "box", (0.60, 0.60, 0.62), (-2.7, 1.2, 0.9), (0.4, 1.0, 1.8))
    unwrap(cab, "UV2", 0.02)


def export_glb(path):
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", export_texcoords=True,
                              export_materials="EXPORT", export_image_format="AUTO",
                              use_selection=False, export_apply=False, export_yup=True)


def glb_texcoord1_count(path):
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise SystemExit("BAKE output is not a GLB")
    jlen = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + jlen])
    prims = [p for m in doc.get("meshes", []) for p in m.get("primitives", [])]
    return sum("TEXCOORD_1" in p.get("attributes", {}) for p in prims), len(prims)


def setup_lights(sc, opts):
    mode = opts["lights"]
    has_lights = any(o.type == "LIGHT" for o in sc.objects)
    use_default = mode == "default" or (mode == "auto" and not has_lights)
    if not use_default:
        log(f"lights: the file's own ({'lights present' if has_lights else 'none, world only'})")
        return [], "scene"
    sun_data = bpy.data.lights.new("BakeSun", "SUN")
    sun_data.energy = float(opts["sun_strength"])
    sun = bpy.data.objects.new("BakeSun", sun_data)
    # A sun points along its local -Z, so -Z must point along the travel direction.
    sun.rotation_euler = Vector(SUN_TRAVEL).normalized().to_track_quat("-Z", "Y").to_euler()
    sc.collection.objects.link(sun)
    sky = bpy.data.worlds.new("BakeSky")
    sky.use_nodes = True
    bg = sky.node_tree.nodes["Background"]
    bg.inputs[0].default_value = SKY_COLOR
    bg.inputs[1].default_value = float(opts["sky_strength"])
    sc.world = sky
    log(f"lights: default sun {opts['sun_strength']} + sky {opts['sky_strength']}")
    return [sun], "default"


# ---------- per-mesh work ----------

def prepare_mesh(o):
    if o.data.users > 1:           # linked duplicates: one mesh each, so each gets its own maps
        o.data = o.data.copy()
    if len(o.data.materials) == 0:
        o.data.materials.append(bpy.data.materials.new(o.name + "_mat"))
    for i, m in enumerate(o.data.materials):
        if m is None:
            m = bpy.data.materials.new(o.name + "_mat")
            o.data.materials[i] = m
        m.use_nodes = True


def principled_of(mat):
    if mat is None or mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def texture_upstream(node, seen):
    """True when an image texture feeds this node, directly or through other nodes."""
    if node.name in seen:
        return False
    seen.add(node.name)
    if node.type == "TEX_IMAGE":
        return True
    return any(texture_upstream(link.from_node, seen) for sock in node.inputs for link in sock.links)


def opaque_for_bake(meshes):
    """A material's alpha counts only when a texture drives it (a real cutout). A factor alpha with no texture
    (glTF baseColorFactor.a, e.g. a prop exported with alpha 0 and MASK) would make the surface invisible to Cycles,
    so those materials bake as opaque: the Alpha input is unlinked and set to 1. Base colour is kept, so the
    bounce colour does not change. Returns the records put_back_alpha restores; a material shared by several
    meshes is changed once."""
    done, records = set(), []
    for o in meshes:
        for mat in o.data.materials:
            if mat is None or mat.name in done:
                continue
            done.add(mat.name)
            pb = principled_of(mat)
            if pb is None:
                continue
            alpha = pb.inputs["Alpha"]
            if any(texture_upstream(link.from_node, set()) for link in alpha.links):
                continue
            if not alpha.is_linked and float(alpha.default_value) >= 1.0:
                continue
            rec = dict(mat=mat, alpha=alpha, default=float(alpha.default_value),
                       feeds=[link.from_socket for link in alpha.links])
            for link in list(alpha.links):
                mat.node_tree.links.remove(link)
            alpha.default_value = 1.0
            records.append(rec)
            log(f"opaque for bake: {mat.name} (alpha is a factor, not a texture: {rec['default']:.3g})")
    return records


def put_back_alpha(records):
    """Restores the alpha links and values that opaque_for_bake changed, before the GLB is written."""
    for rec in records:
        mat, alpha = rec["mat"], rec["alpha"]
        for link in list(alpha.links):
            mat.node_tree.links.remove(link)
        for sock in rec["feeds"]:
            mat.node_tree.links.new(sock, alpha)
        alpha.default_value = rec["default"]


def world_area(o):
    m = o.matrix_world
    me = o.data
    total = 0.0
    for p in me.polygons:
        v = [m @ me.vertices[i].co for i in p.vertices]
        for k in range(1, len(v) - 1):
            total += 0.5 * (v[k] - v[0]).cross(v[k + 1] - v[0]).length
    return total


def pow2_size(want, lo, hi):
    s = 2 ** round(math.log2(max(want, 1.0)))
    return int(min(max(s, lo), hi))


def point_materials_at(o, img):
    """Every material of the mesh gets one 'BakeTarget' image node showing img; Cycles bakes into it."""
    for mat in o.data.materials:
        nt = mat.node_tree
        node = nt.nodes.get("BakeTarget")
        if node is None:
            node = nt.nodes.new("ShaderNodeTexImage")
            node.name = "BakeTarget"
            node.label = "BakeTarget"
            node.location = (-800, -600)
        node.image = img
        nt.nodes.active = node


def clear_bake_nodes(o):
    for mat in o.data.materials:
        node = mat.node_tree.nodes.get("BakeTarget") if mat else None
        if node is not None:
            mat.node_tree.nodes.remove(node)


def new_bake_image(name, s):
    img = bpy.data.images.new(name, width=s, height=s, alpha=False, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"   # raw linear values; encoded by us below
    return img


def bake_pass(o, kind, uv_name, margin_px, pass_filter=()):
    select_only(o)
    o.data.uv_layers.active = o.data.uv_layers[uv_name]
    t = time.perf_counter()
    if kind == "diffuse":
        bpy.ops.object.bake(type="DIFFUSE", pass_filter=set(pass_filter), margin=margin_px,
                            margin_type="ADJACENT_FACES", use_clear=True, target="IMAGE_TEXTURES",
                            uv_layer=uv_name)
    else:
        bpy.ops.object.bake(type="AO", margin=margin_px, margin_type="ADJACENT_FACES", use_clear=True,
                            target="IMAGE_TEXTURES", uv_layer=uv_name)
    return time.perf_counter() - t


def read_linear(img, s):
    buf = np.empty(s * s * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    # Blender row 0 is the bottom; PNG row 0 is the top, which is glTF v = 0.
    return buf.reshape(s, s, 4)[::-1, :, :3]


def to_srgb8(x):
    x = np.clip(x, 0.0, 1.0)
    y = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(y * 255).astype(np.uint8)


def to_linear8(x):
    return np.round(np.clip(x, 0.0, 1.0) * 255).astype(np.uint8)


def write_png(path, px, gray):
    h, w = px.shape[0], px.shape[1]
    row = px.reshape(h, -1)
    raw = np.zeros((h, row.shape[1] + 1), dtype=np.uint8)   # filter byte 0 on every row
    raw[:, 1:] = row

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0 if gray else 2, 0, 0, 0)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                           + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b""))


def dark_check(lit, ao, mode):
    """(fails, warns, cov_mean, cov_std) for one mesh. Black: a whole-map mean under DARK_MEAN. Flat: the covered
    AO (ao > 0) is about 1 with almost no spread, so no occlusion was baked; an open plane with nothing around it
    is flat too, and allow_dark covers it. full: a black light map fails. hybrid holds bounce only, so a black
    light map is a warning (little nearby surface to bounce from); a black or flat AO still fails, because then
    the mesh was not seen."""
    fails, warns = [], []
    lit_mean, ao_mean = float(lit.mean()), float(ao.mean())
    if lit_mean < DARK_MEAN:
        if mode == "full":
            fails.append(f"light map mean {lit_mean:.4f} is black (under {DARK_MEAN})")
        else:
            warns.append("bounce-only light map is very dark: little nearby surface to bounce from "
                         f"(mean {lit_mean:.4f}, under {DARK_MEAN})")
    if ao_mean < DARK_MEAN:
        fails.append(f"AO mean {ao_mean:.4f} is black (under {DARK_MEAN})")
    covered = ao[ao > 0.0]
    if covered.size == 0:
        return fails, warns, None, None
    cov_mean, cov_std = float(covered.mean()), float(covered.std())
    if cov_mean > FLAT_MEAN and cov_std < FLAT_SPREAD:
        fails.append(f"AO is flat (covered texels mean {cov_mean:.4f}, spread {cov_std:.4f}): no occlusion baked")
    return fails, warns, round(cov_mean, 4), round(cov_std, 4)


# ---------- main ----------

def main():
    out, opts = parse_args()
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    if opts["make_test_scene"]:
        reset_scene(opts)
        build_test_room(bool(opts["ceiling"]))
        src = out / "test_room.glb"
        export_glb(src)
        log(f"test room written: {src.name}")
        stem = opts["stem"] or "test_room"
    else:
        src = Path(opts["glb"]).resolve()
        stem = opts["stem"] or src.stem
    stem = safe_name(stem)

    sc = reset_scene(opts)
    device, device_names = pick_device(sc, opts["device"])
    set_denoiser(sc, device)
    bpy.ops.import_scene.gltf(filepath=str(src))
    log(f"imported {src.name}")

    meshes = [o for o in sc.objects if o.type == "MESH" and len(o.data.polygons) > 0]
    if not meshes:
        raise SystemExit("BAKE no mesh with faces in the input")
    for o in meshes:
        prepare_mesh(o)
    swapped = opaque_for_bake(meshes)   # put back before the GLB is written

    margin_px = max(8, int(opts["margin"]))
    gain = float(opts["gain"])
    area = {o.name: world_area(o) for o in meshes}
    total_area = sum(area.values())
    if total_area <= 0:
        raise SystemExit("BAKE the input has no surface area")
    size = {o.name: pow2_size(float(opts["budget"]) * math.sqrt(area[o.name] / total_area),
                              int(opts["min_size"]), int(opts["max_size"])) for o in meshes}

    uv, before = {}, {}
    for o in meshes:
        if len(o.data.uv_layers) >= 2:
            layer = o.data.uv_layers[1].name
            before[o.name] = q = uv_quality(o, layer)
            if opts["reunwrap"] or q["overlap_pct"] > 0.1 or q["coverage_pct"] < 20.0:
                why = "forced by --reunwrap" if opts["reunwrap"] else "overlaps or covers under 20%"
                unwrap(o, layer, margin_px / size[o.name], "smart")
                uv[o.name] = ("reunwrapped", layer)
            else:
                why = "kept"
                uv[o.name] = ("existing", layer)
        else:
            if len(o.data.uv_layers) == 0:
                # no UVs at all: this placeholder becomes TEXCOORD_0, so the bake set lands in slot 1
                unwrap(o, "UVMap", margin_px / size[o.name])
            method = "sphere" if is_round(o) else "smart"
            unwrap(o, BAKE_UV_NAME, margin_px / size[o.name], method)
            uv[o.name] = (f"{method}_project", BAKE_UV_NAME)
            why = "no set 2 in the file, so a new one"
        log(f"uv {o.name}: {uv[o.name][0]} ({uv[o.name][1]}), map {size[o.name]} px, {why}")
    after = {o.name: uv_quality(o, uv[o.name][1]) for o in meshes}
    for o in meshes:
        log(f"uv {o.name}: coverage {after[o.name]['coverage_pct']}%, overlap {after[o.name]['overlap_pct']}%")

    added, lights_used = setup_lights(sc, opts)
    if sc.world is None:
        sc.world = bpy.data.worlds.new("BakeWorld")
        sc.world.use_nodes = True
    sc.world.light_settings.distance = float(opts["ao_distance"])

    report = {}
    failures, dark_allowed, warned = [], [], []
    bake_total = 0.0
    mode = opts["mode"]
    log(f"mode {mode}: light map {' + '.join(LIGHT_PASSES[mode]).lower()}"
        + (", aoMap used" if mode == "hybrid" else ", no aoMap"))
    for o in meshes:
        name, s, fn = o.name, size[o.name], safe_name(o.name)
        lm_img = new_bake_image(f"{fn}_lightmap", s)
        point_materials_at(o, lm_img)
        t_lm = bake_pass(o, "diffuse", uv[name][1], margin_px, LIGHT_PASSES[mode])
        lit = read_linear(lm_img, s) * gain
        ao_img = new_bake_image(f"{fn}_ao", s)
        point_materials_at(o, ao_img)
        t_ao = bake_pass(o, "ao", uv[name][1], margin_px)
        clear_bake_nodes(o)
        ao = read_linear(ao_img, s)[:, :, 0]
        fails, warns, cov_mean, cov_std = dark_check(lit, ao, mode)
        write_png(out / f"{fn}_lightmap.png", to_srgb8(lit), gray=False)
        write_png(out / f"{fn}_ao.png", to_linear8(ao), gray=True)
        # three.js divides a lightmap by pi in its diffuse term; the gain is already in the PNG.
        # aoMap belongs with hybrid only: the full light map already holds the corner shadows.
        (out / f"{fn}_lightmap.json").write_text(json.dumps(
            dict(mode=mode, intensity=round(math.pi / gain, 6), colorSpace="sRGB", aoMap=mode == "hybrid")))
        report[name] = dict(size=s, uv=uv[name][0], uv_layer=uv[name][1],
                            uv_before=before.get(name), uv_after=after[name],
                            bake_lightmap_s=round(t_lm, 2), bake_ao_s=round(t_ao, 2),
                            lightmap_mean=round(float(lit.mean()), 4),
                            lightmap_clipped_pct=round(100.0 * float((lit >= 0.999).mean()), 2),
                            ao_mean=round(float(ao.mean()), 4), ao_covered_mean=cov_mean,
                            ao_covered_std=cov_std, dark=fails, warn=warns)
        for w in warns:
            log(f"BAKE WARNING {name}: {w}")
            warned.append(f"{name}: {w}")
        if fails:
            if name in opts["allow_dark"]:
                dark_allowed.append(name)
                log(f"BAKE WARNING {name} allowed by allow_dark: {'; '.join(fails)}")
            else:
                failures.append(f"{name}: {'; '.join(fails)}")
        bake_total += t_lm + t_ao
        for img in (lm_img, ao_img):
            bpy.data.images.remove(img)
        log(f"{name}: {s} px, {mode} light map {t_lm:.1f} s, ao {t_ao:.1f} s")

    put_back_alpha(swapped)
    for o in added:
        bpy.data.objects.remove(o, do_unlink=True)
    typo = sorted(set(opts["allow_dark"]) - {o.name for o in meshes})
    if typo:
        failures.append(f"allow_dark names no mesh in the input: {typo}")
    common = dict(mode=mode, input=src.name, device=device, device_names=device_names, meshes=len(meshes),
                  allow_dark=opts["allow_dark"], dark_allowed=dark_allowed, warnings=warned,
                  opaque_for_bake=[r["mat"].name for r in swapped], maps=report)
    if failures:
        summary = dict(common, status="failed", failures=failures)
        (out / "summary.json").write_text(json.dumps(summary, indent=1))
        print("BAKE_SUMMARY " + json.dumps(summary), flush=True)
        raise SystemExit("BAKE FAILED: " + " | ".join(failures)
                         + ". No GLB written. A mesh that is really unlit can be allowed with --allow-dark NAME.")
    glb_out = out / f"{stem}_baked.glb"
    export_glb(glb_out)
    n_t1, n_prim = glb_texcoord1_count(glb_out)
    log(f"exported {glb_out.name}: TEXCOORD_1 on {n_t1} of {n_prim} primitives")

    manifest = dict(
        mode=mode, uv_channel=1, flip_y=False, glb=glb_out.name,
        lightmap=dict(suffix="_lightmap.png", colorspace="sRGB", sidecar="_lightmap.json",
                      content="direct + indirect" if mode == "full" else "indirect only"),
        ao=dict(suffix="_ao.png", colorspace="linear", channel="R", used=mode == "hybrid",
                note=None if mode == "hybrid" else "do not apply with full"),
        meshes={n: dict(lightmap=f"{safe_name(n)}_lightmap.png", ao=f"{safe_name(n)}_ao.png", size=size[n])
                for n in size})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))

    summary = dict(common, status="ok", failures=[], glb=glb_out.name, bake_seconds=round(bake_total, 2),
                   total_seconds=round(time.perf_counter() - t0, 2), samples=int(opts["samples"]),
                   denoise=bool(opts["denoise"]), lights=lights_used, budget=int(opts["budget"]),
                   margin_px=margin_px, gain=gain, ao_distance=float(opts["ao_distance"]),
                   sun_strength=float(opts["sun_strength"]), sky_strength=float(opts["sky_strength"]),
                   reunwrap=bool(opts["reunwrap"]),
                   texcoord1_primitives=n_t1, primitives=n_prim)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print("BAKE_SUMMARY " + json.dumps(summary), flush=True)


main()
