"""Plan once, generate independent components concurrently, then check the assembly.

The plan owns shared coordinates. Component code runs only through loop.sandbox;
library meshes are pinned into the saved steps so replay never needs the library.
"""
from __future__ import annotations

import ast
import builtins
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import symtable
import textwrap
import time

from app.repository import BudgetExceeded
from . import checker, library, loop

WORKERS = 3
MAX_COMPONENTS = 6
STRING = {"type": "string"}
NUMBER = {"type": "number"}


def obj(**properties):
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def array(item, **limits):
    return {"type": "array", "items": item, **limits}


COMPONENT = obj(id=STRING, intent=STRING, specification=STRING, source_items=array({"type": "integer"}), asset_ids=array(STRING))
PLAN = obj(title=STRING, detail=STRING, assumptions=array(STRING),
           parameters=array(obj(name=STRING, value=NUMBER)),
           outlines=array(obj(name=STRING, points=array(array(NUMBER, minItems=2, maxItems=2), minItems=2))),
           components=array(COMPONENT, minItems=1, maxItems=MAX_COMPONENTS))
CODE = obj(code=STRING, assumptions=array(STRING))
REPAIR = obj(replacements=array(obj(id=STRING, code=STRING)), assumptions=array(STRING))

PLAN_PROMPT = """Plan this local construction detail for several component builders working concurrently.
Return an assembly specification, not Python. Read the source closely: preserve outlines, folded edges,
thin layers, openings, terminations and the visible fixing shapes. Labels and logos are not geometry.
Use 3 to 5 component groups (at most 6); a group may contain several related real parts.
Assign every visible part to exactly one group, including the small connections. Don't add unseen products.
The independent source checklist follows the catalog. Cover every item: source_items records which item
ids each component implements (relations may span groups). Never merge separate visible plies into one
sheet just because their colours match. A field layer and its overlapping flashing need separate geometry.

The shared frame is mm: x=0 is the exposed section, the assembly extends along +x; z is up;
+y is the LEFT of the drawing. Use a compact run, typically 600-1000 mm. All sizes are assumptions,
never inferred from pixel measurements. State assumptions and any thin-layer exaggeration.
Define every shared level, offset, thickness, extent and repeated fixing location in parameters, with
unique UPPER_SNAKE_CASE names. Give important closed section profiles and open fold paths as named
outlines in (y,z); no duplicate closing point. These are assumed model coordinates, not measured values.
Resolve touching faces and fold offsets NOW so independent builders cannot invent different interfaces.
For sheet(), thickness offsets LEFT of each directed path segment; account for that in adjoining faces.
Keep cavities open. Describe true shape detail (radii, returns, beads, grooves, heads) where the drawing
supports it. A bent component is continuous; a shaped profile is not a box. Avoid arbitrary embellishment.
Each component specification must name the exact shared parameters/outlines, contacts and its scope.

The optional catalog contains the owner's existing meshes, in mm, unscaled. Local z is the longest
axis, not necessarily a screw or nut's working axis. Use an asset only when its shape AND dimensions
are suitable; do not use a mismatched part merely because its name matches. Prefer a compatible detailed
fixing to a crude cylinder. Continuous layers and beads usually need procedural geometry. List selected
asset_ids per component; an empty list is fine. Product names in the catalog must not become scene labels.
Catalog:
"""

