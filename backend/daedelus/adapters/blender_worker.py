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
GEOM = ("MESH", "CURVE")
MOD_TYPES = {"decimate": "DECIMATE", "solidify": "SOLIDIFY", "array": "ARRAY", "mirror": "MIRROR",
             "subdivision": "SUBSURF", "bevel": "BEVEL", "taper": "SIMPLE_DEFORM"}


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
        if o.type not in GEOM:
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
    if obj.type not in GEOM or not obj.data.materials or obj.data.materials[0] is None:
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
        elif m.type == "SUBSURF":
            d.update(levels=m.levels, render_levels=m.render_levels)
        elif m.type == "DECIMATE":
            d.update(ratio=r5(m.ratio))
        elif m.type == "SOLIDIFY":
            d.update(thickness=r5(m.thickness))
        elif m.type == "ARRAY":
            d.update(count=m.count, offset=[r5(v) for v in m.relative_offset_displace])
        elif m.type == "MIRROR":
            d.update(axis=[bool(v) for v in m.use_axis])
        out.append(d)
    return out


def state_hash(obj):
    """Hash of the object's *own* state (not its children)."""
    h = hashlib.sha256()
    h.update(obj.type.encode())
    for row in obj.matrix_local:
        h.update(json.dumps([r5(v) for v in row]).encode())
    if obj.type in GEOM:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        mesh = ev.to_mesh()
        for v in mesh.vertices:
            h.update(json.dumps([r5(v.co.x), r5(v.co.y), r5(v.co.z)]).encode())
        ev.to_mesh_clear()
        h.update(json.dumps(material_props(obj), sort_keys=True).encode())
        if obj.type == "MESH":
            h.update(json.dumps([p.use_smooth for p in obj.data.polygons[:1]]).encode())
    h.update(json.dumps(modifier_props(obj), sort_keys=True).encode())
    return h.hexdigest()


def inspect():
    objs = comp_objects()
    components, states, measurements, properties = [], {}, {}, {}
    for cid, o in sorted(objs.items()):
        parent = o.parent.get(ID_PROP) if o.parent is not None else None
        components.append({"id": cid, "name": o.get("daedelus_name", o.name),
                           "kind": {"MESH": "mesh", "CURVE": "curve"}.get(o.type, "group"),
                           "parent_id": parent, "native_ref": o.name,
                           "metadata": {"blender_type": o.type}})
        states[cid] = state_hash(o)
        bb = world_bbox(subtree(o))
        m = {}
        if bb:
            mn, mx = bb
            m = {"width": r5(mx.x - mn.x), "depth": r5(mx.y - mn.y), "height": r5(mx.z - mn.z),
                 "min_z": r5(mn.z), "max_z": r5(mx.z)}
        meshes = [x for x in subtree(o) if x.type in GEOM]
        m["object_count"] = len(subtree(o))
        m["vertex_count"] = sum(len(x.data.vertices) for x in meshes if x.type == "MESH")
        m["poly_count"] = evaluated_polys(meshes)
        measurements[cid] = m
        mods = {mm["name"]: mm for x in meshes for mm in modifier_props(x)}
        properties[cid] = {
            "scale": [r5(s) for s in o.scale],
            "location": [r5(s) for s in o.location],
            "material": material_props(meshes[0]) if meshes else None,
            "taper": mods.get("dd_taper", {}).get("factor", 0.0),
            "bevel": mods.get("dd_bevel", {}).get("width", 0.0),
            "subdivision": mods.get("dd_subdivision", {}).get("levels", 0),
            "modifiers": sorted({mm["type"] for x in meshes for mm in modifier_props(x)}),
            "own_modifiers": modifier_props(o),
            "dimensions": {k: m.get(k) for k in ("width", "depth", "height")},
            "rotation_euler": [r5(math.degrees(a)) for a in o.rotation_euler],
            "smooth": bool(o.type == "MESH" and len(o.data.polygons) and
                           o.data.polygons[0].use_smooth),
            "children": sorted(c.get(ID_PROP) for c in o.children if ID_PROP in c.keys()),
            "curve": ({"resolution": o.data.resolution_u, "bevel_resolution":
                       o.data.bevel_resolution, "radius": r5(o.data.bevel_depth)}
                      if o.type == "CURVE" else None),
            "own_poly_count": evaluated_polys([o]) if o.type in GEOM else 0,
        }
    return {"components": components, "states": states, "measurements": measurements,
            "properties": properties}


