"""Sort and name every component, once. Each page is one Muse call over a sheet of renders. What it says is
an inference, recorded as such in `named_by`; a name a person gave in the file stays. Safe to run again: it
only takes components with no category, and it saves each page.

    python -m goal.catalogue_labels
"""
from __future__ import annotations

import base64
import io
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

from . import picture
from .library import STORE, open_store

PAGE = 30
SIDE = 220
CATEGORIES = ("brick", "block", "stone", "concrete", "mortar", "adhesive", "tile", "board", "timber", "insulation", "membrane", "flashing", "sealant",
              "fixing", "wall tie", "reinforcement", "steel section", "metal trim", "window", "door", "glazing", "weep", "drainage", "fragment", "unknown")
CLOSED = 0.75  # share of edges joined, for a mesh to be a solid and not a hollow surface
JOINT = 0.025  # metres: how far a joint surface may stand off its unit
SAME_MATERIAL = {"tile": ("tile", "fragment"), "brick": ("brick", "mortar", "fragment"), "block": ("block", "mortar", "fragment"), "timber": ("timber", "fragment"),
                 "board": ("board", "fragment"), "insulation": ("insulation", "fragment"), "concrete": ("concrete", "mortar", "fragment"), "stone": ("stone", "fragment")}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["parts"], "properties": {"parts": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "category", "name"], "properties": {
        "id": {"type": "integer"}, "category": {"type": "string", "enum": list(CATEGORIES)}, "name": {"type": "string"}}}}}}
PROMPT = """Each cell is one component from a 3D model of a building detail, with its id in red. Below are its real size in mm and
any name in the file. Give each a category and a short name a builder would use, such as "steel stud", "half brick" or "angle
flashing". A window, door or frame made of many pieces is a window or door, not glazing. Use "fragment" for something that is
only a loose face or an offcut of a part rather than a part, and "unknown" with the name "unclear" if it can't be told. Never go
by colour, which means nothing here. A name in the file may be wrong or only a number. Components:
"""


def mortar_shells(db) -> int:
    """Masonry models draw a joint as a hollow surface around its unit, in the unit's own group and a joint
    wider all round. A picture can't tell that from a tie or a sheet; the group and the sizes can."""
    units = defaultdict(list)
    rows = db.execute("select p.model, p.path, p.component, c.category, c.closed, p.x0, p.y0, p.z0, p.x1, p.y1, p.z1 from placements p join components c on c.id = p.component").fetchall()
    for model, path, _, category, closed, *box in rows:
        if category in ("brick", "block") and closed >= CLOSED:
            units[model, tuple(json.loads(path)[:-1])].append(np.array(box))
    votes, seen = Counter(), Counter()
    for model, path, component, category, closed, *box in rows:
        if closed >= CLOSED or category in ("brick", "block"):
            continue
        seen[component] += 1
        box = np.array(box)
        for unit in units.get((model, tuple(json.loads(path)[:-1])), ()):
            if (box[:3] <= unit[:3] + 0.002).sum() + (box[3:] >= unit[3:] - 0.002).sum() >= 4 and (np.abs(box - unit) <= JOINT).all():
                votes[component] += 1
                break
    shells = [c for c, n in votes.items() if n >= seen[c] / 2]
    for component in shells:
        db.execute("update components set category='mortar', name=null, muse_name='mortar joint surface', named_by='geometry' where id=?", (component,))
    db.commit()
    return len(shells)


def doubts(db) -> int:
    """Muse disagrees with what the file's names said about the material. Marked for a person, not settled here."""
    db.execute("update components set review=0")
    for family, allowed in SAME_MATERIAL.items():
        marks = ",".join("?" * len(allowed))
        db.execute(f"update components set review=1 where family=? and named_by is not 'geometry' and category not in ({marks})", (family, *allowed))
    db.commit()
    return db.execute("select count(*) from components where review=1").fetchone()[0]


def sheet(rows) -> bytes:
    columns = 6
    page = Image.new("RGB", (columns * SIDE, (len(rows) + columns - 1) // columns * SIDE), "white")
    pen = ImageDraw.Draw(page)
    for k, row in enumerate(rows):
        cell = picture.view([{"triangles": np.load(STORE / "parts" / f"{row[0]}.npy").astype(float), "family": "unknown"}], size=SIDE - 8)
        x, y = (k % columns) * SIDE, (k // columns) * SIDE
        page.paste(cell, (x + 4, y + 4))
        pen.text((x + 8, y + 6), str(row[0]), fill=(200, 0, 0))
    out = io.BytesIO()
    page.save(out, "JPEG", quality=85)
    return out.getvalue()


def label(rows) -> tuple[list, int]:
    from app import meta_client
    facts = [{"id": r[0], "mm": [round(r[1] * 1000), round(r[2] * 1000), round(r[3] * 1000)], "in_file": r[4]} for r in rows]
    content = [{"type": "input_text", "text": PROMPT + json.dumps(facts)},
               {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(sheet(rows)).decode()}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "low"},
                                        "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "parts", "schema": SCHEMA, "strict": True}}}, timeout=200)
    text = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    return json.loads(text)["parts"], (result.get("usage") or {}).get("total_tokens") or 0


def main():
    db = open_store()
    if "category" not in [row[1] for row in db.execute("pragma table_info(components)")]:
        db.execute("alter table components add column category text")
        db.execute("alter table components add column muse_name text")
    rows = db.execute("select id, longest, middle, shortest, raw_name from components where category is null order by copies desc").fetchall()
    pages = [rows[k:k + PAGE] for k in range(0, len(rows), PAGE)]
    known = {r[0] for r in rows}
    spent = done = 0

    def safe(page):
        try:
            return label(page)
        except Exception as error:  # noqa: BLE001 - a lost page stays unlabelled and the next run takes it
            print("page failed", str(error)[:120], flush=True)
            return [], 0

    with ThreadPoolExecutor(6) as pool:
        for parts, tokens in pool.map(safe, pages):
            spent += tokens
            for part in parts:
                if part["id"] in known:  # a reply can only speak for a component it was shown
                    db.execute("update components set category=?, muse_name=? where id=?", (part["category"], part["name"], part["id"]))
                    done += 1
            db.commit()
            print("labelled", done, "of", len(rows), "tokens", spent, flush=True)
    if "review" not in [row[1] for row in db.execute("pragma table_info(components)")]:
        db.execute("alter table components add column review integer")
    print("mortar joint surfaces", mortar_shells(db), "marked for review", doubts(db))
    print(db.execute("select category, count(*), sum(copies) from components group by category order by 2 desc").fetchall())


if __name__ == "__main__":
    main()
