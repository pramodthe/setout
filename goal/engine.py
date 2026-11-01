"""Build a planned detail. The plan says what each part is and how parts relate. This repairs positions and makes the 3D: a drawn solid or sheet is its own outline run along
the length, units and fixings are real catalogue components, and every relation is checked before and after repair. Nothing here decides what a part is. Sizes come from the
catalogue and the drawing's own proportions, and anything else, like a spacing, is reported as an assumption.

    python -m goal.engine <drawing>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import plan as planner
from .compose import _block
from .hatching import _components
from .library import STORE
from .part_search import OUT, search
from .plan import TRUSTED
from .regions import INK, Region, _runs

RUN = 1.0  # metres along the length, a choice of how much to show
GROW = 3  # pixels: outlines close over the line between them, or parts stand apart by a line's width
TOUCH = 0.0042  # of the sheet: within this, two parts in the section touch
SHEET_MM = 3.0  # a sheet drawn as a single line has no thickness to read, so it gets a visible one, reported
BRIDGE = 0.5  # a part bridging a gap goes this share of each side into it
DASH = (20, 500, 45, 0.4, 42)  # a dash: fewest and most pixels, longest side, fill of its box, and the furthest gap to the next one
CREEP = 0.15  # a row or column this far short of the fullest one is an outline creeping along a line
STRETCHER = (1.6, 3.2)  # how much longer than wide a unit laid along the run is: shorter is a half or a header, longer is a lintel
SAME_MEANING = 0.03  # search scores this close mean the same to the search, so fit decides between them
SHEET_METRES = 0.5  # what a detail sheet shows when nothing on it gives a size
COMMON = 0.03  # how much being widely used counts against a small misfit in shape


def as_region(ident, mask) -> Region:
    rows, ys = {}, np.flatnonzero(mask.any(1))
    for y in ys:
        rows[int(y)] = _runs(mask[y])
    xs = np.flatnonzero(mask.any(0))
    return Region(ident, rows, (int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1), int(mask.sum()))


def closed(regions, plan, size) -> dict:
    """Each built region as a mask, grown over the drawn lines between regions. A fixing's own outline goes to what surrounds it, or a run of fixings would leave a slot along the length."""
    owner = {r: p for p in plan["parts"] for r in p["regions"]}
    kept = [r for r in regions if owner.get(r.id, {}).get("form", "none") in ("solid", "units", "sheet", "spaced")]
    label = np.zeros((size[1], size[0]), int)
    for region in sorted(kept, key=lambda r: -r.area):
        for y, runs in region.rows.items():
            for a, b in runs:
                label[y, a:b] = region.id
    for region in kept:
        if owner[region.id]["form"] == "spaced":
            label[label == region.id] = -1
    holes = label == -1
    for _ in range(max(size)):
        if not holes.any():
            break
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near = np.roll(label, (dy, dx), (0, 1))
            take = holes & (near > 0)
            label[take] = near[take]
            holes[take] = False
    for _ in range(GROW):
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near = np.roll(label, (dy, dx), (0, 1))
            take = (label <= 0) & (near > 0)
            label[take] = near[take]
    return {r.id: label == r.id for r in kept if (label == r.id).any()}


def snap(points, ink, reach=30, step=3) -> list:
    """Move a line's corners onto the ink. A model's coordinates land a percent or two off, and the drawing knows exactly where its line is."""
    def score(line):
        hits = 0
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
            hits += ink[np.linspace(y0, y1, n).astype(int).clip(0, ink.shape[0] - 1), np.linspace(x0, x1, n).astype(int).clip(0, ink.shape[1] - 1)].sum()
        return hits
    line = [list(p) for p in points]
    for _ in range(3):
        for k, (x, y) in enumerate(line):
            best, kept = -1, (x, y)
            for dx in range(-reach, reach + 1, step):
                for dy in range(-reach, reach + 1, step):
                    line[k] = [x + dx, y + dy]
                    found = score(line)
                    if found > best:
                        best, kept = found, (x + dx, y + dy)
            line[k] = list(kept)
    return line


