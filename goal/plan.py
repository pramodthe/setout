"""Plan a detail from its drawing: one Muse call that lists every part, says how it is built and where, and records how the parts
relate. It places nothing. The relations are read before any geometry exists and stay fixed, so the engine repairs positions and
never decides what belongs where. A part the drawing doesn't show is marked implied, and stays labelled as inference.

    python -m goal.plan <drawing>
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import hatching, labels, regions as regions_
from .catalogue_labels import CATEGORIES
from .part_search import OUT

FORMS = ("solid", "units", "sheet", "spaced", "none")
RELATIONS = ("sits_on", "against", "laps_over", "fixed_into", "spans_between", "set_in")
SLIVER, SLIVER_SHEET = 0.1, 0.0005  # fill of its own box, and share of the sheet: smaller on both is a gap between hatch lines
DOT, DOT_REACH, DOT_GAP, STIPPLE = 12, 6, 60, 0.002  # pixels in a dot, how far dots reach to join, how far a strip may thin out, and share of the sheet that makes a stippled part
TRUSTED = 0.75  # a relation below this confidence doesn't bind the engine
PART = {"type": "object", "additionalProperties": False, "required": ["id", "name", "query", "category", "form", "seen", "regions", "path", "spacing_mm", "reason"], "properties": {
    "id": {"type": "string"}, "name": {"type": "string"}, "query": {"type": "string"}, "category": {"type": "string", "enum": [c for c in CATEGORIES if c not in ("fragment", "unknown")]},
    "form": {"type": "string", "enum": list(FORMS)}, "seen": {"type": "string", "enum": ["drawn", "implied"]}, "regions": {"type": "array", "items": {"type": "integer"}},
    "path": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}, "spacing_mm": {"type": "number"}, "reason": {"type": "string"}}}
RELATION = {"type": "object", "additionalProperties": False, "required": ["subject", "relation", "target", "second_target", "confidence"], "properties": {
    "subject": {"type": "string"}, "relation": {"type": "string", "enum": list(RELATIONS)}, "target": {"type": "string"}, "second_target": {"type": "string"}, "confidence": {"type": "number"}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["assembly", "direction_of_run", "parts", "relations", "unsure"], "properties": {
    "assembly": {"type": "string"}, "direction_of_run": {"type": "string"}, "parts": {"type": "array", "items": PART}, "relations": {"type": "array", "items": RELATION},
    "unsure": {"type": "array", "items": {"type": "string"}}}}
PROMPT = """This is a 2D cross-section of a building detail. Red numbers mark its closed regions. Plan how it is built in 3D: the section is one cut, and the assembly continues
into the page. You decide what every part is and how parts relate. You do not place anything: an engine does that.

List every part, one entry per kind of part. A part may cover many regions. Fields:
id: short, unique. name: what a builder calls it. query: words to search a catalogue of 3D components. category: the closest category.
form: solid = one piece that runs unbroken into the page, taken from its outline (concrete, plaster, a bed of mortar).
units = separate units repeated into the page with joints, filling its regions (brick, block).
sheet = a thin sheet running into the page along a line (a membrane, a flashing, also when drawn dashed). Give it as regions if a region is that line, otherwise as path.
spaced = separate objects repeated into the page at intervals (a tie, a screw, a weep vent).
none = not a part: an air gap, a line around another part, a label, a symbol.
seen: drawn if the drawing shows it, implied if it isn't drawn but this assembly can't be built without it. A section cuts between repeated things and so often misses them: where two parts stand
apart with nothing shown joining them, ask what holds them together. Only list an implied part that is really needed.
regions: its numbers. Every number must appear in exactly one part, with form none where it is not a part. Text or a symbol written on a region doesn't stop it being a part: give the region to the part it
belongs to, and list anything the symbol means as a part of its own. A line drawn around another part is that part's edge, so give it form none.
path: [x, y] points, each 0 to 1 of the image width and height, for a part with no region of its own. For a sheet, points along its line, in order from one end. For a solid, its outline, such as a
stippled strip no number sits on. For a spaced part, the point where it crosses the section, or its two ends if it bridges something. Otherwise empty.
spacing_mm: only for spaced parts, the usual interval. It is your assumption and will be labelled so. Otherwise 0.
reason: one short line on what in the drawing tells you.

