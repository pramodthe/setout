"""Trusted Blender finishing pass. Reads a self-contained GLB, never model Python.

Run with Blender --background --factory-startup --disable-autoexec --python this.py
-- input.glb output-directory. Only surface shading, UVs, camera and light change.
"""
from __future__ import annotations

import json
import math
import platform
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

# Match the kit's generic surfaces; custom and already textured materials stay intact.
SURFACES = {
    "concrete": (.16, .25, 90), "brick": (.16, .35, 220),
    "mortar": (.1, .2, 300), "galvanised": (.08, .04, 400),
    "tile": (.04, .03, 40), "bed": (.12, .2, 260),
}


def select(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def surface(material):
    """Bake a seamless generic sample once per material, shared by all its parts."""
    bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or any(n.type == "TEX_IMAGE" for n in material.node_tree.nodes):
        return False
    spec = material.get("setout_surface")
    values = (spec["noise"], spec["bump"], spec["scale"]) if spec else SURFACES.get(material.name)
    if not values or not any(values[:2]):
        return False
    noise_amount, bump_amount, frequency = values
    nodes, links = material.node_tree.nodes, material.node_tree.links
    base = tuple(bsdf.inputs["Base Color"].default_value)
    roughness = bsdf.inputs["Roughness"].default_value
    uv = nodes.new("ShaderNodeTexCoord")
    separate = nodes.new("ShaderNodeSeparateXYZ")
    links.new(uv.outputs["UV"], separate.inputs[0])
    # Map the tile onto a 4D torus so opposite edges match exactly.
    components = []
    for axis in ("X", "Y"):
        angle = nodes.new("ShaderNodeMath")
        angle.operation = "MULTIPLY"
        angle.inputs[1].default_value = math.tau
        links.new(separate.outputs[axis], angle.inputs[0])
        for function in ("SINE", "COSINE"):
            trig = nodes.new("ShaderNodeMath")
            trig.operation = function
            links.new(angle.outputs[0], trig.inputs[0])
            components.append(trig.outputs[0])
    vector = nodes.new("ShaderNodeCombineXYZ")
    for i in range(3):
        links.new(components[i], vector.inputs[i])
    noise = nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = max(2, frequency * .1 / math.tau)
    noise.inputs["Detail"].default_value = 4
    links.new(vector.outputs[0], noise.inputs["Vector"])
    links.new(components[3], noise.inputs["W"])
    color = nodes.new("ShaderNodeValToRGB")
    color.color_ramp.elements[0].color = (*(c * (1 - noise_amount) for c in base[:3]), base[3])
    color.color_ramp.elements[1].color = (*(min(1, c * (1 + noise_amount)) for c in base[:3]), base[3])
    links.new(noise.outputs["Fac"], color.inputs[0])
    links.new(color.outputs["Color"], bsdf.inputs["Base Color"])
    rough = nodes.new("ShaderNodeMapRange")
    rough.inputs["To Min"].default_value = max(.02, roughness - .07)
    rough.inputs["To Max"].default_value = min(1, roughness + .07)
    links.new(noise.outputs["Fac"], rough.inputs["Value"])
    links.new(rough.outputs[0], bsdf.inputs["Roughness"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_amount
    bump.inputs["Distance"].default_value = .0006
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bpy.ops.mesh.primitive_plane_add(size=.1)
    sample = bpy.context.object
    sample.data.materials.append(material)
    target = nodes.new("ShaderNodeTexImage")
    nodes.active = target
    baked = {}
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    emission = nodes.new("ShaderNodeEmission")
    links.new(color.outputs["Color"], emission.inputs["Color"])
    for channel, kind in (("color", "EMIT"), ("normal", "NORMAL"), ("roughness", "ROUGHNESS")):
        image = bpy.data.images.new(f"{material.name}-{channel}", 512, 512, alpha=False)
        if channel != "color":
            image.colorspace_settings.name = "Non-Color"
        target.image = image
        links.new(emission.outputs[0] if channel == "color" else bsdf.outputs[0], output.inputs["Surface"])
        bpy.ops.object.bake(type=kind, margin=0)
        image.pack()
        baked[channel] = image
    bpy.data.objects.remove(sample, do_unlink=True)
    # Remove procedural links so the glTF exporter sees standard PBR texture nodes.
    for name in ("Base Color", "Roughness", "Normal"):
        for link in list(bsdf.inputs[name].links):
            links.remove(link)
    for channel, image in baked.items():
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.extension = "REPEAT"
        if channel == "normal":
            normal = nodes.new("ShaderNodeNormalMap")
            links.new(tex.outputs["Color"], normal.inputs["Color"])
            links.new(normal.outputs[0], bsdf.inputs["Normal"])
        else:
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color" if channel == "color" else "Roughness"])
    return True


def project_uv(obj):
    """World-scale planar UVs on existing faces; no welding or mesh modification."""
    uv = obj.data.uv_layers.active or obj.data.uv_layers.new(name="Surface")
    normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    for face in obj.data.polygons:
        normal = normal_matrix @ face.normal
        axis = max(range(3), key=lambda i: abs(normal[i]))
        u, v = ((1, 2), (0, 2), (0, 1))[axis]
        for index in face.loop_indices:
            p = obj.matrix_world @ obj.data.vertices[obj.data.loops[index].vertex_index].co
            uv.data[index].uv = (p[u] / .1, p[v] / .1)


def light(name, location, target, energy, size):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.shape, data.size = energy, "DISK", size
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def camera_side(objects, target, span):
    """Choose the side exposing more parts, without using names or changing geometry."""
    graph = bpy.context.evaluated_depsgraph_get()
    transparent = {o for o in objects if o.data.materials and all(
        m and m.use_nodes and any(n.type == "BSDF_PRINCIPLED" and n.inputs["Transmission Weight"].default_value > .5
                                  for n in m.node_tree.nodes) for m in o.data.materials)}

    def visible(eye, point, wanted):
        direction = (point - eye).normalized()
        for _ in range(8):
            hit, where, _, _, obj, _ = bpy.context.scene.ray_cast(graph, eye, direction, distance=(point - eye).length + span * .0001)
            if not hit:
                return False
            if obj.original == wanted:
                return True
            if obj.original not in transparent:
                return False
            eye = where + direction * span * .00001
        return False

    def score(eye):
        return sum(any(visible(eye, obj.matrix_world @ face.center, obj)
                       for face in list(obj.data.polygons)[::max(1, len(obj.data.polygons) // 24)]) for obj in objects)

    eyes = [target + Vector((-1.6, side, 1.25)) * span for side in (-1.8, 1.8)]
    return max(eyes, key=score)


def run(source, out):
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objects:
        raise ValueError("The model contains no meshes")
    if len(objects) > 2000 or sum(len(o.data.polygons) for o in objects) > 2_000_000:
        raise ValueError("The model exceeds the presentation render limit")
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    baked = set()
    materials = list(dict.fromkeys(m for o in objects for m in o.data.materials if m))
    for material in materials:
        # Do not replace a UV map used by another original texture on the same mesh.
        shared_texture = any(material in list(o.data.materials) and any(
            n.type == "TEX_IMAGE" for m in o.data.materials if m and m != material and m.use_nodes for n in m.node_tree.nodes
        ) for o in objects)
        if not shared_texture and material.use_nodes and surface(material):
            baked.add(material)
    for obj in objects:
        if any(m in baked for m in obj.data.materials):
            project_uv(obj)
    select(objects)
    bpy.ops.export_scene.gltf(filepath=str(out / "model.glb"), export_format="GLB", use_selection=True, export_extras=True)
    bake_seconds = time.monotonic() - started
    points = [o.matrix_world @ Vector(p) for o in objects for p in o.bound_box]
    low = Vector(tuple(min(p[k] for p in points) for k in range(3)))
    high = Vector(tuple(max(p[k] for p in points) for k in range(3)))
    target = (low + high) / 2
    span = max(max(high - low), .01)
    camera = bpy.data.cameras.new("Presentation")
    camera.type = "ORTHO"
    obj = bpy.data.objects.new("Presentation", camera)
    scene.collection.objects.link(obj)
    obj.location = camera_side(objects, target, span)
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = obj
    bpy.context.view_layer.update()
    framed = [obj.matrix_world.inverted() @ p for p in points]
    width = max(p.x for p in framed) - min(p.x for p in framed)
    height = max(p.y for p in framed) - min(p.y for p in framed)
    camera.ortho_scale = max(width, height * 1.5) * 1.25
    camera.clip_end = span * 100
    camera.clip_start = span * .001
    world = bpy.data.worlds.new("Studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.65, .7, .8, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .45
    scene.world = world
    light("Key", target + Vector((-3, -4, 6)) * span, target, 700 * span ** 2, 4 * span)
    light("Fill", target + Vector((3, -1, 2)) * span, target, 250 * span ** 2, 3 * span)
    light("Rim", target + Vector((1, 3, 4)) * span, target, 500 * span ** 2, 3 * span)
    bpy.ops.mesh.primitive_plane_add(size=200 * span, location=(target.x, target.y, low.z - .001 * span))
    ground = bpy.context.object
    mat = bpy.data.materials.new("Studio backdrop")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (.72, .75, .8, 1)
    mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = .85
    ground.data.materials.append(mat)
    scene.cycles.samples = 48
    # CPU baking is inexpensive; the still can use the local Mac's GPU.
    if platform.system() == "Darwin":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "METAL"
        prefs.refresh_devices()
        if any(device.type == "METAL" for device in prefs.devices):
            for device in prefs.devices:
                device.use = device.type == "METAL"
            scene.cycles.device = "GPU"
    scene.cycles.adaptive_threshold = .035
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.render.resolution_x, scene.render.resolution_y = 1500, 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 94
    scene.render.filepath = str(out / "render.jpg")
    bpy.ops.render.render(write_still=True)
    (out / "render.json").write_text(json.dumps({"parts": len(objects), "textured_materials": len(baked),
        "bake_seconds": round(bake_seconds, 2), "seconds": round(time.monotonic() - started, 2)}))


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    run(Path(args[0]).resolve(), Path(args[1]).resolve())
