"""A section to a local 3D assembly, as a draft to look at.

A separate route from the verified versions, and it stays apart from them: it never writes a version,
never reaches the verifier and can never be published. Shapes come from the drawing's own closed
outlines, what each one is from one model call, and what the slice can't show from a second.
Everything is labelled for what it is. Sizes are assumptions, because a section that isn't to
scale gives shape and no measurements, and parts the drawing doesn't show are `inferred`.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

import numpy as np

from .store import VAR, figure_path

IDENT = re.compile(r"^[0-9a-f]{32}$")
# Rough order things go together, bottom of the assembly first. Only an order to step through.
ORDER = ("concrete", "block", "brick", "stone", "reinforcement", "mortar", "fixing", "flashing", "drainage", "weep",
         "insulation", "board", "timber", "thinset", "tile", "plaster", "sealant", "metal", "glass", "unknown")
COURSE_MM = {"brick": 76.0, "block": 200.0}  # the usual course, only to give the draft a sensible size
LONGEST_MM = 1500.0
_folder = lambda: Path(VAR) / "section-drafts"  # noqa: E731


def _path(ident) -> Path:
    return _folder() / f"{ident}.json"


def get(ident) -> dict | None:
    if not IDENT.match(ident) or not _path(ident).exists():
        return None
    return json.loads(_path(ident).read_text())


def _save(ident, record) -> None:
    _folder().mkdir(parents=True, exist_ok=True)
    temporary = _path(ident).with_suffix(".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(_path(ident))


def start(doc_id, crop_id) -> dict:
    ident = uuid.uuid4().hex
    _save(ident, {"id": ident, "state": "running", "step": "reading the drawing"})
    threading.Thread(target=_run, args=(ident, doc_id, crop_id), daemon=True).start()
    return {"id": ident, "state": "running"}


def scale_of(regions, marked, size) -> float:
    """Millimetres per pixel, only so the draft has a sensible size. It measures nothing, and it comes
    from a usual course height, or failing that a usual section size."""
    for family, course in COURSE_MM.items():
        heights = [r.height for r in regions if marked.get(r.id, {}).get("family") == family and r.height > 8]
        if len(heights) >= 3:
            return course / float(np.median(heights))
    return LONGEST_MM / max(size)


def scene_of(pieces, title, assembly, mm, height) -> dict:
    """The viewer's scene. Z up in pictures becomes Y up here, as the viewer wants."""
    solids, rank = [], {family: k for k, family in enumerate(ORDER)}
    ordered = sorted(enumerate(pieces), key=lambda item: rank.get(item[1][0]["family"], len(ORDER)))
    for number, (piece, origin) in ordered:
        tris = piece["triangles"][..., [0, 2, 1]] * mm
        family, shown = piece["family"], origin == "section"
        solids.append({"id": f"{family}_{number}", "name": family, "layer": family, "source_label": None,
                       "representation": "solid", "color": "", "class_id": None,
                       "note": "Outline from the drawing. Size assumed." if shown else "Not in the drawing: a model listed it.",
                       "provenance": "assumption" if shown else "inferred", "confidence": None,
                       "inferred_by": None if shown else "section-draft:muse",
                       "tris": tris.reshape(-1).round(2).tolist(), "tri_count": len(tris),
                       "bounds": [*tris.reshape(-1, 3).min(0).round(2).tolist(), *tris.reshape(-1, 3).max(0).round(2).tolist()],
                       "params": [], "product": None, "install_after": [], "source_regions": []})
    return {"detail_id": "section-draft", "title": title, "units": "mm", "viewer_scale": 0.01, "origin": "section-draft-v0",
            "source": {}, "context": {"assembly": assembly}, "install_order": [s["id"] for s in solids], "relations": [],
            "unresolved": [], "verification": [], "solids": solids}


def _run(ident, doc_id, crop_id) -> None:
    from goal import compose, hidden, labels, regions as regions_
    try:
        picture = figure_path(doc_id, crop_id)
        regions, size = regions_.find(picture)
        if not regions:
            raise ValueError("no closed outlines found in this drawing")
        _save(ident, {"id": ident, "state": "running", "step": f"naming {len(regions)} regions"})
        labelled, first = labels.ask(picture, regions, size)
        _save(ident, {"id": ident, "state": "running", "step": "working out what the slice doesn't show"})
        groups, second = hidden.ask(picture, regions, size, labelled)
        marked = {r["id"]: r for r in labelled["regions"]}
        pieces = hidden.assemble(regions, marked, groups["groups"], size, compose.DEPTH_SHARE)
        scene = scene_of(pieces, labelled.get("assembly") or "Section draft", labelled.get("assembly"), scale_of(regions, marked, size), size[1])
        tokens = (first.get("total_tokens") or 0) + (second.get("total_tokens") or 0)
        _save(ident, {"id": ident, "state": "done", "scene": scene, "regions": len(regions), "tokens": tokens,
                      "from_drawing": sum(1 for _, origin in pieces if origin == "section"),
                      "inferred": sum(1 for _, origin in pieces if origin == "hidden")})
    except Exception as error:  # a failed draft is a message on the page, never a lost server thread
        _save(ident, {"id": ident, "state": "failed", "error": str(error)[:300]})