def traced(points, ink) -> list | None:
    """A dashed line's real course: dash to nearest dash from the planned start. None if the dashes run out well before the planned line does."""
    dashes = []
    for ys, xs in _components(ink):
        wide, high = np.ptp(xs) + 1, np.ptp(ys) + 1
        if DASH[0] <= len(ys) <= DASH[1] and max(wide, high) <= DASH[2] and len(ys) >= DASH[3] * wide * high:
            dashes.append((float(xs.mean()), float(ys.mean())))
    if not dashes:
        return None
    left = np.array(dashes)
    at = left[np.argmin(np.hypot(*(left - points[0]).T))]
    line, longest = [tuple(at)], float(sum(np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])))
    while True:
        left = left[np.hypot(*(left - at).T) > 1]
        if not len(left):
            break
        gaps = np.hypot(*(left - at).T)
        if gaps.min() > DASH[4]:
            break
        at = left[np.argmin(gaps)]
        line.append(tuple(at))
    walked = float(sum(np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:])))
    return line if walked >= 0.6 * longest else None


def mask_of(points, size, width=None) -> np.ndarray:
    picture = Image.new("1", size)
    pen = ImageDraw.Draw(picture)
    if width:
        pen.line([tuple(p) for p in points], fill=1, width=width, joint="curve")
    else:
        pen.polygon([tuple(p) for p in points], fill=1)
    return np.asarray(picture).copy()


def solid_box(mask) -> list:
    """A mask's box, by the rows and columns it really fills: a grown outline creeps thinly along the lines beside it."""
    rows, columns = mask.sum(1), mask.sum(0)
    ys, xs = np.flatnonzero(rows >= CREEP * rows.max()), np.flatnonzero(columns >= CREEP * columns.max())
    return [int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1]


def merged_boxes(masks) -> list:
    """Courses of units: regions whose boxes mostly overlap are one unit split by a symbol drawn on it."""
    boxes = []
    for mask in masks:
        box = solid_box(mask)
        for other in boxes:
            wide = min(box[2], other[2]) - max(box[0], other[0])
            high = min(box[3], other[3]) - max(box[1], other[1])
            if wide > 0 and high > 0 and wide * high >= 0.4 * min((box[2] - box[0]) * (box[3] - box[1]), (other[2] - other[0]) * (other[3] - other[1])):
                other[:] = [min(box[0], other[0]), min(box[1], other[1]), max(box[2], other[2]), max(box[3], other[3])]
                break
        else:
            boxes.append(box)
    return boxes


def stretch(hit, wide, high) -> float:
    """How far a unit is from a section of this shape, once the scale is free: its proportions count, and a unit used hundreds of times is a truer unit than a one-off."""
    return abs(np.log((hit["size"][1] / hit["size"][2]) / (wide / high))) - COMMON * np.log(hit["copies"])


