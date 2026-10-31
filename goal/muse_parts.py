"""Muse makes every part and the whole assembly. Our code gives it generic shapes and a way of working.

The way of working is what a drafter does: list the parts, check the list, block out where each goes and
check that against the drawing, detail each part to fit its block, assemble, then have a separate reader
name the faults and repair only what they name. None of this code holds any geometry.

    modal run -m goal.muse_parts::build --drawing <png> --name <name>
Model code passes a static check, then runs with the network blocked. Everything lands under data/goal/blender.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .blender_agent import DATA, HERE, ask, check, code_of, flipped, image_part
from .blender_modal import app, render_model_code

MOST_PARTS = 8
LAYOUT_ROUNDS, PART_ROUNDS, SCENE_ROUNDS = 2, 2, 3
REPAIRS_PER_ROUND = 2
HEAD = "from blender_kit import *\nimport math\nstart()\nmaterials()\n"
PLAN = """Read this section. Reply with only JSON: {"assembly": "what this is", "components": [{"name": "snake_case", "what": "one line",
"seen": true or false}]}. List up to 6 reusable components to model, the most important first, including ones the slice
does not show. Layers that are one simple sheet or slab need no component."""
PLAN_CHECK = """Here is a plan for building the whole local assembly this section belongs to:
{plan}
Walk through it as a builder, from the ground up and from outside to inside, and past the ends of what the section shows. What
must physically exist that the plan leaves out, such as whatever surrounds or carries what is drawn? Reply with only the
same JSON, corrected, with up to 8 components."""
LAYOUT = """Block out this assembly before any detail. Components: {names}.
First write named constants fixing the assembly: every level (z), every across position (y), every along position (x), and every
overall size, so all parts can share them. Then a line reading exactly # BLOCKOUT, then one simple box() or prism() per
component and per plain layer, in its true place, using those constants, each named after what it is. Touching parts share a
constant, so they meet exactly. No imports, no start(), no finish(). Output one ```python block."""
LAYOUT_FIX = """Your blockout so far:
```python
{code}
```
{result}
Fix it, keeping the # BLOCKOUT line. Give the whole code in one ```python block."""
PART = """Model one reusable component: {name}: {what}. Assembly: {assembly}.
These named constants already exist, and every part shares them, so use them and don't redefine them:
{constants}
Write one function def {name}(group="{name}") that builds this part in its true place from those constants, so it meets its
neighbours exactly. One-line docstring. Use only the shapes, as real and as detailed as the real thing: real profiles, every
member, every repeated unit. Build only this part: no slab, wall or neighbour. No imports, no start(), no finish(). End
with a line reading exactly # DEMO and then {name}(). Output one ```python block."""
PART_FIX = """Your component so far:
```python
{code}
```
{result}
Fix it. Say in a few lines what is wrong, then give the whole code, function, # DEMO line and call, in one ```python block."""
SCENE = """Build the whole assembly as a scene. These already exist, so don't redefine them. Named constants:
{constants}
Your components, each already built in its true place when called:
{library}
Call every component, and add the plain layers from shapes using the same constants. No imports, no start(), no finish().
Output one ```python block."""
SCENE_FIX = """Your scene so far:
```python
{code}
```
A separate reader named these faults against the drawing:
{faults}
Fix what is in the scene. Give the whole corrected scene in one ```python block."""
READER = """You check a 3D model of a building detail against its 2D section drawing. You did not build it. Be exact and unforgiving.
Look at the drawing first, then the two renders: section is the cut face straight on, with the drawing's left on the left, and
cut is a three-quarter view. Name what is wrong: parts missing, layers in the wrong order, gaps closed, parts floating or not
meeting, wrong shapes, or what must exist around the detail that isn't there. Reply with only JSON:
{"faults": [{"part": "the component's name, or scene", "fault": "one line"}]}, worst first, at most 8."""


def run(code: str, kit: str) -> tuple[dict, str, bool]:
    """(results, log, worked). A rejected or failed run comes back as its log, for the model to fix."""
    try:
        check(code)
        results = render_model_code.remote(kit, code)
    except Exception as error:  # noqa: BLE001 - a model's bad code is its feedback
        return {}, f"rejected: {error}", False
    log = results.get("log.txt", b"").decode()
    return results, log, "section.jpg" in results and "Traceback" not in log


def pictures(results) -> list:
    return [{"type": "input_text", "text": "section:"}, image_part(flipped(results["section.jpg"])),
            {"type": "input_text", "text": "cut:"}, image_part(results["cut.jpg"])]


def signature(code: str) -> str:
    return "\n".join(re.findall(r"^def .*$", code, re.M))


def json_in(text: str) -> dict:
    return json.loads(re.search(r"\{.*\}", text, re.S).group(0))


def faults_in(text: str) -> list:
    try:
        found = json_in(text).get("faults", [])
    except (AttributeError, ValueError):
        return []
    return [f for f in found if isinstance(f, dict) and f.get("fault")]


def _say(**row) -> None:
    print(json.dumps(row), flush=True)


