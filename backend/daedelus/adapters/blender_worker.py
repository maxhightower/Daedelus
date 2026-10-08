"""Runs *inside* Blender (``blender -b [file] --python blender_worker.py -- job.json``).

Every component is a Blender object carrying a stable ``daedelus_id`` custom
property, so component identity survives renames and revisions.
All operations are declarative and idempotent.
"""

import hashlib
import json
import math
import sys
import traceback

import bpy  # type: ignore
from mathutils import Vector  # type: ignore

ID_PROP = "daedelus_id"


def r5(x):
    return round(float(x), 5) + 0.0


def comp_objects():
    return {o[ID_PROP]: o for o in bpy.data.objects if ID_PROP in o.keys()}


def subtree(obj, exclude=()):
    out = []
    stack = [obj]
    while stack:
        o = stack.pop()
        if o.get(ID_PROP) in exclude:
            continue
        out.append(o)
        stack.extend(o.children)
    return out


def world_bbox(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        if o.type != "MESH":
            continue
        ev = o.evaluated_get(dg)
        mesh = ev.to_mesh()
        mw = ev.matrix_world
        pts.extend(mw @ v.co for v in mesh.vertices)
        ev.to_mesh_clear()
    if not pts:
        return None
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def material_props(obj):
    if obj.type != "MESH" or not obj.data.materials or obj.data.materials[0] is None:
        return None
    mat = obj.data.materials[0]
    props = {"name": mat.name}
    if mat.use_nodes and mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            c = bsdf.inputs["Base Color"].default_value
            props["base_color"] = "#%02x%02x%02x" % tuple(
                max(0, min(255, int(round(lin_to_srgb(c[i]) * 255)))) for i in range(3))
            props["roughness"] = r5(bsdf.inputs["Roughness"].default_value)
            props["metallic"] = r5(bsdf.inputs["Metallic"].default_value)
    return props


def lin_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def srgb_to_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_lin(h):
    h = h.lstrip("#")
    return tuple(srgb_to_lin(int(h[i:i + 2], 16) / 255.0) for i in (0, 2, 4)) + (1.0,)


def modifier_props(obj):
    out = []
    for m in obj.modifiers:
        d = {"name": m.name, "type": m.type}
        if m.type == "SIMPLE_DEFORM":
            d.update(method=m.deform_method, factor=r5(m.factor), axis=m.deform_axis)
        elif m.type == "BEVEL":
            d.update(width=r5(m.width), segments=m.segments)
        out.append(d)
    return out


def state_hash(obj):
    """Hash of the object's *own* state (not its children)."""
    h = hashlib.sha256()
    h.update(obj.type.encode())
    for row in obj.matrix_local:
        h.update(json.dumps([r5(v) for v in row]).encode())
    if obj.type == "MESH":
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        mesh = ev.to_mesh()
        for v in mesh.vertices:
            h.update(json.dumps([r5(v.co.x), r5(v.co.y), r5(v.co.z)]).encode())
        ev.to_mesh_clear()
        h.update(json.dumps(material_props(obj), sort_keys=True).encode())
    h.update(json.dumps(modifier_props(obj), sort_keys=True).encode())
    return h.hexdigest()


def inspect():
    objs = comp_objects()
    components, states, measurements, properties = [], {}, {}, {}
    for cid, o in sorted(objs.items()):
        parent = o.parent.get(ID_PROP) if o.parent is not None else None
        components.append({"id": cid, "name": o.get("daedelus_name", o.name),
                           "kind": "mesh" if o.type == "MESH" else "group",
                           "parent_id": parent, "native_ref": o.name,
                           "metadata": {"blender_type": o.type}})
        states[cid] = state_hash(o)
        bb = world_bbox(subtree(o))
        m = {}
        if bb:
            mn, mx = bb
            m = {"width": r5(mx.x - mn.x), "depth": r5(mx.y - mn.y), "height": r5(mx.z - mn.z),
                 "min_z": r5(mn.z), "max_z": r5(mx.z)}
        meshes = [x for x in subtree(o) if x.type == "MESH"]
        m["object_count"] = len(subtree(o))
        m["vertex_count"] = sum(len(x.data.vertices) for x in meshes)
        measurements[cid] = m
        mods = {mm["name"]: mm for x in meshes for mm in modifier_props(x)}
        properties[cid] = {
            "scale": [r5(s) for s in o.scale],
            "location": [r5(s) for s in o.location],
            "material": material_props(meshes[0]) if meshes else None,
            "taper": mods.get("dd_taper", {}).get("factor", 0.0),
            "bevel": mods.get("dd_bevel", {}).get("width", 0.0),
            "dimensions": {k: m.get(k) for k in ("width", "depth", "height")},
        }
    return {"components": components, "states": states, "measurements": measurements,
            "properties": properties}


def make_primitive(spec):
    prim = spec.get("primitive", "cube")
    size = spec.get("size", [1, 1, 1])
    loc = spec.get("location", [0, 0, 0])
    if prim == "empty":
        bpy.ops.object.empty_add(type="PLAIN_AXES", location=loc)
    elif prim == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=spec.get("vertices", 24), radius=0.5,
                                            depth=1.0, location=loc)
    elif prim == "uv_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=loc)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    obj = bpy.context.active_object
    if prim != "empty":
        # bake size into mesh data so object scale stays a free, editable parameter
        obj.scale = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        cuts = int(spec.get("subdivide", 0))
        if cuts > 0:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.subdivide(number_cuts=cuts)
            bpy.ops.object.mode_set(mode="OBJECT")
    obj.name = spec.get("name", spec["id"])
    obj[ID_PROP] = spec["id"]
    obj["daedelus_name"] = spec.get("name", spec["id"])
    parent = spec.get("parent")
    if parent:
        p = comp_objects().get(parent)
        if p is None:
            raise ValueError(f"parent component not found: {parent}")
        mw = obj.matrix_world.copy()
        obj.parent = p
        obj.matrix_world = mw
    return obj


