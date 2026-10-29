"""Explicit live prerequisite checks: python -m app.v3_smoke [--cad]."""
from __future__ import annotations

import argparse
import io
import json
import tempfile
from pathlib import Path
from contextlib import ExitStack, contextmanager
import threading

from PIL import Image

from . import database as d, meta_client
from .agent_tools import obj, STRING
from .model_gateway import ModelGateway
from .repository import Repository


@contextmanager
def heartbeat(repo, fence):
    stop = threading.Event()
    def pulse():
        while not stop.wait(15):
            try:
                repo.heartbeat(fence)
            except Exception:
                return
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def cad_probe():
    from .cad_components import build
    code = 'import cadquery as cq\ndef build(p):\n    return cq.Workplane("XY").box(p["w"],p["d"],p["h"]).faces(">Z").workplane().rarray(p["pitch"],p["pitch"],p["count"],p["count"]).hole(p["bore"])\n'
    built = build(code, {"w":80,"d":80,"h":10,"bore":10,"pitch":20,"count":3}, [
        {"id":"width","kind":"extent","selector":{"axis":"x"},"expected_expr":"w","tolerance":.01},
        {"id":"bore","kind":"hole_diameter","selector":{"axis":"y","center":["0","0","0"]},
         "expected_expr":"bore","tolerance":.1}])
    assert all(check["status"] == "PASS" for check in built["checks"]), built["checks"]
    return {"cad_measurement":"pass", "step_bytes":len(built["step"]), "trusted_mesh_bytes":len(built["stl"])}


def run(include_cad=False, omit_reasoning=False):
    with tempfile.TemporaryDirectory(prefix="setout-v3-smoke-") as root, ExitStack() as cleanup:
        repo = Repository("sqlite:///" + root + "/v3.db", root + "/artifacts")
        images = []
        for color in ("red", "blue"):
            image = io.BytesIO()
            Image.new("RGB", (64, 64), color).save(image, "PNG")
            reference = repo.artifacts.put(image.getvalue(), "image/png")
            with d.transaction(repo.engine) as con:
                repo.artifact(con, reference)
            images.append(reference["id"])
        job = repo.enqueue("smoke", {})
        fence = repo.claim("smoke")
        cleanup.enter_context(heartbeat(repo, fence))
        gateway = ModelGateway(repo, fence)
        initial = [{"role": "user", "content": [{"type": "input_text", "text": "Inspect this solid-color image and call inspect_image with its color."},
                    {"type": "input_image", "image_url": "artifact:" + images[0]}]}]
        first = gateway.call({"model": meta_client.default_model(), "input": initial, "max_output_tokens": 8192,
                "tools": [{"type": "function", "name": "inspect_image", "description": "Report the primary image color.",
                           "parameters": obj({"color": STRING})}]}, "spike", "first")
        calls = [o for o in first["output"] if o.get("type") == "function_call"]
        assert len(calls) == 1 and calls[0]["name"] == "inspect_image", "native function call missing"
        arguments = json.loads(calls[0]["arguments"])
        assert "color" in arguments, f"invalid first tool arguments: {arguments!r}"
        assert "red" in arguments["color"].lower(), "first image was not read"
        prior = [item for item in first["output"] if not omit_reasoning or item.get("type") != "reasoning"]
        continuation = initial + prior + [
            {"type": "function_call_output", "call_id": calls[0]["call_id"], "output": json.dumps({"accepted": True})},
            {"role": "user", "content": [{"type": "input_text", "text": "Here is the second image. Call compare_images with the first and second image colors."},
             {"type": "input_image", "image_url": "artifact:" + images[1]}]}]
        second = gateway.call({"model": meta_client.default_model(), "input": continuation, "max_output_tokens": 8192,
                "tools": [{"type": "function", "name": "compare_images", "description": "Report both observed colors in order.",
                           "parameters": obj({"first_color": STRING, "second_color": STRING})}]}, "spike", "second")
        calls = [o for o in second["output"] if o.get("type") == "function_call"]
        assert len(calls) == 1 and calls[0]["name"] == "compare_images", "continuation function call missing"
        result = json.loads(calls[0]["arguments"])
        assert "red" in result["first_color"].lower() and "blue" in result["second_color"].lower(), "image continuation failed"
        output = {"native_tools": "pass", "image_continuation": "pass", "model": meta_client.default_model(),
                  "reasoning_replay": "omitted" if omit_reasoning else "included"}
        if include_cad:
            output.update(cad_probe())
        repo.end(fence, "succeeded", output)
        return output


def linux_renderer():
    """Build the real Linux worker image and exercise the bundled viewer."""
    import modal
    root = Path(__file__).resolve().parent.parent
    image = (modal.Image.debian_slim(python_version="3.12")
             .pip_install_from_requirements(str(root / "requirements.txt"))
             .run_commands("python -m playwright install --with-deps chromium")
             .add_local_dir(str(root / "app"), "/root/app", ignore=["__pycache__"])
             .add_local_dir(str(root / "tests"), "/root/tests", ignore=["__pycache__"])
             .add_local_dir(str(root / "migrations"), "/root/migrations", ignore=["__pycache__"])
             .add_local_dir(str(root / "web" / "dist"), "/root/web/dist")
             .add_local_file(str(root / "alembic.ini"), "/root/alembic.ini"))
    sandbox = modal.Sandbox.create(app=modal.App.lookup("setout-v3-check", create_if_missing=True),
                 image=image, timeout=300, block_network=True, cpu=2, memory=2048,
                 env={"SETOUT_BROWSER_TESTS": "1"}, workdir="/root")
    try:
        process = sandbox.exec("python", "-m", "pytest", "tests/test_render_v3.py", "-q", "--tb=short", timeout=180)
        output, errors = process.stdout.read(), process.stderr.read()
        process.wait()
        if process.returncode:
            raise RuntimeError("Linux renderer check failed:\n" + (output + errors)[-6000:])
        return {"linux_renderer": "pass", "test_output": output.strip()}
    finally:
        sandbox.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cad", action="store_true")
    parser.add_argument("--cad-only", action="store_true")
    parser.add_argument("--linux-renderer", action="store_true")
    parser.add_argument("--omit-reasoning", action="store_true")
    args = parser.parse_args()
    print(json.dumps(linux_renderer() if args.linux_renderer else cad_probe() if args.cad_only else run(args.cad,args.omit_reasoning), indent=2))
