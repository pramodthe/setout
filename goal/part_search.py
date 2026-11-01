"""Find catalogue components by meaning and by look. Each component is embedded twice: its written card as text, and its
render as an image a text can be compared with. Size then decides between the ones that mean the right thing.

    python -m goal.part_search            # builds the index once
    python -m goal.part_search "cavity wall tie" "wall tie" 
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np

from .library import STORE
from .reference import DATA

OUT = DATA / "goal" / "framework"
INDEX = OUT / "index.npz"
TOOL = DATA / "goal" / "embed-venv" / "bin" / "python"
THUMBS = STORE / "page" / "thumbs"
TEXT_SHARE = 0.65  # the card says more than a small render does
RELATED = {"brick": ("brick", "block"), "block": ("block", "brick"), "wall tie": ("wall tie", "fixing"), "fixing": ("fixing", "wall tie"), "weep": ("weep", "drainage"), "flashing": ("flashing", "metal trim", "membrane"),
           "membrane": ("membrane", "flashing"), "metal trim": ("metal trim", "flashing")}


def embed(**ask) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "vectors.npz"
    subprocess.run([str(TOOL), str(Path(__file__).with_name("embed_tool.py"))],
                   input=json.dumps({**ask, "out": str(path)}), text=True, check=True, capture_output=True)
    with np.load(path) as held:
        return {k: held[k] for k in held.files}


def rows() -> list:
    db = sqlite3.connect(STORE / "library.db")
    return db.execute("select id, category, coalesce(name, muse_name), muse_name, longest, middle, shortest, copies, whole, review from components where category not in ('fragment', 'unknown') and whole = 1").fetchall()


def card(row) -> str:
    return f"{row[2] or row[1]}, {row[3] or ''}, {row[1]}, {row[4] * 1000:.0f} x {row[5] * 1000:.0f} x {row[6] * 1000:.0f} mm"


def build():
    held = [r for r in rows() if (THUMBS / f"{r[0]}.jpg").exists()]
    vectors = embed(texts=[card(r) for r in held], images=[str(THUMBS / f"{r[0]}.jpg") for r in held])
    np.savez(INDEX, ids=np.array([r[0] for r in held]), texts=vectors["texts"], images=vectors["images"])
    print("indexed", len(held), "of", len(rows()))


def search(queries: list[tuple[str, str]], keep=8) -> list[list[dict]]:
    """For each (words, category): the closest components, best first."""
    with np.load(INDEX) as held:
        ids, texts, images = held["ids"], held["texts"], held["images"]
    info = {r[0]: r for r in rows()}
    asked = embed(texts=[q[0] for q in queries], clip_texts=[f"a 3D render of {q[0]}" for q in queries])
    out = []
    for k, (_, category) in enumerate(queries):
        score = TEXT_SHARE * texts @ asked["texts"][k] + (1 - TEXT_SHARE) * images @ asked["clip_texts"][k]
        allowed = RELATED.get(category, (category,))
        order = [i for i in np.argsort(-score) if info[int(ids[i])][1] in allowed][:keep]
        out.append([{"id": int(ids[i]), "category": info[int(ids[i])][1], "name": info[int(ids[i])][2], "size": [info[int(ids[i])][j] for j in (4, 5, 6)],
                     "copies": info[int(ids[i])][7], "doubtful": bool(info[int(ids[i])][9]), "score": round(float(score[i]), 3)} for i in order])
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        build()
    else:
        for hit in search([(sys.argv[1], sys.argv[2])])[0]:
            print(hit["id"], hit["category"], hit["name"], [round(s * 1000, 1) for s in hit["size"]], "x", hit["copies"], hit["score"])
