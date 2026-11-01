"""Muse builds a detail in a live Blender scene, step by step, looking at its own work. It has tools and not one shot: run code against the scene, look from any side, list what's
there, undo. That is what a model gets from a Blender MCP. The code runs only in the no-network sandbox. There are shapes and no components, so every real part is Muse's own.

    python -m goal.blender_live <drawing> <name>
"""
from __future__ import annotations

import ast
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

from PIL import Image

from . import blender_check
from .blender_agent import BANNED
from .reference import DATA

HERE = Path(__file__).resolve().parent
OUT = DATA / "goal" / "live"
ALLOWED = {"blender_kit", "math", "bpy", "bmesh", "mathutils", "random"}
TURNS, TOKENS, WRAP_UP, CHECK_ROOM = 26, 300_000, 0.8, 0.92  # tokens are billed ones, as the app counts them. Past WRAP_UP of them Muse is told to finish, and past CHECK_ROOM the checker is skipped
VIEWS = ("three_quarter", "section", "other_side", "top", "side")
CHECKED = {"isometric": "three_quarter", "isometric other side": "other_side", "front": "section", "side": "side", "top": "top"}
REFUSALS = 3  # finish is sent back this many times at most, then what's left goes to the person
TOOLS = [
    {"type": "function", "name": "run_python", "description": "Run a step of Python against the live scene. Whatever earlier steps built is still there, and constants and functions you defined stay defined. Returns any error, what the scene now holds, and two renders.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["intent", "code"], "properties": {"intent": {"type": "string", "description": "What this step adds, in a few words a person can read"}, "code": {"type": "string"}}}},
    {"type": "function", "name": "look", "description": "Render the scene from other sides.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["views"], "properties": {"views": {"type": "array", "items": {"type": "string", "enum": list(VIEWS)}}}}},
    {"type": "function", "name": "list_parts", "description": "Read what is in the scene: every object with its group, material and extent in mm. Use this to measure. Never run code only to read the scene.",
     "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}}},
    {"type": "function", "name": "undo", "description": "Remove your last step from the scene.", "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}}},
    {"type": "function", "name": "finish", "description": "Ask to end the work, only when the source and your model match and the whole assembly is there. An independent checker then compares your model with the source from every side, and sends it back with what to fix if parts are missing or wrong.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["what", "unsure"], "properties": {"what": {"type": "string", "description": "What this detail is"}, "unsure": {"type": "array", "items": {"type": "string"}}}}},
]
SYSTEM = """You build one construction detail as a real local 3D assembly in a live Blender scene, the way a good detailer would show it to a builder. A person looks at it and must see at once
what each part is, how it meets the next, and how it goes together.

What you are given is either a 2D section or a picture of a 3D detail, perhaps several views of one. From a section, follow the rules for the section below. From a 3D picture, build what it shows, in the same arrangement and with the same parts: choose one cut face to be the section at x = 0, and judge your three_quarter view against the picture, not the section view. Titles, labels and logos in a picture are not parts. Name parts for what they are, never with a brand printed on them.

First understand it. Before any code, say in a few lines what this detail is, every part in it, what the section can't show but must exist, such as the door above a sill or the ties across a cavity, and the order a builder would put them up. A section cuts one slice: the assembly runs on past it. Stop at the local assembly, never a whole building.

Then build in small steps, biggest masses first, then layers, then objects, one step at a time. After every step look at what you got and fix what's wrong before moving on. Never rewrite what is already right.

The frame, sizes in mm: the section lies in the y-z plane at x = 0 and the assembly runs along +x, away from you as you look at the cut face. z is up. Looking at the cut face, +y is on your LEFT, so the drawing's left edge is the largest y and its right edge the smallest. The section view is that same look: when it matches the drawing, your orientation is right, so never mirror a finished scene. Keep the cut face at x = 0 exposed.
Rules:
- Every size is a named constant. The drawing isn't to scale, so nothing is measured from pixels: choose sizes a builder would recognise, and keep the drawing's arrangement and the order of its layers.
- Each real part is its own named object, named as a builder says it, with a group for its layer or assembly. Repeated things are loops, each unit its own object.
- A thing that bends is one sheet(), never split at its folds. A shaped section is a prism() with its real outline, never a box.
- A layer too thin to see, such as a membrane, is drawn thicker and you say so in finish.
- Keep gaps open: a cavity stays empty. Parts meet: nothing floats and nothing passes through something else unless it really does, as a fixing does.
- Clear beats busy. Show what matters to this detail in a run of about 1000 mm, and leave out what doesn't.
Never invent a product name, a standard or an approval. Call finish only when the section view reads like the drawing and nothing that must exist is missing.
"""


def api() -> str:
    source = (HERE / "blender_kit.py").read_text()
    tree = ast.parse(source)
    lines = [f"{n.name}({ast.unparse(n.args)}): {ast.get_docstring(n) or ''}" for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("prism", "box", "sheet", "lathe", "rod", "material")]
    names = ", ".join(re.findall(r'^\s+"(\w+)": _material\(', source, re.M))
    return "Already imported in every step: bpy, math, and these shapes. They take mm:\n" + "\n".join(lines) + f"\nNamed materials: {names}. You may also use bpy and bmesh directly for shapes these can't make."


def check(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] not in ALLOWED for a in node.names):
            raise ValueError(f"only {sorted(ALLOWED)} may be imported")
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] not in ALLOWED:
            raise ValueError(f"only {sorted(ALLOWED)} may be imported")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED:
            raise ValueError(f"{node.func.id}() is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("dunder attributes are not allowed")


def picture(data: bytes, longest=1000) -> dict:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((longest, longest))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=86)
    return {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()}


def sandbox(steps, views, export=False) -> dict:
    """Replay the scene on Modal. Returns {file name: bytes}."""
    import modal
    kit = (HERE / "blender_kit.py").read_text()
    found: dict = {}
    for name, piece in modal.Function.from_name("setout-blender", "live_step").remote_gen(kit, steps, list(views), export):
        found[name] = found.get(name, b"") + piece
    return found


def summary(scene) -> str:
    rows = {}
    for row in scene["parts"]:
        rows.setdefault(row["group"], []).append(row)
    lines = [f"{len(scene['parts'])} objects"]
    for group, held in rows.items():
        names = {}
        for row in held:
            names[row["part"]] = names.get(row["part"], 0) + 1
        lines.append(f"{group}: " + ", ".join(f"{n} x{c}" if c > 1 else n for n, c in list(names.items())[:14]))
    return "\n".join(lines)


class Live:
    """One model working on one scene. `steps` is the scene, so a revision starts from an earlier one's steps."""
    def __init__(self, drawing: bytes, respond, steps=None, intents=None, say=print, folder=None, show=picture, on_step=None, ask=None, features=None):
        self.drawing, self.respond, self.say, self.folder, self.show, self.on_step = drawing, respond, say, folder, show, on_step
        self.ask, self.features, self.checks, self.refused = ask, features, [], 0
        self.steps, self.intents = list(steps or []), list(intents or [])
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
        self.spent, self.result = 0, None

    def run_python(self, args):
        try:
            check(args["code"])
        except (ValueError, SyntaxError) as error:
            return {"error": f"rejected: {error}"}, []
        found = sandbox([*self.steps, args["code"]], ("three_quarter", "section"))
        scene = json.loads(found["scene.json"])
        if scene["error"]:
            return {"error": scene["error"], "note": "The step was not kept. The scene is as it was before it."}, []
        self.steps.append(args["code"])
        self.intents.append(args["intent"])
        if self.on_step:
            self.on_step(self, scene, found)
        return {"ok": True, "scene": summary(scene), "printed": scene.get("printed", ""), "seconds": scene["seconds"]}, self.images(found, ("three_quarter", "section"))

    def list_parts(self, _):
        scene = json.loads(sandbox(self.steps, ())["scene.json"])
        return {"parts": [{k: row[k] for k in ("name", "group", "material", "from", "to")} for row in scene["parts"]][:150]}, []

    def look(self, args):
        wanted = [v for v in args["views"] if v in VIEWS][:3] or ["three_quarter"]
        found = sandbox(self.steps, wanted)
        return {"views": wanted}, self.images(found, wanted)

    def undo(self, _):
        if not self.steps:
            return {"error": "nothing to undo"}, []
        self.steps.pop()
        self.intents.pop()
        return {"ok": True, "steps_left": len(self.steps)}, []

    def finish(self, args):
        if not self.ask or not self.steps or self.spent > TOKENS * CHECK_ROOM:
            self.result = args
            return {"ok": True}, []
        self.say("[0] check Comparing the model with your drawing from every side")
        names = list(CHECKED)
        found = sandbox(self.steps, list(CHECKED.values()))
        result, tokens = blender_check.check(self.ask, self.drawing, self.features, {n: found[f"{CHECKED[n]}.jpg"] for n in names if f"{CHECKED[n]}.jpg" in found})
        self.spent += tokens
        self.checks.append(result)
        self.say(f"[0] check {result['right']} of {result['total']} right" + (f". To fix: {'; '.join(r['what'] for r in result['blocking'][:4])}" if result["blocking"] else ""))
        if result["passed"] or self.refused >= REFUSALS:
            self.result = {**args, "unsure": [*args.get("unsure", []), *[f"{r['what']}: {r['note'] or r['state']}" for r in result["blocking"]]]}
            return {"ok": True}, []
        self.refused += 1
        return {"refused": True, "checker_says": blender_check.fix_list(result), "note": "An independent checker compared your model with the source. Fix these, look again, then call finish."}, self.images(found, ("three_quarter", "other_side"))

    def images(self, found, views) -> list:
        out = []
        for view in views:
            data = found.get(f"{view}.jpg")
            if data:
                if self.folder:
                    (self.folder / f"latest_{view}.jpg").write_bytes(data)
                out += [{"type": "input_text", "text": f"{view}:"}, self.show(data)]
        return out

    def run(self, brief=None):
        if self.ask and not self.features:
            self.features, tokens = blender_check.read_source(self.ask, self.drawing)
            self.spent += tokens
            self.say(f"[0] check Noted {len(self.features['items'])} things the model must show")
        text = brief or "Here is the detail. Say what it is, then build it."
        content = [{"type": "input_text", "text": text}, self.show(self.drawing, 1400)]
        if self.steps:  # a revision: it sees the scene as it stands
            found = sandbox(self.steps, ("three_quarter", "section"))
            content += self.images(found, ("three_quarter", "section"))
        conversation = [{"type": "message", "role": "user", "content": content}]
        warned = False
        for turn in range(TURNS):
            if self.spent > TOKENS:
                self.say(f"stopping: {self.spent} tokens")
                break
            response = self.respond({"instructions": SYSTEM + "\n" + api(), "tools": TOOLS, "max_output_tokens": 32768, "reasoning": {"effort": "medium"}, "input": conversation})
            usage = response.get("usage") or {}
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
            self.spent += (usage.get("input_tokens") or 0) - cached + (usage.get("output_tokens") or 0)
            conversation += response["output"]
            calls = [o for o in response["output"] if o.get("type") == "function_call"]
            words = "".join(p.get("text", "") for o in response["output"] if o.get("type") == "message" for p in o.get("content", []))
            if words:
                self.say(f"[{turn}] {words[:600]}")
            if not calls:
                conversation.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Carry on with your tools. Call finish when it's done."}]})
                continue
            pictures = []
            for call in calls:
                args = json.loads(call["arguments"] or "{}")
                started = time.time()
                result, images = getattr(self, call["name"])(args)
                self.say(f"[{turn}] {call['name']} {args.get('intent', '')[:70]} -> {str(result)[:170]} ({time.time() - started:.0f}s, {self.spent} tokens)")
                conversation.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)})
                pictures += images
            if pictures:
                conversation.append({"type": "message", "role": "user", "content": pictures})
            if self.spent > TOKENS * WRAP_UP and not warned:
                warned = True
                conversation.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "You are near the budget. Fix only what is plainly wrong, then call finish."}]})
            if self.folder:
                (self.folder / "steps.json").write_text(json.dumps({"steps": self.steps, "intents": self.intents, "tokens": self.spent, "result": self.result}, indent=1))
            if self.result:
                break
        if self.ask and self.steps and self.result is None:  # out of turns: the person still gets a checked scene, and what's left
            self.refused = REFUSALS
            self.finish({"what": self.features["detail"], "unsure": ["Muse ran out of turns before it finished"]})
        return self


def ask_direct(prompt, images, schema, name) -> tuple[dict, int]:
    from . import plan
    return plan.direct(prompt, images, schema, name)


def respond_direct(payload) -> dict:
    from app import meta_client
    return meta_client.respond_tools({"model": meta_client.default_model(), **payload}, timeout=280)


if __name__ == "__main__":
    live = Live(Path(sys.argv[1]).read_bytes(), respond_direct, folder=OUT / sys.argv[2], ask=ask_direct).run()
    print("steps", len(live.steps), "tokens", live.spent, "finished", bool(live.result))
    found = sandbox(live.steps, VIEWS, export=True)
    for name, data in found.items():
        (live.folder / name).write_bytes(data)
    print(summary(json.loads(found["scene.json"])))
