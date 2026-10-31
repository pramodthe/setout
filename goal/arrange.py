"""Arrange library parts to a drawing. The drawing's outlines set every visible layer, filled with real
library parts, and the matched assembly supplies only what a section can't show.

For those hidden parts, the layers of the match and of the section are lined up along each axis, and that
mapping moves them into place. Nothing is modelled here: geometry is the library's, or the drawing's outline.

    python -m goal.arrange <drawing> <matched model> <detail id> <title>
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

from . import labels, regions as regions_
from .baseline import cut
from .library import STORE

STORE_MESHES = STORE / "meshes"
LABELS = STORE / "drawing_labels"
RUN = 1.2  # metres of assembly along the run, a choice of how much to draw
HELD = 0.25  # share of a hidden part's outline that must lie on something drawn
JOINT = 0.17  # head joint, as a share of a unit's smaller side

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
    db = sqlite3.connect(STORE / "library.db")
    return [row[0] for row in db.execute("select coalesce(s.family, i.family) from instances i left join shapes s on s.id = i.shape where i.model = ? order by i.piece", (name,))]


def library_names(name) -> list:
    db = sqlite3.connect(STORE / "library.db")
    return [row[0] for row in db.execute("select s.label from instances i left join shapes s on s.id = i.shape where i.model = ? order by i.piece", (name,))]


def fit(name, regions, marked, size, scale):
    """(moved pieces, what lined up). Coordinates: along, across, up, in metres, on the drawing's own frame."""
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
    span_z = (qz[1] - qz[0]) * scale
    span_u = (qu[1] - qu[0]) * scale

    def f_u(v):
        s = (np.asarray(v) - u[0]) / (u[1] - u[0])
        return np.interp(s, *zip(*map_u)) * span_u + np.where(s < 0, s, 0) * span_u + np.where(s > 1, s - 1, 0) * span_u

    def f_z(v):
        s = (np.asarray(v) - z[0]) / (z[1] - z[0])
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


def _cuboid(along0, along1, u0, u1, z0, z1):
    from .compose import _block
    return _block(along0, along1, u0, u1, z0, z1)


def best_shape(db, family, width, height) -> tuple | None:
    """The whole library unit of a family closest to a layer's cross-section, in proportion and in size."""
    rows = db.execute("select id, label, longest, middle, shortest, copies from shapes where family = ? and copies >= 2 and longest < 6 * shortest and longest >= 1.4 * middle and whole = 1",
                      (family,)).fetchall()
    if not rows:
        return None
    wide, thin = max(width, height), min(width, height)
    return min(rows, key=lambda r: abs(np.log((r[3] / r[4]) / (wide / thin))) + abs(np.log(r[3] / wide)) + abs(np.log(r[4] / thin)) - 0.05 * np.log(r[5]))


def visible(regions, marked, size, scale, run, db) -> list:
    """The parts the drawing shows, in its own outlines: layers of units from real library parts,
    everything else as the exact outline along the run. Coordinates: along, across, up, in metres."""
    pieces = []
    left, bottom = min(r.box[0] for r in regions), max(r.box[3] for r in regions)
    courses = sorted({r.box[1] for r in regions})

    def across(v):
        return (v - left) * scale

    def up(v):
        return (bottom - v) * scale

    for region in regions:
        label = marked.get(region.id) or {"family": "unknown", "depth": "continuous"}
        family, how = label["family"], label["depth"]
        if how == "empty":
            continue
        u0, u1, z1, z0 = across(region.box[0]), across(region.box[2]), up(region.box[1]), up(region.box[3])
        width, height = u1 - u0, z1 - z0
        square = region.area >= 0.9 * region.width * region.height
        shape = best_shape(db, family, width, height) if how == "units" and square else None
        if shape is None:
            bands = [_cuboid(0, run, across(a), across(b), up(y1), up(y0)) for a, b, y0, y1 in region.bands()]
            pieces.append({"triangles": np.vstack(bands), "family": family, "name": family})
            continue
        ident, name, longest, middle, shortest, _ = shape
        mesh = np.load(STORE_MESHES / f"{ident}.npy").astype(float)
        tall = height >= width
        fit_w, fit_h = (shortest, middle) if tall else (middle, shortest)
        length, joint = longest * height / fit_h, JOINT * min(width, height)
        start = -(length + joint) / 2 if courses.index(region.box[1]) % 2 else 0.0
        along = start
        while along < run:
            a, b = max(along, 0.0), min(along + length, run)
            if b - a > length * 0.2:
                t = mesh * [(b - a) / longest, 1, 1] if (b - a) < length - 1e-6 else mesh * [length / longest, 1, 1]
                out = np.empty_like(t)
                out[..., 0] = t[..., 0] + a
                axes = (2, 1) if tall else (1, 2)
                out[..., 1] = t[..., axes[0]] * width / fit_w + u0
                out[..., 2] = t[..., axes[1]] * height / fit_h + z0
                pieces.append({"triangles": out, "family": family, "name": name or family})
            if b + joint < run:
                pieces.append({"triangles": _cuboid(b, b + joint, u0, u1, z0, z1), "family": "mortar", "name": "head joint"})
            along += length + joint
    return pieces


