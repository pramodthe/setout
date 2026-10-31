"""Small builders injected into generated scripts so they do not have to drive bpy.

Imported inside Blender. ``bpy`` is imported when a helper runs, not at import
time, so the module can be loaded by tests that do not have Blender.
"""
from __future__ import annotations

import json


def mm(value) -> float:
    """Millimetres to Blender metres."""
    return float(value) / 1000.0


def box_mm(name, size, location=(0, 0, 0), *, collection, dims=None, material=None,
           build_order=0, color=(0.7, 0.7, 0.7, 1.0), metallic=0.0, roughness=0.5):
    """Axis-aligned box. ``size`` and ``location`` are millimetres.

    ``location`` is the minimum corner. The object origin is the centre.
    """
    import bmesh
    import bpy

    sx, sy, sz = (mm(size[0]), mm(size[1]), mm(size[2]))
    ox, oy, oz = (mm(location[0]), mm(location[1]), mm(location[2]))
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for vert in bm.verts:
        vert.co.x *= sx
        vert.co.y *= sy
        vert.co.z *= sz
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (ox + sx / 2.0, oy + sy / 2.0, oz + sz / 2.0)
    bpy.context.scene.collection.objects.link(obj)
    return add_part(name, obj, collection=collection, dims=dims, material=material,
                    build_order=build_order, color=color, metallic=metallic, roughness=roughness)


def extrude_profile(name, points, depth, *, location=(0, 0, 0), collection, dims=None,
                    material=None, build_order=0, color=(0.7, 0.7, 0.7, 1.0),
                    metallic=0.0, roughness=0.5):
    """Extrude a closed (x, z) millimetre profile along +Y by ``depth`` millimetres.

    ``location`` offsets the section. The object origin is the vertex centroid.
    """
    import bmesh
    import bpy

    if len(points) < 3:
        raise ValueError("extrude_profile needs at least 3 points")
    ox, oy, oz = (float(location[0]), float(location[1]), float(location[2]))
    section = [((float(x) + ox) / 1000.0, (float(z) + oz) / 1000.0) for x, z in points]
    y0 = oy / 1000.0
    y1 = y0 + float(depth) / 1000.0
    cx = sum(x for x, _z in section) / len(section)
    cz = sum(z for _x, z in section) / len(section)
    cy = (y0 + y1) / 2.0
    local = [(x - cx, z - cz) for x, z in section]
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    near = [bm.verts.new((x, y0 - cy, z)) for x, z in local]
    far = [bm.verts.new((x, y1 - cy, z)) for x, z in local]
    bm.verts.ensure_lookup_table()
    bm.faces.new(near)
    bm.faces.new(list(reversed(far)))
    count = len(local)
    for index in range(count):
        nxt = (index + 1) % count
        bm.faces.new((near[index], near[nxt], far[nxt], far[index]))
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (cx, cy, cz)
    bpy.context.scene.collection.objects.link(obj)
    return add_part(name, obj, collection=collection, dims=dims, material=material,
                    build_order=build_order, color=color, metallic=metallic, roughness=roughness)


def add_part(name, obj, *, collection, dims=None, material=None, build_order=0,
             color=(0.7, 0.7, 0.7, 1.0), metallic=0.0, roughness=0.5):
    """Name an object, move it into ``collection``, and set the contract props."""
    import bpy

    obj.name = name
    holder = _collection(collection)
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    holder.objects.link(obj)
    obj["dims"] = json.dumps(dict(dims or {}))
    obj["build_order"] = int(build_order)
    obj["material"] = str(material or name)
    obj["component_ref"] = ""
    _principled(obj, obj["material"], color, metallic, roughness)
    return obj


def _collection(name: str):
    import bpy

    found = bpy.data.collections.get(name)
    if found is None:
        found = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(found)
    return found


def _principled(obj, material_name: str, color, metallic, roughness) -> None:
    import bpy

    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(material_name)
        mat.use_nodes = True
        bsdf = next((node for node in mat.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = _rgba(color)
            bsdf.inputs["Metallic"].default_value = float(metallic)
            bsdf.inputs["Roughness"].default_value = float(roughness)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def _rgba(color) -> tuple:
    values = tuple(color or (0.7, 0.7, 0.7, 1.0))
    if len(values) == 3:
        return (*values, 1.0)
    return tuple(values[:4])
