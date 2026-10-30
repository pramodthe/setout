"""Carry what a person decided on one detail to near-identical regions in similar details.

Details repeat with small changes, so most of a second copy needs no second look. When a
detail is finished, each other detail is lined up with it by the offset most of their shapes
agree on, mirrored if that fits better. A region with a near-identical partner in the right
place gets the partner's class as a proposal, source `propagated`. A person still confirms
it, and it never overwrites a decision or a name the sheet itself gives.

A region near-identical to one a person rejected is set aside with that reason, and comes
back with one key. Regions left over may take a class from the same hatch and shape, but
only within one file: a hatch means what its own file says it means.

Near-identical is measured, never assumed: same kind of hatch or outline, same size within
`SIZE`, the same place once lined up, and the same shape by overlap. Details that share too
little are not similar and carry nothing. Nothing here reads a name.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from app import synthetic

from . import names, sheets

SIZE = 1.25  # sides may differ by this factor
SIZE_FLOOR = 0.5  # or by this share of text height, for thin parts
PLACE = 3.0  # share of text height: how far a partner may sit from where the offset puts it
VOTE_BIN = 2.0  # share of text height: offsets this close agree
SHAPE = 0.7  # overlap of two shapes laid on each other
SIMILAR = 0.5  # share of a detail's regions with a partner, for two details to be similar
FEWEST = 3  # fewer partners than this proves nothing
ROUNDS = 3  # parts of a detail can move against each other: each round lines up what is left
GRID = 48  # cells per side when comparing two shapes


@dataclass
class Shape:
    id: str
    kind: tuple
    centre: np.ndarray
    size: np.ndarray
    rings: list


@dataclass
class Match:
    pairs: list  # (donor region id, target region id, overlap)
    share: float  # of the target's regions that have a partner
    mirrored: bool


def _kind(region) -> tuple:
    key = names.hatch_key(region["hatch"])
    if key:
        return key
    return (names.SOLID,) if region["hatch"] else ("outline",)


def shapes_of(record, keep) -> list[Shape]:
    out = []
    for region in record["regions"]:
        if keep(region):
            rings = [np.asarray(r, dtype=float) for r in region["rings"]]
            points = np.concatenate(rings)
            low, high = points.min(axis=0), points.max(axis=0)
            out.append(Shape(region["id"], _kind(region), (low + high) / 2.0, high - low, rings))
    return out


def _mirrored(shape, axis_x) -> Shape:
    rings = [r * (-1.0, 1.0) + (2.0 * axis_x, 0.0) for r in shape.rings]
    centre = shape.centre * (-1.0, 1.0) + (2.0 * axis_x, 0.0)
    return Shape(shape.id, shape.kind, centre, shape.size, rings)


def overlap(a: Shape, b: Shape) -> float:
    """Intersection over union of two shapes laid centre on centre. Each axis gets its own
    scale, so a thin part is compared by its shape, not lost between grid cells."""
    span = np.maximum(np.maximum(a.size, b.size), 1e-9)
    scale = GRID / span
    half = GRID / 2.0 + 2.0

    def cells(shape):
        return [(r - shape.centre) * scale + half for r in shape.rings]

    box = (0, 0, GRID + 4, GRID + 4)
    first, second = synthetic.rasterize(cells(a), box), synthetic.rasterize(cells(b), box)
    union = np.count_nonzero(first | second)
    return float(np.count_nonzero(first & second)) / union if union else 0.0


def _offset(donors, targets, unit) -> tuple[np.ndarray, int]:
    """The offset most same-kind, same-size pairs agree on, and how many agree."""
    votes: dict[tuple, list] = {}
    for a in donors:
        for b in targets:
            if a.kind == b.kind and _same_size(a, b, unit):
                step = b.centre - a.centre
                key = tuple(np.round(step / (VOTE_BIN * unit)).astype(int).tolist())
                votes.setdefault(key, []).append(step)
    if not votes:
        return np.zeros(2), 0
    best = max(votes.values(), key=len)
    return np.median(np.array(best), axis=0), len(best)


def _same_size(a, b, unit) -> bool:
    small, large = np.minimum(a.size, b.size), np.maximum(a.size, b.size)
    return bool(np.all((large <= small * SIZE) | (large - small <= SIZE_FLOOR * unit)))


def _pairs(donors, targets, offset, unit) -> list:
    found = []
    for a in donors:
        for b in targets:
            if a.kind != b.kind or not _same_size(a, b, unit):
                continue
            miss = float(np.hypot(*(a.centre + offset - b.centre)))
            if miss <= PLACE * unit:
                score = overlap(a, b)
                if score >= SHAPE:
                    found.append((score - miss / (1000.0 * unit), a.id, b.id, score))
    found.sort(reverse=True)
    used_a, used_b, out = set(), set(), []
    for _, a, b, score in found:
        if a not in used_a and b not in used_b:
            used_a.add(a)
            used_b.add(b)
            out.append((a, b, round(score, 3)))
    return out


def _lined_up(donors, targets, unit) -> list:
    """Pairs from up to ROUNDS offsets. A variant often keeps the wall and moves the slab, so
    one offset fits one group of parts, and another fits the rest."""
    pairs = []
    for _ in range(ROUNDS):
        offset, agree = _offset(donors, targets, unit)
        if agree < FEWEST:
            break
        found = _pairs(donors, targets, offset, unit)
        if len(found) < FEWEST:
            break
        pairs += found
        used_a, used_b = {p[0] for p in found}, {p[1] for p in found}
        donors = [s for s in donors if s.id not in used_a]
        targets = [s for s in targets if s.id not in used_b]
    return pairs


def match_details(donor, target, wanted=lambda r: not r["dropped"]) -> Match | None:
    """Line two details up and pair their regions, or None if they are not similar. A record's
    regions are compared with what a person kept or rejected; `wanted` says which regions of
    the target are in play."""
    unit = donor.get("text_height") or 1.0
    every = shapes_of(donor, lambda r: not r["dropped"] or r["rejected"])
    others = shapes_of(target, wanted)
    if len(every) < FEWEST or len(others) < FEWEST:
        return None
    best = None
    axis = (donor["source"].get("detail", {}).get("box") or [0, 0, 0, 0])
    middle = (axis[0] + axis[2]) / 2.0
    for mirrored in (False, True):
        donors = [_mirrored(s, middle) for s in every] if mirrored else every
        pairs = _lined_up(donors, others, unit)
        if pairs and (best is None or len(pairs) > len(best[0])):
            best = (pairs, mirrored)
    if best is None:
        return None
    pairs, mirrored = best
    share = len(pairs) / len(others)
    if len(pairs) < FEWEST or share < SIMILAR:
        return None
    return Match(pairs, round(share, 3), mirrored)


def _label(record) -> str:
    detail = record["source"].get("detail", {})
    return f"detail {detail.get('index', '?')}"


def _open(region) -> bool:
    return not region["dropped"] and not region["rejected"] and region["proposal"]["state"] == names.PROPOSED


def _shape_key(region, thin_width):
    return names.group_key(SimpleNamespace(width=region["width"], hatch=region["hatch"]), thin_width)


def between(donor, target, by_hatch=True) -> dict:
    """Carry one finished detail's decisions onto another, in place. Returns the counts.
    `by_hatch` also lets regions left over take a class from the same hatch and shape."""
    counts = {"propagated": 0, "set_aside": 0, "hatch": 0}
    by_id = {r["id"]: r for r in donor["regions"]}
    match = match_details(donor, target, _open)
    if match:
        place = {b: (a, score) for a, b, score in match.pairs}
        for region in target["regions"]:
            if region["id"] not in place or not _open(region):
                continue
            a, score = place[region["id"]]
            source = by_id[a]
            if source["rejected"]:
                region["dropped"] = f"near-identical to a region rejected in {_label(donor)}"
                counts["set_aside"] += 1
            elif source["proposal"]["class_id"] != names.UNKNOWN and region["proposal"]["class_id"] == names.UNKNOWN:
                region["proposal"].update(class_id=source["proposal"]["class_id"], source=names.PROPAGATED,
                                          candidates=[source["proposal"]["class_id"]],
                                          reason=f"near-identical to {a} in {_label(donor)} (overlap {score:.2f}), "
                                                 "named by a person",
                                          carried_from={"sheet": donor["sheet_id"], "region": a, "overlap": score})
                counts["propagated"] += 1
    if not by_hatch or donor["source"].get("sha256") != target["source"].get("sha256"):
        return counts
    thin = synthetic.Config().thin_px / donor["render"]["px_per_unit"]
    groups: dict[tuple, set] = {}
    for region in donor["regions"]:
        key = _shape_key(region, thin)
        if key and not region["rejected"] and region["proposal"]["class_id"] != names.UNKNOWN:
            groups.setdefault(key, set()).add(region["proposal"]["class_id"])
    for region in target["regions"]:
        if not _open(region) or region["proposal"]["class_id"] != names.UNKNOWN:
            continue
        classes = groups.get(_shape_key(region, thin))
        if classes and len(classes) == 1:
            class_id = next(iter(classes))
            region["proposal"].update(class_id=class_id, source=names.HATCH, candidates=[class_id],
                                      reason=f"same hatch and shape as a region named by a person in {_label(donor)}")
            counts["hatch"] += 1
    return counts


def carry(store, sheet_id) -> dict:
    """A detail is finished: carry its decisions to every detail still open in the store."""
    donor = sheets.read_sheet(store, sheet_id)
    total = {"propagated": 0, "set_aside": 0, "hatch": 0, "details": 0}
    for other in sheets.sheet_ids(store):
        if other == sheet_id:
            continue
        target = sheets.read_sheet(store, other)
        if not any(_open(r) for r in target["regions"]):
            continue
        counts = between(donor, target)
        if any(counts.values()):
            sheets.write_sheet(store, target)
            sheets.log_event(store, other, {"action": "carried", "from": sheet_id, **counts})
            total["details"] += 1
            for key, value in counts.items():
                total[key] += value
    return total


def simulate(records, answer, by_hatch=True) -> dict:
    """What propagation saves, with no one reviewing: details are taken in order, and
    `answer(record, region)` is what a person would call each region (a class, or None for
    not a part). A region that arrives with no class costs a pick; one that arrives with a
    class costs nothing to name, and a wrong one is counted and costs a pick. Nothing is saved."""
    live = copy.deepcopy(records)
    out = {"details": len(live), "regions": 0, "picks_without": 0, "picks_with": 0, "rejects_without": 0, "rejects_with": 0,
           "propagated": 0, "propagated_right": 0, "propagated_wrong": 0, "hatch": 0, "hatch_right": 0, "hatch_wrong": 0,
           "set_aside": 0, "set_aside_wrong": 0, "details_no_pick": 0}
    for k, record in enumerate(live):
        picks = 0
        for region in record["regions"]:
            if region["dropped"] and not region["dropped"].startswith("near-identical"):
                continue
            truth = answer(record, region)
            out["regions"] += 1
            out["rejects_without" if truth is None else "picks_without"] += 1
            if region["dropped"]:
                out["set_aside"] += 1
                out["set_aside_wrong"] += int(truth is not None)
                continue
            proposal = region["proposal"]
            carried = proposal["source"] in (names.PROPAGATED, names.HATCH)
            if carried:
                word = "propagated" if proposal["source"] == names.PROPAGATED else "hatch"
                out[word] += 1
                out[f"{word}_right" if proposal["class_id"] == truth else f"{word}_wrong"] += 1
            if truth is None:
                out["rejects_with"] += 1
                region["rejected"] = True
            elif proposal["class_id"] != truth:
                out["picks_with"] += 1
                picks += 1
                proposal.update(class_id=truth, state=names.CORRECTED)
            else:
                proposal["state"] = names.CONFIRMED
        out["details_no_pick"] += int(picks == 0)
        for later in live[k + 1:]:
            between(record, later, by_hatch)
    return out

