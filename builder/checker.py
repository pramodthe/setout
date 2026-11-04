"""An independent check of a scene against its source. One reading lists what the drawing or picture must show, before any 3D is looked at. Another ticks each item from renders of every
side of the model. Neither sees the builder's words or names, so it can only go by what is there, and code decides from the ticks.

A third reading is the contract: the source's parts and how they relate, read from the source alone and then kept. Its parts are matched to the model's objects by number and extent, never by the builder's
names, and builder/measure.py decides each relation from the geometry. What it finds goes to the person and never to the builder, and the verdict still comes from the ticks alone.
"""
from __future__ import annotations

import io
import json

from PIL import Image

IMPORTANCE = ("must", "should")
STATES = ("right", "wrong", "missing", "cant_tell")
ITEM = {"type": "object", "additionalProperties": False, "required": ["id", "what", "importance"], "properties": {"id": {"type": "integer"}, "what": {"type": "string"}, "importance": {"type": "string", "enum": list(IMPORTANCE)}}}
FEATURES = {"type": "object", "additionalProperties": False, "required": ["title", "detail", "items"], "properties": {"title": {"type": "string"}, "detail": {"type": "string"}, "items": {"type": "array", "items": ITEM}}}
TICK = {"type": "object", "additionalProperties": False, "required": ["id", "state", "note"], "properties": {"id": {"type": "integer"}, "state": {"type": "string", "enum": list(STATES)}, "note": {"type": "string"}}}
TICKS = {"type": "object", "additionalProperties": False, "required": ["ticks", "not_in_source"], "properties": {"ticks": {"type": "array", "items": TICK}, "not_in_source": {"type": "array", "items": {"type": "string"}}}}
READ = """The image shows a building detail: a 2D section, or a picture of a 3D detail. Someone will build it in 3D, and you will judge that later, so list now what a correct model has to show.
title: a short name a person would call this detail, 2 to 5 words. detail: what it is, in a sentence. List every part you can see, and how the main parts meet, each as one item a person could tick by looking at a model. Say which side or level it is on where that matters, as in "brick wall standing on the left end of the slab" or "membrane turned up the wall face".
Be concrete, and list only what this image shows. Titles, labels, logos and dimensions are not items. Don't list colours or textures.
importance: must = the detail is wrong without it. should = it is shown, but a reader would still understand the detail without it.
"""
CHECK = """The first image is the source, a building detail. The others are renders of a 3D model made from it, seen from different sides. Views named isometric show the model at an angle, and front, side and top look straight at it. The front view is the cut face. Front detail images are overlapping close-ups of that same cut face, not extra objects: inspect them for thin layers, corner bends and connections before marking an item cant_tell.
You did not build this model. Judge each item below against the renders, from every view, and be strict.
state: right = you can see it and it is where the source has it. wrong = it is there, but in the wrong place or the wrong shape. missing = it isn't in any view. cant_tell = no view shows that area.
A part that is only hinted at, or stands in for the real shape, is wrong and not right. Check the actual outline, bends, openings, layer order and where parts meet, not just their presence. Do not infer physical dimensions from pixels. note: for wrong or missing, what to change, in one line; for cant_tell, what is hidden or too small to judge. Otherwise empty.
not_in_source: anything large in the model that the source doesn't show. A model may run further than a picture, and may show what must exist behind a section, so list only what plainly doesn't belong.
Views, in order after the source: {views}
Items:
"""
RELATIONS = {
    "on": "a sits on b, directly or on layers between them.",
    "against": "a stands beside b, face to face, touching it.",
    "through": "a really passes into or through b.",
    "wraps": "a bends around a corner of b, touching more than one of its faces.",
    "covers": "a lies over or outside b at a lap or a cap, with a on the outside or on top.",
    "apart": "a and b are kept apart by a real gap.",
    "left_of": "on the image, a is left of b. Only for a 2D section.",
}
SURE = 0.75  # relations the reading is less sure of are not held against the model
PART = {"type": "object", "additionalProperties": False, "required": ["id", "what"], "properties": {"id": {"type": "integer"}, "what": {"type": "string"}}}
RELATION = {"type": "object", "additionalProperties": False, "required": ["a", "relation", "b", "confidence"], "properties": {"a": {"type": "integer"}, "relation": {"type": "string", "enum": list(RELATIONS)}, "b": {"type": "integer"}, "confidence": {"type": "number"}}}
CONTRACT = {"type": "object", "additionalProperties": False, "required": ["parts", "relations"], "properties": {"parts": {"type": "array", "items": PART}, "relations": {"type": "array", "items": RELATION}}}
MATCHES = {"type": "object", "additionalProperties": False, "required": ["matches"], "properties": {"matches": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "objects"], "properties": {"id": {"type": "integer"}, "objects": {"type": "array", "items": {"type": "integer"}}}}}}}
READ_CONTRACT = """The image shows a building detail: a 2D section, or a picture of a 3D detail. List its distinct real parts, each with an integer id and what it is, and then how they relate. A void is not a part.
Relations, a then b:
""" + "\n".join(f"{name}: {meaning}" for name, meaning in RELATIONS.items()) + """
List only what the image plainly shows, and the relations that make this detail work: what sits on what, what turns up what, what laps over what, which gaps stay open, what passes through. No sizes or coordinates. Titles, labels and logos are not parts. confidence: 0.9 to 1 unmistakable, 0.7 to 0.85 clear, 0.4 to 0.6 plausible but unclear. At most 30 relations.
"""
MATCH = """The image is a building detail, and a 3D model was made from it. Below are the detail's parts, and the model's objects, each with a number, a material if it has a standard one, and its extent in mm. In the model x runs along the assembly, the section lies at x = 0, z is up, and +y is on the LEFT of the image, so the image's left edge is the largest y.
For each part, give every object number that is that part. A repeated part can have many objects. If no object is that part, give an empty list. Don't guess from sizes alone: the arrangement and the order of layers decide.
"""