def drawn_mask(regions, marked, size) -> np.ndarray:
    mask = np.zeros((size[1], size[0]), bool)
    for region in regions:
        if (marked.get(region.id) or {}).get("depth") != "empty":
            for y, runs in region.rows.items():
                for a, b in runs:
                    mask[y, a:b] = True
    return mask


def hidden_parts(name, regions, marked, size, scale, run) -> tuple:
    """What the drawing can't show, from the matched assembly, moved onto the drawing's layers. A part
    that lands where the drawing has nothing is dropped, since it has nothing to sit in."""
    moved, lined = fit(name, regions, marked, size, scale)
    mask = drawn_mask(regions, marked, size)
    left, bottom = min(r.box[0] for r in regions), max(r.box[3] for r in regions)
    out, dropped = [], 0
    for piece, title in zip(moved, library_names(name)):
        t = piece["triangles"]
        if piece["family"] not in KEEP_SIZE or t[..., 0].min() < 0 or t[..., 0].max() > run:
            continue
        x0, x1 = (left + np.array([t[..., 1].min(), t[..., 1].max()]) / scale).round().astype(int)
        y0, y1 = (bottom - np.array([t[..., 2].max(), t[..., 2].min()]) / scale).round().astype(int)
        patch = mask[max(y0, 0):max(y1, 0) + 1, max(x0, 0):max(x1, 0) + 1]
        if patch.size and patch.mean() >= HELD:
            out.append({**piece, "name": title or piece["family"]})
        else:
            dropped += 1
    print("hidden parts dropped, nothing drawn there:", dropped)
    return out, lined


def plain_name(name) -> str:
    return re.sub(r"[ _]*group[ _]*\d+$", "", str(name), flags=re.I).strip(" _-") or str(name)


def rows_of(pieces) -> list:
    """Parts by name, for the app, in mm."""
    rows: dict = {}
    for piece in pieces:
        name = plain_name(piece.get("name") or piece["family"]).lower().replace(" ", "_")
        material = MATERIAL.get(piece["family"], piece["family"])
        row = rows.setdefault(name, {"name": name, "material": material, "group": piece["family"], "pieces": 0, "tris": []})
        row["pieces"] += 1
        row["tris"] += (piece["triangles"].reshape(-1) * 1000).round(2).tolist()
    return list(rows.values())


def scale_of(regions, marked) -> float:
    """Metres per pixel, only so parts come out a believable size: a usual course height where the drawing
    has courses, otherwise a usual section size. Nothing is measured from it."""
    for family, course in (("brick", 0.076), ("block", 0.19)):
        heights = [r.height for r in regions if marked.get(r.id, {}).get("family") == family and marked[r.id].get("depth") == "units" and r.height > 8]
        if len(heights) >= 2:
            return course / float(np.median(heights))
    return 1.5 / max(max(r.box[2] for r in regions) - min(r.box[0] for r in regions), max(r.box[3] for r in regions) - min(r.box[1] for r in regions))


def main(drawing, name, ident, title):
    regions, size = regions_.with_ink(drawing)
    kept = Path(drawing).with_suffix(".inklabels.json")
    work = LABELS / kept.name
    LABELS.mkdir(exist_ok=True)
    if work.exists():
        answer = json.loads(work.read_text())
    else:
        answer, usage = labels.ask(drawing, regions, size)
        work.write_text(json.dumps(answer))
        print("tokens", usage.get("total_tokens"))
    marked = {r["id"]: r for r in answer["regions"]}
    scale = scale_of(regions, marked)
    db = sqlite3.connect(STORE / "library.db")
    shown = visible(regions, marked, size, scale, RUN, db)
    unseen, lined = hidden_parts(name, regions, marked, size, scale, RUN)
    print(name, "regions", len(regions), "shown", len(shown), "hidden", len(unseen), "landmarks", lined, "scale mm/px", round(scale * 1000, 2))
    from collections import Counter
    print(Counter((marked.get(r.id) or {}).get("family") for r in regions).most_common(10), Counter(p.get("name") for p in unseen).most_common(8))
    import io

    from PIL import Image

    from app import blender_details
    blender_details.VAR = "/Users/pramodthebe/Desktop/setout/data/goal/var"
    out = io.BytesIO()
    Image.open(drawing).convert("RGB").save(out, "PNG")
    print(blender_details.publish(ident, title, "library-arranged-trial", rows_of(shown + unseen), out.getvalue()))


if __name__ == "__main__":
    main(*sys.argv[1:5])