class Run:
    def __init__(self, drawing, name):
        self.kit = (HERE / "blender_kit.py").read_text()
        self.folder = DATA / "goal" / "blender" / name
        self.folder.mkdir(parents=True, exist_ok=True)
        self.source = image_part(Path(drawing).read_bytes(), 1400)
        self.spent = 0

    def ask(self, content, **options) -> str:
        text, usage = ask(content, **options)
        self.spent += usage.get("total_tokens") or 0
        return text

    def plan(self) -> dict:
        first = json_in(self.ask([{"type": "input_text", "text": PLAN}, self.source]))
        checked = json_in(self.ask([{"type": "input_text", "text": PLAN_CHECK.format(plan=json.dumps(first))}, self.source]))
        checked["components"] = checked["components"][:MOST_PARTS]
        (self.folder / "plan.json").write_text(json.dumps({"first": first, "checked": checked}, indent=1))
        _say(step="plan", first=[c["name"] for c in first["components"]], checked=[c["name"] for c in checked["components"]])
        return checked

    def read(self, results) -> list:
        text = self.ask([{"type": "input_text", "text": "The section drawing:"}, self.source, *pictures(results)], instructions=READER)
        return faults_in(text)

    def layout(self, wanted) -> str:
        names = ", ".join(c["name"] for c in wanted["components"])
        prompt = LAYOUT.format(names=names)
        content, best = [{"type": "input_text", "text": prompt}, self.source], None
        for number in range(LAYOUT_ROUNDS):
            code = code_of(self.ask(content)).strip()
            results, log, worked = run(HEAD + code.replace("# BLOCKOUT", "") + "\nfinish()\n", self.kit)
            faults = self.read(results) if worked else []
            if worked and "# BLOCKOUT" in code:
                best = code
                (self.folder / f"blockout{number}.jpg").write_bytes(flipped(results["section.jpg"]))
            _say(step="layout", round=number, worked=worked, faults=len(faults))
            result = "A reader named these faults:\n" + json.dumps(faults) if worked else "It failed:\n" + log[-1500:]
            content = [{"type": "input_text", "text": prompt}, self.source, *(pictures(results) if worked else []),
                       {"type": "input_text", "text": LAYOUT_FIX.format(code=code, result=result)}]
        if not best:
            raise RuntimeError("no blockout worked")
        (self.folder / "layout.py").write_text(best)
        return best.split("# BLOCKOUT")[0].strip()

    def part(self, item, constants, assembly, code=None, note="") -> str | None:
        """Model a part, or with `code` and `note` repair it. Returns the function, or what it was."""
        prompt = PART.format(assembly=assembly, constants=constants, name=item["name"], what=item["what"])
        content = [{"type": "input_text", "text": prompt}, self.source]
        if code:
            content.append({"type": "input_text", "text": PART_FIX.format(code=code + f"\n# DEMO\n{item['name']}()", result=note)})
        best = code
        for number in range(1 if code else PART_ROUNDS):
            written = code_of(self.ask(content)).strip()
            results, log, worked = run(HEAD + constants + "\n" + written + "\nfinish()\n", self.kit)
            if worked:
                best = written.split("# DEMO")[0].strip()
                (self.folder / f"part_{item['name']}.jpg").write_bytes(results["cut.jpg"])
            _say(step="part", part=item["name"], round=number, worked=worked, repair=bool(code))
            result = "It rendered. Its renders are above." if worked else "It failed:\n" + log[-1500:]
            content = [{"type": "input_text", "text": prompt}, self.source, *(pictures(results) if worked else []),
                       {"type": "input_text", "text": PART_FIX.format(code=written, result=result)}]
        return best

    def scene(self, wanted, constants, library) -> None:
        items = {c["name"]: c for c in wanted["components"]}
        prompt = SCENE.format(constants=constants, library="\n".join(signature(code) for code in library.values()))
        content = [{"type": "input_text", "text": prompt}, self.source]
        for number in range(SCENE_ROUNDS):
            code = code_of(self.ask(content)).strip()
            whole = HEAD + constants + "\n\n" + "\n\n".join(library.values()) + "\n\n" + code + "\nfinish()\n"
            (self.folder / f"scene{number}.py").write_text(whole)
            results, log, worked = run(whole, self.kit)
            faults = self.read(results) if worked else [{"part": "scene", "fault": "It failed:\n" + log[-1500:]}]
            counted = re.search(r"^parts (\d+)", log, re.M)
            if worked:
                (self.folder / f"scene{number}_section.jpg").write_bytes(flipped(results["section.jpg"]))
                (self.folder / f"scene{number}_cut.jpg").write_bytes(results["cut.jpg"])
            (self.folder / f"scene{number}_faults.json").write_text(json.dumps(faults, indent=1))
            _say(step="scene", round=number, worked=worked, parts=int(counted.group(1)) if counted else None, faults=len(faults))
            if number == SCENE_ROUNDS - 1:
                break
            named = [f for f in faults if f.get("part") in library][:REPAIRS_PER_ROUND]
            for fault in named:
                library[fault["part"]] = self.part(items[fault["part"]], constants, wanted["assembly"], library[fault["part"]],
                                                   "A reader named this fault in the assembly: " + fault["fault"])
            rest = [f for f in faults if f not in named]
            content = [{"type": "input_text", "text": prompt}, self.source, *(pictures(results) if worked else []),
                       {"type": "input_text", "text": SCENE_FIX.format(code=code, faults=json.dumps(rest))}]


@app.local_entrypoint()
def build(drawing: str, name: str):
    job = Run(drawing, name)
    wanted = job.plan()
    constants = job.layout(wanted)
    library = {}
    for item in wanted["components"]:
        made = job.part(item, constants, wanted["assembly"])
        if made:
            library[item["name"]] = made
    (job.folder / "library.json").write_text(json.dumps(library, indent=1))
    job.scene(wanted, constants, library)
    print("tokens", job.spent, "library", sorted(library), flush=True)