def op_set_dimensions(obj, params):
    bb = world_bbox(subtree(obj))
    if bb is None:
        raise ValueError("component has no geometry to measure")
    mn, mx = bb
    cur = {"width": mx.x - mn.x, "depth": mx.y - mn.y, "height": mx.z - mn.z}
    axes = {"width": 0, "depth": 1, "height": 2}
    keep_ground = params.get("keep_ground", True)
    for key, idx in axes.items():
        tgt = params.get(key)
        if tgt is None or cur[key] <= 1e-9:
            continue
        obj.scale[idx] *= float(tgt) / cur[key]
    bpy.context.view_layer.update()
    if keep_ground:
        nb = world_bbox(subtree(obj))
        if nb:
            obj.location.z += mn.z - nb[0].z
    return f"dimensions -> {{{', '.join(f'{k}: {params.get(k)}' for k in axes if params.get(k))}}}"


def op_set_material(obj, params):
    exclude = set(params.get("exclude", []))
    name = f"dd_mat_{obj[ID_PROP]}"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = hex_to_lin(params["base_color"])
    if "roughness" in params:
        bsdf.inputs["Roughness"].default_value = float(params["roughness"])
    if "metallic" in params:
        bsdf.inputs["Metallic"].default_value = float(params["metallic"])
    n = 0
    for o in subtree(obj, exclude):
        if o.type == "MESH":
            o.data.materials.clear()
            o.data.materials.append(mat)
            n += 1
    return f"material {params['base_color']} on {n} mesh(es)"


def _modifier(o, name, mtype):
    m = o.modifiers.get(name)
    if m is None:
        m = o.modifiers.new(name=name, type=mtype)
    return m


def op_set_taper(obj, params):
    n = 0
    for o in subtree(obj, set(params.get("exclude", []))):
        if o.type != "MESH":
            continue
        m = _modifier(o, "dd_taper", "SIMPLE_DEFORM")
        m.deform_method = "TAPER"
        m.deform_axis = params.get("axis", "Z")
        m.factor = float(params["factor"])
        n += 1
    return f"taper {params['factor']} on {n} mesh(es)"


def op_set_bevel(obj, params):
    n = 0
    for o in subtree(obj, set(params.get("exclude", []))):
        if o.type != "MESH":
            continue
        m = _modifier(o, "dd_bevel", "BEVEL")
        m.width = float(params["width"])
        m.segments = int(params.get("segments", 2))
        n += 1
    return f"bevel {params['width']} on {n} mesh(es)"


def op_set_transform(obj, params):
    if "location" in params:
        obj.location = params["location"]
    if "rotation_euler" in params:
        obj.rotation_euler = [math.radians(a) for a in params["rotation_euler"]]
    if "scale" in params:
        obj.scale = params["scale"]
    return "transform set"


def op_add_primitive(_obj, params):
    if params["id"] in comp_objects():
        return f"component {params['id']} already exists (idempotent no-op)"
    make_primitive(params)
    return f"created {params.get('primitive', 'cube')} {params['id']}"


