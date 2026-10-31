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


def build(groups, regions, size, depth_share) -> list[dict]:
    """Each group as a grid of boxes at its region, along the depth and upward."""
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
        for c in range(columns):
            y = depth * (c + 0.5) / columns
            for r in range(rows):
                z = low + (high - low) * (r + 0.5) / rows
                pieces.append({"triangles": _block(x - sizes[0] / 2, x + sizes[0] / 2, y - sizes[2] / 2, y + sizes[2] / 2,
                                                   z - sizes[1] / 2, z + sizes[1] / 2), "family": group["family"]})
    return pieces
