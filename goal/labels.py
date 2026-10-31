"""One Muse call that says what each region of a section is. The model never sees a reference."""
from __future__ import annotations

import base64
import io
import json

from PIL import Image, ImageDraw, ImageFont

from .families import FAMILIES

DEPTHS = ("continuous", "units", "spaced", "empty")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["assembly", "regions"], "properties": {
    "assembly": {"type": "string"},
    "regions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["id", "family", "depth"], "properties": {
                    "id": {"type": "integer"}, "family": {"type": "string", "enum": list(FAMILIES)},
                    "depth": {"type": "string", "enum": list(DEPTHS)}}}}}}
PROMPT = """This is a 2D cross-section through a building construction detail: a cut through real
assemblies such as masonry walls, cavities, floors, flashings and fixings. It has no text.
Each closed region has a red number. For every number, say what the part is, and how it behaves along
the depth, the direction into the page, which the section does not show.
family: the kind of component.
depth: continuous = the same section runs the whole length (a slab, a board, a flashing, a bed joint).
units = separate units repeated with joints (brick, block, tile). spaced = recurs at intervals (a tie,
bolt, anchor, weep). empty = not a part at all, such as an air cavity or a space enclosed by other parts.
Think first about what the assembly is, then answer every number. Regions:
"""


def overlay(picture, regions) -> bytes:
    image = Image.open(picture).convert("RGB")
    pen = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=13)
    for region in regions:
        spans = region.rows[(region.box[1] + region.box[3]) // 2 if (region.box[1] + region.box[3]) // 2 in region.rows else min(region.rows)]
        x0, x1 = max(spans, key=lambda s: s[1] - s[0])
        pen.text(((x0 + x1) / 2, (region.box[1] + region.box[3]) / 2), str(region.id), fill=(220, 0, 0), font=font, anchor="mm")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def ask(picture, regions, size) -> tuple[dict, dict]:
    from app import meta_client
    rows = [{"id": r.id, "x": round((r.box[0] + r.box[2]) / 2 / size[0], 3), "y": round((r.box[1] + r.box[3]) / 2 / size[1], 3),
             "width": round(r.width / size[0], 3), "height": round(r.height / size[1], 3)} for r in regions]
    data = base64.b64encode(overlay(picture, regions)).decode()
    content = [{"type": "input_text", "text": PROMPT + json.dumps(rows)},
               {"type": "input_image", "image_url": f"data:image/png;base64,{data}"}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "medium"},
                                        "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "section_regions",
                                                            "schema": SCHEMA, "strict": True}}}, timeout=280)
    text = "".join(part.get("text", "") for item in result["output"] if item.get("type") == "message"
                   for part in item.get("content", []))
    return json.loads(text), result.get("usage") or {}
