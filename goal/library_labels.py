"""Name the library's unlabelled parts, once, in advance. Each page is one Muse call over a sheet of renders.

    python -m goal.library_labels
"""
from __future__ import annotations

import base64
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

from . import picture
from .families import FAMILIES
from .library import STORE

PAGE = 30
SIDE = 220
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["parts"], "properties": {"parts": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "family", "name"], "properties": {
        "id": {"type": "integer"}, "family": {"type": "string", "enum": list(FAMILIES)}, "name": {"type": "string"}}}}}}
PROMPT = """Each cell is one part from a 3D model of a building detail, with its id. Its size in mm is listed below. Say what each
part is, as a builder would name it: family, and a short name such as "steel stud" or "half brick". If a part can't be told,
use family unknown and name "unclear". Never guess from the colour, which means nothing here. Parts:
"""


def sheet(rows) -> bytes:
    columns = 6
    page = Image.new("RGB", (columns * SIDE, (len(rows) + columns - 1) // columns * SIDE), "white")
    pen = ImageDraw.Draw(page)
    for k, row in enumerate(rows):
        triangles = np.load(STORE / "meshes" / f"{row[0]}.npy").astype(float)
        cell = picture.view([{"triangles": triangles, "family": "unknown"}], size=SIDE - 8)
        x, y = (k % columns) * SIDE, (k // columns) * SIDE
        page.paste(cell, (x + 4, y + 4))
        pen.text((x + 8, y + 6), str(row[0]), fill=(200, 0, 0))
    out = io.BytesIO()
    page.save(out, "JPEG", quality=85)
    return out.getvalue()


def label(rows) -> tuple[list, int]:
    from app import meta_client
    sizes = [{"id": r[0], "mm": [round(r[1] * 1000), round(r[2] * 1000), round(r[3] * 1000)]} for r in rows]
    content = [{"type": "input_text", "text": PROMPT + json.dumps(sizes)},
               {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(sheet(rows)).decode()}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "low"},
                                        "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "parts", "schema": SCHEMA, "strict": True}}}, timeout=200)
    text = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    return json.loads(text)["parts"], (result.get("usage") or {}).get("total_tokens") or 0


def main():
    db = sqlite3.connect(STORE / "library.db")
    rows = db.execute("select id, longest, middle, shortest from shapes where family='unknown' order by copies desc").fetchall()
    pages = [rows[k:k + PAGE] for k in range(0, len(rows), PAGE)]
    spent = done = 0

    def safe(page):
        try:
            return label(page)
        except Exception as error:  # noqa: BLE001 - a lost page stays unlabelled
            print("page failed", str(error)[:120], flush=True)
            return [], 0

    with ThreadPoolExecutor(6) as pool:
        for parts, tokens in pool.map(safe, pages):
            spent += tokens
            for part in parts:
                if part["family"] != "unknown":
                    db.execute("update shapes set family=?, label=?, source='muse' where id=? and family='unknown'", (part["family"], part["name"], part["id"]))
                    done += 1
            db.commit()
            print("labelled", done, "tokens", spent, flush=True)
    print(db.execute("select count(*), sum(copies) from shapes where family='unknown'").fetchone(), db.execute("select family, count(*), sum(copies) from shapes group by family order by 3 desc").fetchall())


if __name__ == "__main__":
    main()