Then list how parts relate, using part ids. Only list what the drawing shows or what building it requires:
sits_on: subject rests on the top of target. against: subject stands against the side of target. laps_over: subject lies over target and runs past its edge.
fixed_into: subject enters target to hold something. spans_between: subject bridges a gap, from target to second_target. set_in: subject sits inside target, such as a tie in a bed joint or a vent in a joint.
confidence: 0 to 1. second_target is empty unless the relation is spans_between.
assembly: what this detail is. direction_of_run: what runs into the page. unsure: anything you can't tell from the drawing.
Regions, as share of the image:
"""


ADDITIONS = {"type": "object", "additionalProperties": False, "required": ["parts", "relations"], "properties": {"parts": {"type": "array", "items": PART}, "relations": {"type": "array", "items": RELATION}}}
CHECK = """This is a section of a building detail, with a plan of its parts below. A section is one thin cut, so it misses what repeats between cuts and what sits beyond it. Think through how a builder puts
this up, step by step, and list only the parts they would need that the plan leaves out, with the relations that place them. Two parts that stand apart need something holding them together. A sheet that ends
needs something securing that end. Don't repeat a part. Don't add what is merely good practice elsewhere. An empty list is a fine answer. Each part: form spaced or sheet, seen implied, regions empty, path
giving where it crosses the section as [x, y] points 0 to 1 of the image (its two ends if it bridges a gap), spacing_mm your usual interval, reason why it must exist. Plan:
"""


def clean(regions, size) -> list:
    """Regions worth a number. Hatching leaves slivers between its lines, small and filling little of their box. A heavy line fills as little, but it is long."""
    return [r for r in regions if r.area / (r.width * r.height) >= SLIVER or r.area >= SLIVER_SHEET * size[0] * size[1]]


def speckled(picture, regions, size) -> list:
    """Stipple has no outline, so no region finds it: it is ink dots close together. Sparse dots, like a concrete fill's, never join up."""
    ink = np.asarray(Image.open(picture).convert("L")) < regions_.INK
    dots = np.zeros_like(ink)
    for ys, xs in hatching._components(ink):
        if len(ys) <= DOT:
            dots[ys, xs] = True
    grown = dots
    for _ in range(DOT_REACH):
        grown = grown | np.roll(grown, 1, 0) | np.roll(grown, -1, 0) | np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
    number, out = max(r.id for r in regions), []
    for ys, xs in hatching._components(grown):
        if len(ys) >= STIPPLE * size[0] * size[1]:
            out.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, ys, xs])
    joined = []
    for piece in sorted(out, key=lambda p: p[1]):  # a strip of stipple thins out in places and is still one strip
        last = joined[-1] if joined else None
        if last and min(piece[2], last[2]) - max(piece[0], last[0]) >= 0.7 * (piece[2] - piece[0]) and piece[1] - last[3] <= DOT_GAP:
            last[:4] = [min(last[0], piece[0]), last[1], max(last[2], piece[2]), max(last[3], piece[3])]
            last[4], last[5] = np.r_[last[4], piece[4]], np.r_[last[5], piece[5]]
        else:
            joined.append(piece)
    regions_out = []
    for x0, y0, x1, y1, ys, xs in joined:
        spans = np.full((y1 - y0, 2), np.nan)
        for y in np.unique(ys):
            spans[y - y0] = xs[ys == y].min(), xs[ys == y].max() + 1
        steady = np.nanmedian(spans, 0)  # a dotted edge has no line to follow, so the strip's edge is its typical one, wherever dots fall short
        number += 1
        rows = {y: [(int(steady[0]), int(steady[1]))] for y in range(y0, y1)}
        regions_out.append(regions_.Region(number, rows, (int(steady[0]), y0, int(steady[1]), y1), int((steady[1] - steady[0]) * (y1 - y0))))
    return regions_out


