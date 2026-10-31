"""The annotation store, and the edits a reviewer makes to a sheet.

A store is a folder under data/ (gitignored) with one folder per sheet: `sheet.json`,
`sheet.png` and `events.jsonl`, an append-only log of every decision and its seconds.
Sheets are written whole, through a temporary file, so a crash never leaves half a record.

The guard is the one from tools/dxf_census.py: outside any repository, or under its
data/ folder. A drawing, render or mask never lands in tracked files.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from app import synthetic

from . import names, planar, regions

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dxf_census import allowed_output as _census_guard  # noqa: E402

SCHEMA = "setout-annotation-v1"
DEFAULT_STORE = ROOT / "data" / "annotation"
SHEET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class Refused(Exception):
    """A path or edit this tool will not act on."""


def allowed_output(path) -> bool:
    """Under a repository's data/ folder, or in no repository at all. Stricter than the
    census guard in one way: this worktree's checkout may sit inside another repo."""
    path = Path(path).resolve()
    if not _census_guard(path):
        return False
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            data = (folder / "data").resolve()
            return path == data or data in path.parents
    return True


def require_output(path) -> Path:
    if not allowed_output(path):
        raise Refused(f"Refusing a path inside the repo tree outside data/: {path}\n"
                      "Drawings, renders and masks must never land in tracked files.")
    return Path(path)


def require_store(path) -> Path:
    """A store holds renders of private drawings: in a repository it lives in
    data/annotation and nowhere else. Outside any repository it may live anywhere."""
    path = Path(path).resolve()
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            home = (folder / "data" / "annotation").resolve()
            if path != home and home not in path.parents:
                raise Refused(f"Refusing a store outside data/annotation: {path}")
            break
    return require_output(path)


def sheet_dir(store, sheet_id) -> Path:
    if not SHEET_ID.match(str(sheet_id)):
        raise Refused(f"not a sheet id: {sheet_id!r}")
    return require_store(store) / sheet_id


def sheet_ids(store) -> list[str]:
    store = Path(store)
    if not store.is_dir():
        return []
    return sorted(p.parent.name for p in store.glob("*/sheet.json"))


def read_sheet(store, sheet_id) -> dict:
    path = sheet_dir(store, sheet_id) / "sheet.json"
    if not path.is_file():
        raise Refused(f"no such sheet: {sheet_id}")
    return json.loads(path.read_text())


def write_sheet(store, record) -> None:
    folder = sheet_dir(store, record["sheet_id"])
    folder.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as out:
            json.dump(record, out, separators=(",", ":"))
        os.replace(temp, folder / "sheet.json")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def log_event(store, sheet_id, event: dict) -> None:
    line = json.dumps({"at": round(time.time(), 3), **event})
    with open(sheet_dir(store, sheet_id) / "events.jsonl", "a") as out:
        out.write(line + "\n")


# ---------------------------------------------------------------- reviewer edits

def region_of(record, region_id) -> dict:
    found = next((r for r in record["regions"] if r["id"] == region_id), None)
    if found is None:
        raise Refused(f"no region {region_id}")
    return found


def has_decisions(record) -> bool:
    """A person has touched this sheet: re-proposing it would throw their work away."""
    return bool(record.get("excluded_from_export")) or any(
        r["rejected"] or r.get("seconds", 0.0) > 0 or r["kind"] in ("merged", "split")
        or r["proposal"]["state"] != names.PROPOSED for r in record["regions"])


def is_decided(region) -> bool:
    """Set aside, rejected, or named by a person."""
    return bool(region["dropped"] or region["rejected"]
                or region["proposal"]["state"] in (names.CONFIRMED, names.CORRECTED))


def accept(record, region_id) -> None:
    region = region_of(record, region_id)
    if region["proposal"]["class_id"] == names.UNKNOWN:
        raise Refused(f"{region_id} has no class to accept: pick one")
    region["proposal"]["state"] = names.CONFIRMED
    region["rejected"] = False
    region["dropped"] = None


def accept_all(record) -> list[str]:
    """Accept every open proposal that has a class. Near-identical details arrive named, and
    a person checking one by eye should not have to press a key per region."""
    done = []
    for region in record["regions"]:
        if not is_decided(region) and region["proposal"]["class_id"] != names.UNKNOWN:
            region["proposal"]["state"] = names.CONFIRMED
            done.append(region["id"])
    if not done:
        raise Refused("no proposal with a class to accept")
    return done


def correct(record, region_id, class_id, known) -> None:
    if class_id not in known:
        raise Refused(f"not an ontology class: {class_id!r}")
    region = region_of(record, region_id)
    proposal = region["proposal"]
    if class_id == proposal["class_id"] and proposal["source"] is not None:
        proposal["state"] = names.CONFIRMED  # choosing the proposal is accepting it
    else:
        proposal["corrected_from"] = proposal["class_id"]
        proposal.update(class_id=class_id, state=names.CORRECTED)
    region["rejected"] = False
    region["dropped"] = None


def reject(record, region_id) -> None:
    """Not a part. It stays in the record, out of the dataset."""
    region = region_of(record, region_id)
    region["rejected"] = True
    region["proposal"]["state"] = names.PROPOSED


def restore(record, region_id) -> None:
    """Bring back a region the rules set aside or a person rejected."""
    region = region_of(record, region_id)
    region["rejected"] = False
    region["dropped"] = None
    region["proposal"]["state"] = names.PROPOSED


def _next_id(record) -> str:
    """Ids are never reused: the event log and undo refer to regions by id."""
    used = [int(r["id"][1:]) for r in record["regions"] if r["id"][1:].isdigit()]
    number = max(record.get("last_region", 0), *used, 0) + 1
    record["last_region"] = number
    return f"r{number}"