def read_source(ask, drawing) -> tuple[dict, int]:
    return ask(READ, [drawing], FEATURES, "studio_features")


def read_contract(ask, drawing) -> tuple[dict, int]:
    return ask(READ_CONTRACT, [drawing], CONTRACT, "studio_contract")


def match(ask, drawing, contract, objects) -> tuple[dict, int]:
    """{part id: [object names]}. `objects` are rows of the scene, and their names never reach the model."""
    numbered = [{"number": k, "material": row["material"], "from": row["from"], "to": row["to"]} for k, row in enumerate(objects)]
    found, tokens = ask(MATCH + "Parts:\n" + json.dumps(contract["parts"]) + "\nObjects:\n" + json.dumps(numbered), [drawing], MATCHES, "studio_match")
    return {str(m["id"]): [objects[k]["name"] for k in m["objects"] if 0 <= k < len(objects)] for m in found["matches"]}, tokens


def held_to(contract) -> list:
    """The relations the model answers to, by their place in the contract: a person can strike one out."""
    return [(k, r) for k, r in enumerate(contract["relations"]) if r["confidence"] >= SURE and not r.get("struck")]


def relations(contract, matched) -> dict:
    """What the sandbox measures."""
    return {"checks": [{"a": matched.get(str(r["a"]), []), "relation": r["relation"], "b": matched.get(str(r["b"]), [])} for _, r in held_to(contract)]}


def reckon(contract, matched, measured) -> dict:
    """The measurement in a person's words. Overlaps the contract expects are dropped: one it says passes through, or two objects of one part."""
    if not measured or measured.get("error"):
        return {}
    measured = {"loose": measured.get("loose", []), "overlaps": measured.get("overlaps", []), "relations": measured.get("relations") or []}
    if not contract or not matched:
        return {**measured, "rows": []}
    words = {p["id"]: p["what"] for p in contract["parts"]}
    rows = []
    for (index, relation), holds in zip(held_to(contract), measured["relations"]):
        objects = matched.get(str(relation["a"]), []) + matched.get(str(relation["b"]), [])
        rows.append({"index": index, "a": words.get(relation["a"], "?"), "relation": relation["relation"], "b": words.get(relation["b"], "?"), "objects": objects, "state": "unknown" if holds is None else "holds" if holds else "fails"})
    expected = {frozenset((x, y)) for _, r in held_to(contract) if r["relation"] == "through" for x in matched.get(str(r["a"]), []) for y in matched.get(str(r["b"]), []) if x != y}
    together = [set(names) for names in matched.values()]
    overlaps = [o for o in measured["overlaps"] if frozenset((o["a"], o["b"])) not in expected and not any({o["a"], o["b"]} <= names for names in together)]
    return {"rows": rows, "loose": measured["loose"], "overlaps": overlaps}


def verdict(features, ticks) -> dict:
    """Every source item needs visual evidence. Importance orders repairs; it doesn't excuse missing detail."""
    answered = {t["id"]: t for t in ticks["ticks"]}
    rows = []
    for item in features["items"]:
        tick = answered.get(item["id"]) or {"state": "cant_tell", "note": "the checker gave no answer"}
        rows.append({**item, "state": tick["state"], "note": tick["note"]})
    blocking = sorted((r for r in rows if r["state"] != "right"), key=lambda r: r["importance"] != "must")
    extras = ticks["not_in_source"]
    return {"rows": rows, "blocking": blocking, "right": sum(r["state"] == "right" for r in rows), "total": len(rows), "not_in_source": extras, "passed": not blocking and not extras}


def quality(result) -> tuple:
    """Lower is better: preserve essential parts first, then remove inventions and repair details."""
    return (sum(r["importance"] == "must" and r["state"] != "right" for r in result["rows"]),
            len(result["not_in_source"]),
            sum(r["importance"] != "must" and r["state"] != "right" for r in result["rows"]))


def check(ask, drawing, features, views: dict) -> tuple[dict, int]:
    """`views` is {name: jpeg} in the order the checker should look."""
    views = dict(views)
    if "front" in views:
        front = Image.open(io.BytesIO(views["front"])).convert("RGB")
        for label, region in (("top left", (0, 0, 0.6, 0.6)), ("top right", (0.4, 0, 1, 0.6)),
                              ("bottom left", (0, 0.4, 0.6, 1)), ("bottom right", (0.4, 0.4, 1, 1))):
            bounds = tuple(round(v * (front.width if k % 2 == 0 else front.height)) for k, v in enumerate(region))
            out = io.BytesIO()
            front.crop(bounds).save(out, "JPEG", quality=92)
            views[f"front detail {label}"] = out.getvalue()
    items = [{"id": i["id"], "what": i["what"]} for i in features["items"]]
    ticks, tokens = ask(CHECK.replace("{views}", ", ".join(views)) + json.dumps(items), [drawing, *views.values()], TICKS, "studio_check")
    return verdict(features, ticks), tokens


def fix_list(result) -> str:
    return "\n".join([f"- {row['what']}: {row['state']}. {row['note']}" for row in result["blocking"]]
                     + [f"- Not in the source: {item}. Remove it." for item in result["not_in_source"]])