COMPONENT_PROMPT = """Build ONLY the assigned component group from this shared assembly plan and source.
Other groups are being built concurrently. Do not build their parts, read their objects, modify the scene,
delete geometry, reset Blender, render, export, or change shared materials. Code executes in a function
scope. P contains the plan's parameters; OUTLINES contains its (y,z) paths. Honour those coordinates
exactly at interfaces. Do not independently choose new slab levels, thicknesses or overall extents.
Each real part is a named object; use the assigned component id as its group. Use semantic names,
no brands. Keep the section x=0 exposed. Preserve folds, fillets, openings and actual edge profiles;
use curves/lathe/meshes or bpy/bmesh for shapes the generic kit cannot express. Cylinders are not a
substitute for shaped hardware. Repeated objects use loops. Millimetres in kit; bpy uses metres.
Use rounded_path() for curved sheet bends instead of reimplementing fillet math. Use 48 or more sides
on visible turned heads and beads. Detail means correct shapes and connections, not ornamental clutter.
Make a complete component in one concise program. Never return markdown or explanations in code.
Do not reference variables from another component. No global/nonlocal statements or star imports.

Selected library assets are available through:
place_asset(asset_id, name, origin, direction, material, group, roll=0)
asset_id must be a literal string from this component's asset_ids. It rigidly places the original mesh
at origin; local +z follows direction, with roll in degrees. Never scale, stretch or edit it. If unsuitable,
omit it and model the actual shape. Origin is the centre of the local base, not a screw's tip necessarily.
Return any additional assumptions honestly.\n"""


def validate_plan(plan, catalog, features=None):
    components = plan["components"]
    if not 1 <= len(components) <= MAX_COMPONENTS:
        raise ValueError("the assembly needs one to six component groups")
    for key in ("parameters", "outlines", "components"):
        names = [p["id" if key == "components" else "name"] for p in plan[key]]
        pattern = r"[A-Za-z][A-Za-z0-9_-]{0,63}" if key == "components" else r"[A-Za-z][A-Za-z0-9_]{0,63}"
        if len(names) != len(set(names)) or any(not re.fullmatch(pattern, n) for n in names):
            raise ValueError(f"{key} must have unique simple identifiers")
    for p in plan["parameters"]:
        if not isinstance(p["value"], (int, float)) or not math.isfinite(p["value"]):
            raise ValueError("parameters must be finite numbers")
    for outline in plan["outlines"]:
        if len(outline["points"]) < 2 or any(len(p) != 2 or any(not math.isfinite(v) for v in p) for p in outline["points"]):
            raise ValueError("outlines must contain finite (y,z) points")
    known = {a["id"] for a in catalog}
    if any(set(c["asset_ids"]) - known for c in components):
        raise ValueError("the plan requested an unknown library asset")
    if features:
        required = {i["id"] for i in features["items"]}
        covered = {i for c in components for i in c["source_items"]}
        if required != covered:
            raise ValueError(f"source checklist coverage differs: missing {sorted(required - covered)}, unknown {sorted(covered - required)}")


def component_step(plan, component, code, meshes):
    """Validate, isolate variables, and embed only the assets actually referenced."""
    loop.check(code)
    tree, used = ast.parse(code), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)) or (isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)):
            raise ValueError("component variables must stay local")
        if isinstance(node, ast.Name) and node.id == "place_asset" and not isinstance(node.ctx, ast.Load):
            raise ValueError("place_asset cannot be redefined")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "place_asset":
            ident = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
            if ident not in component["asset_ids"] or ident not in meshes:
                raise ValueError("place_asset needs a literal, available asset id selected in the plan")
            used.add(ident)
    params = {p["name"]: p["value"] for p in plan["parameters"]}
    outlines = {p["name"]: p["points"] for p in plan["outlines"]}
    body = f"P = {params!r}\nOUTLINES = {outlines!r}\n"
    if used:
        body += f"_assets = { {ident: meshes[ident] for ident in sorted(used)}!r}\n"
        body += ("def place_asset(asset_id, name, origin, direction, material, group, roll=0):\n"
                 "    return mesh_part(name, _assets[asset_id], origin, direction, material, group, roll)\n")
    body += code
    name = "_component_" + component["id"].replace("-", "_")
    step = f"def {name}():\n" + textwrap.indent(body, "    ") + f"\n{name}()\ndel {name}\n"
    loop.check(step)
    kit = ast.parse((loop.HERE / "kit.py").read_text())
    exports = next(ast.literal_eval(n.value) for n in kit.body if isinstance(n, ast.Assign) and
                   any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets))
    provided = set(dir(builtins)) | set(exports) | {"bpy", "math", "__name__"}
    scopes = list(symtable.symtable(step, "component", "exec").get_children())
    while scopes:
        scope = scopes.pop()
        missing = {s.get_name() for s in scope.get_symbols() if s.is_referenced() and s.is_global()} - provided
        if missing:
            raise ValueError("undefined component names: " + ", ".join(sorted(missing)))
        scopes.extend(scope.get_children())
    return step, sorted(used)


