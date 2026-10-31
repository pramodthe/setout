"""Headless Blender entry point.

    blender -b --factory-startup --python harness.py -- job.json

``job.json`` carries the script, overrides, output directory and whether to
render a preview. The script runs in restricted globals. Overrides are injected
before the script and transforms are applied again afterwards.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.make.ast_check import check
from app.make.helpers import add_part, box_mm, extrude_profile, mm
from app.make.render_settings import PREVIEW, render_settings

_WATCHED_ENV = (
    "LLM_API_KEY",
    "ALT_LLM_API_KEY",
    "META_API_KEY",
    "OPENAI_API_KEY",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "AUTHORIZATION",
)
_ALLOWED_IMPORTS = frozenset({"bpy", "bmesh", "math", "mathutils", "json"})
_BBOX_LIMIT_MM = 2000.0
_MIN_THICKNESS_MM = 0.05


def main() -> None:
    job_path = _job_path()
    job = json.loads(Path(job_path).read_text())
    out = Path(job["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    script = job.get("script") or ""
    overrides = job.get("overrides") or {}
    preview = bool(job.get("preview", True))
    mode = job.get("mode") or "preview"
    quality = job.get("quality") or "draft"
    warnings: list[str] = []
    present = _secret_env_names()
    try:
        faults = check(script)
        if not faults.ok:
            _finish(out, ok=False, traceback=faults.text, warnings=warnings, secret_env_present=present)
            return
        _reset_scene()
        namespace = _execute(script, overrides)
        warnings.extend(_reapply_overrides(overrides, namespace))
        warnings.extend(_sanity())
        _export_glb(out / "scene.glb")
        tree = _tree(namespace)
        (out / "tree.json").write_text(json.dumps(tree, indent=2))
        engine = ""
        if mode == "render":
            engine = _render_still(out / "render.png", render_settings(quality))
        elif preview:
            engine = _render_still(out / "preview.png", PREVIEW)
        _finish(out, ok=True, traceback="", warnings=warnings, secret_env_present=present, engine=engine)
    except Exception:
        _finish(out, ok=False, traceback=traceback.format_exc(), warnings=warnings, secret_env_present=present)


def restricted_globals(overrides: dict | None) -> dict:
    """Globals for ``exec``. No ``open``, and imports outside the allowlist fail."""
    return {
        "__builtins__": _safe_builtins(),
        "__name__": "__setout_script__",
        "mm": mm,
        "box_mm": box_mm,
        "extrude_profile": extrude_profile,
        "add_part": add_part,
        "OVERRIDES": dict(overrides or {}),
    }


def _execute(script: str, overrides: dict) -> dict:
    namespace = restricted_globals(overrides)
    exec(script, namespace)  # noqa: S102 - the harness is the restricted exec boundary
    return namespace


def _safe_builtins() -> dict:
    import builtins

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            raise ImportError("relative import is not allowed")
        root = (name or "").split(".", 1)[0]
        if root not in _ALLOWED_IMPORTS:
            raise ImportError(f"import of {root!r} is not allowed")
        return __import__(name, globals, locals, fromlist, level)

    return {
        "__build_class__": builtins.__build_class__,
        "__import__": safe_import,
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "repr": repr,
        "reversed": reversed,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "Exception": Exception,
        "IndexError": IndexError,
        "KeyError": KeyError,
        "RuntimeError": RuntimeError,
        "StopIteration": StopIteration,
        "TypeError": TypeError,
        "ValueError": ValueError,
        "True": True,
        "False": False,
        "None": None,
    }


def _reset_scene() -> None:
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"


def _reapply_overrides(overrides: dict, namespace: dict) -> list[str]:
    import bpy

    warnings: list[str] = []
    params = namespace.get("PARAMS")
    if not isinstance(params, dict):
        params = None
    for name, spec in (overrides or {}).items():
        if not isinstance(spec, dict):
            warnings.append(f"unmatched override: {name}")
            continue
        obj = bpy.data.objects.get(name)
        if obj is None:
            warnings.append(f"unmatched override: {name}")
            continue
        location = spec.get("location")
        if isinstance(location, (list, tuple)) and len(location) == 3:
            obj.location = tuple(float(value) / 1000.0 for value in location)
        rotation = spec.get("rotation_deg")
        if isinstance(rotation, (list, tuple)) and len(rotation) == 3:
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = tuple(math.radians(float(value)) for value in rotation)
        dims = spec.get("dims") or {}
        if isinstance(dims, dict) and dims:
            current = _read_dims(obj)
            for key, value in dims.items():
                current[str(key)] = value
                if params is not None and key in params:
                    params[key] = value
            obj["dims"] = json.dumps(current)
    bpy.context.view_layer.update()
    return warnings


def _sanity() -> list[str]:
    import bpy

    warnings: list[str] = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        corners = [_world(obj, corner) for corner in obj.bound_box]
        mins = [min(corner[i] for corner in corners) for i in range(3)]
        maxs = [max(corner[i] for corner in corners) for i in range(3)]
        for index, (lo, hi) in enumerate(zip(mins, maxs)):
            if abs(lo) > _BBOX_LIMIT_MM / 1000.0 or abs(hi) > _BBOX_LIMIT_MM / 1000.0:
                warnings.append(f"{obj.name} extends beyond {_BBOX_LIMIT_MM:.0f} mm of the origin")
                break
            if (hi - lo) * 1000.0 < _MIN_THICKNESS_MM:
                axis = "xyz"[index]
                warnings.append(f"{obj.name} is under {_MIN_THICKNESS_MM} mm on {axis}")
    if not any(obj.type == "MESH" for obj in bpy.data.objects):
        warnings.append("scene has no mesh objects")
    return warnings


def _world(obj, corner):
    from mathutils import Vector

    return obj.matrix_world @ Vector(corner)


def _export_glb(path: Path) -> None:
    import bpy

    bpy.context.view_layer.update()
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        export_extras=True,
        export_yup=True,
        export_apply=False,
        export_cameras=False,
        export_lights=False,
        use_selection=False,
    )


def _tree(namespace: dict) -> dict:
    import bpy

    bpy.context.view_layer.update()
    scene = bpy.context.scene
    root_name = namespace.get("SCENE_NAME") or scene.collection.name or "Scene"
    children = [_collection_node(child) for child in scene.collection.children]
    for obj in scene.collection.objects:
        if obj.type == "MESH":
            children.append(_object_node(obj))
    return {
        "name": str(root_name),
        "type": "collection",
        "axes": "blender_z_up",
        "length_unit": "mm",
        "children": children,
    }


def _collection_node(collection) -> dict:
    children = [_collection_node(child) for child in collection.children]
    for obj in collection.objects:
        if obj.type == "MESH":
            children.append(_object_node(obj))
    return {"name": collection.name, "type": "collection", "children": children}


def _object_node(obj) -> dict:
    from mathutils import Vector

    translation = obj.matrix_world.to_translation()
    rotation = obj.matrix_world.to_euler("XYZ")
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mins = [round(min(corner[i] for corner in corners) * 1000.0, 3) for i in range(3)]
    maxs = [round(max(corner[i] for corner in corners) * 1000.0, 3) for i in range(3)]
    material = obj.get("material")
    if not material and obj.material_slots:
        material = obj.material_slots[0].name
    return {
        "name": obj.name,
        "type": "mesh",
        "location": [round(axis * 1000.0, 3) for axis in translation],
        "rotation_deg": [round(math.degrees(axis), 3) for axis in rotation],
        "dims": _read_dims(obj),
        "material": material or "",
        "component_ref": obj.get("component_ref") or "",
        "build_order": int(obj.get("build_order") or 0),
        "bbox_mm": {"min": mins, "max": maxs},
    }


def _read_dims(obj) -> dict:
    raw = obj.get("dims")
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    try:
        return {str(key): raw[key] for key in raw.keys()}
    except Exception:
        return dict(raw) if isinstance(raw, dict) else {}


def _render_still(path: Path, settings: dict) -> str:
    import bpy

    scene = bpy.context.scene
    engine = _set_engine(scene, settings["engine"])
    scene.render.resolution_x = int(settings["width"])
    scene.render.resolution_y = int(settings["height"])
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.use_file_extension = True
    try:
        scene.view_settings.view_transform = "Standard"
    except Exception:
        pass
    if engine.startswith("BLENDER_EEVEE"):
        scene.eevee.taa_render_samples = int(settings["samples"])
    elif engine == "CYCLES":
        scene.cycles.samples = int(settings["samples"])
        scene.cycles.device = settings.get("device") or "CPU"
        scene.cycles.use_denoising = bool(settings.get("denoise"))
    _studio(scene)
    _frame_camera(scene)
    bpy.ops.render.render(write_still=True)
    return engine


def _set_engine(scene, requested: str) -> str:
    order = [requested]
    if requested.startswith("BLENDER_EEVEE"):
        order = ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"]
    last = ""
    for name in order:
        try:
            scene.render.engine = name
            return name
        except TypeError as exc:
            last = str(exc)
    raise RuntimeError(f"render engine {requested} is not available ({last})")


def _studio(scene) -> None:
    import math
    import bpy

    world = bpy.data.worlds.new("Studio")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.72, 0.73, 0.74, 1.0)
        background.inputs["Strength"].default_value = 0.45
    sun = bpy.data.lights.new("Key", "SUN")
    sun.energy = 2.2
    sun_obj = bpy.data.objects.new("Key", sun)
    sun_obj.rotation_euler = (math.radians(48), math.radians(-18), math.radians(35))
    scene.collection.objects.link(sun_obj)
    fill = bpy.data.lights.new("Fill", "AREA")
    fill.energy = 40
    fill.size = 1.2
    fill_obj = bpy.data.objects.new("Fill", fill)
    fill_obj.location = (-0.6, -0.8, 0.7)
    fill_obj.rotation_euler = (math.radians(60), 0.0, math.radians(-30))
    scene.collection.objects.link(fill_obj)


def _frame_camera(scene) -> None:
    import math
    import bpy
    from mathutils import Vector

    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    found = False
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        found = True
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for index in range(3):
                mins[index] = min(mins[index], world[index])
                maxs[index] = max(maxs[index], world[index])
    if not found:
        raise RuntimeError("scene has no mesh objects to frame")
    center = (mins + maxs) / 2.0
    radius = max((maxs - mins).length * 0.5, 0.05)
    data = bpy.data.cameras.new("IsoCam")
    data.lens = 35
    data.clip_start = 0.001
    data.clip_end = 1000
    camera = bpy.data.objects.new("IsoCam", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    bpy.context.view_layer.update()
    direction = Vector((1.0, -1.0, 0.8)).normalized()
    fov = data.angle or math.radians(50)
    distance = radius / max(math.sin(fov / 2.0), 0.05) * 1.2
    camera.location = center + direction * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()


def _secret_env_names() -> list[str]:
    return [name for name in _WATCHED_ENV if os.environ.get(name)]


def _finish(out: Path, *, ok: bool, traceback: str, warnings: list[str],
            secret_env_present: list[str], engine: str = "") -> None:
    payload = {
        "ok": ok,
        "traceback": traceback[-8000:],
        "warnings": warnings,
        "secret_env_present": secret_env_present,
        "engine": engine,
    }
    (out / "result.json").write_text(json.dumps(payload, indent=2))


def _job_path() -> str:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = argv[1:]
    if not argv:
        raise SystemExit("harness expected a job.json path after --")
    return argv[0]


if __name__ == "__main__":
    main()
