"""Generic shapes for headless Blender, built from sizes in mm. Only shapes, never components: every
real part is written by the model or placed whole from the owner's library.

Across, up and along are y, z and x: a section lies in the y-z plane and runs along x. Every part joins
a group, and the groups are what an exploded view pulls apart.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import bpy
from mathutils import Vector

__all__ = ["start", "materials", "material", "prism", "box", "sheet", "rounded_path", "lathe", "rod", "mesh_part"]

MM = 0.001
OUT = Path(os.environ.get("SETOUT_OUT", "."))


def start() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _add(name, verts, faces, material, group):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(c * MM for c in v) for v in verts], [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(MATERIALS[material])
    obj["group"], obj["part"] = group, name.split(".")[0]
    return obj


def _smooth(obj):
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    return obj


def prism(name, outline, x0, x1, material, group):
    """A closed outline in (across, up), run from x0 to x1."""
    n = len(outline)
    verts = [(x0, y, z) for y, z in outline] + [(x1, y, z) for y, z in outline]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    faces += [(k, (k + 1) % n, n + (k + 1) % n, n + k) for k in range(n)]
    return _add(name, verts, faces, material, group)


def box(name, low, high, material, group):
    (x0, y0, z0), (x1, y1, z1) = low, high
    return prism(name, [(y0, z0), (y1, z0), (y1, z1), (y0, z1)], x0, x1, material, group)


def sheet_outline(path, thickness):
    """A bent sheet as a closed outline: the path, then its offset back the other way, mitred at bends."""
    points = [Vector(p) for p in path]
    normals = []
    for a, b in zip(points, points[1:]):
        d = (b - a).normalized()
        normals.append(Vector((-d.y, d.x)))
    offset = []
    for k, point in enumerate(points):
        if k == 0:
            n = normals[0]
        elif k == len(points) - 1:
            n = normals[-1]
        else:
            n = (normals[k - 1] + normals[k]).normalized()
            n = n / max(n.dot(normals[k]), 0.3)
        offset.append(point + n * thickness)
    return [tuple(p) for p in points] + [tuple(p) for p in reversed(offset)]


def _simple_outline(points):
    """A self-crossing section cannot be triangulated into a reliable solid."""
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on(a, b, p):
        return all(min(a[k], b[k]) - 1e-8 <= p[k] <= max(a[k], b[k]) + 1e-8 for k in (0, 1))

    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = points[j], points[(j + 1) % n]
            ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
            if (ab_c * ab_d < 0 and cd_a * cd_b < 0 or
                abs(ab_c) < 1e-8 and on(a, b, c) or abs(ab_d) < 1e-8 and on(a, b, d) or
                abs(cd_a) < 1e-8 and on(c, d, a) or abs(cd_b) < 1e-8 and on(c, d, b)):
                return False
    return True


def sheet(name, path, thickness, x0, x1, material, group):
    """One continuous bent sheet. Thickness offsets LEFT of the directed (y,z) path. Keep inner bends wider than thickness; reversing or self-crossing folds are rejected, not filled with spurious triangles."""
    outline = sheet_outline(path, thickness)
    if not _simple_outline(outline):
        turns = []
        for i in range(1, len(path) - 1):
            a, p, b = path[i - 1:i + 2]
            length = math.dist(a, p) * math.dist(p, b)
            if length:
                turns.append((sum((p[k] - a[k]) * (b[k] - p[k]) for k in (0, 1)) / length, i))
        sharpest = min(turns) if turns else (1, 0)
        detail = f"; sharp reversal near path vertices {path[sharpest[1] - 1:sharpest[1] + 2]}" if sharpest[0] < -0.5 else ""
        raise ValueError(f"sheet {name!r} crosses itself after thickness offset{detail}; correct the path itself (including a faulty shared outline), remove backtracking, or increase bend radius relative to thickness")
    return prism(name, outline, x0, x1, material, group)


def rounded_path(path, radius, segments=12):
    """Round the interior corners of an open (y,z) path with tangent circular arcs, for sheet(). Radius is mm, clamped to fit adjacent segments; endpoints stay fixed."""
    points = [tuple(p) for p in path]
    if radius < 0 or not math.isfinite(radius) or segments < 1:
        raise ValueError("rounding needs a nonnegative radius and at least one segment")
    if len(points) < 3 or radius == 0:
        return points
    result = [points[0]]
    for a, p, b in zip(points, points[1:], points[2:]):
        left, right = math.dist(a, p), math.dist(b, p)
        if min(left, right) < 1e-9:
            continue
        u = ((a[0] - p[0]) / left, (a[1] - p[1]) / left)
        v = ((b[0] - p[0]) / right, (b[1] - p[1]) / right)
        turn = -u[0] * v[1] + u[1] * v[0]
        if abs(turn) < 1e-9:
            result.append(p)
            continue
        half = math.acos(max(-1, min(1, u[0] * v[0] + u[1] * v[1]))) / 2
        cut = min(radius / math.tan(half), left * 0.49, right * 0.49)
        actual = cut * math.tan(half)
        bisector = (u[0] + v[0], u[1] + v[1])
        length = math.hypot(*bisector)
        center = tuple(p[k] + bisector[k] / length * actual / math.sin(half) for k in (0, 1))
        start = tuple(p[k] + u[k] * cut for k in (0, 1))
        end = tuple(p[k] + v[k] * cut for k in (0, 1))
        angle = math.atan2(start[1] - center[1], start[0] - center[0])
        delta = math.atan2(end[1] - center[1], end[0] - center[0]) - angle
        delta = delta % math.tau if turn > 0 else -((-delta) % math.tau)
        result.extend((center[0] + actual * math.cos(angle + delta * i / segments),
                       center[1] + actual * math.sin(angle + delta * i / segments)) for i in range(segments + 1))
    result.append(points[-1])
    return result


def lathe(name, profile, origin, direction, material, group, sides=20, smooth=True):
    """A turned part: `profile` is (radius, distance along) from `origin` along `direction`."""
    axis = Vector(direction).normalized()
    helper = Vector((0, 0, 1)) if abs(axis.z) < 0.9 else Vector((1, 0, 0))
    u = axis.cross(helper).normalized()
    v = axis.cross(u)
    base = Vector(origin)
    verts, faces = [], []
    for radius, along in profile:
        for k in range(sides):
            a = 2 * math.pi * k / sides
            verts.append(tuple(base + axis * along + (u * math.cos(a) + v * math.sin(a)) * radius))
    rings = len(profile)
    for r in range(rings - 1):
        for k in range(sides):
            next_ = (k + 1) % sides
            faces.append((r * sides + k, r * sides + next_, (r + 1) * sides + next_, (r + 1) * sides + k))
    faces.append(tuple(range(sides - 1, -1, -1)))
    faces.append(tuple((rings - 1) * sides + k for k in range(sides)))
    obj = _add(name, verts, faces, material, group)
    return _smooth(obj) if smooth else obj


def rod(name, a, b, radius, material, group, sides=16):
    a, b = Vector(a), Vector(b)
    return lathe(name, [(radius, 0), (radius, (b - a).length)], a, b - a, material, group, sides)


def mesh_part(name, triangles, origin, direction, material, group, roll=0):
    """Place a supplied triangle mesh whole, without scaling. Local +z follows direction; roll rotates about it in degrees. Coordinates and origin are mm."""
    axis = Vector(direction)
    if axis.length < 1e-9:
        raise ValueError("a library part needs a nonzero direction")
    from mathutils import Quaternion
    rotation = Vector((0, 0, 1)).rotation_difference(axis.normalized()) @ Quaternion((0, 0, 1), math.radians(roll))
    base, vertices, faces, indices = Vector(origin), [], [], {}
    for triangle in triangles:
        face = []
        for point in triangle:
            key = tuple(point)
            if key not in indices:
                indices[key] = len(vertices)
                vertices.append(tuple(base + rotation @ Vector(point)))
            face.append(indices[key])
        faces.append(tuple(face))
    return _add(name, vertices, faces, material, group)


def _material(name, base, roughness=0.6, metallic=0.0, noise=0.0, bump=0.0, scale=60.0, vary=0.0, transmission=0.0, ior=1.45):
    material = bpy.data.materials.new(name)
    material["base"] = list(base)
    material["setout_surface"] = {"noise": noise, "bump": bump, "scale": scale}
    material.diffuse_color = (*base, 1)
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    shader.inputs["Base Color"].default_value = (*base, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["IOR"].default_value = ior
    shader.inputs["Transmission Weight"].default_value = transmission
    if noise or bump or vary:
        coords = nodes.new("ShaderNodeTexCoord")
        texture = nodes.new("ShaderNodeTexNoise")
        texture.inputs["Scale"].default_value = scale
        texture.inputs["Detail"].default_value = 8
        links.new(coords.outputs["Object"], texture.inputs["Vector"])
        tone = nodes.new("ShaderNodeHueSaturation")
        info = nodes.new("ShaderNodeObjectInfo")
        shift = nodes.new("ShaderNodeMath")
        shift.operation = "MULTIPLY_ADD"
        shift.inputs[1].default_value = 2 * vary
        shift.inputs[2].default_value = 1 - vary
        links.new(info.outputs["Random"], shift.inputs[0])
        mix = nodes.new("ShaderNodeMath")
        mix.operation = "MULTIPLY_ADD"
        mix.inputs[1].default_value = 2 * noise
        mix.inputs[2].default_value = 1 - noise
        links.new(texture.outputs[0], mix.inputs[0])
        total = nodes.new("ShaderNodeMath")
        total.operation = "MULTIPLY"
        links.new(mix.outputs[0], total.inputs[0])
        links.new(shift.outputs[0], total.inputs[1])
        links.new(total.outputs[0], tone.inputs["Value"])
        tone.inputs["Color"].default_value = (*base, 1)
        links.new(tone.outputs["Color"], shader.inputs["Base Color"])
        if bump:
            relief = nodes.new("ShaderNodeBump")
            relief.inputs["Strength"].default_value = bump
            links.new(texture.outputs[0], relief.inputs["Height"])
            links.new(relief.outputs["Normal"], shader.inputs["Normal"])
    return material


MATERIALS: dict = {}


def materials() -> None:
    MATERIALS.update({
        "concrete": _material("concrete", (0.46, 0.45, 0.43), 0.9, noise=0.16, bump=0.25, scale=90),
        "brick": _material("brick", (0.42, 0.13, 0.08), 0.85, noise=0.16, bump=0.35, scale=220, vary=0.2),
        "mortar": _material("mortar", (0.62, 0.59, 0.53), 0.95, noise=0.1, bump=0.2, scale=300),
        "membrane": _material("membrane", (0.035, 0.05, 0.08), 0.32),
        "aluminium": _material("aluminium", (0.78, 0.79, 0.8), 0.32, metallic=1.0),
        "frame": _material("frame", (0.11, 0.115, 0.125), 0.38, metallic=0.6),
        "steel": _material("steel", (0.62, 0.63, 0.65), 0.28, metallic=1.0),
        "galvanised": _material("galvanised", (0.56, 0.58, 0.6), 0.45, metallic=1.0, noise=0.08, scale=400),
        "packer": _material("packer", (0.72, 0.5, 0.2), 0.7),
        "silicone": _material("silicone", (0.82, 0.82, 0.8), 0.4),
        "backing": _material("backing", (0.1, 0.25, 0.55), 0.75),
        "sealant": _material("sealant", (0.16, 0.16, 0.17), 0.45),
        "tile": _material("tile", (0.74, 0.7, 0.62), 0.22, noise=0.04, scale=40, vary=0.03),
        "bed": _material("bed", (0.58, 0.56, 0.5), 0.95, noise=0.12, bump=0.2, scale=260),
        "glass": _material("glass", (0.85, 0.93, 0.95), 0.03, transmission=1.0),
        "rubber": _material("rubber", (0.02, 0.02, 0.02), 0.6),
    })


TALL, SLENDER = 0.6, 0.15  # a part this tall against the scene, and this thin against its height, is left out of framing an angled view


def material(name, base, roughness=0.6, metallic=0.0, transmission=0.0) -> str:
    """A new material by colour, each channel 0 to 1, for anything the named ones don't suit. Returns its name."""
    MATERIALS[name] = _material(name, base, roughness, metallic=metallic, transmission=transmission)
    return name


