"""Split a sheet into details, and read its legend.

One file can hold dozens of separate details. A detail, not the file, is the unit a person
reviews and a dataset is built from, so this comes first.

Details are found by whitespace. Boxes of linework that touch or nearly touch are one
detail, so a frame gathers whatever it holds, and an element poking through a frame stays
with it because its box still touches the frame. The gap is a share of text height, the one
thing drawn at a known paper size. A rectangle holding most of the sheet is its border and
does not count, or it would pull everything together.

A frame holding several groups is then split: each group is a detail of its own, and the
frame belongs to none of them. Only groups whose boxes clear each other are seen, so a long
L-shaped part whose box bridges two groups keeps them together.

A legend is a column of small swatches, each with text beside it. Its entries give hatches a
meaning for this file, and it is not a detail. Nothing here reads a layer name.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app import dxf_ingest

from .drawing import Drawing, _gap_to_box

GAP = 1.0  # share of text height: whitespace this wide separates two details
ATTACH = 12.0  # share of text height: how far a small fragment reaches for a detail
FEW = 5  # a cluster of fewer entities than this is a fragment, not a detail
FRAME_HOLDS = 0.6  # a rectangle holding this share of its cluster is its frame
SQUARE = 0.01  # a rectangle's sides may run this far off the sheet's axes: drafted frames do
BORDER = 0.75  # a bare rectangle holding this share of everything is the sheet's border
SWATCH_ITEMS = 3  # a swatch is a rectangle and a hatch, maybe two
LEGEND_ROWS = 3  # fewer rows than this is not a legend
TEXT_REACH = 1.0  # a swatch's text starts within this many swatch widths of it


@dataclass
class Detail:
    index: int  # from 1, top to bottom then left to right
    box: tuple  # x0, y0, x1, y1 of its linework
    curves: list = field(default_factory=list)  # indices into the drawing
    hatches: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    callouts: list = field(default_factory=list)
    framed: bool = False  # a rectangle runs round it
    groups_inside: int = 1  # details its frame was split into, this one among them
    frame: str | None = None  # handle of its frame, when that is a closed rectangle


@dataclass
class LegendEntry:
    pattern: str | None  # verbatim, None for a swatch with no hatch
    scale: float | None
    angle: float | None
    text: str  # verbatim
    box: tuple = ()


@dataclass
class Sheet:
    details: list
    legend: list
    fragments: int  # small clusters left with no detail
    border: list  # handles of rectangles taken for the sheet's border


def _box(points) -> tuple:
    return tuple(float(v) for v in (*points.min(axis=0), *points.max(axis=0)))


def _item_boxes(drawing: Drawing) -> np.ndarray:
    boxes = [_box(c.points) for c in drawing.curves] + [_box(np.concatenate(h.rings)) for h in drawing.hatches]
    return np.array(boxes, dtype=float).reshape(-1, 4)


def _touching(boxes, gap) -> tuple[np.ndarray, np.ndarray]:
    """Index pairs of boxes within `gap` of each other, by a sweep along x."""
    n = len(boxes)
    if n < 2:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    order = np.argsort(boxes[:, 0])
    x0 = boxes[order, 0]
    y0, y1 = boxes[order, 1], boxes[order, 3]
    reach = np.searchsorted(x0, boxes[order, 2] + gap, side="right")
    left, right = [], []
    for k in range(n - 1):
        if reach[k] <= k + 1:
            continue
        others = np.arange(k + 1, reach[k])
        near = (y0[others] <= y1[k] + gap) & (y1[others] >= y0[k] - gap)
        left.append(np.full(int(near.sum()), k))
        right.append(others[near])
    if not left:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    return order[np.concatenate(left)], order[np.concatenate(right)]


def _labels(n, i, j) -> np.ndarray:
    """Connected components of n things joined by pairs."""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(i.tolist(), j.tolist()):
        parent[find(a)] = find(b)
    return np.array([find(k) for k in range(n)], dtype=np.int64)


def _is_rectangle(curve) -> bool:
    """Closed, four corners, square to the sheet, give or take a slight skew."""
    if not curve.closed or len(curve.points) != 4:
        return False
    steps = np.roll(curve.points, -1, axis=0) - curve.points
    return bool(np.all(np.min(np.abs(steps), axis=1) <= SQUARE * np.max(np.abs(steps), axis=1)))


def _borders(drawing, boxes) -> set:
    """Curve indices of rectangles holding most of the sheet. With one detail on the sheet
    that rectangle is its frame, and the result is the same with or without it."""
    out = set()
    total = len(boxes)
    for k, curve in enumerate(drawing.curves):
        if _is_rectangle(curve):
            x0, y0, x1, y1 = boxes[k]
            held = np.count_nonzero((boxes[:, 0] >= x0) & (boxes[:, 2] <= x1) & (boxes[:, 1] >= y0) & (boxes[:, 3] <= y1))
            if held - 1 >= BORDER * total and held > FEW:
                out.add(k)
    return out


def _text_beside(box, drawing, taken) -> int | None:
    """The text just right of a swatch and level with it."""
    x0, y0, x1, y1 = box
    best, best_gap = None, None
    for k, text in enumerate(drawing.texts):
        if k in taken or text.box is None or not text.text.strip():
            continue
        tx0, ty0, _, ty1 = text.box
        gap = tx0 - x1
        if -0.1 * (x1 - x0) <= gap <= TEXT_REACH * (x1 - x0) and ty0 <= y1 and ty1 >= y0:
            if best is None or gap < best_gap:
                best, best_gap = k, gap
    return best


def _read_legend(drawing, boxes, labels) -> tuple[list, set, set]:
    """(entries, cluster labels used, text indices used). Small clusters with text beside
    them, enough of them in one column. A cluster's hatch gives the row its pattern."""
    curve_count = len(drawing.curves)
    rows = []
    taken: set = set()
    for label in np.unique(labels):
        members = np.flatnonzero(labels == label)
        if len(members) > SWATCH_ITEMS:
            continue
        box = (float(boxes[members, 0].min()), float(boxes[members, 1].min()),
               float(boxes[members, 2].max()), float(boxes[members, 3].max()))
        text = _text_beside(box, drawing, taken)
        if text is None:
            continue
        taken.add(text)
        rows.append((box, int(label), text, [int(m) - curve_count for m in members if m >= curve_count]))
    if not rows:
        return [], set(), set()
    widths = np.array([r[0][2] - r[0][0] for r in rows])
    lefts = np.array([r[0][0] for r in rows])
    best = None
    for k in range(len(rows)):  # the biggest set of rows sharing a left edge
        together = np.flatnonzero(np.abs(lefts - lefts[k]) <= 0.25 * widths[k])
        if best is None or len(together) > len(best):
            best = together
    if len(best) < LEGEND_ROWS:
        return [], set(), set()
    entries, used_labels, used_texts = [], set(), set()
    for k in sorted(best.tolist(), key=lambda row: -rows[row][0][3]):  # top to bottom, as it reads
        box, label, text, hatches = rows[k]
        used_labels.add(label)
        used_texts.add(text)
        words = drawing.texts[text].text
        if hatches:
            for h in hatches:
                hatch = drawing.hatches[h]
                entries.append(LegendEntry(hatch.pattern, hatch.scale, hatch.angle, words, box))
        else:
            entries.append(LegendEntry(None, None, None, words, box))
    return entries, used_labels, used_texts


