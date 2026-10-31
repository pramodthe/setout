"""What holds each score down? Swap in perfect information one piece at a time, on training models.

Reading a reference is what this is for, so nothing here may run on a held-out model, and nothing
here may feed a generator. It answers where effort should go, and that is all.

    python -m goal.diagnose
"""
from __future__ import annotations

import json
from collections import Counter

import numpy as np

from . import compose, judge, regions as regions_, section
from .baseline import cut, held_out
from .families import family_of
from .reference import DATA
from .trial import DEV, WORK

KEYS = ("pieces", "iou", "layers", "families", "repetition")


def true_labels(name, pieces, cuts, axis, regions, size) -> dict:
    """Each region's real family: the smallest cut piece around its middle, in drawing coordinates."""
    across = 1 - axis
    every = np.vstack([segments for _, segments in cuts])[:, :, [across, 2]]
    low, high = every.reshape(-1, 2).min(0), every.reshape(-1, 2).max(0)
    scale = (section.SIDE - 2 * section.MARGIN) / max(high - low)
    boxes = []
    for index, segments in cuts:
        points = segments[:, :, [across, 2]].reshape(-1, 2)
        boxes.append((index, points.min(0), points.max(0)))
    out = {}
    for region in regions:
        y = (region.box[1] + region.box[3]) // 2
        spans = region.rows.get(y) or next(iter(region.rows.values()))
        x0, x1 = max(spans, key=lambda s: s[1] - s[0])
        point = np.array([((x0 + x1) / 2 - section.MARGIN) / scale + low[0], (size[1] - y - section.MARGIN) / scale + low[1]])
        around = [(np.prod(b - a), i) for i, a, b in boxes if (point >= a).all() and (point <= b).all()]
        if not around:
            out[region.id] = {"family": "unknown", "depth": "empty"}
            continue
        piece = pieces[min(around)[1]]
        family = family_of(piece["path"][::-1], sorted(piece["materials"], key=piece["materials"].get, reverse=True))
        how = "units" if family in compose.UNIT_FAMILIES else "spaced" if family in compose.SPACED_FAMILIES else "continuous"
        out[region.id] = {"family": family, "depth": how}
    return out


def main():
    if set(DEV) & set(held_out()):
        raise SystemExit("a held-out model is in the dev list")
    table, agree = {}, Counter()
    confusion = Counter()
    for name in DEV:
        picture = WORK / f"{name}.png"
        pieces, triangles, axis, position, cuts = cut(name)
        regions, size = regions_.find(picture)
        muse = {r["id"]: r for r in json.loads((WORK / f"{name}.labels.json").read_text())["regions"]}
        truth = true_labels(name, pieces, cuts, axis, regions, size)
        reference = judge.load(name)
        box = np.array([p["box"] for p in pieces])
        real_depth = box[:, axis + 3].max() - box[:, axis].min()
        real_across, real_up = box[:, 4 - axis].max() - box[:, 1 - axis].min(), box[:, 5].max() - box[:, 2].min()
        share = compose.DEPTH_SHARE
        rows = {"muse labels": compose.build(regions, muse, size), "true labels": compose.build(regions, truth, size)}
        compose.DEPTH_SHARE = real_depth / max(real_across, real_up)
        rows["true labels + depth"] = compose.build(regions, truth, size)
        rows["muse labels + depth"] = compose.build(regions, muse, size)
        depth_ratio = compose.DEPTH_SHARE
        compose.DEPTH_SHARE = share
        table[name] = {kind: judge.score(built, reference) for kind, built in rows.items()}
        table[name]["depth_ratio"] = round(float(depth_ratio), 2)
        for region in regions:
            t, m = truth[region.id]["family"], (muse.get(region.id) or {}).get("family", "none")
            if t != "unknown":
                agree["match" if t == m else "miss"] += 1
                if t != m:
                    confusion[(t, m)] += 1
            else:
                agree["truth unknown"] += 1
        print(name, "depth ratio", table[name]["depth_ratio"], flush=True)
    for kind in ("muse labels", "muse labels + depth", "true labels", "true labels + depth"):
        print(f"{kind:22}", " ".join(f"{k} {np.mean([table[n][kind][k] for n in DEV]):.2f}" for k in KEYS))
    print("label agreement", dict(agree), "confusions", confusion.most_common(12))
    (DATA / "reference-3d" / "diagnosis.json").write_text(json.dumps({"table": table, "agree": agree, "confusion": [[*k, v] for k, v in confusion.most_common(40)]}, indent=1))


if __name__ == "__main__":
    main()
