"""Network-disabled Blender finishing service, separate from the model-code sandbox.

Deploy with: modal deploy builder/render_sandbox.py
"""
from pathlib import Path

import modal

from builder.sandbox import image

app = modal.App("setout-presentation")
# File-based deployment mounts this module alone; include its shared image dependency.
render_image = image.add_local_python_source("builder").add_local_file(
    Path(__file__).with_name("presentation.py"), "/opt/presentation.py")


@app.function(image=render_image, cpu=8.0, memory=8192, timeout=290, block_network=True, max_containers=1)
def finish_model(data: bytes):
    import json
    import runpy
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "input.glb"
        source.write_bytes(data)
        runpy.run_path("/opt/presentation.py")["run"](source, root / "out")
        stats_path = root / "out" / "render.json"
        stats = json.loads(stats_path.read_text())
        stats.update(backend="modal", function_call_id=modal.current_function_call_id())
        stats_path.write_text(json.dumps(stats))
        for name in ("model.glb", "render.jpg", "render.json"):
            payload = (root / "out" / name).read_bytes()
            for start in range(0, len(payload), 1 << 20):
                yield name, payload[start:start + (1 << 20)]
