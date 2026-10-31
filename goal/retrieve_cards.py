"""Retrieve by words: a real drawing against a written card for each model, built from its named components, so
nobody has to read bare section lines. Every model with catalogued parts gets a card, large or exploded ones too.

    python -m goal.retrieve_cards <drawing.png> [...]
"""
from __future__ import annotations

import base64
import io
import json
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .library import STORE
from .retrieve_test import OUT

PROMPT = """The image is a 2D section of a building detail. Below are cards for the 3D models we hold: each lists its size and its components with
counts. Say what the drawing is and list what it is built from. Then give the three cards closest to it: the same kind of junction with the same
components. Verdict "same" if a card is this junction built this way, "similar" if it is this kind of junction built differently, "nothing close" if none
is. Many drawings have no match here, so never settle for the least bad one. Cards:
"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["what", "built_from", "verdict", "matches"], "properties": {
    "what": {"type": "string"}, "built_from": {"type": "array", "items": {"type": "string"}}, "verdict": {"type": "string", "enum": ["same", "similar", "nothing close"]},
    "matches": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["number", "why"], "properties": {"number": {"type": "integer"}, "why": {"type": "string"}}}}}}
SKIP = ("fragment", "unknown")


def cards() -> list:
    db = sqlite3.connect(STORE / "library.db")
    out = []
    rows = db.execute("select p.model, coalesce(c.name, c.muse_name), c.category from placements p join components c on c.id = p.component join models m on m.name = p.model where m.held_out = 0").fetchall()
    for model in sorted({r[0] for r in rows}):
        kinds = Counter((name or category).lower() for m, name, category in rows if m == model and category not in SKIP)
        size = db.execute("select wide, deep, high from models where name = ?", (model,)).fetchone()
        out.append((model, f"{[round(s, 1) for s in size]} m: " + ", ".join(f"{name} x{count}" for name, count in kinds.most_common(14))))
    return out


def ask(drawing, held) -> dict:
    from app import meta_client
    image = Image.open(drawing).convert("RGB")
    image.thumbnail((1400, 1400))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    text = PROMPT + "\n".join(f"{k + 1}. {card}" for k, (_, card) in enumerate(held))
    content = [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()}]
    result = meta_client.respond_tools({"model": meta_client.default_model(), "reasoning": {"effort": "medium"}, "input": [{"type": "message", "role": "user", "content": content}],
                                        "text": {"format": {"type": "json_schema", "name": "matches", "schema": SCHEMA, "strict": True}}}, timeout=240)
    reply = "".join(p.get("text", "") for item in result["output"] if item.get("type") == "message" for p in item.get("content", []))
    answer = json.loads(reply)
    answer["names"] = [held[m["number"] - 1][0] for m in answer["matches"] if 0 < m["number"] <= len(held)]
    answer["tokens"] = (result.get("usage") or {}).get("total_tokens") or 0
    return answer


if __name__ == "__main__":
    held = cards()
    print(len(held), "cards; example:", held[0][1][:200], flush=True)
    with ThreadPoolExecutor(4) as pool:
        answers = dict(zip(sys.argv[1:], pool.map(lambda d: ask(d, held), sys.argv[1:])))
    for drawing, answer in answers.items():
        print(f"\n{Path(drawing).name[:44]} | {answer['verdict']} | {answer['tokens']} tokens\n  read: {answer['what'][:150]}\n  got: {[n[:34] for n in answer['names'][:3]]}\n  why: {answer['matches'][0]['why'][:170] if answer['matches'] else ''}")
    (OUT / "cards.json").write_text(json.dumps({Path(k).name: v for k, v in answers.items()}, indent=1))