def evaluated_polys(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    n = 0
    for o in objs:
        ev = o.evaluated_get(dg)
        mesh = ev.to_mesh()
        n += len(mesh.polygons)
        ev.to_mesh_clear()
    return n


def make_primitive(spec):
    prim = spec.get("primitive", "cube")
    size = spec.get("size", [1, 1, 1])
    loc = spec.get("location", [0, 0, 0])
    segs = int(spec.get("vertices", 24))
    if prim == "empty":
        bpy.ops.object.empty_add(type="PLAIN_AXES", location=loc)
    elif prim == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=segs, radius=0.5, depth=1.0, location=loc)
    elif prim == "cone":
        bpy.ops.mesh.primitive_cone_add(vertices=segs, radius1=0.5,
                                        radius2=0.5 * float(spec.get("top_ratio", 0.0)),
                                        depth=1.0, location=loc)
    elif prim == "uv_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=max(8, segs), ring_count=max(4, segs // 2),
                                             radius=0.5, location=loc)
    elif prim == "ico_sphere":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=int(spec.get("detail", 2)),
                                              radius=0.5, location=loc)
    elif prim == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=0.5,
                                         minor_radius=0.5 * float(spec.get("thickness", 0.25)),
                                         major_segments=max(8, segs), minor_segments=12,
                                         location=loc)
    elif prim == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=loc)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    obj = bpy.context.active_object
    if spec.get("rotation_euler"):
        obj.rotation_euler = [math.radians(a) for a in spec["rotation_euler"]]
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
        if o.type in GEOM:
            o.data.materials.clear()
            o.data.materials.append(mat)
            n += 1
    return f"material {params['base_color']} on {n} object(s)"


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


