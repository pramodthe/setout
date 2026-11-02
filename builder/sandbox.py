"""Headless Blender on Modal, where a model's code runs. Blender is a Python module here, so nothing is installed on anyone's machine.

    modal deploy builder/sandbox.py
"""
from __future__ import annotations

from pathlib import Path

import modal

app = modal.App("setout-blender")
image = (modal.Image.debian_slim(python_version="3.13")
         .apt_install("libxrender1", "libxxf86vm1", "libxfixes3", "libxi6", "libxkbcommon0", "libsm6", "libgl1", "libegl1")
         .pip_install("bpy==5.2.3", "numpy"))


@app.function(image=image, cpu=8.0, memory=8192, timeout=290, block_network=True)
def live_step(kit: str, steps: list, views: list, export: bool = False):
    """A model's live scene. The scene is the list of code steps it has run, replayed in order, so it needs no saved file, an undo only drops the last step, and every
    size stays in readable code. The last step's failure comes back as feedback, and the steps before it still stand. Comes back in pieces, as the network is blocked."""
    import io
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
    names = {k: getattr(module, k) for k in module.__all__ if k != "start"}
    space = {"__name__": "__main__", "bpy": bpy, "math": __import__("math"), **names}
    started, error, printed = time.time(), None, io.StringIO()
    stdout = sys.stdout
    for number, step in enumerate(steps):
        try:
            sys.stdout = printed if number == len(steps) - 1 else io.StringIO()  # only the newest step's prints reach the model
            exec(compile(step, f"step_{number + 1}.py", "exec"), space)
            for obj in bpy.context.scene.objects:  # which step made each part, for playing the build back
                if obj.type == "MESH" and "step" not in obj:
                    obj["step"] = number + 1
        except Exception:
            error = traceback.format_exc(limit=4)[-1800:]
            if number < len(steps) - 1:
                error = f"step {number + 1} failed on replay: {error}"
            break
        finally:
            sys.stdout = stdout
    rows = module.inventory()
    meta = json.dumps({"error": error, "seconds": round(time.time() - started, 1), "parts": rows, "printed": printed.getvalue()[-2000:]}).encode()
    yield "scene.json", meta
    for view in views:
        module.fit_view(view)
    if export:
        module.export_glb("model.glb")
    for path in sorted(out.iterdir()):
        payload = path.read_bytes()
        for start in range(0, max(len(payload), 1), 1 << 20):
            yield path.name, payload[start:start + (1 << 20)]