class Scene:
    def __init__(self, drawing):
        self.drawing = drawing
        self.plan, self.regions, self.size = planner.make(drawing)
        self.ink = np.asarray(Image.open(drawing).convert("L")) < INK
        self.parts = {p["id"]: p for p in self.plan["parts"]}
        self.masks = closed(self.regions, self.plan, self.size)
        self.items, self.notes, self.joints, self.before = [], [], {}, {}
        self.section = {}  # part id -> mask of where it stands in the section
        self.touch = max(int(TOUCH * max(self.size)), 2)
        self.bed = np.median([np.mean([r[3] - r[1] for r in merged_boxes([self.masks[i]])]) for p in self.parts.values() if p["category"] == "mortar" for i in p["regions"] if i in self.masks] or [0])
        self.hits = dict(zip([p["id"] for p in self.plan["parts"] if p["form"] in ("units", "spaced")],
                             search([(p["query"], p["category"]) for p in self.plan["parts"] if p["form"] in ("units", "spaced")], keep=40)))
        self.scale = self.find_scale()
        self.left, self.bottom = min(int(np.flatnonzero(m.any(0))[0]) for m in self.masks.values()), max(int(np.flatnonzero(m.any(1))[-1]) + 1 for m in self.masks.values())
        self.joint = self.bed * self.scale or 0.01

    def stretchers(self, part) -> list:
        return [h for h in self.hits[part["id"]] if STRETCHER[0] * h["size"][1] <= h["size"][0] <= STRETCHER[1] * h["size"][1]] or self.hits[part["id"]]

    def find_scale(self) -> float:
        """Metres per pixel, read from the real size of the catalogue unit that fits the drawn units best. Only so that catalogue parts fit: a drawing's sizes are never measured."""
        for part in self.parts.values():
            if part["form"] == "units" and self.hits[part["id"]]:
                boxes = merged_boxes([self.masks[r] for r in part["regions"] if r in self.masks])
                wide, high = np.median([b[2] - b[0] for b in boxes]), np.median([b[3] - b[1] for b in boxes])
                hit = min(self.stretchers(part), key=lambda h: stretch(h, wide, high))
                self.notes.append(f"scale from {hit['name']} ({hit['id']}) against {part['name']}: {(hit['size'][1] / wide + hit['size'][2] / high) * 500:.3f} mm per pixel")
                return (hit["size"][1] / wide + hit["size"][2] / high) / 2
        self.notes.append(f"no units to set a scale: {SHEET_METRES} m across the sheet, assumed")
        return SHEET_METRES / max(self.size)

    def across(self, x):
        return (x - self.left) * self.scale

    def up(self, y):
        return (self.bottom - y) * self.scale

    def add(self, part, triangles, source, count=1):
        self.items.append({"part": part["id"] if isinstance(part, dict) else part, "triangles": np.asarray(triangles, np.float32), "source": source, "count": count})

    def outline(self, part, mask):
        for other in self.joints:  # units keep their exact size, and what's drawn beside them fits round them
            if other != part["id"]:
                mask = mask & ~self.section[other]
        if not mask.any():
            self.notes.append(f"Not built: {part['name']}, nothing left of its outline")
            return
        rows = as_region(0, mask).bands()
        triangles = [_block(0, RUN, self.across(a), self.across(b), self.up(y1), self.up(y0)) for a, b, y0, y1 in rows]
        self.add(part, np.vstack(triangles), "drawing outline")
        self.section[part["id"]] = self.section.get(part["id"], np.zeros_like(mask)) | mask

    def units(self, part):
        boxes = merged_boxes([self.masks[r] for r in part["regions"] if r in self.masks])
        wide, high = np.median([b[2] - b[0] for b in boxes]), np.median([b[3] - b[1] for b in boxes])
        hit = min(self.stretchers(part), key=lambda h: stretch(h, wide, high) + abs(np.log(h["size"][1] / (wide * self.scale))))
        mesh = np.load(STORE / "parts" / f"{hit['id']}.npy").astype(float)
        mesh -= mesh.reshape(-1, 3).min(0)
        length = hit["size"][0]
        self.notes.append(f"{part['name']}: catalogue {hit['id']} {hit['name']}, {length * 1000:.0f} long")
        columns = {}
        for box in boxes:
            columns.setdefault(round(box[0] / max(wide, 1)), []).append(box)
        for column in columns.values():
            for k, box in enumerate(sorted(column, key=lambda b: b[1])):
                u0, u1, z1, z0 = self.across(box[0]), self.across(box[2]), self.up(box[1]), self.up(box[3])
                along = -(length + self.joint) / 2 if k % 2 else 0.0
                seams = []
                while along < RUN:
                    a, b = max(along, 0.0), min(along + length, RUN)
                    if b - a > 0.02:
                        t = mesh * [(b - a) / length, (u1 - u0) / hit["size"][1], (z1 - z0) / hit["size"][2]] + [a, u0, z0]
                        self.add(part, t, f"catalogue {hit['id']}")
                    if b + self.joint < RUN:
                        seams.append(b)
                    along += length + self.joint
                self.joints.setdefault(part["id"], []).append({"box": box, "seams": seams})
                self.section[part["id"]] = self.section.get(part["id"], np.zeros_like(self.ink)) | mask_of([(box[0], box[1]), (box[2] - 1, box[1]), (box[2] - 1, box[3] - 1), (box[0], box[3] - 1)], self.size)

    def seams(self):
        """Head joints, last, because a vent set in a joint takes that joint's place."""
        for part, courses in self.joints.items():
            mortar = []
            for course in courses:
                u0, u1, z1, z0 = self.across(course["box"][0]), self.across(course["box"][2]), self.up(course["box"][1]), self.up(course["box"][3])
                mortar += [_block(s, s + self.joint, u0, u1, z0, z1) for s in course["seams"]]
            if mortar:
                self.add("head_joints", np.vstack(mortar), "engine: joint between units")

    def sheet(self, part):
        if part["regions"]:
            for region in part["regions"]:
                if region in self.masks:
                    self.outline(part, self.masks[region])
            return
        rough = [(x * self.size[0], y * self.size[1]) for x, y in part["path"]]
        if len(rough) < 2:
            self.notes.append(f"Not built: {part['name']}, the plan gave it no line")
            return
        line = traced(rough, self.ink) or snap(rough, self.ink)
        width = max(int(round(SHEET_MM / 1000 / self.scale)), 3)
        self.notes.append(f"{part['name']}: {SHEET_MM} mm thick is a visible stand-in, the drawing shows a line")
        self.before[part["id"]] = mask_of(rough, self.size, width)
        sheet = mask_of(line, self.size, width)
        for other, mask in self.section.items():
            if self.parts.get(other, {}).get("category") != "mortar":
                sheet &= ~mask
        self.outline(part, sheet)

    def course_at(self, x, y):
        for courses in self.joints.values():
            for course in courses:
                if course["box"][0] - self.touch <= x <= course["box"][2] + self.touch and course["box"][1] - self.touch <= y <= course["box"][3] + self.touch:
                    return course
        return None

    def joints_of(self, part, ends) -> list:
        """Every place a part set in a repeated solid can go: each row of that solid at both of the part's ends, with the part centred on the gap it bridges."""
        relation = next((r for r in self.plan["relations"] if r["subject"] == part["id"] and r["relation"] == "set_in" and r["target"] in self.section), None)
        if relation is None:
            return []
        if relation["target"] in self.joints:
            return []
        mask = self.section[relation["target"]]
        (x0, _), (x1, _) = ends
        low, high = min(x0, x1) - self.touch, max(x0, x1) + self.touch

        def gap_in(y):
            spans = _runs(mask[y])
            gaps = [(p, q) for p, q in zip(spans, spans[1:]) if q[0] - p[1] > self.touch and low <= (p[1] + q[0]) / 2 <= high]
            return max(gaps, key=lambda g: g[1][0] - g[0][1], default=None)
        bridged = np.array([gap_in(y) is not None for y in range(mask.shape[0])])
        out = []
        for a, b in _runs(bridged):  # one bridge for each joint, however many rows it has
            y = (a + b) // 2
            first, second = gap_in(y)
            reach = BRIDGE * min(first[1] - first[0], second[1] - second[0])
            out.append(((first[1] - reach, y), (second[0] + reach, y)))
        return out

    def driven(self, part, ends) -> tuple:
        """A fixing the plan placed roughly, moved the shortest way until it enters what it fixes into. The drawing never moves, only the fixing."""
        relation = next((r for r in self.plan["relations"] if r["subject"] == part["id"] and r["relation"] == "fixed_into" and r["target"] in self.section), None)
        if relation is None or part["regions"]:
            return ends
        ys, xs = np.nonzero(self.section[relation["target"]])
        (x0, y0), (x1, y1) = ends
        tips = [(x0, y0), (x1, y1)]
        gaps = [np.hypot(xs - x, ys - y) for x, y in tips]
        tip = int(np.argmin([g.min() for g in gaps]))
        k = int(np.argmin(gaps[tip]))
        dx, dy = xs[k] - tips[tip][0], ys[k] - tips[tip][1]
        return (x0 + dx, y0 + dy), (x1 + dx, y1 + dy)

    def spaced(self, part):
        """Repeats of one catalogue object through the section. The plan gives a rough place, the drawing and the relations give the exact one."""
        spacing = (part["spacing_mm"] or 450) / 1000
        rough = self.anchors(part)
        if not rough or not self.hits[part["id"]]:
            self.notes.append(f"Not built: {part['name']}, no place in the plan or nothing like it in the catalogue")
            return
        self.notes.append(f"{part['name']}: {spacing * 1000:.0f} mm centres, assumed")
        self.before[part["id"]] = np.any([mask_of(list(ends), self.size, 5) for ends in rough], 0)
        rough = [self.driven(part, ends) for ends in rough]
        places = [j for ends in rough for j in self.joints_of(part, ends)]
        if places:  # a part laid in a joint can't share it with a sheet
            taken = np.any([m for other, m in self.section.items() if self.parts.get(other, {}).get("form") == "sheet"] or [np.zeros_like(self.ink)], 0)
            places = [ends for ends in places if not (mask_of(list(ends), self.size, 5) & taken).any()] or places
        places = places or rough
        wanted = float(np.median([np.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]) for ends in places])) * self.scale
        course = self.course_at((rough[0][0][0] + rough[0][1][0]) / 2, (rough[0][0][1] + rough[0][1][1]) / 2) if len(part["path"]) == 1 else None
        best = max(h["score"] for h in self.hits[part["id"]])
        hit = min((h for h in self.hits[part["id"]] if h["score"] >= best - SAME_MEANING), key=lambda h: abs(np.log(h["size"][0] / max(wanted, 0.01))) + (2 if course and h["size"][2] > 2.5 * self.joint else 0) + (h["category"] != part["category"]) - COMMON * np.log(h["copies"]))
        mesh = np.load(STORE / "parts" / f"{hit['id']}.npy").astype(float)
        mesh -= mesh.reshape(-1, 3).min(0)
        mesh -= [0, hit["size"][1] / 2, hit["size"][2] / 2]
        count = 0
        for k, ((x0, y0), (x1, y1)) in enumerate(places):
            heading = np.array([self.across(x1) - self.across(x0), self.up(y1) - self.up(y0)])
            length = float(np.linalg.norm(heading))
            heading /= max(length, 1e-9)
            normal = np.array([-heading[1], heading[0]])
            centre = np.array([self.across(x0), self.up(y0)]) + heading * (length - hit["size"][0]) / 2
            placed = np.empty_like(mesh)
            placed[..., 1] = centre[0] + mesh[..., 0] * heading[0] + mesh[..., 2] * normal[0]
            placed[..., 2] = centre[1] + mesh[..., 0] * heading[1] + mesh[..., 2] * normal[1]
            stations = list(np.arange(spacing / 2 * (1 + k % 2), RUN, spacing))
            if course and course["seams"]:
                stations = sorted({min(course["seams"], key=lambda s: abs(s - t)) for t in stations})
                course["seams"] = [s for s in course["seams"] if s not in stations]
                stations = [s + self.joint / 2 for s in stations]
            for station in stations:
                t = placed.copy()
                t[..., 0] = mesh[..., 1] + station
                self.add(part, t, f"catalogue {hit['id']}" + ("" if part["seen"] == "drawn" else ", implied"))
                count += 1
        self.notes.append(f"{part['name']}: catalogue {hit['id']} {hit['name']}, {hit['size'][0] * 1000:.0f} long against {wanted * 1000:.0f} wanted, {count} placed at {len(places)} places")
        self.section[part["id"]] = np.any([mask_of(list(ends), self.size, 5) for ends in places], 0)

    def anchors(self, part) -> list:
        """Rough ends of a spaced part in the section: each drawn region's long axis, the planned ends, or, from one planned point, the width of the course it stands in."""
        out = []
        for region in part["regions"]:
            box = solid_box(self.region_mask(region))
            middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            out.append(((box[0], middle[1]), (box[2], middle[1])) if box[2] - box[0] >= box[3] - box[1] else ((middle[0], box[1]), (middle[0], box[3])))
        if out:
            return out
        points = [(x * self.size[0], y * self.size[1]) for x, y in part["path"]]
        if not points:
            return []
        if len(points) >= 2:
            return [(points[0], points[-1])]
        x, y = points[0]
        course = self.course_at(x, y)
        if course:
            return [((course["box"][0], (course["box"][1] + course["box"][3]) / 2), (course["box"][2], (course["box"][1] + course["box"][3]) / 2))]
        return [((x - 40, y), (x + 40, y))]

    def region_mask(self, ident):
        region = next(r for r in self.regions if r.id == ident)
        mask = np.zeros_like(self.ink)
        for y, runs in region.rows.items():
            for a, b in runs:
                mask[y, a:b] = True
        return mask

    def build(self):
        order = {"units": 0, "solid": 1, "sheet": 2, "spaced": 4}
        for part in sorted((p for p in self.parts.values() if p["form"] in order), key=lambda p: order[p["form"]] + (p["form"] == "sheet" and not p["regions"])):
            if part["form"] == "units":
                if self.hits[part["id"]]:
                    self.units(part)
                else:
                    self.notes.append(f"Not built: {part['name']}, nothing like it in the catalogue")
            elif part["form"] == "sheet":
                self.sheet(part)
            elif part["form"] == "spaced":
                self.spaced(part)
            elif part["regions"]:
                for region in part["regions"]:
                    if region in self.masks:
                        self.outline(part, self.masks[region])
            elif len(part["path"]) >= 3:
                self.outline(part, mask_of([(x * self.size[0], y * self.size[1]) for x, y in part["path"]], self.size))
        self.seams()

    def touching(self, a, b, section) -> bool:
        """Whether two parts come within TOUCH pixels in the section."""
        if a not in section or b not in section:
            return False
        near = self.grown(section[a])
        if (near & section[b]).any():
            return True
        beds = [p["id"] for p in self.plan["parts"] if p["category"] == "mortar" and p["id"] in section]  # a bed joint between two parts is how they touch
        return any((near & section[bed]).any() and (self.grown(section[bed]) & section[b]).any() for bed in beds)

    def grown(self, mask):
        for _ in range(self.touch):
            mask = mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0) | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)
        return mask

    def contract(self) -> list:
        """Each relation, on the plan's rough places and on the repaired ones. A drawn outline never moves, so a relation broken between two of them is the plan disagreeing with its drawing."""
        rows = []
        rough = {**self.section, **self.before}
        for relation in self.plan["relations"]:
            if relation["confidence"] < TRUSTED:
                rows.append({**relation, "before": "not binding", "after": "not binding"})
                continue
            targets = [relation["target"]] + ([relation["second_target"]] if relation["relation"] == "spans_between" and relation["second_target"] else [])
            state = [all(self.touching(relation["subject"], t, section) for t in targets) for section in (rough, self.section)]
            rows.append({**relation, "before": "kept" if state[0] else "broken", "after": "kept" if state[1] else "broken"})
        return rows

    def clashes(self) -> list:
        """Solid parts sharing section area, outside anything the plan says goes into something else."""
        allowed = {frozenset((r["subject"], t)) for r in self.plan["relations"] if r["relation"] in ("set_in", "fixed_into", "spans_between", "laps_over") for t in (r["target"], r["second_target"]) if t}
        for sheet in (p["id"] for p in self.plan["parts"] if p["form"] == "sheet"):  # a sheet is bedded in mortar, and a fixing goes through a sheet
            allowed |= {frozenset((sheet, p["id"])) for p in self.plan["parts"] if p["category"] == "mortar" or p["category"] == "fixing"}
        out, names = [], list(self.section)
        for k, a in enumerate(names):
            for b in names[k + 1:]:
                if frozenset((a, b)) not in allowed:
                    shared = int((self.section[a] & self.section[b]).sum())
                    if shared > 40:
                        out.append((a, b, shared))
        return out


if __name__ == "__main__":
    scene = Scene(sys.argv[1])
    scene.build()
    state = scene.contract()
    print("items", len(scene.items), "triangles", sum(len(i["triangles"]) for i in scene.items), "scale mm/px", round(scene.scale * 1000, 3), "joint mm", round(scene.joint * 1000, 1))
    print(*scene.notes, sep="\n")
    print("broken before", sum(r["before"] == "broken" for r in state), "after", sum(r["after"] == "broken" for r in state), "of", sum(r["after"] != "not binding" for r in state))
    print([(r["subject"], r["relation"], r["target"], r["before"], r["after"]) for r in state if r["after"] != "kept"])
    print("clashes", scene.clashes())
    print("faults", planner.faults(scene.plan, scene.regions))
