"""Turn the reference models into a component library: every unique part once, and how parts join.

A model holds hundreds of pieces, mostly copies. Copies of a part share one shape row, and each model
keeps where its pieces sit and what touches what. Everything lands under gitignored data/goal/library.

    python -m goal.library
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

import numpy as np

from .baseline import held_out
from .families import spread
from .reference import DATA, DB

STORE = DATA / "goal" / "library"
TOUCH = 0.003  # metres: pieces this close are in contact
SIZE_STEP = 0.005  # metres: pieces this alike are the same part
SCHEMA = """
create table shapes (id integer primary key, key text unique, family text, label text, source text, copies integer, models integer,
                     longest real, middle real, shortest real, triangles integer);
create table instances (id integer primary key, model text, piece integer, shape integer references shapes(id), family text, x0 real, y0 real, z0 real, x1 real, y1 real, z1 real);
create table contacts (model text, a integer, b integer, kind text);
create table models (name text primary key, held_out integer, pieces integer, shapes integer, wide real, deep real, high real);
"""


def canonical(triangles) -> np.ndarray:
    """A part in its own frame: longest side along x, shortest along z, one corner at the origin."""
    points = triangles.reshape(-1, 3)
    centre = points.mean(0)
    _, _, axes = np.linalg.svd(points - centre, full_matrices=False)
    if np.linalg.det(axes) < 0:
        axes[2] *= -1
    turned = (points - centre) @ axes.T
    return (turned - turned.min(0)).reshape(-1, 3, 3).astype(np.float32)


def key_of(piece, count) -> str:
    sizes = [round(s / SIZE_STEP) for s in piece["sizes"]]
    return f"{sizes[0]}_{sizes[1]}_{sizes[2]}_{count}"


def contacts(boxes) -> list:
    """Pairs whose boxes meet, and how: `on` when one sits on the other, `beside` when they share a side."""
    low, high = boxes[:, :3], boxes[:, 3:]
    out = []
    for i in range(len(boxes)):
        near = (low[i + 1:] <= high[i] + TOUCH).all(1) & (high[i + 1:] >= low[i] - TOUCH).all(1)
        for k in np.flatnonzero(near) + i + 1:
            overlap = np.minimum(high[i], high[k]) - np.maximum(low[i], low[k])
            out.append((i, int(k), "on" if overlap[2] <= TOUCH * 2 else "beside"))
    return out


def build() -> dict:
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "meshes").mkdir(exist_ok=True)
    path = STORE / "library.db"
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    held = set(held_out())
    shapes, models = {}, {}
    rows = json.loads((DB / "index.json").read_text())
    for row in rows:
        name = row["name"]
        record = json.loads((DB / f"{name}.json").read_text())
        triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
        families = spread(record["pieces"])
        boxes = np.array([p["box"] for p in record["pieces"]])
        seen = set()
        for index, (piece, family) in enumerate(zip(record["pieces"], families)):
            count = piece["tris"][1] - piece["tris"][0]
            key = key_of(piece, count)
            shape = shapes.get(key)
            if shape is None and name not in held:
                shape = shapes[key] = {"id": len(shapes) + 1, "family": Counter(), "label": Counter(), "copies": 0, "models": set(), "sizes": piece["sizes"], "triangles": count}
                np.save(STORE / "meshes" / f"{shape['id']}.npy", canonical(triangles[piece["tris"][0]:piece["tris"][1]]))
            if shape is not None and name not in held:
                shape["family"][family] += 1
                shape["copies"] += 1
                shape["models"].add(name)
                if piece.get("name"):
                    shape["label"][piece["name"]] += 1
            seen.add(key)
            db.execute("insert into instances (model, piece, shape, family, x0, y0, z0, x1, y1, z1) values (?,?,?,?,?,?,?,?,?,?)",
                       (name, index, shape["id"] if shape else None, family, *piece["box"]))
        linked = contacts(boxes) if max(row["size_m"]) <= 8 and "EXPLODED" not in name.upper() else []
        db.executemany("insert into contacts values (?,?,?,?)", [(name, a, b, kind) for a, b, kind in linked])
        size = boxes[:, 3:].max(0) - boxes[:, :3].min(0)
        db.execute("insert into models values (?,?,?,?,?,?,?)", (name, int(name in held), len(boxes), len(seen), *size.tolist()))
        print(f"{name[:32]:32} {len(boxes):5} pieces {len(linked):6} contacts", flush=True)
    for key, shape in shapes.items():
        known = [(f, n) for f, n in shape["family"].most_common() if f != "unknown"]
        family = known[0][0] if known else "unknown"
        label = shape["label"].most_common(1)[0][0] if shape["label"] else None
        db.execute("insert into shapes values (?,?,?,?,?,?,?,?,?,?,?)", (shape["id"], key, family, label, "names" if known else None, shape["copies"], len(shape["models"]),
                                                                        *shape["sizes"], shape["triangles"]))
    db.commit()
    stats = {"shapes": len(shapes), "instances": db.execute("select count(*) from instances").fetchone()[0], "contacts": db.execute("select count(*) from contacts").fetchone()[0],
             "unknown_shapes": db.execute("select count(*) from shapes where family='unknown'").fetchone()[0],
             "unknown_copies": db.execute("select sum(copies) from shapes where family='unknown'").fetchone()[0],
             "copies": db.execute("select sum(copies) from shapes").fetchone()[0]}
    print(stats)
    print(db.execute("select family, count(*), sum(copies) from shapes group by family order by 3 desc").fetchall())
    db.close()
    return stats


if __name__ == "__main__":
    build()
