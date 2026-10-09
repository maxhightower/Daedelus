"""Dump a .blend's objects for NATIVE-1 comparisons. Runs inside Blender:

    blender -b FILE.blend --factory-startup --python tools/native_validation/blend_inspect.py -- OUT.json
    python tools/native_validation/blend_inspect.py --diff BEFORE.json AFTER.json

Per object: Daedelus component id (``daedelus_id`` custom property), parent, transform,
dimensions, materials (base colour, roughness), modifiers (type and key settings), mesh
vertex/face counts and a hash of evaluated vertex positions (so modifier effects count).
"""
from __future__ import annotations

import hashlib
import json
import sys


def dump(out: str) -> None:
    import bpy  # noqa: PLC0415 (only inside Blender)

    deps = bpy.context.evaluated_depsgraph_get()
    objs = {}
    for o in bpy.data.objects:
        rec = {"type": o.type, "daedelus_id": o.get("daedelus_id"), "daedelus_name": o.get("daedelus_name"),
               "parent": o.parent.name if o.parent else None,
               "location": [round(v, 5) for v in o.location],
               "rotation": [round(v, 5) for v in o.rotation_euler],
               "scale": [round(v, 5) for v in o.scale],
               "dimensions": [round(v, 5) for v in o.dimensions],
               "materials": [], "modifiers": []}
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if not m:
                continue
            mat = {"name": m.name}
            if m.use_nodes and m.node_tree and "Principled BSDF" in m.node_tree.nodes:
                p = m.node_tree.nodes["Principled BSDF"]
                mat["base_color"] = [round(v, 4) for v in p.inputs["Base Color"].default_value]
                mat["roughness"] = round(p.inputs["Roughness"].default_value, 4)
            rec["materials"].append(mat)
        for md in o.modifiers:
            mod = {"name": md.name, "type": md.type}
            for attr in ("deform_method", "factor", "angle", "deform_axis", "width", "segments",
                         "levels", "render_levels", "thickness"):
                if hasattr(md, attr):
                    v = getattr(md, attr)
                    mod[attr] = round(v, 5) if isinstance(v, float) else v
            rec["modifiers"].append(mod)
        if o.type == "MESH":
            ev = o.evaluated_get(deps)
            me = ev.to_mesh()
            h = hashlib.sha256()
            for v in me.vertices:
                h.update(("%.5f,%.5f,%.5f;" % tuple(v.co)).encode())
            rec["mesh"] = {"vertices": len(me.vertices), "faces": len(me.polygons),
                           "evaluated_hash": h.hexdigest()[:16]}
            ev.to_mesh_clear()
        objs[o.name] = rec
    info = {"blender": bpy.app.version_string, "file": bpy.data.filepath,
            "scene": bpy.context.scene.name, "render_engine": bpy.context.scene.render.engine,
            "objects": objs}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    print(f"blend_inspect: {len(objs)} objects -> {out}")


def diff(a_path: str, b_path: str) -> int:
    a = json.load(open(a_path, encoding="utf-8"))["objects"]
    b = json.load(open(b_path, encoding="utf-8"))["objects"]
    key = lambda d: {(v.get("daedelus_id") or n): v for n, v in d.items()}  # noqa: E731
    ka, kb = key(a), key(b)
    changed = 0
    for k in sorted(set(ka) | set(kb)):
        if k not in ka or k not in kb:
            print(f"{k}: {'added' if k in kb else 'removed'}")
            changed += 1
            continue
        fields = [f for f in ka[k] if ka[k][f] != kb[k].get(f)]
        print(f"{k}: {'unchanged' if not fields else 'CHANGED ' + ', '.join(fields)}")
        changed += bool(fields)
    return 0


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if args and args[0] == "--diff":
        sys.exit(diff(args[1], args[2]))
    dump(args[0])
