"""Export the source Blender assembly, with UVs/materials, for the Anatomy page.

Run export_anatomy(path) in Blender. Only generated web assets are written;
source objects, materials, textures, and animation are left intact.
"""
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import bpy
import numpy as np

CENTER = np.array([-29.7837925, 10.2510214, 2.2069609])
CONVERT = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])


def export_anatomy(output_directory):
    scene = bpy.context.scene
    previous_frame = scene.frame_current
    scene.frame_set(1)
    output = Path(output_directory)
    (output / "textures").mkdir(parents=True, exist_ok=True)
    excluded = {"ID27551", "ID27569", "ID27577"}
    objects = sorted((o for o in scene.objects if o.type == "MESH" and o.data.polygons
                      and o.name not in excluded), key=lambda o: o.name)
    materials, material_lookup, textures = [], {}, {}
    positions, normals, uvs, offsets, layers, features = [], [], [], [], [], []
    indices, face_materials, parts = [], [], []
    vertex_base = 0

    def material_index(material):
        key = material.name if material else "default"
        if key in material_lookup:
            return material_lookup[key]
        color, roughness, metalness, texture = [0.72, 0.72, 0.69], 0.8, 0, None
        if material and material.use_nodes:
            bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf:
                color = list(bsdf.inputs["Base Color"].default_value)[:3]
                roughness = float(bsdf.inputs["Roughness"].default_value)
                metalness = float(bsdf.inputs["Metallic"].default_value)
                links = bsdf.inputs["Base Color"].links
                image_node = links[0].from_node if links else None
                if image_node and image_node.type == "TEX_IMAGE" and image_node.image:
                    im = image_node.image
                    if im.name not in textures:
                        payload = bytes(im.packed_file.data) if im.packed_file else Path(bpy.path.abspath(im.filepath)).read_bytes()
                        extension = ".png" if payload.startswith(b"\x89PNG") else ".jpg"
                        filename = "textures/" + hashlib.sha256(payload).hexdigest()[:16] + extension
                        (output / filename).write_bytes(payload)
                        textures[im.name] = filename
                    texture = textures[im.name]
                    color = [1, 1, 1]
                    # The importer supplied diffuse maps, not PBR metal channels.
                    if re.search("metal|galvan|alumin", im.name, re.I):
                        metalness, roughness = 0.65, 0.42
                    elif re.search("wood|stud|grain|advantech|osb", im.name, re.I):
                        metalness, roughness = 0, 0.82
        entry = {"color": color, "roughness": max(0.35, roughness), "metalness": metalness, "texture": texture}
        # Share materials by their actual values, not the importer's opaque IDs.
        existing = next((i for i, item in enumerate(materials) if item == entry), None)
        index = existing if existing is not None else len(materials)
        if existing is None:
            materials.append(entry)
        material_lookup[key] = index
        return index

    try:
        for o in objects:
            ancestry = []
            parent = o.parent
            while parent:
                ancestry.append(parent.name)
                parent = parent.parent
            images = [n.image.name for m in o.data.materials if m and m.use_nodes
                      for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
            context = " ".join(ancestry + images).lower()
            if re.search(r"electri|breaker|panel_guts|panel_interior|panel_break|200.?a|meter|outlet|wire|carlo|halo_|plumb|pipe|drain|duct|elbow|valvula|sump|ceiling_fan|sparsam", context):
                category = 2
            elif re.search(r"insul|tyvek", context):
                category = 4
            elif re.search(r"stud|truss|lvl|beam|simpson|joist|lumber|rebar|footing|concrete|wood_cherry|wood_osb", context):
                category = 1
            elif re.search(r"groundcover|veget|maguey|asphalt", context):
                category = 3
            else:
                category = 0
            mesh = o.data
            mesh.calc_loop_triangles()
            triangle_vertices = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
            triangle_loops = np.empty_like(triangle_vertices)
            triangle_mats = np.empty(len(mesh.loop_triangles), dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", triangle_vertices)
            mesh.loop_triangles.foreach_get("loops", triangle_loops)
            mesh.loop_triangles.foreach_get("material_index", triangle_mats)
            vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", vertices)
            vertices = vertices.reshape(-1, 3)
            corner_normals = np.empty(len(mesh.corner_normals) * 3, dtype=np.float32)
            mesh.corner_normals.foreach_get("vector", corner_normals)
            corner_normals = corner_normals.reshape(-1, 3)
            matrix = np.array(o.matrix_world, dtype=np.float64)
            world = (vertices @ matrix[:3, :3].T + matrix[:3, 3] - CENTER) @ CONVERT.T
            normal_matrix = np.linalg.inv(matrix[:3, :3]).T
            ns = corner_normals[triangle_loops] @ normal_matrix.T @ CONVERT.T
            ns /= np.maximum(np.linalg.norm(ns, axis=1, keepdims=True), 1e-10)
            uv = np.zeros((len(mesh.loops), 2), dtype=np.float32)
            if mesh.uv_layers.active:
                mesh.uv_layers.active.data.foreach_get("uv", uv.ravel())
            corner_positions = world[triangle_vertices]
            corner_uv = uv[triangle_loops]
            # These imported beam/web UVs sample only a few pixels across metres
            # of timber. Repair their presentation mapping in the export only.
            if "Smart_LVL" in ancestry or ("Difference-036" in ancestry and "Wood_OSB_0.jpg" in images):
                dominant = np.argmax(np.abs(ns), axis=1)
                u = np.where(dominant == 0, corner_positions[:, 2], corner_positions[:, 0])
                v = np.where(dominant == 1, corner_positions[:, 2], corner_positions[:, 1])
                u_scale, v_scale = (1.2, 0.6) if "Smart_LVL" in ancestry else (0.25, 0.25)
                corner_uv = np.column_stack((u / u_scale, v / v_scale))
            values = np.concatenate([corner_positions, ns, corner_uv], axis=1).astype(np.float32)
            # Do not round positions: this collapsed thin source layers in v1.
            unique, inverse = np.unique(values, axis=0, return_inverse=True)
            tri = inverse.reshape(-1, 3)
            if np.linalg.det(matrix[:3, :3]) < 0:
                tri = tri[:, [0, 2, 1]]
            count = len(unique)
            offset = np.array(o.get("explode_world_offset", [0, 0, 0])) @ CONVERT.T
            label = next((n for n in ancestry if not re.match(r"^(group|instance|component|sketchup|difference|row|inner|left_side|right_side|nailer|tab)", n, re.I)), o.name)
            feature = 0
            if "group_1" in ancestry:
                feature = 5
            if "Smart_LVL" in ancestry:
                feature, label = 3, "Supporting LVL beam"
            if "Simpson_LUS210" in ancestry:
                feature, label = 1, "Simpson_LUS210"
            # Three source groups form the web and two flanges of the same I-joist.
            if any(n in ancestry for n in ["Difference-036", "Difference-037", "Difference-038"]):
                feature, label = 2, "Floor joist at hanger"
            if "group_19" in ancestry:
                feature, label = 4, "Subfloor panels"
            if re.search("No_57_Flooring", context):
                feature, label = 6, "Hardwood floor finish"
            # Keep all surfaces of a source component together during separation.
            group = next((n for n in ancestry if re.search("Simpson_LUS210", n)), ancestry[0] if ancestry and ancestry[0] != "SketchUp" else o.name)
            if feature == 2:
                group = "floor-joist-at-hanger"
            if feature in [1, 2, 3, 4, 6]:
                category = 0 if feature in [4, 6] else 1
            positions.append(unique[:, :3].astype("<f4"))
            normals.append(np.rint(unique[:, 3:6] * 127).astype("i1"))
            uvs.append(unique[:, 6:].astype("<f4"))
            offsets.append(np.tile(offset, (count, 1)).astype("<f4"))
            layers.append(np.full(count, category, dtype="u1"))
            features.append(np.full(count, feature, dtype="u1"))
            indices.append((tri + vertex_base).astype("<u4"))
            slot_map = np.array([material_index(m) for m in mesh.materials] or [material_index(None)])
            face_materials.append(slot_map[np.minimum(triangle_mats, len(slot_map) - 1)])
            parts.append({"id": o.name, "sourceLabel": label, "layer": category, "feature": feature,
                          "group": group, "start": vertex_base, "count": count,
                          "center": ((world.min(0) + world.max(0)) / 2).round(6).tolist(),
                          "min": world.min(0).round(6).tolist(), "max": world.max(0).round(6).tolist(),
                          "offset": offset.tolist()})
            vertex_base += count

        pos = np.concatenate(positions)
        triangles = np.concatenate(indices)
        mats = np.concatenate(face_materials)
        # Remove coincident triangles with the SAME winding. Reverse faces remain
        # for intentionally two-sided source sheets, rendered with back-face culling.
        _, point_ids = np.unique(np.round(pos, 6), axis=0, return_inverse=True)
        ids = point_ids[triangles]
        canonical = np.sort(ids, axis=1)
        parity = ((ids[:, 0] > ids[:, 1]).astype(int) + (ids[:, 0] > ids[:, 2]) + (ids[:, 1] > ids[:, 2])) % 2
        keys = np.column_stack((canonical, parity))
        priority = np.argsort([0 if materials[m]["texture"] else 1 for m in mats], kind="stable")
        _, keep_order = np.unique(keys[priority], axis=0, return_index=True)
        keep = priority[keep_order]
        keep = keep[(canonical[keep, 0] != canonical[keep, 1]) & (canonical[keep, 1] != canonical[keep, 2])]
        removed = len(triangles) - len(keep)
        keep = keep[np.argsort(mats[keep], kind="stable")]
        triangles, mats = triangles[keep], mats[keep]
        groups, cursor = [], 0
        for index in range(len(materials)):
            count = int(np.count_nonzero(mats == index)) * 3
            if count:
                groups.append({"start": cursor, "count": count, "material": index})
                cursor += count
        # Average the existing artist offsets for sibling surfaces. No Blender edits.
        offset_array = np.concatenate(offsets)
        siblings = defaultdict(list)
        for p in parts:
            siblings[p["group"]].append(p)
        for group in siblings.values():
            offset = np.mean([p["offset"] for p in group], axis=0)
            for p in group:
                p["offset"] = offset.tolist()
                offset_array[p["start"]:p["start"] + p["count"]] = offset
        blocks, attributes, cursor = [], {}, 0
        for name, array, dtype, size in [
            ("position", pos, "f32", 3), ("normal", np.concatenate(normals), "i8", 3),
            ("uv", np.concatenate(uvs), "f32", 2), ("explode", offset_array, "f32", 3),
            ("layer", np.concatenate(layers), "u8", 1), ("feature", np.concatenate(features), "u8", 1),
            ("index", triangles.astype("<u4"), "u32", 1),
        ]:
            padding = (-cursor) % 4
            blocks.append(b"\0" * padding)
            cursor += padding
            payload = array.tobytes()
            attributes[name] = {"offset": cursor, "length": array.size, "type": dtype, "itemSize": size}
            blocks.append(payload)
            cursor += len(payload)
        with gzip.open(output / "house.bin.gz", "wb", compresslevel=6) as stream:
            for block in blocks:
                stream.write(block)
        metadata = {"version": 2, "components": len(parts), "vertices": vertex_base,
                    "triangles": len(triangles), "removedCoincidentTriangles": removed,
                    "attributes": attributes, "materials": materials, "groups": groups,
                    "layerCounts": dict(Counter(p["layer"] for p in parts)), "parts": parts}
        (output / "house.json").write_text(json.dumps(metadata, separators=(",", ":")))
        return {"components": len(parts), "vertices": vertex_base, "triangles": len(triangles),
                "removedCoincidentTriangles": removed, "materials": len(materials), "textures": len(textures),
                "compressedBytes": (output / "house.bin.gz").stat().st_size,
                "featureCounts": dict(Counter(p["feature"] for p in parts))}
    finally:
        scene.frame_set(previous_frame)