def _frame_of(drawing, members, boxes) -> int | None:
    """The rectangle holding most of a cluster, if any. Judged by what it holds, not by its
    size: something tall can poke through a frame and make the cluster far bigger than it."""
    best, most = None, 0
    for k in members.tolist():
        if k < len(drawing.curves) and _is_rectangle(drawing.curves[k]):
            x0, y0, x1, y1 = boxes[k]
            inside = np.count_nonzero((boxes[members, 0] >= x0) & (boxes[members, 2] <= x1)
                                      & (boxes[members, 1] >= y0) & (boxes[members, 3] <= y1)) - 1
            if inside > most and inside >= FEW and inside >= FRAME_HOLDS * (len(members) - 1):
                best, most = k, inside
    return best


def _attach(big, small, boxes, reach) -> int:
    """Small clusters join the nearest big one within reach. Returns how many did not."""
    fragments = 0
    box_of = {k: (boxes[v, 0].min(), boxes[v, 1].min(), boxes[v, 2].max(), boxes[v, 3].max()) for k, v in big.items()}
    for members in small.values():
        low, high = boxes[members, :2].min(axis=0), boxes[members, 2:].max(axis=0)
        here = (*low, *high)
        apart = {k: max(b[0] - here[2], here[0] - b[2], b[1] - here[3], here[1] - b[3], 0.0) for k, b in box_of.items()}
        nearest = min(apart, key=apart.get)
        if apart[nearest] <= reach:
            big[nearest] = np.concatenate([big[nearest], members])
        else:
            fragments += 1
    return fragments


def _groups_in_frame(members, boxes, gap, reach, frame) -> tuple[list, int]:
    """([indices of each group], fragments), or ([], 0) when the frame holds one group."""
    inner = members[members != frame]
    i, j = _touching(boxes[inner], gap)
    labels = _labels(len(inner), i, j)
    found = {int(label): inner[labels == label] for label in np.unique(labels)}
    big = {k: v for k, v in found.items() if len(v) >= FEW}
    if len(big) < 2:
        return [], 0
    small = {k: v for k, v in found.items() if len(v) < FEW}
    fragments = _attach(big, small, boxes, reach)
    return list(big.values()), fragments


