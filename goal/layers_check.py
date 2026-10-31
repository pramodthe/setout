"""Why does `layers` stay low? By ray direction, and what is built where the reference has something.
Training models only.

    python -m goal.layers_check
"""
from __future__ import annotations

import json
from collections import Counter

import numpy as np

from . import compose, hidden, judge, regions as regions_
from .baseline import held_out
from .trial import DEV, WORK


def by_axis(grid, families, other, other_families):
    rng, out = np.random.default_rng(0), []
    for axis in range(3):
        rest = [k for k in range(3) if k != axis]
        scores = []
        for _ in range(judge.RAYS):
            pick = [slice(None)] * 3
            pick[rest[0]], pick[rest[1]] = rng.integers(grid.shape[rest[0]]), rng.integers(grid.shape[rest[1]])
            mine, theirs = judge._order(grid[tuple(pick)], families), judge._order(other[tuple(pick)], other_families)
            if theirs:
                scores.append(2 * judge._common(mine, theirs) / (len(mine) + len(theirs)))
        out.append(round(float(np.mean(scores)), 2) if scores else None)
    return out


def main():
    assert not set(DEV) & set(held_out())
    confusion = Counter()
    for name in DEV:
        regions, size = regions_.find(WORK / f"{name}.png")
        labelled = json.loads((WORK / f"{name}.labels.json").read_text())
        groups = json.loads((WORK / f"{name}.hidden.json").read_text())["groups"]
        built = compose.build(regions, {r["id"]: r for r in labelled["regions"]}, size) + hidden.build(groups, regions, size, compose.DEPTH_SHARE)
        reference = judge.load(name)
        fitted = judge.align(built, reference)
        low, high = judge._bounds(reference)
        spacing, shape = judge._grid_for(low, high, judge.GRID)
        mine, mine_f = judge.voxels(fitted, low, spacing, shape)
        theirs, their_f = judge.voxels(reference, low, spacing, shape)
        here = Counter()
        for a, b in zip(theirs[theirs > 0], mine[theirs > 0]):
            truth, made = their_f[a - 1], mine_f[b - 1] if b else "nothing"
            here[truth, made] += 1
        confusion.update(here)
        known = sum(v for (t, _), v in here.items() if t != "unknown")
        right = sum(v for (t, m), v in here.items() if t == m and t != "unknown")
        unknown = sum(v for (t, _), v in here.items() if t == "unknown") / sum(here.values())
        print(f"{name[:20]:20} rays x/depth-or-across/up {by_axis(mine, mine_f, theirs, their_f)}  solid known {1 - unknown:.0%}, right where known {right / max(known, 1):.0%}", flush=True)
    totals = Counter()
    for (truth, _), v in confusion.items():
        totals[truth] += v
    print("reference family -> what we built there (share of its voxels):")
    for truth, total in totals.most_common(10):
        row = sorted(((v / total, m) for (t, m), v in confusion.items() if t == truth), reverse=True)[:4]
        print(f"  {truth:13} {total:7}  " + "  ".join(f"{m} {share:.0%}" for share, m in row))


if __name__ == "__main__":
    main()