def ask(drawing, regions, size) -> tuple[dict, dict]:
    from app import meta_client
    rows = [{"id": r.id, "x": round((r.box[0] + r.box[2]) / 2 / size[0], 3), "y": round((r.box[1] + r.box[3]) / 2 / size[1], 3), "width": round(r.width / size[0], 3), "height": round(r.height / size[1], 3)} for r in regions]
    picture = base64.b64encode(labels.overlay(drawing, regions)).decode()
    content = [{"type": "input_text", "text": PROMPT + json.dumps(rows)}, {"type": "input_image", "image_url": f"data:image/png;base64,{picture}"}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "high"}, "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "detail_plan", "schema": SCHEMA, "strict": True}}}, timeout=420)
    reply = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    return json.loads(reply), result.get("usage") or {}


def complete(drawing, regions, plan) -> tuple[dict, dict]:
    """A second reading with the plan in hand: what the plan leaves out that a builder would still need. Whatever it adds is implied, and says so."""
    from app import meta_client
    picture = base64.b64encode(labels.overlay(drawing, regions)).decode()
    text = CHECK + json.dumps({k: plan[k] for k in ("assembly", "parts", "relations")})
    content = [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": f"data:image/png;base64,{picture}"}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "medium"}, "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "additions", "schema": ADDITIONS, "strict": True}}}, timeout=280)
    reply = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    return json.loads(reply), result.get("usage") or {}


def make(drawing) -> tuple[dict, list, tuple]:
    """The plan for a drawing, kept once made, so a change to the engine spends no tokens."""
    found, size = regions_.with_ink(drawing)
    regions = clean(found, size)
    regions += speckled(drawing, regions, size)
    path = OUT / f"{Path(drawing).stem}.plan.json"
    if path.exists():
        return json.loads(path.read_text()), regions, size
    plan, usage = ask(drawing, regions, size)
    added, more = complete(drawing, regions, plan)
    plan["parts"] += [{**part, "seen": "implied", "regions": []} for part in added["parts"]]
    plan["relations"] += added["relations"]
    plan["tokens"] = usage.get("total_tokens", 0) + more.get("total_tokens", 0)
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=1))
    return plan, regions, size


def faults(plan, regions) -> list:
    """What's wrong with a plan as a plan, before anything is built."""
    out, ids, used = [], {p["id"] for p in plan["parts"]}, {}
    for part in plan["parts"]:
        for region in part["regions"]:
            used[region] = used.get(region, 0) + 1
        if part["form"] == "sheet" and not part["regions"] and len(part["path"]) < 2:
            out.append(f"{part['id']}: a sheet with no line")
    missing = [r.id for r in regions if r.id not in used]
    if missing:
        out.append(f"regions with no part: {missing}")
    out += [f"region {r} is in {n} parts" for r, n in used.items() if n > 1]
    for relation in plan["relations"]:
        for key in ("subject", "target"):
            if relation[key] not in ids:
                out.append(f"relation names no part: {relation[key]}")
    return out


if __name__ == "__main__":
    plan, regions, size = make(sys.argv[1])
    print(len(regions), "regions |", plan["assembly"], "|", plan["direction_of_run"], "| tokens", plan["tokens"])
    for part in plan["parts"]:
        print(f"  {part['id']:18} {part['form']:7} {part['seen']:8} {part['category']:12} {part['regions']} {part['path'][:6]} {part['spacing_mm'] or ''} | {part['name']} | {part['query']} | {part['reason'][:80]}")
    for relation in plan["relations"]:
        print("  ", relation["subject"], relation["relation"], relation["target"], relation["second_target"], relation["confidence"])
    print("unsure:", plan["unsure"])
    print("faults:", faults(plan, regions))
