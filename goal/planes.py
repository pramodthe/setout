"""Where to cut the section for a model: the plane that explains it best, as a person chooses one.

This prepares the exam question, so it may read the model. The generator only ever gets the picture.
The first rule, cut the most pieces, chose the wrong axis on 2 of 5 models and held overlap at 0.40,
against 0.67 from the best plane.

    python -m goal.planes [names...]
"""
from __future__ import annotations

import json
import sys

import numpy as np

from . import judge, section
from .reference import DATA, DB

STORE = DATA / "reference-3d" / "planes"
SHARES = (0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9)


def representative(name) -> tuple[int, float]:
    """(axis, position), cached: the same exam every time."""
    STORE.mkdir(exist_ok=True)
    kept = STORE / f"{name}.json"
    if kept.exists():
        found = json.loads(kept.read_text())
        return found["axis"], found["position"]
    pieces = json.loads((DB / f"{name}.json").read_text())["pieces"]
    triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
    reference = judge.load(name)
    low, high = judge._bounds(reference)
    spacing, shape = judge._grid_for(low, high, judge.GRID)
    theirs = judge.voxels(reference, low, spacing, shape)[0] > 0
    best = (-1.0, 0, 0.0)
    for axis in (0, 1):
        for share in SHARES:
            position = float(low[axis] + (high[axis] - low[axis]) * share)
            cuts = section.section(pieces, triangles, axis, position)
            if cuts:
                solid = section.extruded(cuts, axis, low[axis], high[axis])
                best = max(best, (judge.overlap(judge.voxels(solid, low, spacing, shape)[0] > 0, theirs), axis, position))
    kept.write_text(json.dumps({"axis": best[1], "position": best[2], "overlap": round(best[0], 3)}))
    return best[1], best[2]


if __name__ == "__main__":
    for model in sys.argv[1:]:
        print(model, representative(model))
