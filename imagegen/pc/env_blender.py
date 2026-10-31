"""Equirectangular HDR environment map for a venue, a sky, or both. Runs inside Blender on the PC:

  blender -b --factory-startup --python-exit-code 1 -P env_blender.py -- --options options.json --out OUT_DIR

This file is copied into the job folder as it is, so it imports nothing from the Mac side.

options.json (written by env.py; every key present):
  venue   file name of the venue .glb inside the job folder, or null (sky only)
  at      [x, y, z] camera position in glTF (three.js) units, or null (sky only: the camera sits at the origin)
  sky     null, or {"sun_az": AZ, "sun_el": EL, "turbidity": T or null}
            AZ: 0 = glTF -Z, 90 = +X, clockwise seen from above; EL: 0 = horizon, 90 = zenith.
            turbidity (1 to 10) forces Hosek-Wilkie, the only sky model that reads it.
  clamp   Cycles indirect light clamp (default 20)
  width   render width in pixels; the height is half of it
  render  file name of the raw render inside OUT_DIR (env.py reads it back)

What it does: Cycles on the GPU when one is found (OptiX, then CUDA, then CPU), 256 samples, OpenImageDenoise,
Standard view transform (no look, exposure 0, gamma 1), float Radiance HDR output so the values stay scene-linear.
The camera is an equirectangular panorama with rotation (90, 0, -90) degrees: the image centre (u 0.5) looks at +X,
u 0.75 at +Z, u 0.25 at -Z, and both edges at -X. Row 0 is up. Checked end to end on Blender 5.1.2 with markers at
+X, +Z, -Z and +Y: the file's first scanline is the top, so env.py reads the rows as written, with no flip.

Outputs in OUT_DIR: the raw render (options "render"), summary.json, and one stdout line that starts with ENV_SUMMARY.
The job fails with "status": "failed" before rendering when there is no sky and no light (no sun or lamp above zero,
no emissive material that reaches the output).
"""
import json
import math
import sys
import time
from pathlib import Path

try:  # Blender provides bpy. Without it (the Mac tests) only the pure helpers below can run.
    import addon_utils
    import bpy
except ImportError:
    addon_utils = None
    bpy = None