def _rebuilt(record, base, rings, kind, region_id) -> dict:
    """A new region from a reviewer's edit, measured like any other."""
    rings = [planar.simplify_ring(r, record["tolerance"]["eps"]) for r in rings]
    rings = [r for r in rings if len(r) >= 3]
    width = regions.width_of(rings)
    k = record["render"]["px_per_unit"]
    return {**base, "id": region_id, "kind": kind,
            "rings": [[[round(float(x), 6), round(float(y), 6)] for x, y in r] for r in rings],
            "area": round(abs(planar.rings_area(rings)), 6), "width": round(width, 6), "width_px": round(width * k, 3),
            "thin": bool(width * k < synthetic.Config().thin_px),
            "anchor": [round(float(v), 6) for v in regions.anchor_of(rings)],
            "dropped": None, "rejected": False, "seconds": 0.0}


def _as_rings(region) -> list:
    return [np.array(r, dtype=float) for r in region["rings"]]


def _edges(rings):
    a = np.concatenate(rings)
    b = np.concatenate([np.roll(r, -1, axis=0) for r in rings])
    return a, b


def merge(record, region_ids) -> str:
    """Several regions become one outline. Its name comes from the first that has one."""
    ids = list(dict.fromkeys(region_ids))
    if len(ids) < 2:
        raise Refused("merge takes at least two regions")
    chosen = [region_of(record, i) for i in ids]
    a, b = _edges([ring for region in chosen for ring in _as_rings(region)])
    arrangement = planar.Arrangement(a, b, record["tolerance"]["eps"])
    at = np.array([planar.interior_point(arrangement.face_rings(f)) for f in arrangement.faces])
    keep = np.zeros(len(at), dtype=bool)
    for region in chosen:
        keep |= planar.points_in_rings(_as_rings(region), at)
    rings = arrangement.union_rings(np.flatnonzero(keep).tolist())
    named = next((r for r in chosen if r["proposal"]["class_id"] != names.UNKNOWN), chosen[0])
    fresh_id = _next_id(record)
    proposal = dict(named["proposal"])
    proposal.update(state=names.PROPOSED, verbatim=[t for r in chosen for t in r["proposal"]["verbatim"]],
                    reason=f"merged from {', '.join(ids)}; {named['proposal']['reason']}")
    base = {k: named[k] for k in ("handles", "hatch", "linetypes", "lineweight")}
    base["handles"] = sorted({h for r in chosen for h in r["handles"]})
    base["proposal"] = proposal
    fresh = _rebuilt(record, base, rings, "merged", fresh_id)
    record["regions"] = [r for r in record["regions"] if r["id"] not in ids] + [fresh]
    return fresh["id"]


def split(record, region_id, start, end) -> list[str]:
    """Cut a region along a line through two points. The line is extended across the
    region, so the reviewer only has to show where the cut runs."""
    region = region_of(record, region_id)
    rings = _as_rings(region)
    points = np.concatenate(rings)
    start, end = np.array(start, dtype=float), np.array(end, dtype=float)
    direction = end - start
    length = float(np.hypot(*direction))
    if length <= 0:
        raise Refused("a split needs two different points")
    reach = float(np.max(np.hypot(*(points - start).T))) + length
    unit = direction / length
    a, b = _edges(rings)
    a = np.concatenate([a, [start - unit * reach]])
    b = np.concatenate([b, [end + unit * reach]])
    arrangement = planar.Arrangement(a, b, record["tolerance"]["eps"])
    faces = [f for f in arrangement.faces]
    at = np.array([planar.interior_point(arrangement.face_rings(f)) for f in faces])
    inside = planar.points_in_rings(rings, at)
    pieces = [arrangement.union_rings([k]) for k in np.flatnonzero(inside).tolist()]
    if len(pieces) < 2:
        raise Refused("that line does not cut the region in two")
    new_ids = []
    base = {k: region[k] for k in ("handles", "hatch", "linetypes", "lineweight")}
    made = []
    for rings_piece in pieces:
        proposal = dict(region["proposal"])
        proposal.update(state=names.PROPOSED, reason=f"split from {region_id}; {region['proposal']['reason']}")
        fresh = _rebuilt(record, {**base, "proposal": proposal}, rings_piece, "split", _next_id(record))
        made.append(fresh)
        new_ids.append(fresh["id"])
    record["regions"] = [r for r in record["regions"] if r["id"] != region_id] + made
    return new_ids


def summary(record) -> dict:
    """How far review has got, and what it has cost."""
    live = [r for r in record["regions"] if not r["dropped"]]
    decided = [r for r in live if is_decided(r)]
    seconds = sum(r.get("seconds", 0.0) for r in record["regions"])
    states = {names.CONFIRMED: 0, names.CORRECTED: 0}
    for region in live:
        if region["proposal"]["state"] in states and not region["rejected"]:
            states[region["proposal"]["state"]] += 1
    detail = record["source"].get("detail") or {}
    label = record["source"].get("file") or record["sheet_id"]
    if record["source"].get("details_in_file", 1) > 1:
        label = f"{label} d{detail.get('index', 0):02d}"
    return {"sheet_id": record["sheet_id"], "file": record["source"].get("file"), "label": label, "regions": len(live),
            "decided": len(decided), "rejected": sum(1 for r in live if r["rejected"]), **states,
            "set_aside": sum(1 for r in record["regions"] if r["dropped"]),
            "done": len(decided) == len(live), "seconds": round(seconds, 2),
            "seconds_per_part": round(seconds / len(decided), 2) if decided else None,
            "excluded": record.get("excluded_from_export")}
