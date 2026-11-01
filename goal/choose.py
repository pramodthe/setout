"""Muse chooses the catalogue part for each planned part from a shortlist the search made, looking at their renders, and says how units are bonded and fixings repeat.
The engine then carries that out and reports anything that doesn't fit.
"""
from __future__ import annotations

import io
import json

from PIL import Image, ImageDraw, ImageFont

from .library import STORE

SIDE = 170
LABEL = 150
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["choices"], "properties": {"choices": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["part", "component", "bond", "spacing_mm", "every", "why"], "properties": {
        "part": {"type": "string"}, "component": {"type": "integer"}, "bond": {"type": "string", "enum": ["running", "stack", "none"]}, "spacing_mm": {"type": "number"}, "every": {"type": "integer"}, "why": {"type": "string"}}}}}}
PROMPT = """The image shows candidate 3D components from a catalogue, one row for each planned part, each with its id in red. Below is what the plan needs and each candidate's real size.
Choose one candidate for each part, by what it is first and its size second. A candidate's size must suit the size wanted: say so in why if none does, and take the nearest of the right kind.
A unit's real size sets the scale of the whole drawing, so choose a full-size unit as builders use it, and the same unit for parts that are the same thing. "used" says how often a candidate appears in real models.
bond: for units, how courses lap: running or stack. none for anything else.
spacing_mm: for a spaced part, the interval along the run. It is an assumption and is labelled so. 0 for units.
every: for a spaced part laid in repeated joints, 1 for each joint, 2 for every second joint. 1 otherwise.
why: one short line.
Assembly: {assembly}
Parts:
"""


def sheet(shortlists) -> bytes:
    columns = max(len(row["candidates"]) for row in shortlists)
    image = Image.new("RGB", (LABEL + columns * SIDE, len(shortlists) * SIDE), "white")
    pen = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=15)
    for r, row in enumerate(shortlists):
        pen.multiline_text((6, r * SIDE + 8), "\n".join(row["name"][k:k + 18] for k in range(0, min(len(row["name"]), 72), 18)), fill="black", font=font)
        for c, hit in enumerate(row["candidates"]):
            thumb = STORE / "page" / "thumbs" / f"{hit['id']}.jpg"
            x, y = LABEL + c * SIDE, r * SIDE
            if thumb.exists():
                held = Image.open(thumb).convert("RGB")
                held.thumbnail((SIDE - 8, SIDE - 8))
                image.paste(held, (x + 4, y + 4))
            pen.text((x + 8, y + 6), str(hit["id"]), fill=(220, 0, 0), font=font)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def lines(shortlists) -> list:
    return [{"part": row["part"], "name": row["name"], "form": row["form"], "wanted": row["wanted"],
             "candidates": [{"id": h["id"], "name": h["name"], "category": h["category"], "mm": [round(s * 1000, 1) for s in h["size"]], "used": h["copies"]} for h in row["candidates"]]} for row in shortlists]


def choose(model, plan, shortlists, guidance="") -> tuple[dict, int]:
    """Part id -> Muse's choice, and the tokens it took. A part with no shortlist has no choice to make."""
    if not shortlists:
        return {}, 0
    prompt = guidance + PROMPT.replace("{assembly}", plan["assembly"]) + json.dumps(lines(shortlists))
    answer, tokens = model(prompt, [sheet(shortlists)], SCHEMA, "assembly_choose", "medium")
    return {c["part"]: c for c in answer["choices"]}, tokens


def in_order(model, plan, make_scene, guidance="") -> tuple[dict, list, int]:
    """Units, then fixings at the scale those units set. Returns the choices, every shortlist and the tokens."""
    units = make_scene({}).shortlists(("units",))
    chosen, spent = choose(model, plan, units, guidance)
    spaced = make_scene(chosen).shortlists(("spaced",))
    more, extra = choose(model, plan, spaced, guidance)
    return {**chosen, **more}, units + spaced, spent + extra
