"""Can a model use the kit? It reads a section, writes a Blender scene, looks at its own render and
fixes it. It sees the drawing, the kit's API and its own results, and never a hand-written scene.

    modal run -m goal.blender_agent::agent --drawing <png> --name <name> [--rounds 3]
Model code passes a static check, then runs with the network blocked. Outputs go under data/goal/blender.
"""
from __future__ import annotations

import ast
import base64
import io
import json
import re
import time
from pathlib import Path

from PIL import Image

from .blender_modal import app, render_model_code

HERE = Path(__file__).resolve().parent
DATA = next(p / "data" for p in (HERE, *HERE.parents) if (p / "data" / "goal").is_dir())
ALLOWED = {"blender_kit", "math"}
BANNED = {"exec", "eval", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr", "input", "breakpoint"}
SYSTEM = """You turn one 2D construction section into a real local 3D assembly, as a builder would see it. You write a
Python scene for Blender using only the kit below. A section shows one slice: first work out what this detail really is,
what it belongs to, and every part the slice doesn't show, then build the assembly around it. Build the whole local
assembly the section belongs to, and stop there, never a whole building. A section through a door or window sill means
the whole frame: jamb and head, its glazing, and the wall around the opening up past the head. A section through a wall
base means the wall itself, up a storey-height piece or so, with its ties, joints and vents. Use the kit's assembly parts,
such as sliding_door, cavity_wall, fixings_along, seal_joint and floor_finish, rather than rebuilding them from boxes.

The frame: sizes in mm. A section lies in the y-z plane and runs along x. z is up. y runs from the left of the drawing
(negative) to its right (positive). Put the drawing's section face at x = 0, and run the assembly along +x from there, so
the cut face stays exposed and shows the drawing exactly.

Rules:
- Start with: from blender_kit import *  then  import math  if needed, then start() and materials().
- Every size is a named constant at the top. Nothing is measured from pixels: the drawing is not to scale, so choose
  believable sizes and keep the drawing's arrangement and order of layers.
- A thing that bends is one sheet(): never split a membrane or flashing at its folds. A shaped section is a prism() with its
  real outline, never a box. Repeated things are loops: fixings, packers and vents recur along x at a spacing, bricks are
  brick_wall(), tiles are tiles().
- Give every part a group name, so its layer can be pulled apart later.
- Show every part of the drawing, keep its gaps open, and add what must exist that it doesn't show.
- End with: finish()
Output only the Python code, in one ```python block."""
FIX = """Above is your scene's result: section.png is the cut face straight on, to compare with the drawing, with the drawing's
left on the left. cut.png is the three-quarter view. List in a few lines what is wrong against the drawing, or looks unlike
the real thing: missing parts, wrong order of layers, closed gaps, parts floating or not touching, wrong shapes. Then give
the whole corrected scene in one ```python block."""


def api() -> str:
    """The kit as a model needs it: signatures, docstrings and material names, not the code."""
    source = (HERE / "blender_kit.py").read_text()
    tree = ast.parse(source)
    wanted = ("start", "materials", "prism", "box", "sheet", "lathe", "rod", "screw", "brick_wall", "tiles", "finish", "packers_along", "fixings_along",
              "seal_joint", "sliding_door", "cavity_wall", "weep_vents", "floor_finish", "lintel")
    lines = [ast.get_docstring(tree) or ""]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            lines.append(f"{node.name}({ast.unparse(node.args)}): {ast.get_docstring(node) or ''}")
    names = re.findall(r'^\s+"(\w+)": _material\(', source, re.M)
    lines.append("material names: " + ", ".join(names))
    return "\n".join(lines)


def check(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] not in ALLOWED for a in node.names):
            raise ValueError("only blender_kit and math may be imported")
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] not in ALLOWED:
            raise ValueError("only blender_kit and math may be imported")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED:
            raise ValueError(f"{node.func.id}() is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("dunder attributes are not allowed")


def image_part(png: bytes, longest=1100) -> dict:
    picture = Image.open(io.BytesIO(png)).convert("RGB")
    picture.thumbnail((longest, longest))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=88)
    return {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()}


def code_of(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def flipped(png: bytes) -> bytes:
    """The camera sees the cut face from behind the drawing, so left and right swap back here."""
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(out, "JPEG", quality=88)
    return out.getvalue()


def ask(content: list) -> tuple[str, dict]:
    from app import meta_client
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "high"}, "instructions": SYSTEM + "\n\nThe kit:\n" + api(),
                                        "max_output_tokens": 32768, "input": [{"type": "message", "role": "user", "content": content}]}, timeout=290)
    text = "".join(part.get("text", "") for item in result["output"] if item.get("type") == "message" for part in item.get("content", []))
    return text, result.get("usage") or {}


@app.local_entrypoint()
def agent(drawing: str, name: str, rounds: int = 3):
    kit = (HERE / "blender_kit.py").read_text()
    folder = DATA / "goal" / "blender" / name
    folder.mkdir(parents=True, exist_ok=True)
    source = image_part(Path(drawing).read_bytes(), 1400)
    content = [{"type": "input_text", "text": "Here is the section drawing. Write the scene."}, source]
    history, spent = [], 0
    for number in range(rounds):
        started = time.time()
        text, usage = ask(content)
        spent += usage.get("total_tokens") or 0
        code = code_of(text)
        (folder / f"round{number}.py").write_text(code)
        (folder / f"round{number}.txt").write_text(text[:6000])
        try:
            check(code)
            results = render_model_code.remote(kit, code)
            log = results.get("log.txt", b"").decode()
        except Exception as error:
            results, log = {}, f"rejected: {error}"
        for file, payload in results.items():
            payload = flipped(payload) if file == "section.jpg" else payload
            (folder / f"round{number}_{file}").write_bytes(payload)
            results[file] = payload
        counted = re.search(r"^parts (\d+)", log, re.M)
        failed = "Traceback" in log or log.startswith("rejected")
        history.append({"round": number, "tokens": usage.get("total_tokens"), "seconds": round(time.time() - started), "chars": len(code),
                        "parts": int(counted.group(1)) if counted else None, "failed": failed})
        print(json.dumps(history[-1]), flush=True)
        follow = [{"type": "input_text", "text": "Your scene so far:\n```python\n" + code + "\n```"}]
        if failed or "section.jpg" not in results:
            follow.append({"type": "input_text", "text": "It failed. Error:\n" + log[-1800:] + "\nFix it and give the whole scene in one ```python block."})
            content = [content[0], source] + follow
        else:
            content = [{"type": "input_text", "text": "Here is the section drawing again."}, source, *follow,
                       {"type": "input_text", "text": "section.png:"}, image_part(results["section.jpg"]),
                       {"type": "input_text", "text": "cut.png:"}, image_part(results["cut.jpg"]), {"type": "input_text", "text": FIX}]
    (folder / "history.json").write_text(json.dumps({"rounds": history, "tokens": spent}, indent=1))
    print("tokens", spent)
