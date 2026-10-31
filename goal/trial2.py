"""The section route with hidden parts listed by an untrained model. Training models only.

    python -m goal.trial2
"""
from __future__ import annotations

import json
from collections import Counter

import numpy as np

from . import compose, hidden, judge, regions as regions_
from .baseline import cut, held_out
from .trial import DEV, WORK

KEYS = ("pieces", "iou", "layers", "families", "repetition", "layers_soft")


def main():
    assert not set(DEV) & set(held_out())
    rows = []
    for name in DEV:
        picture = WORK / f"{name}.png"
        regions, size = regions_.find(picture)
        labelled = json.loads((WORK / f"{name}.labels.json").read_text())
        kept = WORK / f"{name}.hidden.json"
        if kept.exists():
            answer = json.loads(kept.read_text())
        else:
            answer, usage = hidden.ask(picture, regions, size, labelled)
            answer["usage"] = usage
            kept.write_text(json.dumps(answer, indent=1))
        marked = {r["id"]: r for r in labelled["regions"]}
        pieces, _, axis, _, _ = cut(name)
        box = np.array([p["box"] for p in pieces])
        run = box[:, axis + 3].max() - box[:, axis].min()
        drawn = max(box[:, 4 - axis].max() - box[:, 1 - axis].min(), box[:, 5].max() - box[:, 2].min())
        reference = judge.load(name)
        shares = {"default": (compose.DEPTH_SHARE, None), "left": (compose.DEPTH_SHARE, "left"), "right": (compose.DEPTH_SHARE, "right")}
        row = {"name": name, "groups": len(answer["groups"]), "tokens": answer["usage"].get("total_tokens"), "real": len(reference)}
        for kind, (share, side) in shares.items():
            built = [piece for piece, _ in hidden.assemble(regions, marked, answer["groups"], size, share, side)]
            row[kind] = judge.score(built, reference)
            row[kind + "_built"] = len(built)
        rows.append(row)
        made = Counter()
        for group in answer["groups"]:
            made[group["family"]] += group["columns"] * group["rows"]
        print(f"{name[:20]:20} groups {row['groups']:3} tokens {row['tokens']} real {row['real']} built {row['default_built']} hidden {made.most_common(6)}", flush=True)
        for kind in shares:
            print(f"   {kind:8}" + " ".join(f"{k} {row[kind][k]:.2f}" for k in KEYS), flush=True)
    for kind in ("default", "left", "right"):
        print(f"mean {kind:8}", " ".join(f"{k} {np.mean([r[kind][k] for r in rows]):.2f}" for k in KEYS))


if __name__ == "__main__":
    main()
