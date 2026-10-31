"""Parametric building components for headless Blender: our own geometry, built from sizes in mm.

Across, up and along are y, z and x: a section lies in the y-z plane and runs along x. Every part joins
a group, and the groups are what an exploded view pulls apart.
"""
from __future__ import annotations

import math
import os
import random
from pathlib import Path

import bpy
from mathutils import Vector

__all__ = ["start", "materials", "prism", "box", "sheet", "lathe", "rod", "screw", "brick_wall", "tiles", "PARTS", "tally", "finish",
           "packers_along", "fixings_along", "seal_joint", "sliding_door", "cavity_wall", "weep_vents", "floor_finish", "lintel", "export_parts"]

MM = 0.001
OUT = Path(os.environ.get("SETOUT_OUT", "."))
PARTS: list = []


def start() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    PARTS.clear()
    random.seed(7)


def _add(name, verts, faces, material, group):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(c * MM for c in v) for v in verts], [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(MATERIALS[material])
    PARTS.append((obj, group, name.split(".")[0]))
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


def sheet(name, path, thickness, x0, x1, material, group):
    """One continuous bent sheet: a membrane, flashing or tray, however many times it folds."""
    return prism(name, sheet_outline(path, thickness), x0, x1, material, group)


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


def screw(name, head, tip, radius, material, group):
    """A threaded screw: a countersunk head, then real ridges down the shank to a point."""
    start_, stop = Vector(head), Vector(tip)
    length = (stop - start_).length
    pitch = radius * 0.9
    profile = [(0.01, 0), (radius * 2.0, 0), (radius * 2.0, radius * 0.5), (radius, radius * 1.8)]
    along = radius * 1.8
    while along < length - radius * 3:
        profile += [(radius * 0.75, along + pitch * 0.5), (radius * 1.15, along + pitch)]
        along += pitch
    profile += [(radius * 0.7, length - radius * 2), (0.05, length)]
    return lathe(name, profile, start_, stop - start_, material, group, 14, smooth=False)


def brick_wall(name, x0, x1, y0, y1, z0, z1, material, joint_material, group, length=230, course=76, joint=10):
    """A leaf in running bond: each brick and each joint a part of its own, as they are built."""
    made, z, row = 0, z0, 0
    while z < z1:
        top = min(z + course, z1)
        x = x0 - (length + joint) / 2 * (row % 2)
        while x < x1:
            a, b = max(x, x0), min(x + length, x1)
            if b > a:
                box(f"{name}_brick", (a, y0, z), (b, y1, top), material, group)
                made += 1
                if b + joint <= x1:
                    box(f"{name}_head_joint", (b, y0 + 3, z), (b + joint, y1 - 3, top), joint_material, group)
            x += length + joint
        if top + joint < z1:
            box(f"{name}_bed_joint", (x0, y0 + 3, top), (x1, y1 - 3, top + joint), joint_material, group)
        z = top + joint
        row += 1
    return made


def tiles(name, x0, x1, y0, y1, z0, z1, size, joint, material, group):
    y = y0
    while y < y1 - 1:
        x = x0
        while x < x1 - 1:
            box(name, (x, y, z0), (min(x + size, x1), min(y + size, y1), z1), material, group)
            x += size + joint
        y += size + joint


def packers_along(x0, x1, y0, y1, z0, z1, spacing=400, length=90, group="packers"):
    """Separate packers under something, recurring along the run."""
    x = x0 + spacing * 0.3
    while x < x1 - length / 2:
        box("packer", (x - length / 2, y0, z0), (x + length / 2, y1, z1), "packer", group)
        x += spacing


def fixings_along(x0, x1, y, head_z, tip_z, spacing=600, radius=3.0, sealed=True, group="fix"):
    """Screws recurring along the run, driven from head_z down to tip_z, each head under a dome of
    sealant when it pierces a membrane."""
    x = x0 + spacing / 3
    while x < x1:
        screw("screw", (x, y, head_z), (x, y, tip_z), radius, "steel", group)
        if sealed:
            lathe("oversealing", [(radius * 4.6, 0), (radius * 4.2, 4), (radius * 3, 8), (radius * 1.3, 10.5), (0.05, 11)], (x, y, head_z - 2), (0, 0, 1), "sealant", group)
        x += spacing


def seal_joint(x0, x1, y0, y1, z, group="seals"):
    """A sealed joint between two faces, y0 to y1, along the run: a backing rod with silicone over it."""
    width = abs(y1 - y0)
    middle = (y0 + y1) / 2
    rod("backing_rod", (x0, middle, z - width * 0.95), (x1, middle, z - width * 0.95), width * 0.55, "backing", group)
    prism("silicone_seal", [(min(y0, y1), z - width * 0.45), (max(y0, y1), z - width * 0.45), (max(y0, y1), z + width * 0.25), (min(y0, y1), z + width * 0.5)], x0, x1, "silicone", group)


def _panel(name, x0, x1, y0, y1, z0, z1, group):
    stile, rail = 58, 76
    box(f"{name}_rail", (x0, y0, z0), (x1, y1, z0 + rail), "frame", group)
    box(f"{name}_rail", (x0, y0, z1 - 62), (x1, y1, z1), "frame", group)
    box(f"{name}_stile", (x1 - stile, y0, z0 + rail), (x1, y1, z1 - 62), "frame", group)
    middle = (y0 + y1) / 2
    box(f"{name}_glass", (x0, middle - 11, z0 + rail - 8), (x1 - stile + 8, middle + 11, z1 - 54), "glass", group)
    for x in (x1 - 150, max(x0 + 150, x1 - 760)):
        lathe(f"{name}_roller", [(0.05, -6), (16, -6), (17, -3), (17, 3), (16, 6), (0.05, 6)], (x, middle, z0 - 12), (0, 1, 0), "rubber", group)


def sliding_door(x0, x1, y0, y1, z0, top, group="door"):
    """A whole sliding door or window frame in a real extrusion: its sill with feet and two tracks, from
    x0 to the jamb at x1, then the jamb, the head and two glazed panels in their tracks. y0 to y1 is the
    frame's depth across the section, z0 the underside of the sill's feet, top the top of the frame."""
    s = (y1 - y0) / 152
    Y = lambda v: y0 + v * s  # noqa: E731
    Z = lambda v: z0 + v * s  # noqa: E731
    outline = [(0, 0), (32, 0), (32, 8), (20, 8), (20, 26), (58, 26), (58, 62), (64, 62), (64, 26), (100, 26), (100, 62), (106, 62), (106, 26), (142, 26),
               (142, 8), (130, 8), (130, 0), (152, 0), (152, 80), (146, 80), (146, 32), (14, 32), (14, 14), (0, 14)]
    prism("sill", [(Y(a), Z(b)) for a, b in outline], x0, x1 - 10, "frame", group)
    box("jamb", (x1 - 10, y0, z0), (x1, y1, top), "frame", group)
    for a in (58, 100):
        box("jamb_web", (x1 - 44, Y(a), Z(62)), (x1 - 10, Y(a + 6), top - 80), "frame", group)
    head = [(0, 0), (152, 0), (152, -96), (146, -96), (146, -8), (106, -8), (106, -114), (100, -114), (100, -8), (64, -8), (64, -114), (58, -114), (58, -8),
            (6, -8), (6, -96), (0, -96)]
    prism("head", [(Y(a), top + b) for a, b in head], x0, x1, "frame", group)
    _panel("fixed_panel", x0, x1 - 12, Y(68), Y(96), Z(40), top - 106, group + "_panels")
    _panel("sliding_panel", x0, x1 - 12, Y(26), Y(54), Z(40), top - 106, group + "_panels")


def weep_vents(x0, x1, y0, y1, z, spacing=800, group="vents"):
    """Vents through the bottom course of an outer leaf, recurring along the run."""
    x = x0 + spacing / 2
    while x < x1:
        box("weep_vent", (x - 5, y0, z), (x + 5, y1, z + 62), "packer", group)
        x += spacing


def cavity_wall(x0, x1, y_out, y_in, z0, z1, group="wall"):
    """A cavity wall from x0 to x1: an outer and an inner brick leaf in running bond, with a clear cavity
    between them and wall ties crossing it every few courses. y_out and y_in are its two faces."""
    leaf = 110
    sign = 1 if y_in > y_out else -1
    a0, a1, b0, b1 = sorted((y_out, y_out + sign * leaf)) + sorted((y_in - sign * leaf, y_in))
    brick_wall("outer_leaf", x0, x1, a0, a1, z0, z1, "brick", "mortar", group)
    brick_wall("inner_leaf", x0, x1, b0, b1, z0, z1, "brick", "mortar", group)
    z = z0 + 4 * 86 + 81
    while z < z1:
        x = x0 + 225
        while x < x1:
            rod("wall_tie", (x, y_out + sign * 50, z), (x, y_in - sign * 50, z), 2.2, "steel", group)
            x += 450
        z += 5 * 86


def lintel(x0, x1, y0, y1, z, height=75, thickness=6, group="wall"):
    """A steel angle over an opening: one leg under the leaf, one rising behind it."""
    sheet("lintel", [(y0, z), (y1, z), (y1, z + height)], thickness, x0, x1, "steel", group)


def floor_finish(x0, x1, y0, y1, top, name="floor", tile=300, bed=40, group="finish"):
    """A floor build-up finishing at height top: a bed, then real tiles with joints."""
    box(f"{name}_bed", (x0, y0, top - 12 - bed), (x1, y1, top - 12), "bed", group)
    tiles(f"{name}_tile", x0, x1, y0, y1, top - 12, top, tile, 4, "tile", group)


def _material(name, base, roughness=0.6, metallic=0.0, noise=0.0, bump=0.0, scale=60.0, vary=0.0, transmission=0.0, ior=1.45):
    material = bpy.data.materials.new(name)
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


def explode(offsets) -> None:
    for obj, group, _ in PARTS:
        obj.location = tuple(c * MM for c in offsets.get(group, (0, 0, 0)))


def settle() -> None:
    for obj, _, _ in PARTS:
        obj.location = (0, 0, 0)


def stage(sky=(0.92, 0.93, 0.95)) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.use_denoising = True
    world = bpy.data.worlds.new("sky")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (*sky, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.75
    scene.world = world
    data = bpy.data.lights.new("sun", "SUN")
    data.energy = 3.2
    data.angle = math.radians(14)
    sun = bpy.data.objects.new("sun", data)
    sun.rotation_euler = (math.radians(55), math.radians(12), math.radians(-40))
    scene.collection.objects.link(sun)


def shot(file, eye, target, lens=45, size=(1500, 1050), samples=48, ortho=None) -> None:
    scene = bpy.context.scene
    data = bpy.data.cameras.new("camera")
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


def tally() -> dict:
    counts: dict = {}
    for _, _, name in PARTS:
        counts[name] = counts.get(name, 0) + 1
    return counts


def export_glb(file) -> None:
    try:
        bpy.ops.export_scene.gltf(filepath=str(OUT / file), export_format="GLB", export_apply=True)
    except Exception as error:
        print("glb export failed:", str(error)[:300])


def export_parts(file="parts.json.gz") -> None:
    """Every part's triangles in mm, in the order they were built, for the app's viewer. Pieces of one
    kind stay together as one row, the way a parts list reads, and remember how many they are."""
    import gzip
    import json
    import re
    rows: dict = {}
    for obj, group, name in PARTS:
        material = obj.data.materials[0].name
        base = re.sub(r"(_\d+)+$", "", name)
        row = rows.setdefault((base, material), {"name": base, "material": material, "group": group, "pieces": 0, "tris": []})
        row["pieces"] += 1
        mesh = obj.data
        mesh.calc_loop_triangles()
        matrix = obj.matrix_world
        for triangle in mesh.loop_triangles:
            for index in triangle.vertices:
                row["tris"].extend(round(c / MM, 2) for c in matrix @ mesh.vertices[index].co)
    (OUT / file).write_bytes(gzip.compress(json.dumps(list(rows.values())).encode(), 9))


def finish(section_at=0) -> None:
    """The standard pictures, framed from what was built: the cut face straight on, and a three-quarter
    view. A scene calls this last, so a scene never has to place a camera."""
    if os.environ.get("SETOUT_EXPORT"):
        export_parts()
        return
    points = [obj.matrix_world @ Vector(corner) for obj, _, _ in PARTS for corner in obj.bound_box]
    low = Vector((min(p[k] for p in points) / MM for k in range(3)))
    high = Vector((max(p[k] for p in points) / MM for k in range(3)))
    middle = (low + high) / 2
    span = max(high - low)
    print("parts", len(PARTS), tally())
    stage()
    near = Vector((section_at, middle.y, min(middle.z, low.z + 500)))
    shot("section.jpg", (section_at - 4000, near.y, near.z), (section_at, near.y, near.z), size=(1200, 900), samples=24,
         ortho=min(1.15 * (high.y - low.y), 1600))
    shot("cut.jpg", (low.x - 1.25 * span, low.y - 1.0 * span, middle.z + 0.55 * span), tuple(middle), lens=42, size=(1200, 900), samples=24)