def _detail(members, curve_count, boxes, **fields) -> Detail:
    box = (float(boxes[members, 0].min()), float(boxes[members, 1].min()),
           float(boxes[members, 2].max()), float(boxes[members, 3].max()))
    return Detail(0, box, [int(m) for m in members if m < curve_count],
                  [int(m) - curve_count for m in members if m >= curve_count], **fields)


def split(drawing: Drawing) -> Sheet:
    """The sheet's details, in reading order, and its legend."""
    boxes = _item_boxes(drawing)
    if not len(boxes):
        return Sheet([], [], 0, [])
    height = drawing.text_height or max(np.ptp(boxes[:, [0, 2]]), np.ptp(boxes[:, [1, 3]])) / 150.0
    gap, reach = GAP * height, ATTACH * height
    border = _borders(drawing, boxes)
    keep = np.array([k not in border for k in range(len(boxes))])
    i, j = _touching(boxes[keep], gap)
    ids = np.flatnonzero(keep)
    labels = np.full(len(boxes), -1, dtype=np.int64)
    labels[ids] = ids[_labels(len(ids), i, j)]
    entries, legend_labels, legend_texts = _read_legend(drawing, boxes, labels)
    used = np.isin(labels, list(legend_labels)) | (labels < 0)
    found = {}
    for label in np.unique(labels[~used]):
        found[int(label)] = np.flatnonzero(labels == label)
    big = {k: v for k, v in found.items() if len(v) >= FEW}
    small = {k: v for k, v in found.items() if len(v) < FEW}
    if not big:  # a tiny sheet: what there is, is the detail
        big, small = found, {}
    fragments = _attach(big, small, boxes, reach)
    details = []
    curve_count = len(drawing.curves)
    for members in big.values():
        frame = _frame_of(drawing, members, boxes)
        groups, lost = ([], 0) if frame is None else _groups_in_frame(members, boxes, gap, reach, frame)
        fragments += lost
        for group in groups:
            details.append(_detail(group, curve_count, boxes, framed=True, groups_inside=len(groups),
                                   frame=drawing.curves[frame].handle))
        if not groups:
            details.append(_detail(members, curve_count, boxes, framed=frame is not None,
                                   frame=None if frame is None else drawing.curves[frame].handle))
    band = max(reach, 1e-9)
    details.sort(key=lambda d: (-round(d.box[3] / (band * 8.0)), d.box[0]))
    for k, detail in enumerate(details, 1):
        detail.index = k
    _assign_text(drawing, details, reach, legend_texts)
    return Sheet(details, entries, fragments, sorted({drawing.curves[k].handle for k in border}))


def _assign_text(drawing, details, reach, skip) -> None:
    """Each text and callout to the detail it sits on or nearest it, within reach."""
    for k, text in enumerate(drawing.texts):
        if k in skip or text.box is None:
            continue
        middle = ((text.box[0] + text.box[2]) / 2.0, (text.box[1] + text.box[3]) / 2.0)
        gaps = [(_gap_to_box(middle, d.box), d.index) for d in details]
        if gaps and min(gaps)[0] <= reach:
            details[min(gaps)[1] - 1].texts.append(k)
    for k, callout in enumerate(drawing.callouts):
        gaps = [(_gap_to_box(callout.tip, d.box), d.index) for d in details]
        if gaps and min(gaps)[0] <= reach:
            details[min(gaps)[1] - 1].callouts.append(k)


def handles_of(drawing: Drawing, detail: Detail) -> set:
    """Top-level entities that belong to a detail: what its render draws."""
    out = {drawing.curves[k].handle for k in detail.curves} | {drawing.hatches[k].handle for k in detail.hatches}
    out |= {drawing.texts[k].handle for k in detail.texts}
    for k in detail.callouts:
        callout = drawing.callouts[k]
        out |= {h for h in (callout.leader, callout.note) if h}
    return out


def annotation_near(doc, sheet, reach) -> dict:
    """{detail index: handles of dimensions and such}. They are in no list of linework, but
    a detail's render should still show the ones drawn round it."""
    out = {}
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind not in dxf_ingest.DIMENSION_TYPES and kind != "TOLERANCE":
            continue
        box = dxf_ingest._extents(entity)
        if box is None:
            continue
        middle = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        gaps = [(_gap_to_box(middle, d.box), d.index) for d in sheet.details]
        if gaps and min(gaps)[0] <= reach:
            out.setdefault(min(gaps)[1], set()).add(entity.dxf.handle)
    return out