class Build:
    def __init__(self, drawing, ask, say=print, on_step=None):
        self.drawing, self.ask, self.say, self.on_step = drawing, ask, say, on_step
        self.steps, self.intents, self.checks, self.codes = [], [], [], []
        self.features, self.last_check, self.result, self.found = None, None, None, None
        self.spent, self.plan, self.timings, self.used_assets = 0, None, {}, []

    def _component(self, component, catalog, meshes):
        prompt = (COMPONENT_PROMPT + loop.api() + "\nAssembly plan:\n" + json.dumps(self.plan) +
                  "\nAssigned component:\n" + json.dumps(component) + "\nSelected assets:\n" +
                  json.dumps([a for a in catalog if a["id"] in component["asset_ids"]]))
        spent = 0
        for attempt in range(2):
            answer = None
            try:
                answer, tokens = self.ask(prompt, [self.drawing], CODE, "studio_component")
                spent += tokens
                step, used = component_step(self.plan, component, answer["code"], meshes)
                return answer, step, used, spent
            except (ValueError, SyntaxError) as error:
                if attempt:
                    raise
                if answer:
                    prompt += "\nYour rejected program:\n" + answer["code"]
                prompt += "\nCorrect this error: " + str(error) + "\nUse concise code and the supplied kit helpers; do not reimplement them."

    def _render(self):
        # One assembly render/export, not a remote round trip after each part.
        found = loop.sandbox(self.steps, [{"view": v, "size": [2000, 1500]} if v == "section" else v for v in loop.VIEWS], export=True)
        scene = json.loads(found["scene.json"])
        if not scene["error"] and self.on_step:
            self.on_step(self, scene, found)
        return found, scene

    def _check(self, found):
        self.say("Comparing the assembled model with the source")
        result, tokens = checker.check(self.ask, self.drawing, self.features,
                                      {name: found[f"{view}.jpg"] for name, view in loop.CHECKED.items()})
        self.spent += tokens
        self.checks.append(result)
        self.say(f"{result['right']} of {result['total']} source items visible and correct")
        return result

    def _repair(self, found, problem, meshes):
        prompt = (COMPONENT_PROMPT + loop.api() + "\nRepair ONLY affected groups; return complete replacement programs for them. "
                  "All other groups stay byte-for-byte unchanged. Keep shared interfaces unless the reported defect requires both neighbours to change. "
                  "The shared plan can contain faulty paths: if a sheet crosses itself, correct the actual path coordinates in the replacement program. "
                  "Reusing the same broken OUTLINES entry without correcting its vertices is not a repair. "
                  "Do not add features the source doesn't show. An empty replacement list means you cannot safely improve it.\n"
                  "Assembly plan:\n" + json.dumps(self.plan) + "\nCurrent component programs:\n" +
                  json.dumps([{**c, "code": code["code"]} for c, code in zip(self.plan["components"], self.codes)]) +
                  "\nProblems:\n" + problem)
        pictures = [found[f"{v}.jpg"] for v in ("section", "three_quarter", "other_side") if f"{v}.jpg" in found]
        answer, tokens = self.ask(prompt, [self.drawing, *pictures], REPAIR, "studio_repair")
        self.spent += tokens
        by_id = {c["id"]: k for k, c in enumerate(self.plan["components"])}
        steps, codes, assets, seen = list(self.steps), list(self.codes), list(self.used_assets), set()
        for replacement in answer["replacements"]:
            ident = replacement["id"]
            if ident not in by_id or ident in seen:
                raise ValueError("repair returned an unknown or duplicate component")
            seen.add(ident)
            k = by_id[ident]
            steps[k], assets[k] = component_step(self.plan, self.plan["components"][k], replacement["code"], meshes)
            codes[k] = {"code": replacement["code"], "assumptions": answer["assumptions"]}
        self.steps, self.codes, self.used_assets = steps, codes, assets
        return bool(seen)

    def run(self):
        started = time.monotonic()
        catalog = [{k: p[k] for k in ("id", "name", "kind", "size", "triangles")} for p in library.index()][:60]
        self.say("Reading the source independently before planning the assembly")
        self.features, tokens = checker.read_source(self.ask, self.drawing)
        self.spent += tokens
        self.say("Planning shared interfaces and assigning every source item")
        prompt = PLAN_PROMPT + json.dumps(catalog) + "\nSource checklist:\n" + json.dumps(self.features)
        for attempt in range(2):
            self.plan, tokens = self.ask(prompt, [self.drawing], PLAN, "studio_plan")
            self.spent += tokens
            try:
                validate_plan(self.plan, catalog, self.features)
                break
            except ValueError as error:
                if attempt:
                    raise
                prompt += "\nRejected plan:\n" + json.dumps(self.plan) + "\nCorrect this error: " + str(error)
        self.timings["planning_seconds"] = round(time.monotonic() - started, 2)
        meshes = library.meshes([a for c in self.plan["components"] for a in c["asset_ids"]])
        self.say(f"Building {len(self.plan['components'])} component groups, up to {WORKERS} at a time")
        mark = time.monotonic()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(self._component, c, catalog, meshes) for c in self.plan["components"]]
            # Preserve plan order regardless of completion order (also the undo/playback order).
            for component, future in zip(self.plan["components"], futures):
                answer, step, used, tokens = future.result()
                self.codes.append(answer)
                self.steps.append(step)
                self.used_assets.append(used)
                self.spent += tokens
                self.intents.append(component["intent"])
        self.timings["components_seconds"] = round(time.monotonic() - mark, 2)
        self.say("Assembling the components and preparing the first preview")
        found, scene = self._render()
        for _ in range(2):
            if not scene["error"]:
                break
            self.say("Repairing a component execution error")
            if not self._repair(found, scene["error"], meshes):
                raise ValueError(scene["error"])
            found, scene = self._render()
        if scene["error"]:
            raise ValueError(scene["error"])
        self.timings["preview_seconds"] = round(time.monotonic() - started, 2)
        notes = []
        try:
            self.last_check = self._check(found)
            if not self.last_check["passed"]:
                saved = (list(self.steps), list(self.codes), list(self.used_assets), found, self.last_check)
                self.say("Repairing the reported defects, preserving the other components")
                try:
                    if self._repair(found, checker.fix_list(self.last_check), meshes):
                        candidate, scene = self._render()
                        if scene["error"]:
                            raise ValueError(scene["error"])
                        report = self._check(candidate)
                        if checker.quality(report) > checker.quality(saved[-1]):
                            raise ValueError("the repair scored worse than the earlier checked model")
                        found, self.last_check = candidate, report
                except (BudgetExceeded, ValueError, SyntaxError) as error:
                    self.steps, self.codes, self.used_assets, found, self.last_check = saved
                    notes.append("Kept the earlier checked model: " + str(error)[:180])
                    self.say("Keeping the earlier checked assembly")
        except BudgetExceeded:
            notes.append("The budget ran out before the final visual check.")
        self.found = found
        if not self.last_check or not self.last_check["passed"]:
            notes.append("The source check is incomplete; review the remaining findings.")
        self.result = {"title": self.plan["title"], "what": self.plan["detail"],
                       "unsure": list(dict.fromkeys([*self.plan["assumptions"], *[a for c in self.codes for a in c["assumptions"]], *notes]))}
        self.timings["total_seconds"] = round(time.monotonic() - started, 2)
        return self.result

    def metadata(self):
        return {"method": "hybrid", "version": 2, "plan": self.plan, "timings": self.timings,
                "library_assets": sorted({a for group in self.used_assets for a in group}), "workers": WORKERS}
