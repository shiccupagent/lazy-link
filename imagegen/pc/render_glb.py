# Blender (background) script: one contact strip per .glb, four turntable views, so a 3D result can be
# eye-checked without a 3D viewer. Runs on the PC: blender -b -P render_glb.py -- IN_DIR OUT_DIR
import math, os, sys
import bpy
from mathutils import Vector

src, dst = sys.argv[sys.argv.index("--") + 1:][:2]
os.makedirs(dst, exist_ok=True)


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            for d in prefs.devices:
                d.use = d.type == kind
            sc.cycles.device = "GPU"
            break
        except Exception:
            continue
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    sc.render.resolution_x = sc.render.resolution_y = 640
    sc.render.film_transparent = False
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.82, 0.83, 0.86, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    sc.world = world
    return sc


for name in sorted(f for f in os.listdir(src) if f.lower().endswith(".glb")):
    sc = setup_scene()
    bpy.ops.import_scene.gltf(filepath=os.path.join(src, name))
    meshes = [o for o in sc.objects if o.type == "MESH"]
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    center, size = (lo + hi) / 2, max((hi - lo).length, 1e-3)
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(35))
    sc.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = 50
    sc.collection.objects.link(cam)
    sc.camera = cam
    dist = size * 1.7
    for i, ang in enumerate((30, 120, 210, 300)):
        a = math.radians(ang)
        cam.location = center + Vector((math.cos(a) * dist, math.sin(a) * dist, size * 0.45))
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = os.path.join(dst, f"{name[:-4]}_view{i}.png")
        bpy.ops.render.render(write_still=True)
    tris = sum(len(o.data.polygons) for o in meshes)
    print(f"rendered {name}: {len(meshes)} mesh(es), {tris} faces, size {size:.3f}", flush=True)
