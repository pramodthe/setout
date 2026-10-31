"""Muse makes its own parts, then its own assembly. Our code gives it generic shapes and nothing else.

It reads the section and names the components the assembly needs. It models each one alone, looks at a
render and fixes it. Finished parts go in its own library, and the assembly is built from those.

    modal run -m goal.muse_parts --drawing <png> --name <name>
Model code passes a static check, then runs with the network blocked. Everything lands under data/goal/blender.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .blender_agent import DATA, HERE, ask, check, code_of, flipped, image_part
from .blender_modal import app, render_model_code

MOST_PARTS = 6
HEAD = "from blender_kit import *\nimport math\nstart()\nmaterials()\n"
PLAN = """Read this section. Reply with only JSON: {"assembly": "what this is", "components": [{"name": "snake_case", "what": "one line",
"seen": true or false}]}. List up to 6 reusable components you must model to build the whole local assembly, the most
important first, including ones the slice does not show. Layers that are one simple sheet or slab need no component."""
PART = """Model one reusable component: {name}: {what}. Assembly: {assembly}.
Write one function def {name}(...) with arguments for where it goes and its main sizes, ending with group="...", and a one-line
docstring. Build it only from the shapes, as real and as detailed as the real thing: real profiles, every member, every repeated
unit. Then a line reading exactly # DEMO and one call showing it at typical sizes. No imports, no start(), no finish().
Output one ```python block."""
PART_FIX = """Your component so far:
```python
{code}
```
{result}
Fix it. Say in a few lines what is wrong, then give the whole code again, function, # DEMO line and call, in one ```python block."""
SCENE = """Build the whole assembly from the section, as a scene. Your own components, already defined, are:
{library}
Call them, and use shapes for simple layers. Sizes are named constants at the top. No imports, no start(), no finish(), and don't
redefine your components. Output one ```python block."""
SCENE_FIX = """Your scene so far:
```python
{code}
```
Above are its renders. section is the cut face straight on, with the drawing's left on the left. cut is the three-quarter view.
List in a few lines what is wrong against the drawing, then give the whole corrected scene in one ```python block."""


def run(code: str, kit: str) -> tuple[dict, str]:
    """(results, log). A rejected or failed run comes back as its log, for the model to fix."""
    try:
        check(code)
        results = render_model_code.remote(kit, code)
    except Exception as error:  # noqa: BLE001 - a model's bad code is its feedback
        return {}, f"rejected: {error}"
    return results, results.get("log.txt", b"").decode()


def pictures(results) -> list:
    return [{"type": "input_text", "text": "section:"}, image_part(flipped(results["section.jpg"])),
            {"type": "input_text", "text": "cut:"}, image_part(results["cut.jpg"])]


def signature(code: str) -> str:
    return "\n".join(re.findall(r"^def .*$", code, re.M))


def plan(source) -> tuple[dict, int]:
    text, usage = ask([{"type": "input_text", "text": PLAN}, source])
    found = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    found["components"] = found["components"][:MOST_PARTS]
    return found, usage.get("total_tokens") or 0


@app.local_entrypoint()
def main(drawing: str, name: str, part_rounds: int = 3, scene_rounds: int = 3):
    kit = (HERE / "blender_kit.py").read_text()
    folder = DATA / "goal" / "blender" / name
    folder.mkdir(parents=True, exist_ok=True)
    source = image_part(Path(drawing).read_bytes(), 1400)
    wanted, spent = plan(source)
    (folder / "plan.json").write_text(json.dumps(wanted, indent=1))
    print("plan", json.dumps(wanted)[:700], flush=True)
    library = {}
    for item in wanted["components"]:
        prompt = PART.format(assembly=wanted["assembly"], **{k: item[k] for k in ("name", "what")})
        content, best = [{"type": "input_text", "text": prompt}, source], None
        for number in range(part_rounds):
            started = time.time()
            text, usage = ask(content)
            spent += usage.get("total_tokens") or 0
            code = code_of(text).strip()
            results, log = run(HEAD + code + "\nfinish()\n", kit)
            good = "section.jpg" in results and "Traceback" not in log
            if good:
                best = code
                (folder / f"part_{item['name']}.jpg").write_bytes(results["cut.jpg"])
            print(json.dumps({"part": item["name"], "round": number, "good": good, "chars": len(code), "tokens": usage.get("total_tokens"),
                              "seconds": round(time.time() - started)}), flush=True)
            fed = {"result": "It rendered. Its renders are above." if good else "It failed:\n" + log[-1500:]}
            content = [{"type": "input_text", "text": prompt}, source, *(pictures(results) if good else []),
                       {"type": "input_text", "text": PART_FIX.format(code=code, **fed)}]
        if best:
            library[item["name"]] = best.split("# DEMO")[0].strip()
            (folder / f"part_{item['name']}.py").write_text(library[item["name"]])
    shared = "\n\n".join(library.values())
    prompt = SCENE.format(library="\n".join(signature(code) for code in library.values()))
    content = [{"type": "input_text", "text": prompt}, source]
    for number in range(scene_rounds):
        started = time.time()
        text, usage = ask(content)
        spent += usage.get("total_tokens") or 0
        code = code_of(text).strip()
        whole = HEAD + shared + "\n\n" + code + "\nfinish()\n"
        (folder / f"scene{number}.py").write_text(whole)
        results, log = run(whole, kit)
        good = "section.jpg" in results and "Traceback" not in log
        counted = re.search(r"^parts (\d+)", log, re.M)
        if good:
            (folder / f"scene{number}_section.jpg").write_bytes(flipped(results["section.jpg"]))
            (folder / f"scene{number}_cut.jpg").write_bytes(results["cut.jpg"])
        print(json.dumps({"scene": number, "good": good, "parts": int(counted.group(1)) if counted else None, "tokens": usage.get("total_tokens"),
                          "seconds": round(time.time() - started)}), flush=True)
        follow = pictures(results) if good else [{"type": "input_text", "text": "It failed:\n" + log[-1500:]}]
        content = [{"type": "input_text", "text": prompt}, source, *follow, {"type": "input_text", "text": SCENE_FIX.format(code=code)}]
    print("tokens", spent, "library", sorted(library), flush=True)