def fit_view(view, section_at=0, size=(1000, 750), samples=14) -> None:
    """One standard view of the whole scene, framed from what was built, so a model never places a camera. Angled views frame the detail, so a full-height door can't shrink a sill to nothing."""
    size = tuple(view.get("size", size)) if isinstance(view, dict) else size
    focus = view.get("parts", []) if isinstance(view, dict) else []
    view = view["view"] if isinstance(view, dict) else view
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if focus:
        meshes = [o for o in meshes if o.name in focus]
    if not meshes:
        return

    def extent(held):
        points = [o.matrix_world @ Vector(c) for o in held for c in o.bound_box]
        return Vector((min(p[k] for p in points) / MM for k in range(3))), Vector((max(p[k] for p in points) / MM for k in range(3)))
    low, high = extent(meshes)
    middle = (low + high) / 2
    if view == "section":
        shot("section.jpg", (low.x - 4000, middle.y, middle.z), (low.x, middle.y, middle.z), size=size, samples=samples, ortho=1.12 * max(high.y - low.y, 1.33 * (high.z - low.z)))
    elif view == "side":
        shot("side.jpg", (middle.x, low.y - 4000, middle.z), tuple(middle), size=size, samples=samples, ortho=1.12 * max(high.x - low.x, 1.33 * (high.z - low.z)))
    elif view == "top":
        shot("top.jpg", (middle.x, middle.y, high.z + 4000), tuple(middle), size=size, samples=samples, ortho=1.12 * max(high.y - low.y, 1.33 * (high.x - low.x)))
    else:
        tall = TALL * (high.z - low.z) / 1000
        short = meshes if focus else ([o for o in meshes if o.dimensions.z < tall or min(o.dimensions.x, o.dimensions.y) > SLENDER * o.dimensions.z] or meshes)
        low, high = extent(short)
        middle, span = (low + high) / 2, max(max(high - low), 10)
        side = high.y + 1.0 * span if view == "other_side" else low.y - 1.0 * span
        shot(f"{view}.jpg", (low.x - 1.25 * span, side, middle.z + 0.55 * span), tuple(middle), lens=42, size=size, samples=samples)


