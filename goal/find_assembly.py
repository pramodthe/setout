"""Find the library assemblies closest to a drawing: one Muse call over a sheet of their sections.

    python -m goal.find_assembly <drawing.png> [<drawing.png> ...]
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import section
from .baseline import cut, held_out
from .library import STORE
from .reference import DB

SIDE = 340
SHEETS = STORE / "sections"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["what", "matches"], "properties": {"what": {"type": "string"},
          "matches": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["number", "why"],
                      "properties": {"number": {"type": "integer"}, "why": {"type": "string"}}}}}}
PROMPT = """The first image is a 2D section of a building detail. The second is a numbered sheet of sections through assemblies we hold. Say what
the detail is, then give the three numbers closest in how it is built: the same kinds of layers in the same order at the same
junction. Compare construction, never line style or drawing scale."""


def catalogue() -> list:
    rows = json.loads((DB / "index.json").read_text())
    held = set(held_out())
    return [r["name"] for r in rows if r["name"] not in held and max(r["size_m"]) <= 8 and "EXPLODED" not in r["name"].upper()]


def trimmed(path) -> Image.Image:
    """A section cropped to where its ink really is: one stray far-off piece shrank whole details to specks."""
    picture = Image.open(path).convert("L")
    ys, xs = np.nonzero(np.asarray(picture) < 128)
    box = (int(np.percentile(xs, 2)) - 12, int(np.percentile(ys, 2)) - 12, int(np.percentile(xs, 98)) + 12, int(np.percentile(ys, 98)) + 12)
    return picture.crop(box).convert("RGB")


def sheet(names) -> bytes:
    SHEETS.mkdir(exist_ok=True)
    columns = 6
    page = Image.new("RGB", (columns * SIDE, (len(names) + columns - 1) // columns * SIDE), "white")
    pen = ImageDraw.Draw(page)
    for k, name in enumerate(names):
        path = SHEETS / f"{name}.png"
        if not path.exists():
            _, _, axis, _, cuts = cut(name)
            section.draw(cuts, axis, path)
        cell = trimmed(path)
        cell.thumbnail((SIDE - 10, SIDE - 10))
        x, y = (k % columns) * SIDE, (k // columns) * SIDE
        page.paste(cell, (x + 5, y + 5))
        pen.text((x + 8, y + 6), str(k + 1), fill=(200, 0, 0))
    out = io.BytesIO()
    page.save(out, "JPEG", quality=85)
    return out.getvalue()


def find(drawing, names, catalogue_sheet) -> dict:
    from app import meta_client
    image = Image.open(drawing).convert("RGB")
    image.thumbnail((1200, 1200))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    content = [{"type": "input_text", "text": PROMPT}, {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()},
               {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(catalogue_sheet).decode()}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "medium"}, "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "matches", "schema": SCHEMA, "strict": True}}}, timeout=200)
    text = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    answer = json.loads(text)
    answer["names"] = [names[m["number"] - 1] for m in answer["matches"] if 0 < m["number"] <= len(names)]
    answer["tokens"] = (result.get("usage") or {}).get("total_tokens")
    return answer


if __name__ == "__main__":
    names = catalogue()
    page = sheet(names)
    (STORE / "catalogue.jpg").write_bytes(page)
    results = {}
    for drawing in sys.argv[1:]:
        results[Path(drawing).stem] = find(drawing, names, page)
        print(Path(drawing).stem, json.dumps(results[Path(drawing).stem])[:700], flush=True)
    (STORE / "matches.json").write_text(json.dumps(results, indent=1))
