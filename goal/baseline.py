"""Check the judge on real models, and put a floor under every method.

    python -m goal.baseline
Writes data/reference-3d/baseline.json and each held-out section's picture.
"""
from __future__ import annotations

import json
import time

import numpy as np

from . import judge, section
from .reference import DATA, DB

OUT = DATA / "reference-3d" / "sections"


def held_out() -> list[str]:
    return json.loads((DATA / "reference-3d" / "holdout.json").read_text())["held_out"]


def cut(name):
    record = json.loads((DB / f"{name}.json").read_text())
    triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
    from .planes import representative
    axis, position = representative(name)
    return record["pieces"], triangles, axis, position, section.section(record["pieces"], triangles, axis, position)


def main():
    OUT.mkdir(exist_ok=True)
    names, rows = held_out(), []
    for k, name in enumerate(names):
        started = time.time()
        reference = judge.load(name)
        pieces, triangles, axis, position, cuts = cut(name)
        section.draw(cuts, axis, OUT / f"{name}.png")
        box = np.array([p["box"] for p in pieces])
        prism = section.extruded(cuts, axis, box[:, axis].min(), box[:, axis + 3].max())
        other = judge.load(names[(k + 1) % len(names)])
        rows.append({"name": name, "pieces": len(reference), "cut": len(cuts), "axis": axis,
                     "known_share": round(np.mean([p["family"] != "unknown" for p in reference]), 2),
                     "self": judge.score(reference, reference), "other": judge.score(other, reference),
                     "prism": judge.score(prism, reference), "seconds": round(time.time() - started)})
        row = rows[-1]
        print(f"{name[:22]:22} {row['pieces']:5} pieces, cut {row['cut']:4}, known {row['known_share']:.2f}, {row['seconds']}s", flush=True)
        for kind in ("self", "other", "prism"):
            s = row[kind]
            print(f"   {kind:6} pieces {s['pieces']:.2f} iou {s['iou']:.2f} layers {s['layers']:.2f} families {s['families']:.2f} repeat {s['repetition']:.2f} pass {s['passes']}", flush=True)
    (DATA / "reference-3d" / "baseline.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