def inventory() -> list:
    """What is in the scene, in mm, read back from Blender and not from what the code meant to do."""
    rows = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        points = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        low = [min(p[k] for p in points) / MM for k in range(3)]
        high = [max(p[k] for p in points) / MM for k in range(3)]
        rows.append({"name": obj.name, "part": obj.get("part") or obj.name.split(".")[0], "group": obj.get("group") or "other", "material": obj.data.materials[0].name if obj.data.materials else None,
                     "from": [round(v, 1) for v in low], "to": [round(v, 1) for v in high], "triangles": sum(len(p.vertices) - 2 for p in obj.data.polygons), "step": obj.get("step", 0)})
    return rows


def solids() -> dict:
    """Every part as vertices in mm and triangles, for measuring how they meet, with any modifier applied as the export applies it."""
    graph = bpy.context.evaluated_depsgraph_get()
    out = {}
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        held = obj.evaluated_get(graph)
        mesh = held.to_mesh()
        mesh.calc_loop_triangles()
        out[obj.name] = ([tuple((held.matrix_world @ v.co) / MM) for v in mesh.vertices], [tuple(t.vertices) for t in mesh.loop_triangles])
        held.to_mesh_clear()
    return out


def stage(sky=(0.92, 0.93, 0.95)) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.use_denoising = True
    world = bpy.data.worlds.new("sky")
    world.use_nodes = True
    tree = world.node_tree
    ambient = tree.nodes["Background"]
    ambient.inputs[0].default_value = (*sky, 1)
    ambient.inputs[1].default_value = 0.4
    # A bright backdrop need not light every face equally: keep joints and bends readable.
    backdrop = tree.nodes.new("ShaderNodeBackground")
    backdrop.inputs[0].default_value = (*sky, 1)
    backdrop.inputs[1].default_value = 1
    rays = tree.nodes.new("ShaderNodeLightPath")
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(rays.outputs["Is Camera Ray"], mix.inputs[0])
    tree.links.new(ambient.outputs[0], mix.inputs[1])
    tree.links.new(backdrop.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], tree.nodes["World Output"].inputs["Surface"])
    scene.world = world
    data = bpy.data.lights.new("sun", "SUN")
    data.energy = 2.8
    data.angle = math.radians(14)
    sun = bpy.data.objects.new("sun", data)
    sun.rotation_euler = (math.radians(35), 0, math.radians(35))
    scene.collection.objects.link(sun)


