"""A component library from 3D models: every unique part once, and how parts join.

A model holds hundreds of pieces, mostly copies. Copies of a part share one shape row, and each model
keeps where its pieces sit and what touches what. Adding only ever adds: a model already in is skipped,
a part already known gains copies, and labels are never touched. Everything lands under gitignored
data/goal/library.

    python -m goal.library <folder of .dae or .zip models>
    python -m goal.library              # models already read into the database, and nothing else
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from .baseline import held_out
from .families import spread
from .reference import DATA, DB, take_in

STORE = DATA / "goal" / "library"
TOUCH = 0.003  # metres: pieces this close are in contact
SIZE_STEP = 0.005  # metres: pieces this alike are the same part
FLAT = 0.999  # how nearly two normals must agree to be one face direction
FACED = 0.6  # share of a part's area on three square directions, for it to count as squared-off
FACE_SHARE = 0.4  # of each outer face, for a part to count as a whole unit
SCHEMA = """
create table if not exists shapes (id integer primary key, key text unique, family text, label text, source text, copies integer, models integer,
                     longest real, middle real, shortest real, triangles integer, whole integer);
create table if not exists instances (id integer primary key, model text, piece integer, shape integer references shapes(id), family text, x0 real, y0 real, z0 real, x1 real, y1 real, z1 real);
create table if not exists contacts (model text, a integer, b integer, kind text);
create table if not exists models (name text primary key, held_out integer, pieces integer, shapes integer, wide real, deep real, high real);
"""


def squared(triangles, axes) -> np.ndarray:
    """A part's axes from its own flat faces where it has them. A best-fit frame tilts on anything uneven,
    such as a block with cores, and a tilted part shears when it is scaled."""
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = np.linalg.norm(normals, axis=1)
    keep = areas > 0
    normals, areas = normals[keep] / areas[keep, None], areas[keep]
    if not len(areas):
        return axes

    def share(direction, width=FLAT):
        return areas[np.abs(normals @ direction) > width].sum() / areas.sum()

    first = max(normals[np.argsort(-areas)[:200]], key=share)
    across = normals[np.abs(normals @ first) < 1 - FLAT]
    if not len(across):
        return axes
    second = max(across[:400], key=share)
    second = second - first * (second @ first)
    second /= np.linalg.norm(second)
    third = np.cross(first, second)
    if share(first) + share(second) + share(third) < FACED:
        return axes
    return np.array([first, second, third])


def canonical(triangles) -> np.ndarray:
    """A part in its own frame: longest side along x, shortest along z, one corner at the origin."""
    points = triangles.reshape(-1, 3)
    centre = points.mean(0)
    _, _, axes = np.linalg.svd(points - centre, full_matrices=False)
    if np.linalg.det(axes) < 0:
        axes[2] *= -1
    turned = (points - centre) @ squared(triangles, axes).T
    extents = turned.max(0) - turned.min(0)
    return ((turned - turned.min(0))[:, np.argsort(-extents)]).reshape(-1, 3, 3).astype(np.float32)


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


def whole(mesh) -> bool:
    """All six outer faces of a unit are there. Models also hold shells and fragments under the same name."""
    points = mesh.reshape(-1, 3)
    low, high = points.min(0), points.max(0)
    area = np.linalg.norm(np.cross(mesh[:, 1] - mesh[:, 0], mesh[:, 2] - mesh[:, 0]), axis=1) / 2
    for axis in range(3):
        others = [a for a in range(3) if a != axis]
        face = np.prod((high - low)[others])
        for side in (low[axis], high[axis]):
            on = (np.abs(mesh[..., axis] - side) < 0.002).all(1)
            # the meshes hold each face from both sides, and a face may be broken up by cores
            if area[on].sum() / 2 < FACE_SHARE * face:
                return False
    return True


def open_store() -> sqlite3.Connection:
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "meshes").mkdir(exist_ok=True)
    db = sqlite3.connect(STORE / "library.db")
    db.executescript(SCHEMA)
    if "whole" not in [row[1] for row in db.execute("pragma table_info(shapes)")]:
        db.execute("alter table shapes add column whole integer")
    for ident, in db.execute("select id from shapes where whole is null").fetchall():
        db.execute("update shapes set whole=? where id=?", (int(whole(np.load(STORE / "meshes" / f"{ident}.npy").astype(float))), ident))
    db.commit()
    return db


def add_model(db, name, held) -> int:
    """One model into the library. Returns how many new parts it brought."""
    record = json.loads((DB / f"{name}.json").read_text())
    triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
    families = spread(record["pieces"])
    boxes = np.array([p["box"] for p in record["pieces"]])
    known = dict(db.execute("select key, id from shapes"))
    next_id = (db.execute("select max(id) from shapes").fetchone()[0] or 0) + 1
    found, seen, fresh = {}, set(), 0
    for index, (piece, family) in enumerate(zip(record["pieces"], families)):
        count = piece["tris"][1] - piece["tris"][0]
        key = key_of(piece, count)
        seen.add(key)
        if key not in known and not held:
            known[key] = next_id
            mesh = canonical(triangles[piece["tris"][0]:piece["tris"][1]])
            np.save(STORE / "meshes" / f"{next_id}.npy", mesh)
            sure = family != "unknown"
            db.execute("insert into shapes values (?,?,?,?,?,0,0,?,?,?,?,?)", (next_id, key, family, piece.get("name"), "names" if sure else None, *piece["sizes"], count, int(whole(mesh.astype(float)))))
            next_id += 1
            fresh += 1
        if not held and key in known:
            found[key] = found.get(key, 0) + 1
        db.execute("insert into instances (model, piece, shape, family, x0, y0, z0, x1, y1, z1) values (?,?,?,?,?,?,?,?,?,?)",
                   (name, index, known.get(key), family, *piece["box"]))
    for key, copies in found.items():
        db.execute("update shapes set copies = copies + ?, models = models + 1 where key = ?", (copies, key))
    size = boxes[:, 3:].max(0) - boxes[:, :3].min(0)
    linked = contacts(boxes) if size.max() <= 8 and "EXPLODED" not in name.upper() else []
    db.executemany("insert into contacts values (?,?,?,?)", [(name, a, b, kind) for a, b, kind in linked])
    db.execute("insert into models values (?,?,?,?,?,?,?)", (name, int(held), len(boxes), len(seen), *size.tolist()))
    db.commit()
    return fresh


def update(folder=None) -> dict:
    """Bring in whatever is new. A folder's models are read first, then every model not yet in the library."""
    if folder:
        for path in sorted(Path(folder).iterdir()):
            if path.suffix.lower() in (".dae", ".zip") and take_in(path):
                print("read", path.name, flush=True)
    db = open_store()
    held = set(held_out())
    done = {row[0] for row in db.execute("select name from models")}
    for row in json.loads((DB / "index.json").read_text()):
        if row["name"] not in done:
            print(f"{row['name'][:44]:44} {add_model(db, row['name'], row['name'] in held):4} new parts", flush=True)
    stats = {"models": db.execute("select count(*) from models").fetchone()[0], "parts": db.execute("select count(*) from shapes").fetchone()[0],
             "whole": db.execute("select count(*) from shapes where whole=1").fetchone()[0], "copies": db.execute("select sum(copies) from shapes").fetchone()[0],
             "unknown": db.execute("select count(*) from shapes where family='unknown'").fetchone()[0],
             "unnamed": db.execute("select count(*) from shapes where label is null").fetchone()[0]}
    print(stats)
    print(db.execute("select family, count(*), sum(copies) from shapes group by family order by 2 desc").fetchall())
    from . import catalogue  # it builds on these tables
    catalogue.update()
    return stats


if __name__ == "__main__":
    update(*sys.argv[1:2])
