"""Fit a matched assembly to a drawing: its real parts, moved so its layers land on the drawing's.

The layers of the match and of the section are lined up along each axis, as two sequences. That gives one
smooth mapping per axis, and every part moves by it, hidden ones too. A single mapping keeps touching
parts touching. Nothing is drawn here: all geometry is the library's.

    python -m goal.arrange <drawing> <labels.json or -> <matched model> <detail id> <title>
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

from . import judge, labels, regions as regions_
from .baseline import cut
from .reference import DB

BINS = 240
SHORT = 0.012  # of the span: a run this short is a joint or a line, not a layer
GAP = 0.45
SAME_TIME = {"brick": "masonry", "block": "masonry", "stone": "masonry", "mortar": "joint", "metal": "metal", "flashing": "metal", "fixing": "metal",
             "concrete": "concrete", "thinset": "bed", "board": "board", "timber": "board", "tile": "finish", "insulation": "insulation"}
KEEP_SIZE = {"fixing", "reinforcement", "weep", "sealant", "drainage"}  # these move, and never stretch
MATERIAL = {"block": "concrete", "metal": "aluminium", "flashing": "aluminium", "fixing": "steel", "reinforcement": "steel", "thinset": "bed", "sealant": "silicone",
            "weep": "packer", "drainage": "packer", "board": "fibre_cement", "unknown": "generic"}


def _runs(profile):
    """(start, stop, kind) for each stretch of one kind, in 0..1, with short stretches absorbed."""
    runs, start = [], 0
    for k in range(1, len(profile) + 1):
        if k == len(profile) or profile[k] != profile[start]:
            runs.append([start / len(profile), k / len(profile), profile[start]])
            start = k
    runs = [r for r in runs if r[1] - r[0] >= SHORT] or runs
    return runs


def align(a, b) -> list:
    """Line up two sequences of runs. Same kind at a similar place costs little, a skipped run GAP.
    Returns (position in a, position in b) landmarks, in order."""
    n, m = len(a), len(b)
    cost = np.full((n + 1, m + 1), 1e9)
    cost[0, 0] = 0
    step = {}
    for i in range(n + 1):
        for j in range(m + 1):
            for di, dj in ((1, 1), (1, 0), (0, 1)):
                if i < di or j < dj:
                    continue
                if di and dj:
                    x, y = a[i - 1], b[j - 1]
                    c = (0 if x[2] == y[2] else 0.8) + abs((x[0] + x[1]) - (y[0] + y[1])) / 2
                else:
                    c = GAP
                if cost[i - di, j - dj] + c < cost[i, j]:
                    cost[i, j] = cost[i - di, j - dj] + c
                    step[i, j] = (di, dj)
    pairs, i, j = [], n, m
    while (i, j) != (0, 0):
        di, dj = step[i, j]
        if di and dj and a[i - 1][2] == b[j - 1][2]:
            pairs += [(a[i - 1][1], b[j - 1][1]), (a[i - 1][0], b[j - 1][0])]
        i, j = i - di, j - dj
    pairs = sorted(set(pairs) | {(0.0, 0.0), (1.0, 1.0)})
    kept = [pairs[0]]
    for p in pairs[1:]:
        if p[0] > kept[-1][0] + 1e-6 and p[1] > kept[-1][1] + 1e-6:
            kept.append(p)
    return kept


def _profile(boxes, kinds, lo, hi, axis) -> list:
    """The main kind of material at each step along an axis. boxes: (u0, z0, u1, z1)."""
    weight: list = [{} for _ in range(BINS)]
    other = 1 - axis
    for box, kind in zip(boxes, kinds):
        a, b = box[axis], box[axis + 2]
        amount = box[other + 2] - box[other]
        for k in range(max(int((a - lo) / (hi - lo) * BINS), 0), min(int(np.ceil((b - lo) / (hi - lo) * BINS)), BINS)):
            weight[k][kind] = weight[k].get(kind, 0) + amount
    return [max(w, key=w.get) if w else "air" for w in weight]


def library_families(name) -> list:
    """What each piece of a model is, as labelled in the library."""
    from .library import STORE
    db = sqlite3.connect(STORE / "library.db")
    return [row[0] for row in db.execute("select coalesce(s.family, i.family) from instances i left join shapes s on s.id = i.shape where i.model = ? order by i.piece", (name,))]


def fit(name, regions, marked, size):
    """(moved pieces with labels, what lined up). Coordinates: along, across, up, in metres."""
    pieces, triangles, axis, position, cuts = cut(name)
    families = library_families(name)
    across = 1 - axis
    rows = [[pieces[i]["box"][across], pieces[i]["box"][2], pieces[i]["box"][across + 3], pieces[i]["box"][5]] for i, _ in cuts]
    kinds = [SAME_TIME.get(families[i], families[i]) for i, _ in cuts]
    box = np.array(rows)
    u = np.percentile(np.r_[box[:, 0], box[:, 2]], [1, 99])
    z = np.percentile(np.r_[box[:, 1], box[:, 3]], [1, 99])
    q_boxes, q_kinds = [], []
    for r in regions:
        label = marked.get(r.id) or {}
        if label.get("depth") == "empty":
            continue
        q_boxes.append([r.box[0], size[1] - r.box[3], r.box[2], size[1] - r.box[1]])
        q_kinds.append(SAME_TIME.get(label.get("family"), label.get("family", "unknown")))
    q = np.array(q_boxes, float)
    qu, qz = (q[:, 0].min(), q[:, 2].max()), (q[:, 1].min(), q[:, 3].max())
    map_u = align(_runs(_profile(box, kinds, u[0], u[1], 0)), _runs(_profile(q, q_kinds, *qu, 0)))
    map_z = align(_runs(_profile(box, kinds, z[0], z[1], 1)), _runs(_profile(q, q_kinds, *qz, 1)))
    span_z = z[1] - z[0]
    span_u = (qu[1] - qu[0]) / (qz[1] - qz[0]) * span_z  # the section's own proportions, at the match's size

    def f_u(v):
        s = (np.asarray(v) - u[0]) / (u[1] - u[0])
        return np.interp(s, *zip(*map_u)) * span_u + np.where(s < 0, s, 0) * span_u + np.where(s > 1, s - 1, 0) * span_u

    def f_z(v):
        s = (np.asarray(v) - z[0]) / span_z
        return (np.interp(s, *zip(*map_z)) + np.where(s < 0, s, 0) + np.where(s > 1, s - 1, 0)) * span_z

    moved = []
    along_low = min(p["box"][axis] for p in pieces)
    for piece, family in zip(pieces, families):
        t = triangles[piece["tris"][0]:piece["tris"][1]]
        a0, a1 = piece["box"][across], piece["box"][across + 3]
        b0, b1 = piece["box"][2], piece["box"][5]
        out = np.empty_like(t)
        out[..., 0] = t[..., axis] - along_low
        if family in KEEP_SIZE:
            out[..., 1] = t[..., across] - (a0 + a1) / 2 + f_u((a0 + a1) / 2)
            out[..., 2] = t[..., 2] - (b0 + b1) / 2 + f_z((b0 + b1) / 2)
        else:
            out[..., 1] = f_u(t[..., across])
            out[..., 2] = f_z(t[..., 2])
        moved.append({"triangles": out, "family": family, "name": piece.get("name")})
    return moved, {"across": len(map_u), "up": len(map_z)}


def rows_of(moved) -> list:
    """Parts by kind, for the app, in mm."""
    rows: dict = {}
    for piece in moved:
        name = piece["family"]
        material = MATERIAL.get(name, name)
        row = rows.setdefault(name, {"name": name, "material": material, "group": name, "pieces": 0, "tris": []})
        row["pieces"] += 1
        row["tris"] += (piece["triangles"].reshape(-1) * 1000).round(2).tolist()
    return list(rows.values())


def main(drawing, kept, name, ident, title):
    regions, size = regions_.find(drawing)
    if kept != "-" and Path(kept).exists():
        answer = json.loads(Path(kept).read_text())
    else:
        answer, usage = labels.ask(drawing, regions, size)
        print("tokens", usage.get("total_tokens"))
    marked = {r["id"]: r for r in answer["regions"]}
    moved, lined = fit(name, regions, marked, size)
    print(name, "pieces", len(moved), "landmarks", lined)
    from . import picture
    out = Path(drawing).with_suffix("").name
    picture.side_by_side(moved, judge.load(name), str(Path(__file__).resolve().parents[0] / f"../../../../data/goal/arranged_{out}.png"))
    import os
    os.environ.setdefault("SETOUT_VAR", "/Users/pramodthebe/Desktop/setout/data/goal/var")
    from app import blender_details
    blender_details.VAR = "/Users/pramodthebe/Desktop/setout/data/goal/var"
    from PIL import Image
    import io
    picture_bytes = io.BytesIO()
    Image.open(drawing).convert("RGB").save(picture_bytes, "PNG")
    print(blender_details.publish(ident, title, "library-arranged-trial", rows_of(moved), picture_bytes.getvalue()))


if __name__ == "__main__":
    main(*sys.argv[1:6])