def shot(file, eye, target, lens=45, size=(1500, 1050), samples=48, ortho=None) -> None:
    scene = bpy.context.scene
    data = bpy.data.cameras.new("camera")
    data.clip_start = 0.001  # close-ups of thin parts must not disappear at the default near plane
    data.clip_end = 200
    if ortho:
        data.type = "ORTHO"
        data.ortho_scale = ortho * MM
    else:
        data.lens = lens
    camera = bpy.data.objects.new("camera", data)
    scene.collection.objects.link(camera)
    camera.location = tuple(c * MM for c in eye)
    look = Vector(tuple(c * MM for c in target)) - Vector(camera.location)
    camera.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.cycles.samples = samples
    scene.render.filepath = str(OUT / file)
    scene.render.image_settings.file_format = "JPEG" if file.endswith(".jpg") else "PNG"
    scene.render.image_settings.quality = 88
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(camera)


def flatten_materials() -> None:
    """A model file can't carry procedural surfaces, and would show them white: each goes to its plain colour."""
    for item in bpy.data.materials:
        if not item.node_tree or "base" not in item:
            continue
        shader = next((n for n in item.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if shader is None:
            continue
        for key in ("Base Color", "Normal"):
            for link in list(shader.inputs[key].links):
                item.node_tree.links.remove(link)
        shader.inputs["Base Color"].default_value = (*item["base"], 1)


def export_glb(file) -> None:
    flatten_materials()
    try:
        bpy.ops.export_scene.gltf(filepath=str(OUT / file), export_format="GLB", export_apply=True, export_extras=True)
    except Exception as error:
        print("glb export failed:", str(error)[:300])