SAMPLES = 256
DENOISER = "OPENIMAGEDENOISE"
VIEW_TRANSFORM = "Standard"
CAM_ROT = (math.radians(90.0), 0.0, math.radians(-90.0))
# The Sky Texture models in the order they are preferred. "Nishita" is Single Scattering in Blender 4 and later.
SKY_ORDER = ("MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "HOSEK_WILKIE", "PREETHAM")


# ---------- pure helpers (testable without Blender) ----------

def at_to_blender(at):
    """glTF / three.js (x, y, z) to Blender (x, -z, y): the same axis change the glTF importer makes."""
    x, y, z = (float(v) for v in at)
    return (x, -z, y)


def pick_sky_type(available, turbidity=None):
    """The Sky Texture model to build, from the identifiers this Blender offers (read at run time).
    With a turbidity: Hosek-Wilkie, the only model that reads it; raises when this Blender has none.
    Without one: Multiple Scattering, then Single Scattering (Nishita), then Hosek-Wilkie, then Preetham."""
    have = list(available)
    if turbidity is not None:
        if "HOSEK_WILKIE" in have:
            return "HOSEK_WILKIE"
        raise RuntimeError(f"this Blender has no Hosek-Wilkie sky, so turbidity cannot be set (offers {have})")
    for name in SKY_ORDER:
        if name in have:
            return name
    raise RuntimeError(f"no usable Sky Texture model in this Blender (offers {have})")


# ---------- Blender helpers (copies of the bake's light census; bake_blender.py is left as it is) ----------

def log(*a):
    print("ENV", *a, flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def value(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    out = Path(value("--out", "env_out")).resolve()
    opts = {"venue": None, "at": None, "sky": None, "clamp": 20.0, "width": 512, "render": "render.hdr"}
    if value("--options"):
        with open(value("--options")) as f:
            opts.update(json.load(f))
    unknown = set(opts) - {"venue", "at", "sky", "clamp", "width", "render"}
    if unknown:
        raise SystemExit(f"ENV unknown option(s): {sorted(unknown)}")
    return out, opts


def reset_scene(clamp):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "cycles" not in bpy.context.preferences.addons:
        addon_utils.enable("cycles", default_set=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = SAMPLES
    sc.cycles.sample_clamp_indirect = float(clamp)
    sc.cycles.sample_clamp_direct = 0.0          # direct light (the sun) is not clamped
    sc.cycles.use_denoising = True
    sc.render.film_transparent = False
    sc.view_settings.view_transform = VIEW_TRANSFORM
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
    sc.view_settings.gamma = 1.0
    sc.render.image_settings.file_format = "HDR"
    return sc


def pick_device(sc):
    """OPTIX, then CUDA, then CPU. Returns (kind, device names)."""
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


def set_denoiser(sc):
    """OpenImageDenoise, as the job asks. If this Blender cannot set it, the job fails; it does not fall back."""
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = DENOISER
    for layer in sc.view_layers:
        layer.cycles.use_denoising = True
    log(f"denoiser: {DENOISER}")


def socket_positive(sock):
    """True when a socket feeds real light: linked (its value is not known here, so it counts), or its value is
    above zero (a colour counts by its largest channel)."""
    if sock.is_linked:
        return True
    value = sock.default_value
    level = max(tuple(value)[:3]) if hasattr(value, "__len__") else float(value)
    return level > 0.0


def upstream(node, seen):
    """Adds every node that feeds this one, directly or through other nodes, to seen."""
    if node.name in seen:
        return seen
    seen.add(node.name)
    for sock in node.inputs:
        for link in sock.links:
            upstream(link.from_node, seen)
    return seen


def emits(mat):
    """True when an emitter reaches the material output: an Emission shader, or a Principled BSDF with emission."""
    tree = mat.node_tree
    if tree is None:
        return False
    live = set()
    for node in tree.nodes:
        if node.type == "OUTPUT_MATERIAL":
            upstream(node, live)
    for node in tree.nodes:
        if node.name not in live:
            continue
        if node.type == "EMISSION":
            col, strength = node.inputs["Color"], node.inputs["Strength"]
        elif node.type == "BSDF_PRINCIPLED":
            col, strength = node.inputs.get("Emission Color"), node.inputs.get("Emission Strength")
            if col is None or strength is None:
                continue
        else:
            continue
        if socket_positive(col) and socket_positive(strength):
            return True
    return False


def census(sc):
    """(suns, lamps, emissive) of the scene as imported: suns and lamps are lights above zero energy, emissive is
    the number of materials on the meshes that emit light to their output."""
    lights = [o.data for o in sc.objects if o.type == "LIGHT"]
    suns = sum(1 for lt in lights if lt.type == "SUN" and lt.energy > 0.0)
    lamps = sum(1 for lt in lights if lt.type != "SUN" and lt.energy > 0.0)
    mats = {m.name: m for o in sc.objects if o.type == "MESH" for m in o.data.materials if m is not None}
    emissive = sum(1 for m in mats.values() if emits(m))
    return suns, lamps, emissive


# ---------- scene ----------

def build_world(sc, sky):
    """sky None: a black world, so only the venue's own lights light it. Otherwise the Sky Texture: the model is
    picked from this Blender's own list, the sun has no disc (sun_disc off), and its position is set in radians."""
    world = bpy.data.worlds.new("EnvWorld")
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    sc.world = world
    if sky is None:
        bg.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        bg.inputs["Strength"].default_value = 0.0
        return None
    turbidity = sky.get("turbidity")
    available = [i.identifier for i in bpy.types.ShaderNodeTexSky.bl_rna.properties["sky_type"].enum_items]
    kind = pick_sky_type(available, turbidity)
    node = nt.nodes.new("ShaderNodeTexSky")
    node.sky_type = kind
    node.sun_disc = False
    node.sun_rotation = math.radians(float(sky["sun_az"]))
    node.sun_elevation = math.radians(float(sky["sun_el"]))
    if turbidity is not None:
        node.turbidity = float(turbidity)
    nt.links.new(node.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0
    log(f"sky: {kind}, sun az {sky['sun_az']} el {sky['sun_el']}"
        + (f", turbidity {turbidity}" if turbidity is not None else ""))
    return {"skyType": kind, "sunAz": float(sky["sun_az"]), "sunEl": float(sky["sun_el"]),
            "turbidity": None if turbidity is None else float(turbidity), "skyTypesOffered": available}


def build_camera(sc, location):
    cd = bpy.data.cameras.new("EnvCam")
    cd.type = "PANO"
    cd.panorama_type = "EQUIRECTANGULAR"
    cd.clip_start = 0.001
    cd.clip_end = 100000.0      # the venue can be larger than the default clip
    cam = bpy.data.objects.new("EnvCam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.location = location
    cam.rotation_euler = CAM_ROT
    return cam


def render_to(sc, width, path):
    sc.render.resolution_x = int(width)
    sc.render.resolution_y = int(width) // 2
    sc.render.resolution_percentage = 100
    sc.render.filepath = str(path)
    t0 = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    if not path.is_file():
        raise RuntimeError(f"the render wrote no file at {path.name}")
    log(f"rendered {sc.render.resolution_x}x{sc.render.resolution_y} in {time.perf_counter() - t0:.1f} s")


def write_summary(out, summary):
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print("ENV_SUMMARY " + json.dumps(summary), flush=True)


# ---------- main ----------

def main():
    out, opts = parse_args()
    out.mkdir(parents=True, exist_ok=True)
    summary = {"status": "failed", "failures": [], "warnings": [], "venue": opts["venue"], "at": opts["at"],
               "sky": opts["sky"], "clamp": float(opts["clamp"]), "width": int(opts["width"]),
               "samples": SAMPLES, "viewTransform": VIEW_TRANSFORM, "look": "None", "exposure": 0.0,
               "gamma": 1.0, "denoiser": DENOISER, "blenderVersion": bpy.app.version_string,
               "skyType": None, "sun": None, "turbidity": None, "device": None, "deviceNames": None,
               "census": None, "renderFile": opts["render"]}
    try:
        sc = reset_scene(opts["clamp"])
        summary["device"], summary["deviceNames"] = pick_device(sc)
        set_denoiser(sc)
        sky = build_world(sc, opts["sky"])
        if sky:
            summary.update(skyType=sky["skyType"], sun={"az": sky["sunAz"], "el": sky["sunEl"]},
                           turbidity=sky["turbidity"], skyTypesOffered=sky["skyTypesOffered"])
        if opts["venue"]:
            if opts["at"] is None:
                raise RuntimeError("a venue needs a camera position (at)")
            venue = Path(opts["venue"]).resolve()     # Blender runs from the job folder, as bake does
            if not venue.is_file():
                raise RuntimeError(f"venue file {venue.name} is not in the job folder")
            bpy.ops.import_scene.gltf(filepath=str(venue))
            log(f"imported {venue.name}")
        suns, lamps, emissive = census(sc)
        summary["census"] = {"suns": suns, "lamps": lamps, "emissive": emissive, "sky": sky is not None}
        log(f"light: {suns} sun, {lamps} lamps, {emissive} emissive, sky {'on' if sky else 'off'}")
        if sky is None and suns == 0 and lamps == 0 and emissive == 0:
            raise RuntimeError("scene has no light: no sky, 0 suns, 0 lamps, 0 emissive; add --sky or a lit venue")
        if sky is not None and opts["venue"] and suns == 0 and lamps == 0 and emissive == 0:
            summary["warnings"].append("venue has no sun, lamp or emissive material: only the sky lights it")
        location = at_to_blender(opts["at"]) if opts["venue"] else (0.0, 0.0, 0.0)
        build_camera(sc, location)
        log(f"camera at Blender {tuple(round(v, 3) for v in location)}")
        render_to(sc, opts["width"], out / opts["render"])
        summary["status"] = "ok"
    except Exception as e:
        summary["failures"].append(f"{type(e).__name__}: {e}")
        write_summary(out, summary)
        raise SystemExit("ENV FAILED: " + summary["failures"][-1]) from e
    write_summary(out, summary)


if __name__ == "__main__":
    main()
