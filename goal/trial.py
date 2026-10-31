"""Run the section route on training models and score it. The held-out set is not touched here.

    python -m goal.trial [names...]
Labels are kept, so changing how pieces are built costs no tokens.
"""
from __future__ import annotations

import json
import sys

import numpy as np

from . import compose, judge, labels, regions as regions_
from . import section
from .baseline import cut, held_out
from .reference import DATA

DEV = ["01.030.0603.3DW", "01.030.1001.3DW", "03.030.0301.3DW", "02.120.1103.3DW", "0400+Sill"]
WORK = DATA / "reference-3d" / "dev"


def trial(name) -> dict:
    if name in held_out():
        raise SystemExit("held-out models are for final scoring only")
    WORK.mkdir(exist_ok=True)
    picture, kept = WORK / f"{name}.png", WORK / f"{name}.labels.json"
    pieces, triangles, axis, position, cuts = cut(name)
    if not picture.exists():
        section.draw(cuts, axis, picture)
    regions, size = regions_.find(picture)
    if kept.exists():
        answer = json.loads(kept.read_text())
    else:
        answer, usage = labels.ask(picture, regions, size)
        answer["usage"] = usage
        kept.write_text(json.dumps(answer, indent=1))
    marked = {r["id"]: r for r in answer["regions"]}
    built = compose.build(regions, marked, size)
    reference = judge.load(name)
    box = np.array([p["box"] for p in pieces])
    floor = judge.score(section.extruded(cuts, axis, box[:, axis].min(), box[:, axis + 3].max()), reference)
    return {"name": name, "regions": len(regions), "cut": len(cuts), "built": len(built), "real": len(reference),
            "assembly": answer.get("assembly", "")[:70], "tokens": (answer.get("usage") or {}).get("total_tokens"),
            "score": judge.score(built, reference), "prism": floor}


def main():
    rows = []
    for name in sys.argv[1:] or DEV:
        rows.append(trial(name))
        print("done", name, rows[-1]["score"], flush=True)
    keys = ("pieces", "iou", "layers", "families", "repetition")
    for row in rows:
        print(f"{row['name'][:20]:20} regions {row['regions']:4} (cut {row['cut']:4}) built {row['built']:5} of {row['real']:5} tokens {row['tokens']}  {row['assembly']}")
        for kind in ("score", "prism"):
            print(f"   {kind:6}" + " ".join(f"{k} {row[kind][k]:.2f}" for k in keys) + f" pass {row[kind]['passes']}")
    print("mean  ", " ".join(f"{k} {np.mean([r['score'][k] for r in rows]):.2f}" for k in keys))
    print("floor ", " ".join(f"{k} {np.mean([r['prism'][k] for r in rows]):.2f}" for k in keys))


if __name__ == "__main__":
    main()
