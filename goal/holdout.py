"""Freeze the held-out models once, before anything is tuned. Never rerun on a frozen file."""
from __future__ import annotations

import hashlib
import json

from .reference import DATA, DB

FILE = DATA / "reference-3d" / "holdout.json"
COUNT = 7
LARGEST_M = 8.0  # bigger means exploded or a scene: it can't be cut into one section


def choose() -> dict:
    if FILE.exists():
        raise SystemExit(f"{FILE} is frozen")
    rows = json.loads((DB / "index.json").read_text())
    usable = [r for r in rows if max(r["size_m"]) <= LARGEST_M and "EXPLODED" not in r["name"].upper()]
    usable.sort(key=lambda r: hashlib.sha256(r["name"].encode()).hexdigest())
    held, families = [], set()
    for row in usable:  # one from each family first, so the set spans the kinds of detail
        if row["name"][:6] not in families and len(held) < COUNT:
            held.append(row["name"])
            families.add(row["name"][:6])
    for row in usable:
        if len(held) < COUNT and row["name"] not in held:
            held.append(row["name"])
    frozen = {"held_out": sorted(held), "rule": "sha256 order, one per family first",
              "usable": len(usable), "of": len(rows)}
    FILE.write_text(json.dumps(frozen, indent=1))
    return frozen


if __name__ == "__main__":
    print(json.dumps(choose(), indent=1))
