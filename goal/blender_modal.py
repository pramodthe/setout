"""Run a Blender scene headless on Modal, and bring back its renders and model.

Blender runs as a Python module, so nothing is installed on anyone's machine. A scene is plain code
over goal/blender_kit.py, and the outputs land under gitignored data/goal/blender/.

    modal run goal/blender_modal.py --scene goal/scenes/sliding_threshold.py
"""
from __future__ import annotations

from pathlib import Path

import modal

app = modal.App("setout-blender")
image = (modal.Image.debian_slim(python_version="3.13")
         .apt_install("libxrender1", "libxxf86vm1", "libxfixes3", "libxi6", "libxkbcommon0", "libsm6", "libgl1", "libegl1")
         .pip_install("bpy==5.2.3", "numpy"))


@app.function(image=image, cpu=16.0, memory=16384, timeout=1200)
def render(kit: str, scene: str) -> dict:
    """A scene we wrote: {file name: bytes} for everything it wrote, plus its log."""
    return _run(kit, scene)


@app.function(image=image, cpu=8.0, memory=8192, timeout=300, block_network=True)
def render_model_code(kit: str, scene: str) -> dict:
    """The same for code a model wrote: no network, shorter limits, as CAD code runs."""
    return _run(kit, scene)


@app.function(image=image, cpu=4.0, memory=8192, timeout=300, block_network=True)
def export_model_code(kit: str, scene: str):
    """A model's scene as parts for the viewer. With the network blocked a large result can't be uploaded,
    so it comes back in pieces small enough to travel with the reply."""
    import os
    os.environ["SETOUT_EXPORT"] = "1"
    for name, payload in _run(kit, scene).items():
        for start in range(0, max(len(payload), 1), 1 << 20):
            yield name, payload[start:start + (1 << 20)]


@app.function(image=image, cpu=8.0, memory=8192, timeout=290, block_network=True)
def live_step(kit: str, steps: list, views: list, export: bool = False):
    """A model's live scene. The scene is the list of code steps it has run, replayed in order, so it needs no saved file, an undo only drops the last step, and every
    size stays in readable code. The last step's failure comes back as feedback, and the steps before it still stand. Comes back in pieces, as the network is blocked."""
    import json
    import os
    import sys
    import time
    import traceback
    import types

    import bpy

    out = Path("/tmp/out")
    out.mkdir(exist_ok=True)
    for old in out.iterdir():
        old.unlink()
    os.environ["SETOUT_OUT"] = str(out)
    module = types.ModuleType("blender_kit")
    exec(compile(kit, "blender_kit.py", "exec"), module.__dict__)
    sys.modules["blender_kit"] = module
    module.start()
    module.materials()
    module.stage()
    names = {k: getattr(module, k) for k in module.__all__ if k not in ("start", "tally", "finish", "export_parts")}
    space = {"__name__": "__main__", "bpy": bpy, "math": __import__("math"), **names, "material": module.material}
    started, error = time.time(), None
    for number, step in enumerate(steps):
        try:
            exec(compile(step, f"step_{number + 1}.py", "exec"), space)
            for obj in bpy.context.scene.objects:  # which step made each part, for playing the build back
                if obj.type == "MESH" and "step" not in obj:
                    obj["step"] = number + 1
        except Exception:
            error = traceback.format_exc(limit=4)[-1800:]
            if number < len(steps) - 1:
                error = f"step {number + 1} failed on replay: {error}"
            break
    rows = module.inventory()
    meta = json.dumps({"error": error, "seconds": round(time.time() - started, 1), "parts": rows}).encode()
    yield "scene.json", meta
    for view in views:
        module.fit_view(view)
    if export:
        module.export_glb("model.glb")
    for path in sorted(out.iterdir()):
        payload = path.read_bytes()
        for start in range(0, max(len(payload), 1), 1 << 20):
            yield path.name, payload[start:start + (1 << 20)]


def _run(kit: str, scene: str) -> dict:
    import io
    import os
    import sys
    import time
    import traceback
    import types

    out = Path("/tmp/out")
    out.mkdir(exist_ok=True)
    os.environ["SETOUT_OUT"] = str(out)
    module = types.ModuleType("blender_kit")
    exec(compile(kit, "blender_kit.py", "exec"), module.__dict__)
    sys.modules["blender_kit"] = module
    log, started = io.StringIO(), time.time()
    stdout, sys.stdout = sys.stdout, log
    try:
        exec(compile(scene, "scene.py", "exec"), {"__name__": "__main__"})
    except Exception:
        log.write(traceback.format_exc())
    finally:
        sys.stdout = stdout
    log.write(f"\nseconds {time.time() - started:.1f}\n")
    found = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    found["log.txt"] = log.getvalue()[-20000:].encode()
    return found


@app.local_entrypoint()
def main(scene: str):
    here = Path(__file__).resolve().parent
    source = Path(scene)
    results = render.remote((here / "blender_kit.py").read_text(), source.read_text())
    data = next(p / "data" for p in (here, *here.parents) if (p / "data" / "goal").is_dir())
    folder = data / "goal" / "blender" / source.stem
    folder.mkdir(parents=True, exist_ok=True)
    for name, payload in results.items():
        (folder / name).write_bytes(payload)
    print(results["log.txt"].decode()[-3000:])
    print("saved", sorted(results), "to", folder)
