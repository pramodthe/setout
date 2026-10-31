"""How much of the training models a small vocabulary and our shape grammar could express.

Uses training models only: a library built from a held-out model would be reading the answers.

    python -m goal.vocabulary
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np

from .baseline import held_out
from .families import family_of
from .reference import DATA, DB

ON_AXIS = 0.98  # share of surface area that must agree


def form_of(triangles) -> str:
    """box: faces on three square axes. prism: one axis along which it is the same shape all the
    way, which our `profile` can build. turned: faces all round one axis, which `revolve` can. free:
    none of these, so it needs CadQuery or a mesh."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1)
    keep = area > 1e-12
    if not keep.any():
        return "free"
    normals, weight = cross[keep] / area[keep, None], area[keep] / area[keep].sum()
    points = triangles.reshape(-1, 3)
    _, _, axes = np.linalg.svd(points - points.mean(0), full_matrices=False)
    local = np.abs(normals @ axes.T)
    square = ((local > 0.995).any(1) * weight).sum()
    if square >= ON_AXIS:
        return "box"
    for axis in range(3):
        along, across = local[:, axis] > 0.995, local[:, axis] < 0.03
        if ((along | across) * weight).sum() >= ON_AXIS:
            return "prism"
    for axis in range(3):
        rest = np.delete(local, axis, 1)
        if ((np.hypot(*rest.T) > 0.6) * weight).sum() >= ON_AXIS:
            return "turned"
    return "free"


def main():
    held = set(held_out())
    names = [p.stem for p in sorted(DB.glob("*.json")) if p.stem != "index" and p.stem not in held]
    shapes, forms, models, families = Counter(), defaultdict(Counter), defaultdict(set), defaultdict(Counter)
    total = 0
    for name in names:
        record = json.loads((DB / f"{name}.json").read_text())
        triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
        seen = {}
        for piece in record["pieces"]:
            total += 1
            key = tuple(round(s * 39.37 * 4) / 4 for s in piece["sizes"])  # to the quarter inch
            shapes[key] += 1
            models[key].add(name)
            form = seen.setdefault(key, form_of(triangles[piece["tris"][0]:piece["tris"][1]]))
            forms[form]["pieces"] += 1
            family = family_of(piece["path"][::-1], sorted(piece["materials"], key=piece["materials"].get, reverse=True))
            families[family][form] += 1
    ordered = shapes.most_common()
    print(f"{len(names)} training models, {total} pieces, {len(ordered)} sizes to the quarter inch")
    running = 0
    marks = {50, 100, 200, 500, 1000}
    for rank, (_, count) in enumerate(ordered, 1):
        running += count
        if rank in marks:
            print(f"  top {rank:5} sizes cover {running / total:.1%} of pieces")
    shared = sum(c for k, c in ordered if len(models[k]) >= 2)
    print(f"  sizes found in 2+ models cover {shared / total:.1%} of pieces")
    print("forms:", {k: f"{v['pieces'] / total:.1%}" for k, v in sorted(forms.items())})
    print("by family (pieces, box/prism/turned/free):")
    for family, tally in sorted(families.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(tally.values())
        print(f"  {family:14} {n:6}  " + " ".join(f"{tally[k] / n:.0%}" for k in ("box", "prism", "turned", "free")))
    (DATA / "reference-3d" / "vocabulary.json").write_text(json.dumps({
        "models": len(names), "pieces": total, "sizes": len(ordered), "forms": {k: v["pieces"] for k, v in forms.items()},
        "top": {str(r): sum(c for _, c in ordered[:r]) / total for r in sorted(marks)}, "shared": shared / total}, indent=1))


if __name__ == "__main__":
    main()
