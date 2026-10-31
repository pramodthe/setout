"""Score a finished method on the held-out models. Every run here is a look at the test: each is
logged, and nothing learned from one may tune the next without saying so in the log.

    python -m goal.final
"""
from __future__ import annotations

import json

import numpy as np

from . import compose, hidden, judge, labels, regions as regions_, section
from .baseline import cut, held_out
from .reference import DATA

WORK = DATA / "reference-3d" / "heldout"
KEYS = ("pieces", "iou", "layers", "families", "repetition", "layers_soft")


def main():
    WORK.mkdir(exist_ok=True)
    rows, tokens = [], 0
    for name in held_out():
        picture = WORK / f"{name}.png"
        pieces, _, axis, _, cuts = cut(name)
        section.draw(cuts, axis, picture)
        regions, size = regions_.find(picture)
        kept, more = WORK / f"{name}.labels.json", WORK / f"{name}.hidden.json"
        if kept.exists():
            labelled = json.loads(kept.read_text())
        else:
            labelled, usage = labels.ask(picture, regions, size)
            labelled["usage"] = usage
            kept.write_text(json.dumps(labelled, indent=1))
        if more.exists():
            answer = json.loads(more.read_text())
        else:
            answer, usage = hidden.ask(picture, regions, size, labelled)
            answer["usage"] = usage
            more.write_text(json.dumps(answer, indent=1))
        tokens += labelled["usage"].get("total_tokens", 0) + answer["usage"].get("total_tokens", 0)
        marked = {r["id"]: r for r in labelled["regions"]}
        built = compose.build(regions, marked, size) + hidden.build(answer["groups"], regions, size, compose.DEPTH_SHARE)
        reference = judge.load(name)
        score = judge.score(built, reference)
        rows.append({"name": name, "built": len(built), "real": len(reference), "regions": len(regions), "assembly": labelled["assembly"][:80], "score": score})
        print(f"{name[:20]:20} regions {len(regions):4} built {len(built):5} of {len(reference):5}  " + " ".join(f"{k} {score[k]:.2f}" for k in KEYS) + f" pass {score['passes']}  {labelled['assembly'][:50]}", flush=True)
    print("mean", " ".join(f"{k} {np.mean([r['score'][k] for r in rows]):.2f}" for k in KEYS), "passed", sum(r["score"]["passes"] for r in rows), "tokens", tokens)
    (DATA / "reference-3d" / "final.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
