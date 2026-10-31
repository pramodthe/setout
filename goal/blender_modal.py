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