def make_branch(spec):
    """A curve with a round bevel: trunk, branch, cable, handle... Stays an editable curve."""
    pts = spec["points"]
    cu = bpy.data.curves.new(spec.get("name", spec["id"]), type="CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = float(spec.get("radius", 0.05))
    cu.bevel_resolution = int(spec.get("bevel_resolution", 3))
    cu.resolution_u = int(spec.get("resolution", 8))
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY" if spec.get("straight") else "BEZIER")
    radii = spec.get("radii") or [1.0] * len(pts)
    if sp.type == "BEZIER":
        sp.bezier_points.add(len(pts) - 1)
        for bp, p, r in zip(sp.bezier_points, pts, radii):
            bp.co = p
            bp.handle_left_type = bp.handle_right_type = "AUTO"
            bp.radius = float(r)
    else:
        sp.points.add(len(pts) - 1)
        for pp, p, r in zip(sp.points, pts, radii):
            pp.co = (*p, 1.0)
            pp.radius = float(r)
    obj = bpy.data.objects.new(spec.get("name", spec["id"]), cu)
    bpy.context.scene.collection.objects.link(obj)
    obj[ID_PROP] = spec["id"]
    obj["daedelus_name"] = spec.get("name", spec["id"])
    parent = spec.get("parent")
    if parent:
        p = comp_objects().get(parent)
        if p is None:
            raise ValueError(f"parent component not found: {parent}")
        obj.parent = p
        obj.matrix_parent_inverse = p.matrix_world.inverted()
    return obj


def op_add_branch(_obj, params):
    if params["id"] in comp_objects():
        return f"component {params['id']} already exists (idempotent no-op)"
    make_branch(params)
    return f"created curve {params['id']} with {len(params['points'])} points"


def op_extrude_faces(obj, params):
    """Extrude the faces facing ``axis`` in steps; each step can scale (taper) and twist."""
    import bmesh  # type: ignore

    if obj.type != "MESH":
        raise ValueError("extrude_faces needs a mesh component")
    axis = Vector({"Z": (0, 0, 1), "-Z": (0, 0, -1), "X": (1, 0, 0), "-X": (-1, 0, 0),
                   "Y": (0, 1, 0), "-Y": (0, -1, 0)}[params.get("axis", "Z")])
    steps = int(params.get("steps", 1))
    dist = float(params["distance"]) / steps
    scale = float(params.get("scale_per_step", 1.0))
    twist = math.radians(float(params.get("twist_per_step", 0.0)))
    offset = params.get("offset_per_step") or [0.0, 0.0, 0.0]
    from mathutils import Matrix  # type: ignore

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    faces = [f for f in bm.faces if f.normal.dot(axis) > 0.7]
    if not faces:
        bm.free()
        raise ValueError(f"no faces facing {params.get('axis', 'Z')}")
    for _ in range(steps):
        res = bmesh.ops.extrude_face_region(bm, geom=faces)
        verts = [e for e in res["geom"] if isinstance(e, bmesh.types.BMVert)]
        faces = [e for e in res["geom"] if isinstance(e, bmesh.types.BMFace)]
        bmesh.ops.translate(bm, verts=verts, vec=axis * dist + Vector(offset))
        c = sum((v.co for v in verts), Vector()) / max(1, len(verts))
        if scale != 1.0:
            bmesh.ops.scale(bm, verts=verts, vec=Vector([scale if abs(a) < 0.5 else 1.0
                                                         for a in axis]),
                            space=Matrix.Translation(-c))
        if twist:
            bmesh.ops.rotate(bm, verts=verts, cent=c, matrix=Matrix.Rotation(twist, 3, axis))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return f"extruded {steps} step(s) of {dist:.3f} along {params.get('axis', 'Z')}" + \
        (f", scale {scale}/step" if scale != 1 else "") + \
        (f", twist {params.get('twist_per_step')} deg/step" if twist else "")


def op_set_curve_detail(obj, params):
    n = 0
    for o in subtree(obj, set(params.get("exclude", []))):
        if o.type != "CURVE":
            continue
        if "resolution" in params:
            o.data.resolution_u = int(params["resolution"])
        if "bevel_resolution" in params:
            o.data.bevel_resolution = int(params["bevel_resolution"])
        if "radius" in params:
            o.data.bevel_depth = float(params["radius"])
        n += 1
    return f"curve detail {params} on {n} curve(s)"


def op_set_subdivision(obj, params):
    n = 0
    for o in subtree(obj, set(params.get("exclude", []))):
        if o.type != "MESH":
            continue
        lv = int(params["levels"])
        if lv == 0:
            m = o.modifiers.get("dd_subdivision")
            if m:
                o.modifiers.remove(m)
        else:
            m = _modifier(o, "dd_subdivision", "SUBSURF")
            m.levels = lv
            m.render_levels = int(params.get("render_levels", lv))
        n += 1
    return f"subdivision levels {params['levels']} on {n} mesh(es)"


def op_set_modifier(obj, params):
    kind = params["type"]
    mtype = MOD_TYPES[kind]
    remove = bool(params.get("remove"))
    n = 0
    for o in subtree(obj, set(params.get("exclude", []))):
        if o.type != "MESH":
            continue
        name = f"dd_{kind}"
        if remove:
            m = o.modifiers.get(name)
            if m:
                o.modifiers.remove(m)
            n += 1
            continue
        m = _modifier(o, name, mtype)
        if kind == "decimate":
            m.ratio = float(params.get("ratio", 0.5))
        elif kind == "solidify":
            m.thickness = float(params.get("thickness", 0.02))
        elif kind == "array":
            m.count = int(params.get("count", 2))
            m.relative_offset_displace = params.get("offset", [1.0, 0.0, 0.0])
        elif kind == "mirror":
            ax = params.get("axis", "X")
            m.use_axis = (ax == "X", ax == "Y", ax == "Z")
        n += 1
    return f"{'removed' if remove else 'set'} {kind} modifier on {n} mesh(es)"


def op_set_parent(obj, params):
    target = params.get("parent")
    if target:
        p = comp_objects().get(target)
        if p is None:
            raise ValueError(f"parent component not found: {target}")
        if p == obj or obj in p.children_recursive:
            raise ValueError("parenting would create a cycle")
        mw = obj.matrix_world.copy()
        obj.parent = p
        obj.matrix_world = mw
    else:
        mw = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = mw
    return f"parent -> {target or '(none)'}"


def op_set_shading(obj, params):
    n = 0
    for o in subtree(obj, set(params.get("exclude", []))):
        if o.type == "MESH":
            for p in o.data.polygons:
                p.use_smooth = bool(params["smooth"])
            n += 1
    return f"{'smooth' if params['smooth'] else 'flat'} shading on {n} mesh(es)"


def op_remove_component(obj, params):
    objs = subtree(obj)
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    return f"removed {len(objs)} object(s)"


OPS = {
    "add_branch": op_add_branch,
    "extrude_faces": op_extrude_faces,
    "set_subdivision": op_set_subdivision,
    "set_curve_detail": op_set_curve_detail,
    "set_modifier": op_set_modifier,
    "set_parent": op_set_parent,
    "set_shading": op_set_shading,
    "remove_component": op_remove_component,
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
            if name not in ("add_primitive", "add_branch"):
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
    objs = [o for o in scene.objects if o.type in GEOM]
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
                                  use_selection=False, export_apply=True, export_extras=True)
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
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_apply=True,
                                  export_extras=True)
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
    with open(job_path, encoding="utf-8") as f:
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
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(res, f)


main()
