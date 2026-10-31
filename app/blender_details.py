"""Scenes built in headless Blender, shown on the normal detail page.

A trial, kept apart from the versioned details: these are files under SETOUT_VAR, not versions, the
verifier has not seen them, and they can't be reviewed or published. Each one says so in its note.
Parts are in the order they were written, which is not yet a checked install order.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .store import VAR

IDENT = re.compile(r"^blender-[a-z0-9-]{1,60}$")
# The kit's materials as the viewer's material words.
VIEWER_MATERIAL = {"bed": "bedding", "backing": "backing_rod", "galvanised": "steel", "silicone": "sealant", "rubber": "plastic"}
NOTE = "Built in Blender from one section. Sizes are assumed, and nothing here has been verified."


def is_one(ident) -> bool:
    return str(ident).startswith("blender-")


def _folder() -> Path:
    return Path(VAR) / "blender-details"


def scene(ident) -> dict | None:
    path = _folder() / f"{ident}.json"
    return json.loads(path.read_text()) if IDENT.match(ident) and path.exists() else None


def drawing(ident) -> bytes | None:
    path = _folder() / f"{ident}.png"
    return path.read_bytes() if IDENT.match(ident) and path.exists() else None


def scene_of(ident, title, origin, parts) -> dict:
    """The viewer's scene. Across, up and along in the kit are x, y and z in the viewer."""
    solids, run = [], 0.0
    for row in parts:
        tris = row["tris"]
        moved = [v for k in range(0, len(tris), 3) for v in (tris[k + 1], tris[k + 2], tris[k])]
        word = VIEWER_MATERIAL.get(row["material"], row["material"])
        ids = {s["id"] for s in solids}
        ident_part = row["name"] if row["name"] not in ids else f"{row['name']}_{row['material']}"
        run = max(run, *moved[2::3]) if moved else run
        solids.append({"id": ident_part, "name": word, "layer": word, "source_label": None, "representation": "solid", "color": "#9aa3ad",
                       "class_id": None, "provenance": "assumption", "confidence": None, "inferred_by": None,
                       "note": f"{row['pieces']} piece{'s' if row['pieces'] != 1 else ''} in group {row['group']}. {NOTE}",
                       "tris": moved, "tri_count": len(moved) // 9,
                       "bounds": [min(moved[0::3]), min(moved[1::3]), min(moved[2::3]), max(moved[0::3]), max(moved[1::3]), max(moved[2::3])],
                       "params": [{"name": "size", "value": 0, "unit": "mm", "provenance": "assumption", "role": "dimension", "note": "Assumed: the drawing is not to scale."}],
                       "product": None, "install_after": [], "source_regions": []})
    return {"detail_id": ident, "title": title, "units": "mm", "viewer_scale": 0.01, "origin": origin, "source": {}, "unresolved": [],
            "context": {"kind": "section", "illustrative_extent_z_mm": run}, "install_order": [s["id"] for s in solids], "relations": [],
            "verification": [], "solids": solids}


def publish(ident, title, origin, parts, picture=None) -> Path:
    if not IDENT.match(ident):
        raise ValueError("a Blender detail id starts with blender-")
    _folder().mkdir(parents=True, exist_ok=True)
    path = _folder() / f"{ident}.json"
    path.write_text(json.dumps(scene_of(ident, title, origin, parts)))
    if picture:
        (_folder() / f"{ident}.png").write_bytes(picture)
    return path
