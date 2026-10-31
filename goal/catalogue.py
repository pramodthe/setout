"""The catalogue: real components, not mesh fragments.

A model splits one object across many meshes: a nut is forty facets. The pieces in one use of a group that
share corners are one component, and copies of it share one row. `placements` says where each sits, with the
group path, so an assembly such as bolt, nut and washer can be read back. Adding only ever adds, and the
fragment tables in goal.library, which goal.arrange reads, stay as they are.

    python -m goal.catalogue
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import numpy as np

from .baseline import held_out
from .families import spread
from .library import STORE, canonical, key_of, open_store, whole
from .reference import DB

CORNER = 4  # decimals of a metre: corners this close are the same corner
SIZE_STEP = 0.002  # metres: components this alike, with the same triangle count, are one component
TOOL_NAME = re.compile(r"^_*(\d|3dgeom|mesh|geometry|group|instance|object|solid|part|body|_)", re.I)
SCHEMA = """
create table if not exists components (id integer primary key, key text unique, family text, name text, named_by text, raw_name text, copies integer, models integer,
                     longest real, middle real, shortest real, triangles integer, whole integer, closed real);
create table if not exists placements (id integer primary key, model text, component integer references components(id), node integer, path text,
                     family text, x0 real, y0 real, z0 real, x1 real, y1 real, z1 real);
create table if not exists catalogued (model text primary key);
"""


def groups(pieces, triangles) -> list:
    """Lists of piece indices: within one use of a group, pieces joined through shared corners."""
    by_node = defaultdict(list)
    for index, piece in enumerate(pieces):
        by_node[piece["node"]].append(index)
    found = []
    for members in by_node.values():
        parent = {i: i for i in members}

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        corners = {}
        for i in members:
            start, stop = pieces[i]["tris"]
            for corner in {tuple(c) for c in np.round(triangles[start:stop].reshape(-1, 3), CORNER).tolist()}:
                if corner in corners:
                    parent[root(i)] = root(corners[corner])
                else:
                    corners[corner] = i
        joined = defaultdict(list)
        for i in members:
            joined[root(i)].append(i)
        found += joined.values()
    return found


def closed_share(mesh) -> float:
    """Share of edges met by exactly two faces. The meshes hold each face from both sides, so one side is kept."""
    faces = {tuple(sorted(tuple(c) for c in face)) for face in np.round(mesh, 5).tolist()}
    edges = Counter(pair for a, b, c in faces for pair in ((a, b), (b, c), (a, c)))
    return sum(count == 2 for count in edges.values()) / max(len(edges), 1)


def readable(name) -> str | None:
    """A name a person gave, tidied, or None for one a tool numbered."""
    if not name or TOOL_NAME.match(name):
        return None
    tidy = re.sub(r"[ _]*(group|definition|defintion)?[ _]*\d+$", "", name, flags=re.I).replace("_", " ").strip(" -")
    return tidy if len(tidy) > 2 else None


def add_model(db, name, held) -> int:
    record = json.loads((DB / f"{name}.json").read_text())
    triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
    pieces = record["pieces"]
    families = spread(pieces)
    labelled = {row[0]: row[1:] for row in db.execute("select i.piece, s.family, s.label, s.source from instances i join shapes s on s.id = i.shape where i.model = ?", (name,))}
    known = dict(db.execute("select key, id from components"))
    next_id = (db.execute("select max(id) from components").fetchone()[0] or 0) + 1
    fresh, used = 0, Counter()
    for members in groups(pieces, triangles):
        mesh = np.vstack([triangles[slice(*pieces[i]["tris"])] for i in members])
        boxes = np.array([pieces[i]["box"] for i in members])
        turned = canonical(mesh).astype(float)
        sizes = turned.reshape(-1, 3).max(0)
        key = key_of({"sizes": (sizes / SIZE_STEP * 0.005).tolist()}, len(mesh))
        votes = Counter(labelled[i][0] if i in labelled else families[i] for i in members)
        family = next((f for f, _ in votes.most_common() if f != "unknown"), "unknown")
        names = Counter(pieces[i].get("name") or (labelled[i][1] if i in labelled else None) for i in members)
        raw = next((n for n, _ in names.most_common() if n), None)
        if key not in known and not held:
            known[key] = next_id
            np.save(STORE / "parts" / f"{next_id}.npy", turned.astype(np.float32))
            muse = any(i in labelled and labelled[i][2] == "muse" for i in members)
            nice = raw if muse else readable(raw)
            db.execute("insert into components values (?,?,?,?,?,?,0,0,?,?,?,?,?,?)",
                       (next_id, key, family, nice, ("muse" if muse else "file") if nice else None, raw, *sizes.tolist(), len(mesh), int(whole(turned)), closed_share(turned)))
            next_id += 1
            fresh += 1
        if not held:
            used[key] += 1
        db.execute("insert into placements (model, component, node, path, family, x0, y0, z0, x1, y1, z1) values (?,?,?,?,?,?,?,?,?,?,?)",
                   (name, known.get(key), pieces[members[0]]["node"], json.dumps(pieces[members[0]]["path"]), family, *boxes[:, :3].min(0).tolist(), *boxes[:, 3:].max(0).tolist()))
    for key, copies in used.items():
        db.execute("update components set copies = copies + ?, models = models + 1 where key = ?", (copies, key))
    db.execute("insert into catalogued values (?)", (name,))
    db.commit()
    return fresh


def update() -> dict:
    (STORE / "parts").mkdir(exist_ok=True)
    db = open_store()
    db.executescript(SCHEMA)
    held = set(held_out())
    done = {row[0] for row in db.execute("select model from catalogued")}
    for row in json.loads((DB / "index.json").read_text()):
        if row["name"] not in done:
            print(f"{row['name'][:44]:44} {add_model(db, row['name'], row['name'] in held):5} new components", flush=True)
    stats = {"models": len(done) + 0, "components": db.execute("select count(*) from components").fetchone()[0], "placed": db.execute("select count(*) from placements").fetchone()[0],
             "named": db.execute("select count(*) from components where name is not null").fetchone()[0], "whole": db.execute("select count(*) from components where whole=1").fetchone()[0],
             "closed": db.execute("select count(*) from components where closed>=0.95").fetchone()[0], "unknown": db.execute("select count(*) from components where family='unknown'").fetchone()[0]}
    print(stats)
    print(db.execute("select family, count(*), sum(copies) from components group by family order by 2 desc").fetchall())
    return stats


if __name__ == "__main__":
    update()