OPS = {
    "set_dimensions": op_set_dimensions,
    "set_material": op_set_material,
    "set_taper": op_set_taper,
    "set_bevel": op_set_bevel,
    "set_transform": op_set_transform,
    "add_primitive": op_add_primitive,
}


def apply(job):
    results = []
    ok = True
    for i, op in enumerate(job["operations"]):
        name = op["op"]
        try:
            fn = OPS[name]
            obj = None
            if name != "add_primitive":
                obj = comp_objects().get(op.get("component_id"))
                if obj is None:
                    raise ValueError(f"component not found: {op.get('component_id')}")
            detail = fn(obj, op.get("params", {}))
            bpy.context.view_layer.update()
            results.append({"index": i, "op": name, "component_id": op.get("component_id"),
                            "status": "applied", "detail": detail})
        except Exception as exc:  # report precisely, stop: partial plans are rolled back
            ok = False
            results.append({"index": i, "op": name, "component_id": op.get("component_id"),
                            "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
            break
    if ok:
        bpy.ops.wm.save_mainfile(filepath=job["blend_path"], compress=False)
    return {"ok": ok, "results": results}


def setup_preview_scene():
    scene = bpy.context.scene
    objs = [o for o in scene.objects if o.type == "MESH"]
    bb = world_bbox(objs)
    center, radius = Vector((0, 0, 0)), 1.0
    if bb:
        center = (bb[0] + bb[1]) / 2
        radius = max((bb[1] - bb[0]).length / 2, 0.1)
    cam_data = bpy.data.cameras.new("dd_preview_cam")
    cam = bpy.data.objects.new("dd_preview_cam", cam_data)
    scene.collection.objects.link(cam)
    direction = Vector((1.0, -1.45, 0.85)).normalized()
    cam.location = center + direction * radius * 3.1
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.lens = 50
    scene.camera = cam
    sun_data = bpy.data.lights.new("dd_preview_sun", type="SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("dd_preview_sun", sun_data)
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(30))
    scene.collection.objects.link(sun)
    world = scene.world or bpy.data.worlds.new("dd_world")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.55, 0.57, 0.6, 1.0)
        bg.inputs[1].default_value = 0.6
    # ground plane below the scene for contact/readability
    if bb:
        bpy.ops.mesh.primitive_plane_add(size=radius * 8, location=(center.x, center.y, bb[0].z))
        plane = bpy.context.active_object
        plane.name = "dd_preview_ground"
        gm = bpy.data.materials.new("dd_preview_ground")
        gm.use_nodes = True
        b = next(n for n in gm.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        b.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1)
        plane.data.materials.append(gm)


def preview(job):
    out = {}
    if job.get("glb_path"):
        bpy.ops.export_scene.gltf(filepath=job["glb_path"], export_format="GLB",
                                  use_selection=False, export_apply=True)
        out["glb"] = job["glb_path"]
    if job.get("png_path"):
        setup_preview_scene()
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = int(job.get("samples", 24))
        scene.cycles.use_denoising = False
        scene.cycles.seed = 0
        scene.render.resolution_x, scene.render.resolution_y = job.get("size", [480, 360])
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = job["png_path"]
        bpy.ops.render.render(write_still=True)
        out["png"] = job["png_path"]
    return {"ok": True, "previews": out}


def export(job):
    fmt = job["format"]
    path = job["path"]
    if fmt == "glb":
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_apply=True)
    elif fmt == "obj":
        bpy.ops.wm.obj_export(filepath=path, apply_modifiers=True)
    elif fmt == "stl":
        bpy.ops.wm.stl_export(filepath=path, apply_modifiers=True)
    else:
        raise ValueError(f"unsupported export format {fmt}")
    return {"ok": True, "path": path}


def create(job):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for spec in job.get("components", []):
        make_primitive(spec)
    bpy.ops.wm.save_mainfile(filepath=job["blend_path"], compress=False)
    return {"ok": True}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    job_path, result_path = argv[0], argv[1]
    with open(job_path) as f:
        job = json.load(f)
    try:
        action = job["action"]
        if action == "create":
            res = create(job)
            res.update(inspect())
        elif action == "inspect":
            res = inspect()
        elif action == "apply":
            res = apply(job)
            res.update(inspect())
        elif action == "preview":
            res = preview(job)
        elif action == "export":
            res = export(job)
        else:
            raise ValueError(f"unknown action {action}")
    except Exception as exc:
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc()}
    with open(result_path, "w") as f:
        json.dump(res, f)


main()
