"""Ask an untrained model what the section doesn't show, and build what it lists.

This sets the bar: whatever a trained model does must beat what a prompt gets for free.
"""
from __future__ import annotations

import base64
import json

import numpy as np

from .compose import _block
from .families import FAMILIES
from .labels import overlay

SHAPES = ("unit", "plate", "bar", "rod", "cube")
MOST = 600  # pieces from one group
PIECES_PER_REGION = 13.9
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {
    "groups": {"type": "array", "items": {"type": "object", "additionalProperties": False,
               "required": ["family", "what", "shape", "near_region", "columns", "rows", "across", "up", "along"], "properties": {
                   "family": {"type": "string", "enum": list(FAMILIES)}, "what": {"type": "string"},
                   "shape": {"type": "string", "enum": list(SHAPES)}, "near_region": {"type": "integer"},
                   "columns": {"type": "integer"}, "rows": {"type": "integer"},
                   "across": {"type": "number"}, "up": {"type": "number"}, "along": {"type": "number"}}}}}}
PROMPT = """This 2D cross-section of a building detail shows one thin slice. The whole assembly runs
along the depth, into the page, and most of its parts are never cut by this slice: units beyond the
cut, head joints between units, ties and anchors at intervals, wire in bed joints, nuts and bolts,
flashing laps and end dams, weep vents, sealant, closures.
The regions already known are listed below with their numbers. Don't repeat them.
List every group of parts this assembly really has that the slice doesn't show. For each group:
family, what it is in a few words, shape, and near_region, the number of the region it sits at.
columns = how many along the depth, rows = how many stacked upward at that position.
across, up, along = the size of one piece, as a fraction of the section's longest side.
Be complete and realistic: a wall has many head joints and ties.

Assembly: """


def ask(picture, regions, size, labelled) -> tuple[dict, dict]:
    from app import meta_client
    known = [{"id": r["id"], "family": r["family"], "depth": r["depth"]} for r in labelled["regions"]]
    data = base64.b64encode(overlay(picture, regions)).decode()
    content = [{"type": "input_text", "text": PROMPT + labelled.get("assembly", "") + "\nRegions: " + json.dumps(known)},
               {"type": "input_image", "image_url": f"data:image/png;base64,{data}"}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "medium"},
                                        "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "hidden_parts",
                                                            "schema": SCHEMA, "strict": True}}}, timeout=280)
    text = "".join(part.get("text", "") for item in result["output"] if item.get("type") == "message"
                   for part in item.get("content", []))
    return json.loads(text), result.get("usage") or {}


def calibrated(groups, regions, seen) -> list[dict]:
    """Groups scaled down if they would pass what real assemblies hold for a section this size:
    across 17 training models, the middle half run 5.6 to 13.9 pieces per piece cut. An untrained
    model lists far too many on a small detail. It is only ever a ceiling."""
    wanted = sum(g["columns"] * g["rows"] for g in groups)
    room = max(PIECES_PER_REGION * len(regions) - seen, len(groups))
    if wanted <= room:
        return groups
    share = (room / wanted) ** 0.5  # each group shrinks both ways, keeping its shape
    return [{**g, "columns": max(1, round(g["columns"] * share)), "rows": max(1, round(g["rows"] * share))} for g in groups]


def _already_built(group, marked) -> bool:
    """Units along the run and their joints come from the section itself, so a group repeating them
    would sit on top of them."""
    from .compose import UNIT_FAMILIES
    units = {m["family"] for m in marked.values() if m.get("depth") == "units" and m["family"] in UNIT_FAMILIES}
    return group["family"] in units or (group["family"] == "mortar" and bool(units))


def _beds(region, regions, marked, height) -> list[float]:
    """Heights of the mortar beds beside a region: where ties and wire really lie."""
    x0, x1 = region.box[0], region.box[2]
    middle = (x0 + x1) / 2
    near = [r for r in regions if marked.get(r.id, {}).get("family") == "mortar" and r.width > r.height
            and r.box[0] - (x1 - x0) <= middle <= r.box[2] + (x1 - x0)]
    return sorted({round(height - (r.box[1] + r.box[3]) / 2, 1) for r in near})


def assemble(regions, marked, groups, size, depth_share, cutaway=None) -> list[tuple[dict, str]]:
    """Every piece, each with where it came from: `section` if the slice shows it, `hidden` if not."""
    from . import compose
    shown = compose.build(regions, marked, size, depth_share, cutaway)
    wanted = [g for g in groups if not _already_built(g, marked)]
    runs = compose.stepped(regions, marked, size, cutaway, depth_share * max(size)) if cutaway else None
    unseen = build(calibrated(wanted, regions, len(shown)), regions, size, depth_share, marked, runs)
    return [(p, "section") for p in shown] + [(p, "hidden") for p in unseen]


def _in_joint(group) -> bool:
    what = group["what"].lower()
    return group["family"] == "reinforcement" or "wire" in what or "tie" in what


def _piece(group, x, y, z, sizes) -> np.ndarray:
    """The real component the group names, or a plain box when none fits. What it is decides its
    form, and its size comes from the group."""
    from . import components
    what, across, up, along = group["what"].lower(), *sizes
    thin = max(min(across, up, along), 1e-3) / 2
    longest = max(across, up, along)
    if group["family"] == "fixing" or "bolt" in what or "rod" in what or "anchor" in what:
        return components.bolt((x, y, z - longest / 2), (x, y, z + longest / 2), thin)
    if "ladder" in what or "wire" in what or "reinforc" in what:
        return components.ladder_wire(x - across / 2, x + across / 2, y - along / 2, y + along / 2, z, thin * 0.6)
    if "tie" in what:
        return components.wall_tie(x - longest / 2, x + longest / 2, y, z, thin)
    if group["family"] == "block" and group["shape"] == "unit":
        return components.hollow_block(x - across / 2, x + across / 2, y - along / 2, y + along / 2, z - up / 2, z + up / 2)
    if group["shape"] == "rod":
        return components.prism((x, y, z - longest / 2), (x, y, z + longest / 2), thin)
    return _block(x - across / 2, x + across / 2, y - along / 2, y + along / 2, z - up / 2, z + up / 2)


def build(groups, regions, size, depth_share, marked=None, runs=None) -> list[dict]:
    """Each group as a grid of real pieces at its region, along the depth and upward. What lies in
    a joint goes in the joints the section shows."""
    width, height = size
    longest = max(width, height)
    depth = depth_share * longest
    by_id = {r.id: r for r in regions}
    pieces = []
    for group in groups:
        region = by_id.get(group["near_region"])
        if region is None:
            continue
        columns, rows = max(1, group["columns"]), max(1, group["rows"])
        if columns * rows > MOST:
            columns = max(1, MOST // rows)
        sizes = np.clip(np.array([group["across"], group["up"], group["along"]]), 0.002, 1.0) * longest
        x = (region.box[0] + region.box[2]) / 2
        low, high = height - region.box[3], height - region.box[1]
        levels = [low + (high - low) * (r + 0.5) / rows for r in range(rows)]
        if marked and _in_joint(group):
            levels = _beds(region, regions, marked, height) or levels
        for c in range(columns):
            y = depth * (c + 0.5) / columns
            if runs and y > runs.get(region.id, depth):
                continue
            for z in levels:
                pieces.append({"triangles": _piece(group, x, y, z, sizes), "family": group["family"]})
    return pieces
