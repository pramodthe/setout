"""Synthetic section drawings with exact instance masks: the synthetic data engine, v1.

Templates (`synthetic/templates/*.yaml`) are parametric detail programs kept as
library data: discrete choices, parameters drawn from ranges labelled as
synthetic sampling ranges (not standards), optional parts, constraints, and the
words a drawing may print. Styles (`synthetic/styles/*.yaml`) are generic
drafting conventions. For one drawing:

1. `sample` draws choices, parameters and optional parts; `build_program`
   writes a v2 `DetailProgram` of exact primitives (box, sheet, wedge, profile,
   fillet), every value an assumption.
2. `compile_program` -> `build_assembly` -> `section_cut.cut`: the cut regions
   are the ground truth.
3. `draft` writes the DXF with `section_cut.to_dxf` at a not-to-scale stretch
   (su, sv), then adds callouts, dimension strings and clutter. A printed value
   is always the true size of the synthetic geometry.
4. `dxf_ingest` renders it; the image is placed on a sheet, rotated slightly
   and degraded.
5. Each mask is its part's cut loops sent through the same transforms: the
   stretch, `RenderInfo.to_pixel`, the placement and the rotation.

`Config.zoom` renders the same drawing at a higher resolution: the same paper
scanned at z x the dpi. Every random draw stays the zoom 1 draw, so a zoomed
sheet is the zoom 1 sheet with every coordinate multiplied by z.

Pixel coordinates follow the corner convention used by COCO and by the
renderer: pixel (i, j) covers [i, i+1) x [j, j+1), and a mask holds the pixels
whose centres lie inside the polygon.

Nothing here names a part: classes, shapes, roles and words come from the
template, the style and the ontology. Nothing here is a real drawing either:
every sheet prints "SYNTHETIC" and every program says how it was made.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import math
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from ezdxf.enums import MTextEntityAlignment
from ezdxf.tools import pattern as dxf_pattern
from PIL import Image, ImageFilter

from . import dxf_ingest, ontology, section_cut
from .geometry import build_assembly
from .program import (SHAPE_DIMS, DetailProgram, Dim, PathXY, ProgParam, ProgPart, _parse,
                      compile_program, evaluate, expr_names)
from .spec import Provenance, Relation, SourceDoc

GENERATOR = "setout-seg-synth"
VERSION = "1"
ROOT = Path(__file__).resolve().parent.parent / "synthetic"
TEMPLATES = ROOT / "templates"
STYLES = ROOT / "styles"
SHAPES = ("box", "sheet", "wedge", "profile", "fillet")  # exact primitives only
BASIS_MARK = "synthetic sampling range"
# DXF lineweights, 1/100 mm
LINEWEIGHTS = (5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158,
               200, 211)
THIN_MODES = ("outline", "line", "dashed_line", "filled")
LABEL_ENDS = ("arrow", "dot", "tick", "none")
SQRT2 = math.sqrt(2.0)

Ring = list[tuple[float, float]]


@dataclass(frozen=True)
class Config:
    """Rendering and dataset knobs; the defaults make seg-synth-v1.

    `target_px`, `geometry_px`, `margin_px` and `overlap_px` lay the drawing out
    and are zoom 1 pixels. The rest are pixels of the image the model sees, so
    they do not scale with `zoom`, and a part's thin flag can change with it.
    """

    tile: int = 1008
    stride: int = 756  # 252 px overlap between neighbouring tiles
    thin_px: float = 6.0  # thin rule: no disc this wide fits inside the region
    min_mask_px: float = 1.0  # min-width rule: masks are at least this wide
    target_px: tuple[float, float] = (1.0, 8.0)  # width of each drawing's thinnest part
    geometry_px: tuple[float, float] = (450.0, 1800.0)  # long side of the cut geometry on the sheet
    margin_px: tuple[int, int] = (24, 160)
    rotate_p: float = 0.6
    rotate_deg: float = 2.0
    clean_p: float = 0.3  # share of sheets left without photometric damage
    min_remnant_px2: float = 12.0  # clipped pieces smaller than this are dropped
    keep_empty_p: float = 0.35  # share of tiles with ink but no part that are kept as clutter
    max_attempts: int = 40
    overlap_px: int = 2  # tolerated overlap between two parts' masks
    zoom: float = 1.0  # render resolution multiplier, at least 1
    drop_empty_masks: bool = False  # drop clipped pieces whose mask has no pixel; off to reproduce v1


# ---------------------------------------------------------------- templates

@dataclass(frozen=True)
class ParamDef:
    name: str
    unit: str = "mm"
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    dist: str = "uniform"
    expr: str | None = None
    basis: str | None = None

    def draw(self, rng: random.Random) -> float:
        if self.dist == "log_uniform":
            value = math.exp(rng.uniform(math.log(self.lo), math.log(self.hi)))
        else:
            value = rng.uniform(self.lo, self.hi)
        if self.step:
            value = min(max(round(value / self.step) * self.step, self.lo), self.hi)
        return round(value, 6)


@dataclass(frozen=True)
class PointDef:
    x: str
    y: str
    when: str | None = None


@dataclass(frozen=True)
class PartDef:
    id: str
    class_id: str
    shape: str
    when: str | None
    p: float
    dims: tuple[tuple[str, str], ...]
    path: tuple[PointDef, ...]
    circle: tuple[str, str, str, int] | None  # cx, cy, r, vertices: a profile path


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    weight: float
    assembly_class: str | None
    title: str
    choices: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]
    params: tuple[ParamDef, ...]
    parts: tuple[PartDef, ...]
    constraints: tuple[str, ...]
    relations: tuple[tuple[str, str, str], ...]
    labels: dict
    titles: tuple[str, ...]
    notes: tuple[str, ...]
    levels: tuple[tuple[str, tuple[str, ...]], ...]  # level lines a sheet may draw: (y, names)
    breaks: tuple[tuple[int, str, str | None], ...]  # (axis 0: x / 1: y, where, when): drawing cut lines
    sha256: str

    def flags(self) -> list[str]:
        return [f"{choice}_{option}" for choice, options in self.choices for option, _ in options]

    def class_ids(self) -> list[str]:
        return sorted({p.class_id for p in self.parts})


def _need(data, key, where):
    if key not in data:
        raise ValueError(f"{where}: missing {key}")
    return data[key]


def _point(raw, where) -> PointDef:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return PointDef(str(raw[0]), str(raw[1]))
    if isinstance(raw, dict):
        return PointDef(str(_need(raw, "x", where)), str(_need(raw, "y", where)),
                        str(raw["when"]) if raw.get("when") is not None else None)
    raise ValueError(f"{where}: a path point is [x, y] or {{x, y, when}}")


def _param_def(raw, where) -> ParamDef:
    name = str(_need(raw, "name", where))
    unit = raw.get("unit", "mm")
    if unit not in ("mm", "ratio"):
        raise ValueError(f"{where}.{name}: unit must be mm or ratio")
    if "expr" in raw:
        return ParamDef(name=name, unit=unit, expr=str(raw["expr"]))
    lo, hi = _need(raw, "range", f"{where}.{name}")
    basis = str(raw.get("basis") or "")
    if BASIS_MARK not in basis:
        raise ValueError(f"{where}.{name}: a sampled range must say it is a {BASIS_MARK} (basis)")
    dist = raw.get("dist", "uniform")
    if dist not in ("uniform", "log_uniform") or not float(lo) <= float(hi):
        raise ValueError(f"{where}.{name}: bad range or dist")
    if dist == "log_uniform" and float(lo) <= 0:
        raise ValueError(f"{where}.{name}: log_uniform needs a positive range")
    return ParamDef(name=name, unit=unit, lo=float(lo), hi=float(hi), dist=dist, basis=basis,
                    step=float(raw["step"]) if raw.get("step") else None)


def _part_def(raw, where) -> PartDef:
    pid = str(_need(raw, "id", where))
    where = f"{where}.{pid}"
    shape = _need(raw, "shape", where)
    if shape not in SHAPES:
        raise ValueError(f"{where}: shape must be one of {SHAPES} (exact primitives only)")
    dims = tuple((str(k), str(v)) for k, v in (raw.get("dims") or {}).items())
    path = tuple(_point(p, where) for p in raw.get("path") or [])
    circle = None
    if raw.get("circle"):
        c = raw["circle"]
        circle = (str(c["cx"]), str(c["cy"]), str(c["r"]), int(c.get("n", 24)))
        if shape != "profile" or path:
            raise ValueError(f"{where}: a circle is a profile path and replaces `path`")
    missing = [d for d in SHAPE_DIMS[shape] if d not in dict(dims)]
    if missing:
        raise ValueError(f"{where}: {shape} needs dims {missing}")
    if shape in ("sheet", "profile") and not (path or circle):
        raise ValueError(f"{where}: {shape} needs a path")
    p = float(raw.get("p", 1.0))
    if not 0.0 < p <= 1.0:
        raise ValueError(f"{where}: p must be in (0, 1]")
    return PartDef(id=pid, class_id=str(_need(raw, "class_id", where)), shape=shape,
                   when=str(raw["when"]) if raw.get("when") is not None else None, p=p,
                   dims=dims, path=path, circle=circle)


def _break_def(raw, where) -> tuple[int, str, str | None]:
    """{x: expr} or {y: expr}, with an optional when: where the drawing stops
    and draws a break line across what it cuts."""
    axes = [k for k in ("x", "y") if k in raw]
    if len(axes) != 1:
        raise ValueError(f"{where}: a break is {{x: expr}} or {{y: expr}}")
    return (0 if axes[0] == "x" else 1, str(raw[axes[0]]),
            str(raw["when"]) if raw.get("when") is not None else None)


def load_template(path, onto=None) -> Template:
    """Read and validate one template file. Raises ValueError on bad data."""
    path = Path(path)
    text = path.read_text()
    raw = yaml.safe_load(text)
    where = path.name
    if not isinstance(raw, dict) or raw.get("status") not in ("draft", "reviewed"):
        raise ValueError(f"{where}: expected a mapping with status draft or reviewed")
    choices = tuple((str(c["id"]), tuple((str(k), float(v)) for k, v in c["options"].items()))
                    for c in raw.get("choices") or [])
    template = Template(
        id=str(_need(raw, "id", where)), version=int(_need(raw, "version", where)),
        weight=float(raw.get("weight", 1.0)), assembly_class=raw.get("assembly_class"),
        title=str(_need(raw, "title", where)), choices=choices,
        params=tuple(_param_def(p, where) for p in raw.get("params") or []),
        parts=tuple(_part_def(p, where) for p in _need(raw, "parts", where)),
        constraints=tuple(str(c) for c in raw.get("constraints") or []),
        relations=tuple((str(a), str(k), str(b)) for a, k, b in raw.get("relations") or []),
        labels={str(k): tuple(str(w) for w in v) for k, v in (raw.get("labels") or {}).items()},
        titles=tuple(str(t) for t in raw.get("titles") or []),
        notes=tuple(str(n) for n in raw.get("notes") or []),
        levels=tuple((str(d["y"]), tuple(str(t) for t in d["text"])) for d in raw.get("levels") or []),
        breaks=tuple(_break_def(b, where) for b in raw.get("breaks") or []),
        sha256=hashlib.sha256(text.encode()).hexdigest())
    _validate_template(template, onto or ontology.load())
    return template


def _validate_template(t: Template, onto) -> None:
    where = f"template {t.id}"
    if t.assembly_class is not None and onto.assembly(t.assembly_class) is None:
        raise ValueError(f"{where}: unknown assembly class {t.assembly_class!r}")
    for part in t.parts:
        if onto.component(part.class_id) is None:
            raise ValueError(f"{where}.{part.id}: unknown ontology class {part.class_id!r}")
    ids = [p.id for p in t.parts]
    names = [p.name for p in t.params]
    flags = t.flags()
    has = [f"has_{i}" for i in ids]
    space = names + flags + has
    if len(set(ids)) != len(ids) or len(set(space)) != len(space):
        raise ValueError(f"{where}: part ids, parameter names and flags must be unique")
    known = set(flags) | {p.name for p in t.params if p.expr is None}
    for i, part in enumerate(t.parts):  # a part's `when` reads only parts above it
        _check_names(part.when, known | {f"has_{q}" for q in ids[:i]}, f"{where}.{part.id}.when")
    known |= set(has)
    for p in t.params:
        if p.expr is not None:
            _check_names(p.expr, known, f"{where}.{p.name}")
            known.add(p.name)
    for expr in t.constraints:
        _check_names(expr, known, f"{where} constraint")
    for part in t.parts:
        exprs = [e for _, e in part.dims] + [c for q in part.path for c in (q.x, q.y, q.when) if c]
        exprs += list(part.circle[:3]) if part.circle else []
        for expr in exprs:
            _check_names(expr, known, f"{where}.{part.id}")
    for a, kind, b in t.relations:
        if a not in ids or b not in ids or kind not in onto.relations:
            raise ValueError(f"{where}: relation {a} {kind} {b} names an unknown part or kind")
    for class_id in t.labels:
        if class_id not in t.class_ids():
            raise ValueError(f"{where}: labels for {class_id!r}, which no part uses")
    for expr, _ in t.levels:
        _check_names(expr, known, f"{where} level")
    for _, expr, when in t.breaks:
        _check_names(expr, known, f"{where} break")
        _check_names(when, known, f"{where} break")


def _check_names(expr: str | None, known: set[str], where: str) -> None:
    if expr is None:
        return
    _parse(expr)
    unknown = sorted(expr_names(expr) - known)
    if unknown:
        raise ValueError(f"{where}: unknown names {unknown} in {expr!r}")


def load_templates(folder=TEMPLATES) -> list[Template]:
    onto = ontology.load()
    return [load_template(p, onto) for p in sorted(Path(folder).glob("*.yaml"))]


# ---------------------------------------------------------------- sampling

@dataclass
class Sample:
    template: Template
    choices: dict[str, str]
    env: dict[str, float]
    present: list[PartDef]
    attempts: int


def _weighted(rng: random.Random, options):
    names = [k for k, _ in options]
    return rng.choices(names, weights=[w for _, w in options], k=1)[0]


def sample(template: Template, rng: random.Random, max_tries: int = 500) -> Sample:
    """Draw choices, ranged parameters and optional parts, evaluate the derived
    parameters, and redraw until every constraint is >= 0."""
    for attempt in range(1, max_tries + 1):
        env: dict[str, float] = {}
        picked = {}
        for choice, options in template.choices:
            picked[choice] = _weighted(rng, options)
            for option, _ in options:
                env[f"{choice}_{option}"] = 1.0 if option == picked[choice] else 0.0
        for p in template.params:
            if p.expr is None:
                env[p.name] = p.draw(rng)
        present = []
        for part in template.parts:
            on = (part.when is None or evaluate(part.when, env) > 0) and rng.random() < part.p
            env[f"has_{part.id}"] = 1.0 if on else 0.0
            if on:
                present.append(part)
        for p in template.params:
            if p.expr is not None:
                env[p.name] = evaluate(p.expr, env)
        if all(evaluate(c, env) >= 0 for c in template.constraints):
            return Sample(template, picked, env, present, attempt)
    raise ValueError(f"template {template.id}: no sample met the constraints in {max_tries} tries")


def _part_points(part: PartDef, env) -> list[PathXY]:
    """The part's path with conditional points resolved and repeats dropped."""
    if part.circle:
        cx, cy, r, n = part.circle
        raw = [PointDef(f"({cx}) + ({r}) * {math.cos(2 * math.pi * k / n):.6f}",
                        f"({cy}) + ({r}) * {math.sin(2 * math.pi * k / n):.6f}") for k in range(n)]
    else:
        raw = [q for q in part.path if q.when is None or evaluate(q.when, env) > 0]
    out: list[PathXY] = []
    last = None
    for q in raw:
        xy = (evaluate(q.x, env), evaluate(q.y, env))
        if last is not None and math.dist(xy, last) < 1e-6:
            continue
        out.append(PathXY(x=q.x, y=q.y))
        last = xy
    if part.shape == "profile" and len(out) > 2:
        first = (evaluate(out[0].x, env), evaluate(out[0].y, env))
        if math.dist(first, last) < 1e-6:
            out.pop()
    return out


def build_program(s: Sample, drawing_id: str, labels: dict[str, str] | None = None
                  ) -> tuple[DetailProgram, dict[str, Ring]]:
    """The sample as a v2 program, plus each sheet part's centreline (mm).

    Every parameter is an `assumption` (a synthetic sample, not a drawing
    value). A part's `label` is the callout this synthetic drawing prints for
    it, or None.
    """
    labels = labels or {}
    t = s.template
    parts, used, centrelines = [], [], {}
    for part in s.present:
        path = _part_points(part, s.env)
        exprs = [e for _, e in part.dims] + [c for q in path for c in (q.x, q.y)]
        used += [n for e in exprs for n in sorted(expr_names(e))]
        parts.append(ProgPart(id=part.id, type=part.class_id, label=labels.get(part.id),
                              class_id=part.class_id, shape=part.shape,
                              dims=[Dim(name=k, expr=v) for k, v in part.dims], path=path,
                              note=f"synthetic part from template {t.id}@{t.version}"))
        if part.shape == "sheet":
            centrelines[part.id] = [(evaluate(q.x, s.env), evaluate(q.y, s.env)) for q in path]
    units = {p.name: p.unit for p in t.params} | {f: "ratio" for f in t.flags()}
    params = [ProgParam(name=n, value=round(s.env[n], 6), unit=units.get(n, "ratio"),
                        provenance=Provenance.ASSUMPTION, note=_param_note(n, t))
              for n in dict.fromkeys(used)]
    ids = {p.id for p in s.present}
    relations = [Relation(source=a, kind=k, target=b) for a, k, b in t.relations
                 if a in ids and b in ids]
    record = f"{t.id}@{t.version}:{drawing_id}:{sorted(s.env.items())}"
    program = DetailProgram(
        detail_id=drawing_id, title=f"{t.title} (synthetic {drawing_id})",
        origin=f"synthetic:{GENERATOR}@{VERSION} template {t.id}@{t.version} (sampled, not a drawing)",
        kind="section", situation=["synthetic"], assembly_class=t.assembly_class,
        source=SourceDoc(filename=f"synthetic/{t.id}", sha256=hashlib.sha256(record.encode()).hexdigest(),
                         page_index=0, crop_id=drawing_id, page_width_pt=0.0, page_height_pt=0.0),
        params=params, parts=parts, relations=relations)
    return program, centrelines


def _param_note(name: str, t: Template) -> str:
    p = next((q for q in t.params if q.name == name), None)
    if p is None:
        return "synthetic variant flag"
    if p.expr is not None:
        return f"synthetic, = {p.expr}"
    return f"synthetic sample ({p.basis})"


def build_section(program: DetailProgram, onto=None) -> section_cut.SectionDrawing:
    """Compile, build and cut the program at mid-extent. Every part must be cut
    into closed loops: a part the plane misses or leaves open is a template
    error, not something to draw around."""
    onto = onto or ontology.load()
    spec = compile_program(program)
    meshes = build_assembly(spec)
    z = float(spec.context["illustrative_extent_z_mm"]) / 2.0
    section = section_cut.cut(meshes, (0.0, 0.0, z), (0.0, 0.0, 1.0))
    bad = section.missed + [pid for pid, p in section.parts.items() if p.open_paths or not p.loops]
    if bad:
        raise ValueError(f"{program.detail_id}: parts not cut into closed loops: {bad}")
    for part in program.parts:
        cut = section.parts[part.id]
        cut.class_id = part.class_id
        cut.role = onto.component(part.class_id).role
        cut.label = part.label
    return section


# ---------------------------------------------------------------- polygon geometry

def ring_area(ring) -> float:
    """Signed area (positive when the interior is on the left of the walk)."""
    p = np.asarray(ring, dtype=float)
    q = np.roll(p, -1, axis=0)
    return float(np.sum(p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]) / 2.0)


def _edge_arrays(rings):
    a = np.concatenate([np.asarray(r, dtype=float) for r in rings])
    b = np.concatenate([np.roll(np.asarray(r, dtype=float), -1, axis=0) for r in rings])
    return a[:, 0], a[:, 1], b[:, 0], b[:, 1]


def _signed_distance(px, py, edges) -> np.ndarray:
    """Distance from each point to the nearest edge, negative outside (even-odd)."""
    ax, ay, bx, by = edges
    px = np.asarray(px, dtype=float)[:, None]
    py = np.asarray(py, dtype=float)[:, None]
    dx, dy = bx - ax, by - ay
    length2 = np.where(dx * dx + dy * dy > 0, dx * dx + dy * dy, 1.0)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / length2, 0.0, 1.0)
    dist = np.sqrt(np.min((ax + t * dx - px) ** 2 + (ay + t * dy - py) ** 2, axis=1))
    crosses = ((ay > py) != (by > py)) & (
        px < ax + (py - ay) * dx / np.where(by != ay, by - ay, 1.0))
    inside = np.sum(crosses, axis=1) % 2 == 1
    return np.where(inside, dist, -dist)


def inscribed_circle(rings, rel: float = 0.02) -> tuple[float, float, float]:
    """Centre and radius of the largest disc inside the region (holes by
    even-odd), to `rel` of the radius: the pole-of-inaccessibility search, run
    a level at a time so each level is one vectorised distance call."""
    pts = np.concatenate([np.asarray(r, dtype=float) for r in rings])
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    size = min(x1 - x0, y1 - y0)
    if size <= 0:
        return float(x0), float(y0), 0.0
    edges = _edge_arrays(rings)
    h = size / 2.0
    gx, gy = np.meshgrid(np.arange(x0, x1, size) + h, np.arange(y0, y1, size) + h)
    xs = np.append(gx.ravel(), (x0 + x1) / 2.0)
    ys = np.append(gy.ravel(), (y0 + y1) / 2.0)
    hs = np.append(np.full(gx.size, h), 0.0)  # the bbox centre is a probe, not a cell
    best = (-np.inf, float(xs[0]), float(ys[0]))
    floor = 1e-4 * size
    for _ in range(64):
        d = _signed_distance(xs, ys, edges)
        i = int(np.argmax(d))
        if d[i] > best[0]:
            best = (float(d[i]), float(xs[i]), float(ys[i]))
        keep = d + hs * SQRT2 > best[0] + max(rel * best[0], floor)
        if not np.any(keep):
            break
        xs, ys, hs = xs[keep], ys[keep], hs[keep] / 2.0
        xs = np.concatenate([xs - hs, xs + hs, xs - hs, xs + hs])
        ys = np.concatenate([ys - hs, ys - hs, ys + hs, ys + hs])
        hs = np.tile(hs, 4)
    return best[1], best[2], max(best[0], 0.0)


def cross_sections(rings, per_edge: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Widths across the region: from points spread along every outline edge,
    the distance along the inward normal to the next edge, with each point
    weighted by the outline length it stands for."""
    ax, ay, bx, by = _edge_arrays(rings)
    sign = np.concatenate([np.full(len(r), _inward_sign(r, rings)) for r in rings])
    dx, dy = bx - ax, by - ay
    length = np.hypot(dx, dy)
    ok = length > 1e-9
    frac = (np.arange(per_edge) + 0.5) / per_edge
    src = np.repeat(np.flatnonzero(ok), per_edge)
    f = np.tile(frac, int(ok.sum()))
    px, py = ax[src] + f * dx[src], ay[src] + f * dy[src]
    nx, ny = -dy[src] / length[src] * sign[src], dx[src] / length[src] * sign[src]
    # ray p + t n against every segment a + s e
    den = nx[:, None] * dy[None, :] - ny[:, None] * dx[None, :]
    wx, wy = ax[None, :] - px[:, None], ay[None, :] - py[:, None]
    safe = np.where(np.abs(den) > 1e-12, den, 1.0)
    t = (wx * dy[None, :] - wy * dx[None, :]) / safe
    s = (wx * ny[:, None] - wy * nx[:, None]) / safe
    hit = (np.abs(den) > 1e-12) & (t > 1e-9) & (s >= -1e-9) & (s <= 1 + 1e-9)
    hit[np.arange(len(src)), src] = False
    t = np.min(np.where(hit, t, np.inf), axis=1)
    weight = length[src] / per_edge
    finite = np.isfinite(t)
    return t[finite], weight[finite]


def _is_outer(ring, rings) -> bool:
    """A ring nested in an odd number of others is a hole."""
    return sum(1 for other in rings if other is not ring and _contains(other, ring[0])) % 2 == 0


def _inward_sign(ring, rings) -> float:
    """+1 when the region lies left of the ring's walk, else -1."""
    return (1.0 if ring_area(ring) > 0 else -1.0) * (1.0 if _is_outer(ring, rings) else -1.0)


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    if not len(values):
        return 0.0
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, q * cum[-1])])


def region_width(rings) -> tuple[float, float]:
    """(width, narrow) of a region. `width` is the length-weighted median
    cross-section: a strip's thickness (a fold does not inflate it), a
    rectangle's short side, a disc's diameter. `narrow` is the 10th
    percentile: where the region is thinnest, ignoring the last sliver at a
    sharp corner."""
    values, weights = cross_sections(rings)
    return _weighted_quantile(values, weights, 0.5), _weighted_quantile(values, weights, 0.1)


def offset_ring(ring, delta: float, miter_limit: float = 3.0) -> Ring:
    """Move every edge of a simple ring outward by `delta` (mitred corners,
    the mitre clamped to `miter_limit` * delta)."""
    p = np.asarray(ring, dtype=float)
    q = np.roll(p, -1, axis=0)
    e = q - p
    length = np.hypot(e[:, 0], e[:, 1])
    length[length < 1e-12] = 1.0
    out_sign = 1.0 if ring_area(ring) > 0 else -1.0  # right of the walk is outside when area > 0
    n = np.stack([e[:, 1], -e[:, 0]], axis=1) / length[:, None] * out_sign
    n_prev = np.roll(n, 1, axis=0)
    bis = n_prev + n
    dot = np.sum(n_prev * n, axis=1)
    scale = delta / np.maximum(1.0 + dot, 1e-9)
    shift = bis * scale[:, None]
    norm = np.hypot(shift[:, 0], shift[:, 1])
    clamp = norm > miter_limit * delta
    shift[clamp] *= (miter_limit * delta / norm[clamp])[:, None]
    return [tuple(v) for v in (p + shift)]


def keyhole(rings) -> list[Ring]:
    """Rings as COCO polygons, which have no holes: each hole (a ring inside
    another) is joined to its outer ring by a zero-width cut from the hole's
    rightmost vertex to the nearest outer vertex. Returns one polygon per outer
    ring."""
    outers = [r for r in rings if not any(_contains(o, r[0]) for o in rings if o is not r)]
    holes = [r for r in rings if r not in outers]
    merged = [list(o) for o in outers]
    for hole in holes:
        k = next(i for i, o in enumerate(outers) if _contains(o, hole[0]))
        ring = merged[k]
        h = max(range(len(hole)), key=lambda i: hole[i][0])
        j = min(range(len(ring)), key=lambda i: math.dist(ring[i], hole[h]))
        loop = list(hole[h:]) + list(hole[:h]) + [hole[h]]
        merged[k] = ring[:j + 1] + loop + ring[j:]
    return merged


def _contains(ring, point) -> bool:
    return bool(_signed_distance([point[0]], [point[1]], _edge_arrays([ring]))[0] > 0)


def rasterize(rings, box) -> np.ndarray:
    """Boolean mask over box = (x0, y0, w, h): pixels whose centres lie inside
    the rings by the even-odd rule (half-open on edges, so two polygons sharing
    an edge never share a pixel)."""
    x0, y0, w, h = box
    acc = np.zeros((h, w + 1), dtype=np.int32)
    for ring in rings:
        p = np.asarray(ring, dtype=float)
        q = np.roll(p, -1, axis=0)
        for (ax, ay), (bx, by) in zip(p, q):
            if ay == by:
                continue
            lo, hi = (ay, by) if ay < by else (by, ay)
            j0 = max(0, math.ceil(lo - y0 - 0.5))
            j1 = min(h, math.ceil(hi - y0 - 0.5))
            if j1 <= j0:
                continue
            rows = np.arange(j0, j1)
            xc = ax + (y0 + rows + 0.5 - ay) * (bx - ax) / (by - ay)
            cols = np.clip(np.floor(xc - x0 - 0.5).astype(np.int64) + 1, 0, w)
            np.add.at(acc, (rows, cols), 1)
    return (np.cumsum(acc, axis=1)[:, :w] & 1).astype(bool)


def ring_box(rings, pad: int = 0, limit=None) -> tuple[int, int, int, int]:
    pts = np.concatenate([np.asarray(r, dtype=float) for r in rings])
    x0 = math.floor(pts[:, 0].min()) - pad
    y0 = math.floor(pts[:, 1].min()) - pad
    x1 = math.ceil(pts[:, 0].max()) + pad
    y1 = math.ceil(pts[:, 1].max()) + pad
    if limit is not None:
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, limit[0]), min(y1, limit[1])
    return x0, y0, max(x1 - x0, 0), max(y1 - y0, 0)


def clip_ring(ring, x0, y0, x1, y1) -> Ring:
    """Sutherland-Hodgman clip of a ring to the rectangle [x0, x1] x [y0, y1]."""
    pts = [tuple(map(float, p)) for p in ring]
    for axis, bound, keep_low in ((0, x0, False), (0, x1, True), (1, y0, False), (1, y1, True)):
        if not pts:
            break
        inside = (lambda p: p[axis] <= bound) if keep_low else (lambda p: p[axis] >= bound)
        out = []
        for a, b in zip(pts, pts[1:] + pts[:1]):
            ina, inb = inside(a), inside(b)
            if ina:
                out.append(a)
            if ina != inb:
                s = (bound - a[axis]) / (b[axis] - a[axis])
                other = 1 - axis
                cross = [0.0, 0.0]
                cross[axis] = bound
                cross[other] = a[other] + s * (b[other] - a[other])
                out.append((cross[0], cross[1]))
        pts = out
    dedup = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-9]
    if len(dedup) > 1 and math.dist(dedup[0], dedup[-1]) <= 1e-9:
        dedup.pop()
    return dedup


def _boundary(mask: np.ndarray) -> np.ndarray:
    pad = np.pad(mask, 1)
    core = pad[1:-1, 1:-1] & pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
    return mask & ~core


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(r):
        pad = np.pad(out, 1)
        grown = pad[1:-1, 1:-1].copy()
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                grown |= pad[dy:dy + out.shape[0], dx:dx + out.shape[1]]
        out = grown
    return out


# ---------------------------------------------------------------- styles

@dataclass(frozen=True)
class StyleDef:
    id: str
    version: int
    weight: float
    data: dict
    sha256: str


def load_style(path, onto=None) -> StyleDef:
    """Read and validate one drafting style file. Raises ValueError on bad data."""
    path = Path(path)
    text = path.read_text()
    data = yaml.safe_load(text)
    where = path.name
    if not isinstance(data, dict) or data.get("status") not in ("draft", "reviewed"):
        raise ValueError(f"{where}: expected a mapping with status draft or reviewed")
    for key in ("id", "version", "basis", "scan_dpi", "text", "weights", "default", "classes", "thin",
                "labels", "dims", "clutter"):
        _need(data, key, where)
    onto = onto or ontology.load()
    linetypes = set(data.get("linetypes") or {}) | {"continuous"}
    looks = [("default", data["default"])] + list(data["classes"].items())
    for name, look in looks:
        if name != "default" and onto.component(name) is None:
            raise ValueError(f"{where}: unknown ontology class {name!r}")
        for w in look.get("weight", []):
            if w not in data["weights"]:
                raise ValueError(f"{where}.{name}: unknown weight {w!r}")
        for lt in look.get("linetype", ["continuous"]):
            if lt not in linetypes:
                raise ValueError(f"{where}.{name}: unknown linetype {lt!r}")
        for h in look.get("hatch", ["none"]):
            if h != "none" and (not isinstance(h, list) or h[0] not in dxf_pattern.ISO_PATTERN):
                raise ValueError(f"{where}.{name}: hatch {h!r} is not [ISO pattern, lo, hi] or none")
    if set(data["thin"]["modes"]) - set(THIN_MODES):
        raise ValueError(f"{where}: thin modes must be among {THIN_MODES}")
    if set(data["labels"]["ends"]) - set(LABEL_ENDS):
        raise ValueError(f"{where}: label ends must be among {LABEL_ENDS}")
    return StyleDef(id=str(data["id"]), version=int(data["version"]),
                    weight=float(data.get("weight", 1.0)), data=data,
                    sha256=hashlib.sha256(text.encode()).hexdigest())


def load_styles(folder=STYLES) -> list[StyleDef]:
    onto = ontology.load()
    return [load_style(p, onto) for p in sorted(Path(folder).glob("*.yaml"))]


@dataclass
class Look:
    """How one part is drawn: paper sizes in mm."""

    mode: str  # outline | line | dashed_line | filled
    weight: float
    linetype: str  # "continuous" or a style linetype name
    hatch: tuple[str, float, float] | None  # pattern, paper scale, angle


@dataclass
class Drafting:
    """One drawing's sampled drafting choices (paper mm unless named otherwise)."""

    style: StyleDef
    su: float
    sv: float
    dpi: float
    target_px: float
    text_height: float
    font: str
    upper: bool
    class_looks: dict[str, Look]
    thin_modes: dict[str, str]  # class id -> mode for sheet parts
    thin_weight: float
    thin_dashed: str
    hatch_weight: float
    linetypes: dict[str, list[float]]
    labels: dict
    dims: dict
    clutter: dict[str, bool]


def _uniform(rng, pair):
    lo, hi = pair
    return rng.uniform(float(lo), float(hi))


def _pick(rng, options):
    """A list picks uniformly; a mapping picks by weight."""
    if isinstance(options, dict):
        return _weighted(rng, [(k, float(v)) for k, v in options.items()])
    return rng.choice(list(options))


def sample_drafting(style: StyleDef, rng: random.Random, class_ids, cfg: Config) -> Drafting:
    d = style.data
    nts = d.get("nts") or {}
    if rng.random() < float(nts.get("p_true", 1.0)):
        su = sv = 1.0
    else:
        su, sv = _uniform(rng, nts["range"]), _uniform(rng, nts["range"])
    weights = {k: v for k, v in d["weights"].items()}
    looks = {}
    for class_id in class_ids:
        raw = d["classes"].get(class_id, d["default"])
        hatch = _pick(rng, raw.get("hatch", ["none"]))
        looks[class_id] = Look(
            mode="outline", weight=_uniform(rng, weights[_pick(rng, raw.get("weight", ["medium"]))]),
            linetype=_pick(rng, raw.get("linetype", ["continuous"])),
            hatch=None if hatch == "none" else (hatch[0], _uniform(rng, hatch[1:3]),
                                                float(_pick(rng, raw.get("angle", [0]))) + (
                                                    rng.uniform(-8, 8) if raw.get("jitter") else 0.0)))
    thin = d["thin"]
    labels, dims, text = d["labels"], d["dims"], d["text"]
    return Drafting(
        style=style, su=su, sv=sv, dpi=_uniform(rng, d["scan_dpi"]),
        target_px=_uniform(rng, cfg.target_px),
        text_height=_uniform(rng, text["height"]), font=_pick(rng, text["fonts"]),
        upper=rng.random() < float(text.get("upper", 1.0)), class_looks=looks,
        thin_modes={c: _pick(rng, thin["modes"]) for c in class_ids},
        thin_weight=_uniform(rng, weights[_pick(rng, thin.get("weight", ["fine"]))]),
        thin_dashed=_pick(rng, thin.get("dashed", ["continuous"])),
        hatch_weight=_uniform(rng, d.get("hatch_weight", [0.09, 0.13])),
        linetypes={k: [float(x) for x in v] for k, v in (d.get("linetypes") or {}).items()},
        labels={"share": _uniform(rng, labels["share"]), "sides": _pick(rng, labels["sides"]),
                "end": _pick(rng, labels["ends"]), "end_size": _uniform(rng, labels["end_size"]),
                "offset": _uniform(rng, labels["offset"]),
                "weight": _uniform(rng, weights[_pick(rng, labels.get("weight", ["fine"]))])},
        dims={"arrow": _pick(rng, dims["arrows"]), "text_height": _uniform(rng, dims["text_height"]),
              "arrow_size": _uniform(rng, dims["arrow_size"]), "offset": _uniform(rng, dims["offset"]),
              "spacing": _uniform(rng, dims["spacing"]),
              "left": rng.random() < float(dims["chains"]), "right": rng.random() < float(dims["chains"]),
              "overall": rng.random() < float(dims["overall"]),
              "extra": rng.randint(int(dims["extra"][0]), int(dims["extra"][1])),
              "weight": _uniform(rng, weights[_pick(rng, dims.get("weight", ["fine"]))])},
        clutter={k: rng.random() < float(v) for k, v in d["clutter"].items()})


# ---------------------------------------------------------------- measurement

@dataclass
class PartGeometry:
    """One part in drawing units (model mm times the stretch)."""

    rings: list[np.ndarray]
    centre: tuple[float, float]  # centre of the largest inscribed disc: where a leader lands
    width: float  # median cross-section (region_width): the thin rule reads it
    narrow: float  # 10th-percentile cross-section: the min-width rule reads it
    area: float
    line_length: float | None  # centreline length, sheet parts only


def measure(section, centrelines, su: float, sv: float) -> dict[str, PartGeometry]:
    out = {}
    for pid, part in section.parts.items():
        rings = [np.asarray(loop, dtype=float) * (su, sv) for loop in part.loops]
        cx, cy, _ = inscribed_circle(rings)
        width, narrow = region_width(rings)
        line = centrelines.get(pid)
        length = None
        if line:
            pts = np.asarray(line, dtype=float) * (su, sv)
            length = float(np.sum(np.hypot(*np.diff(pts, axis=0).T)))
        out[pid] = PartGeometry(rings=rings, centre=(cx, cy), width=width, narrow=narrow,
                                area=abs(sum(ring_area(rg) for rg in rings)), line_length=length)
    return out


def choose_scale(geo: dict[str, PartGeometry], target_px: float, cfg: Config) -> float:
    """Pixels per drawing unit: the thinnest part's width becomes `target_px`,
    unless that makes the cut geometry smaller or larger than `cfg.geometry_px`."""
    thinnest = min(g.width for g in geo.values())
    pts = np.concatenate([r for g in geo.values() for r in g.rings])
    span = float(max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])))
    lo, hi = cfg.geometry_px[0] / span, cfg.geometry_px[1] / span
    return float(min(max(target_px / max(thinnest, 1e-9), lo), hi))


def overlaps(geo: dict[str, PartGeometry], ppu: float, tolerance: int) -> list[tuple[str, str, int]]:
    """Pairs of parts whose masks share more than `tolerance` pixels at `ppu`.
    The grid is offset by an irrational fraction of a pixel, so an edge two
    parts share never runs exactly through pixel centres (where float noise
    in the two copies of the edge would let both parts claim them)."""
    masks = {}
    for pid, g in geo.items():
        rings = [r * (ppu, -ppu) + (0.31830988, 0.27182818) for r in g.rings]
        box = ring_box(rings, pad=1)
        masks[pid] = (box, rasterize(rings, box))
    bad = []
    ids = list(masks)
    for i, a in enumerate(ids):
        (ax, ay, aw, ah), ma = masks[a]
        for b in ids[i + 1:]:
            (bx, by, bw, bh), mb = masks[b]
            x0, y0 = max(ax, bx), max(ay, by)
            x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
            if x1 <= x0 or y1 <= y0:
                continue
            shared = int(np.count_nonzero(ma[y0 - ay:y1 - ay, x0 - ax:x1 - ax]
                                          & mb[y0 - by:y1 - by, x0 - bx:x1 - bx]))
            if shared > tolerance:
                bad.append((a, b, shared))
    return bad


# ---------------------------------------------------------------- drafting

def _lineweight(mm: float) -> int:
    return min(LINEWEIGHTS, key=lambda w: abs(w - mm * 100.0))


def part_looks(section, geo, d: Drafting, ppu: float, sheet_parts: set[str]) -> dict[str, Look]:
    """Each part's look: its class look, except sheet parts, which follow the
    drawing's thin-layer convention. A single line stands in for a sheet only
    when a line of the sheet's own width can be drawn."""
    px_per_paper_mm = d.dpi / 25.4
    max_line_px = float(d.style.data["thin"].get("max_line_px", 9))
    looks = {}
    for pid, part in section.parts.items():
        base = d.class_looks[part.class_id]
        if pid not in sheet_parts:
            looks[pid] = base
            continue
        g = geo[pid]
        mode = d.thin_modes[part.class_id]
        width_px = g.area / max(g.line_length or 1e-9, 1e-9) * ppu
        line_mm = width_px / px_per_paper_mm
        if mode in ("line", "dashed_line") and (width_px > max_line_px or line_mm > 2.11):
            mode = "outline"
        if mode in ("line", "dashed_line"):
            looks[pid] = Look(mode, max(line_mm, 0.05),
                              d.thin_dashed if mode == "dashed_line" else "continuous", None)
        elif mode == "filled":
            looks[pid] = Look(mode, d.thin_weight, "continuous", None)
        else:
            looks[pid] = Look("outline", d.thin_weight, base.linetype, None)
    return looks


def _linestyle(pid: str, look: Look, d: Drafting, n: float) -> section_cut.LineStyle:
    pattern = None
    linetype = "Continuous"
    if look.linetype != "continuous":
        elements = [x * n for x in d.linetypes[look.linetype]]
        pattern = tuple([sum(abs(x) for x in elements)] + elements)
        linetype = f"SYN_{look.linetype.upper()}"
    hatch = look.hatch if look.hatch and look.hatch[0] != "SOLID" else None  # SOLID: a real fill, in draft
    return section_cut.LineStyle(
        layer=f"P_{pid.upper()}", linetype=linetype, color=7, lineweight=_lineweight(look.weight),
        hatch=hatch[0] if hatch else None, hatch_scale=hatch[1] * n if hatch else 1.0,
        hatch_angle=hatch[2] if hatch else 0.0, pattern=pattern)


def draft(section, geo, looks, centrelines, words, s: Sample, d: Drafting, n: float,
          rng: random.Random, path):
    """The DXF of one synthetic drawing. `n` is drawing units per paper mm."""
    style = section_cut.Style(default=section_cut.LineStyle(layer="0"))
    drafted = section_cut.SectionDrawing(origin=section.origin, normal=section.normal,
                                         u=section.u, v=section.v)
    for pid, part in section.parts.items():
        look = looks[pid]
        key = f"part:{pid}"
        style.by_class[key] = _linestyle(pid, look, d, n)
        copy = section_cut.SectionPart(id=pid, color=part.color, class_id=key, role=part.role)
        if look.mode in ("line", "dashed_line"):
            copy.open_paths = [list(centrelines[pid])]
        else:
            copy.loops = part.loops
        drafted.parts[pid] = copy
    doc = section_cut.to_dxf(drafted, path, style, scale=(d.su, d.sv))
    msp = doc.modelspace()
    for hatch in msp.query("HATCH"):
        hatch.dxf.lineweight = _lineweight(d.hatch_weight)
    for pid, look in looks.items():
        if look.mode == "filled" or (look.hatch and look.hatch[0] == "SOLID"):
            fill = msp.add_hatch(color=7, dxfattribs={"layer": f"P_{pid.upper()}"})
            for ring in geo[pid].rings:
                fill.paths.add_polyline_path([tuple(p) for p in ring], is_closed=True)
    doc.styles.add("SYN_TEXT", font=d.font)
    if "SYN_CENTRE" not in doc.linetypes:
        doc.linetypes.add("SYN_CENTRE", pattern=[16.0 * n, 10.0 * n, -2.5 * n, 1.0 * n, -2.5 * n])
    box = _geometry_box(geo)
    labels = _callouts(msp, geo, words, d, n, box)
    _dimensions(msp, section, d, n, box, rng)
    _clutter(msp, section, geo, s, d, n, box, labels, rng)
    return doc


def _geometry_box(geo) -> tuple[float, float, float, float]:
    pts = np.concatenate([r for g in geo.values() for r in g.rings])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _text(msp, text: str, at, height: float, align, layer="TEXT"):
    entity = msp.add_mtext(text, dxfattribs={"layer": layer, "style": "SYN_TEXT", "char_height": height})
    entity.set_location(at, attachment_point=align)
    return entity


def _end_marker(msp, kind: str, tip, toward, size: float, lw: int):
    dx, dy = toward[0] - tip[0], toward[1] - tip[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    if kind == "arrow":
        base = (tip[0] + ux * size, tip[1] + uy * size)
        w = size * 0.3
        msp.add_solid([tip, (base[0] - uy * w, base[1] + ux * w), (base[0] + uy * w, base[1] - ux * w)],
                      dxfattribs={"layer": "TEXT"})
    elif kind == "dot":
        r = size * 0.3
        dot = msp.add_hatch(color=7, dxfattribs={"layer": "TEXT"})
        dot.paths.add_polyline_path([(tip[0] + r * math.cos(a), tip[1] + r * math.sin(a))
                                     for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)])
    elif kind == "tick":
        h = size * 0.5
        msp.add_line((tip[0] - h * (ux + uy), tip[1] - h * (uy - ux)),
                     (tip[0] + h * (ux + uy), tip[1] + h * (uy - ux)),
                     dxfattribs={"layer": "TEXT", "lineweight": lw})


def _callouts(msp, geo, words: dict[str, str], d: Drafting, n: float, box) -> dict[str, list[float]]:
    """Leaders from each labelled part's inscribed centre to a text column at
    the side of the drawing (beyond any dimension chain on that side), stacked
    so the leaders do not cross. Returns the label heights used per side."""
    used = {"left": [], "right": []}
    if not words:
        return used
    th = d.text_height * n
    lw = _lineweight(d.labels["weight"])
    cx = (box[0] + box[2]) / 2.0
    sides = {"left": [], "right": []}
    for pid in words:
        side = d.labels["sides"] if d.labels["sides"] != "both" else (
            "left" if geo[pid].centre[0] < cx else "right")
        sides[side].append(pid)
    for side, pids in sides.items():
        pids.sort(key=lambda p: -geo[p].centre[1])
        offset = d.labels["offset"] * n
        if d.dims[side]:  # clear the chain, its overall dimension and their text
            offset += _dims_extent(d, n, side) + 2 * th
        col = box[0] - offset if side == "left" else box[2] + offset
        shoulder = 1.5 * th * (1 if side == "left" else -1)
        slots, last = [], None
        for pid in pids:
            lines = words[pid].count("\n") + 1
            y = geo[pid].centre[1]
            if last is not None:
                y = min(y, last - 1.8 * th * lines)
            slots.append(y)
            last = y - 1.8 * th * (lines - 1)
        for pid, y in zip(pids, slots):
            tip = geo[pid].centre
            knee = (col + shoulder, y)
            msp.add_lwpolyline([tip, knee, (col, y)], dxfattribs={"layer": "TEXT", "lineweight": lw})
            _end_marker(msp, d.labels["end"], tip, knee, d.labels["end_size"] * n, lw)
            align = MTextEntityAlignment.MIDDLE_RIGHT if side == "left" else MTextEntityAlignment.MIDDLE_LEFT
            gap = 0.5 * th * (-1 if side == "left" else 1)
            _text(msp, words[pid].replace("\n", "\\P"), (col + gap, y), th, align)
            used[side].append(y)
    return used


def _levels(section, x: float) -> list[float]:
    """Sorted y of every loop edge crossing the vertical line at x (model mm)."""
    ys = set()
    for part in section.parts.values():
        for loop in part.loops:
            for (ax, ay), (bx, by) in zip(loop, loop[1:] + loop[:1]):
                if (ax - x) * (bx - x) < 0:
                    ys.add(round(ay + (x - ax) * (by - ay) / (bx - ax), 6))
    return sorted(ys)


def _dim_override(d: Drafting, n: float) -> dict:
    arrow = d.dims["arrow"]
    lw = _lineweight(d.dims["weight"])
    return {"dimtxt": d.dims["text_height"] * n, "dimasz": d.dims["arrow_size"] * n,
            "dimblk": arrow, "dimtsz": 0.0, "dimexo": 1.0 * n, "dimexe": 1.5 * n, "dimgap": 0.8 * n,
            "dimtad": 1, "dimtxsty": "SYN_TEXT", "dimdec": 1, "dimlwd": lw, "dimlwe": lw}


def _dimension(msp, base, p1, p2, angle: int, value: float, override: dict) -> None:
    msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, text=section_cut.printed(round(value, 1)),
                       dimstyle="EZDXF", override=override, dxfattribs={"layer": "DIM"}).render()


def _dimensions(msp, section, d: Drafting, n: float, box, rng: random.Random) -> None:
    """Build-up chains at the drawing's left and right edges, an optional
    overall dimension, and a few part widths above the geometry. Text is the
    true size in model mm, so a stretched drawing still prints true values."""
    ov = _dim_override(d, n)
    su, sv = d.su, d.sv
    th = d.dims["text_height"] * n
    model = section.bounds()
    off, step = d.dims["offset"] * n, d.dims["spacing"] * n
    for side in ("left", "right"):
        if not d.dims[side]:
            continue
        x = model[0] + 1.0 if side == "left" else model[2] - 1.0
        levels = _chain(_levels(section, x), 1.4 * th / sv)
        if len(levels) < 2:
            continue
        line = box[0] - off if side == "left" else box[2] + off
        for a, b in zip(levels, levels[1:]):
            _dimension(msp, (line, (a + b) / 2 * sv), (x * su, a * sv), (x * su, b * sv), 90, b - a, ov)
        if d.dims["overall"] and len(levels) > 2:
            outer = line - step if side == "left" else line + step
            _dimension(msp, (outer, 0.0), (x * su, levels[0] * sv), (x * su, levels[-1] * sv), 90,
                       levels[-1] - levels[0], ov)
    placed: list[tuple[float, float, float]] = []  # x0, x1, y of each width dimension
    parts = [p for p in section.parts.values() if (p.bounds()[2] - p.bounds()[0]) * su > 4 * th]
    for part in rng.sample(parts, len(parts)):
        if len(placed) >= d.dims["extra"]:
            break
        x0, _, x1, top = part.bounds()
        if _covered(section, part, (off + 2 * th) / sv) or any(
                abs(a - x0 * su) < th and abs(b - x1 * su) < th for a, b, _ in placed):
            continue  # a part in the way, or that span is already dimensioned
        y = top * sv + off
        while any(a < x1 * su and b > x0 * su and abs(y - c) < 2.5 * th for a, b, c in placed):
            y += step
        placed.append((x0 * su, x1 * su, y))
        _dimension(msp, ((x0 + x1) / 2 * su, y), (x0 * su, top * sv), (x1 * su, top * sv), 0, x1 - x0, ov)


def _covered(section, part, clearance: float) -> bool:
    """True when another part reaches into the band `clearance` (model mm)
    above `part`, so a width dimension just above it would cross that part."""
    x0, _, x1, top = part.bounds()
    for q in section.parts.values():
        qx0, qy0, qx1, qy1 = q.bounds()
        if q is not part and qx0 < x1 and qx1 > x0 and qy1 > top + 1e-6 and qy0 < top + clearance:
            return True
    return False


def _chain(levels: list[float], min_gap: float) -> list[float]:
    """Levels at least `min_gap` apart (model mm), keeping both ends."""
    if len(levels) < 2:
        return levels
    kept = [levels[0]]
    for y in levels[1:-1]:
        if y - kept[-1] >= min_gap and levels[-1] - y >= min_gap:
            kept.append(y)
    kept.append(levels[-1])
    return kept if kept[-1] - kept[0] >= min_gap else kept[:1]


def _clutter(msp, section, geo, s: Sample, d: Drafting, n: float, box, labels: dict[str, list[float]],
             rng: random.Random) -> None:
    """Unannotated linework and text: title, notes, break lines, level lines and
    the SYNTHETIC mark every sheet carries."""
    t = s.template
    th = d.text_height * n
    lw = _lineweight(d.dims["weight"])
    case = (lambda w: w) if d.upper else _sentence_case
    width = box[2] - box[0]
    top = box[1] - (d.dims["offset"] + 2 * d.dims["spacing"]) * n - th  # below the geometry and its dims
    mark = f"SYNTHETIC SAMPLE {s.template.id.upper()}"
    mark_y, block = top, _text_width(mark, 0.8 * th)
    if d.clutter.get("title") and t.titles:
        title = case(rng.choice(t.titles))
        scale = "NTS" if (d.su, d.sv) != (1.0, 1.0) else f"SCALE 1:{max(1, round(n))}"
        _text(msp, f"{title}\\P{scale}", (box[0], top), 1.4 * th, MTextEntityAlignment.TOP_LEFT)
        mark_y = top - 4.2 * th
        block = max(block, _text_width(title, 1.4 * th))
    _text(msp, mark, (box[0], mark_y), 0.8 * th, MTextEntityAlignment.TOP_LEFT)
    if d.clutter.get("notes") and t.notes:
        lines = rng.sample(list(t.notes), min(len(t.notes), rng.randint(2, 4)))
        body = "\\P".join([case("NOTES:")] + [f"{i}. {case(x)}" for i, x in enumerate(lines, 1)])
        below = (box[0] + max(0.45 * width, block + 3 * th), top)
        _text(msp, body, below if rng.random() < 0.7 else (box[2] + 0.15 * width, box[3]), 0.9 * th,
              MTextEntityAlignment.TOP_LEFT)
    if d.clutter.get("break_lines"):
        for axis, expr, when in t.breaks:
            if when is None or evaluate(when, s.env) > 0:
                _break_line(msp, geo, axis, evaluate(expr, s.env) * (d.su, d.sv)[axis], n, lw)
    if d.clutter.get("levels") and t.levels:
        expr, texts = rng.choice(t.levels)
        clear = {side: _dims_extent(d, n, side) + th for side in ("left", "right")}
        _level_line(msp, evaluate(expr, s.env) * d.sv, case(rng.choice(texts)), box, th, lw, labels, clear)


def _dims_extent(d: Drafting, n: float, side: str) -> float:
    """How far a side's dimension chain reaches out from the geometry."""
    if not d.dims[side]:
        return 0.0
    return (d.dims["offset"] + (2 if d.dims["overall"] else 1) * d.dims["spacing"]) * n


def _text_width(text: str, height: float) -> float:
    """A generous width estimate: MTEXT height is cap height, and a wide
    font's characters run to about 0.9 of it."""
    return 0.9 * height * max((len(line) for line in text.split("\n")), default=0)


def _sentence_case(text: str) -> str:
    """'RC SLAB TO FALL' -> 'RC slab to fall': a short capital word with no
    vowel, a dot or a hyphen (RC, F.F.L., A-A) keeps its case."""
    def acronym(word):
        core = word.strip(".,:;()")
        letters = core.replace("-", "").replace(".", "")
        return (core.isupper() and len(letters) <= 3
                and ("-" in core or "." in core or not any(c in "AEIOU" for c in letters)))
    out = " ".join(w if acronym(w) else w.lower() for w in text.split(" "))
    return out[:1].upper() + out[1:]


def _level_line(msp, y: float, text: str, box, th: float, lw: int, labels: dict[str, list[float]],
           clear: dict[str, float]) -> None:
    """A level line with a triangle and its name, on whichever end has no
    callout near that height (just the line when both ends are taken). The
    line runs past the dimension chains so its name never sits on them."""
    width = box[2] - box[0]
    x0 = box[0] - max(0.08 * width, clear["left"])
    x1 = box[2] + max(0.08 * width, clear["right"])
    msp.add_line((x0, y), (x1, y), dxfattribs={"layer": "DIM", "linetype": "SYN_CENTRE", "lineweight": lw})
    free = [side for side in ("right", "left") if all(abs(y - u) > 2.5 * th for u in labels[side])]
    if not free:
        return
    tri = 1.2 * th
    x = x1 if free[0] == "right" else x0
    msp.add_lwpolyline([(x, y), (x - tri / 2, y + tri), (x + tri / 2, y + tri)], close=True,
                       dxfattribs={"layer": "DIM", "lineweight": lw})
    if free[0] == "right":
        _text(msp, text, (x + tri, y + tri / 2), th, MTextEntityAlignment.MIDDLE_LEFT)
    else:
        _text(msp, text, (x - tri, y + tri / 2), th, MTextEntityAlignment.MIDDLE_RIGHT)


def _break_line(msp, geo, axis: int, at: float, n: float, lw: int) -> None:
    """A zigzag break symbol where the drawing cuts the parts off: across the
    parts whose outline ends on the line `axis` (0: x, 1: y) == `at`."""
    other = 1 - axis
    tol = 1e-3 * n
    spans = [(r[:, other].min(), r[:, other].max()) for g in geo.values() for r in g.rings
             if abs(r[:, axis].min() - at) < tol or abs(r[:, axis].max() - at) < tol]
    if not spans:
        return
    lo, hi = min(s[0] for s in spans) - 4 * n, max(s[1] for s in spans) + 4 * n
    mid, z = (lo + hi) / 2.0, 3.5 * n
    walk = [(at, lo), (at, mid - z), (at - z, mid - z / 3), (at + z, mid + z / 3), (at, mid + z), (at, hi)]
    msp.add_lwpolyline([p if axis == 0 else (p[1], p[0]) for p in walk],
                       dxfattribs={"layer": "DIM", "lineweight": lw})


# ---------------------------------------------------------------- sheet

def to_pixels(rings, info) -> list[np.ndarray]:
    """Drawing units to sheet pixels with the render's own transform."""
    x0, _, _, y1 = info.extents
    k = info.px_per_unit
    return [np.stack([info.margin_px + (r[:, 0] - x0) * k, info.margin_px + (y1 - r[:, 1]) * k], axis=1)
            for r in rings]


def place(image: Image.Image, rings: dict[str, list[np.ndarray]], cfg: Config, rng: random.Random,
          base_size: tuple[int, int] | None = None):
    """Pad the render to at least one tile with paper, at a random offset, then
    rotate it by a small angle; the rings go through the same affine map.

    `base_size` is the render's size at zoom 1. Offset, rotation and sheet size
    are worked out there and then scaled, so the random draws and the layout
    are the zoom 1 ones. Rounding at a fractional zoom can clip a pixel or two
    of the render's white margin.
    """
    z = cfg.zoom
    w, h = base_size or image.size
    W1, H1 = max(w, cfg.tile), max(h, cfg.tile)
    ox, oy = rng.randint(0, W1 - w), rng.randint(0, H1 - h)
    ox, oy, W, H = round(ox * z), round(oy * z), round(W1 * z), round(H1 * z)
    sheet = Image.new("L", (W, H), 255)
    sheet.paste(image, (ox, oy))
    moved = {pid: [r + (ox, oy) for r in rs] for pid, rs in rings.items()}
    angle = rng.uniform(-cfg.rotate_deg, cfg.rotate_deg) if rng.random() < cfg.rotate_p else 0.0
    if angle == 0.0:
        return sheet, moved, {"offset": [ox, oy], "rotation_deg": 0.0}
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    rot = np.array([[c, -s], [s, c]])
    corners = (np.array([[0, 0], [W1, 0], [0, H1], [W1, H1]], dtype=float) - (W1 / 2, H1 / 2)) @ rot.T
    Wr = round(int(math.ceil(np.ptp(corners[:, 0]))) * z)
    Hr = round(int(math.ceil(np.ptp(corners[:, 1]))) * z)
    centre_in, centre_out = np.array([W / 2, H / 2]), np.array([Wr / 2, Hr / 2])
    inv = rot.T  # output -> input: p_in = R^T (p_out - c_out) + c_in
    shift = centre_in - inv @ centre_out
    data = (inv[0, 0], inv[0, 1], shift[0], inv[1, 0], inv[1, 1], shift[1])
    turned = sheet.transform((Wr, Hr), Image.Transform.AFFINE, data, resample=Image.Resampling.BILINEAR,
                             fillcolor=255)
    out = {pid: [(r - centre_in) @ rot.T + centre_out for r in rs] for pid, rs in moved.items()}
    return turned, out, {"offset": [ox, oy], "rotation_deg": round(angle, 4)}


def degrade(image: Image.Image, rng: np.random.Generator, cfg: Config):
    """Photometric damage of a scan: ink spread or loss, blur, paper and ink
    levels, uneven light, noise, specks and JPEG. Geometry is untouched.

    A zoomed sheet takes the damage its zoom 1 sheet takes, as the same paper
    scanned at z x the dpi: blur and specks are z x wider in pixels, and noise
    and JPEG stay per pixel. Draws are sized by the zoom 1 sheet, which keeps
    `rng` in step with zoom 1 and the record the same.
    """
    z = cfg.zoom
    base = (round(image.height / z), round(image.width / z))  # the array's shape at zoom 1
    record = {}
    img = image
    if rng.random() < cfg.clean_p:
        record["clean"] = True
    else:
        if rng.random() < 0.3:
            img = img.filter(ImageFilter.GaussianBlur(float(rng.uniform(0.5, 0.8)) * z))
            a = np.asarray(img, dtype=np.float32) / 255.0
            if rng.random() < 0.6:
                t0 = float(rng.uniform(0.2, 0.45))
                a = np.clip((a - t0) / (1.0 - t0), 0.0, 1.0)
                record["ink"] = f"thicken {t0:.2f}"
            else:
                t1 = float(rng.uniform(0.65, 0.85))
                a = np.clip(a / t1, 0.0, 1.0)
                record["ink"] = f"thin {t1:.2f}"
            img = Image.fromarray((a * 255.0 + 0.5).astype(np.uint8))
        if rng.random() < 0.4:
            sigma = float(rng.uniform(0.3, 1.0))
            img = img.filter(ImageFilter.GaussianBlur(sigma * z))
            record["blur"] = round(sigma, 3)
    a = np.asarray(img, dtype=np.float32) / 255.0
    paper, ink = float(rng.uniform(0.88, 1.0)), float(rng.uniform(0.0, 0.3))
    gamma = float(math.exp(rng.uniform(math.log(0.7), math.log(1.4))))
    a = ink + (paper - ink) * np.power(a, gamma)
    record.update(paper=round(paper, 3), ink=round(ink, 3), gamma=round(gamma, 3))
    if not record.get("clean"):
        if rng.random() < 0.25:
            field = rng.uniform(-1.0, 1.0, size=(4, 4)).astype(np.float32)
            light = np.asarray(Image.fromarray(field).resize(img.size, Image.Resampling.BICUBIC))
            g = float(rng.uniform(0.02, 0.08))
            a = a * (1.0 - g * (light + 1.0) / 2.0)
            record["light"] = round(g, 3)
        if rng.random() < 0.5:
            sigma = float(rng.uniform(0.01, 0.05))
            noise = rng.normal(0.0, sigma, size=base)
            if z != 1.0:  # the draw above kept `rng` in step; the pixels come from a child stream
                noise = rng.spawn(1)[0].normal(0.0, sigma, size=a.shape)
            a = a + noise.astype(np.float32)
            record["noise"] = round(sigma, 4)
        if rng.random() < 0.3:
            density = float(rng.uniform(5e-5, 5e-4))
            count = int(density * (base[0] * base[1]))
            ys, xs = rng.integers(0, base[0], count), rng.integers(0, base[1], count)
            values = rng.uniform(0.0, 0.35, count)
            ys, xs = (ys * z).astype(np.int64), (xs * z).astype(np.int64)
            for dy in range(max(1, round(z))):  # a speck covers the same paper at every zoom
                for dx in range(max(1, round(z))):
                    a[np.minimum(ys + dy, a.shape[0] - 1), np.minimum(xs + dx, a.shape[1] - 1)] = values
            record["specks"] = count
    out = Image.fromarray((np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
    if not record.get("clean") and rng.random() < 0.4:
        quality = int(rng.integers(35, 91))
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=quality)
        out = Image.open(io.BytesIO(buf.getvalue())).convert("L")
        record["jpeg"] = quality
    return out, record


# ---------------------------------------------------------------- one drawing

@dataclass
class Instance:
    part_id: str
    class_id: str
    rings: list[np.ndarray]  # sheet pixels, holes joined by keyhole cuts
    min_width_px: float
    thin: bool
    widened: bool
    mode: str  # how the part is drawn: outline | line | dashed_line | filled
    linetype: str


@dataclass
class Drawing:
    drawing_id: str
    image: Image.Image  # degraded sheet
    clean: Image.Image  # the same sheet before photometric damage
    instances: list[Instance]
    record: dict = field(default_factory=dict)


def _choose(rng: random.Random, items, attr="weight"):
    return rng.choices(items, weights=[getattr(i, attr) for i in items], k=1)[0]


def _words(s: Sample, d: Drafting, rng: random.Random) -> dict[str, str]:
    """Callout text for a share of the present parts, from the template's own
    words for each class; a long phrase may break over two lines."""
    vocab = s.template.labels
    candidates = [p for p in s.present if vocab.get(p.class_id)]
    k = max(1, round(d.labels["share"] * len(candidates))) if candidates else 0
    chosen = rng.sample(candidates, k)
    words = {}
    for part in chosen:
        text = rng.choice(vocab[part.class_id])
        text = text if d.upper else _sentence_case(text)
        if len(text) > 16 and " " in text and rng.random() < 0.4:
            cut = text.rfind(" ", 0, len(text) // 2 + 4)
            text = text[:cut] + "\n" + text[cut + 1:] if cut > 0 else text
        words[part.id] = text
    return words


def make_drawing(index: int, seed: int, templates, styles, cfg: Config = Config(),
                 workdir=None) -> Drawing:
    """One synthetic drawing with its instance masks; deterministic in (seed, index)."""
    started = time.perf_counter()
    rng = random.Random(f"{GENERATOR}:{seed}:{index}")
    nprng = np.random.default_rng([seed, index])
    drawing_id = f"s{seed}_d{index:05d}"
    onto = ontology.load()
    rejected = []
    for attempt in range(1, cfg.max_attempts + 1):
        template = _choose(rng, templates)
        style = _choose(rng, styles)
        s = sample(template, rng)
        d = sample_drafting(style, rng, template.class_ids(), cfg)
        words = _words(s, d, rng)
        program, centrelines = build_program(s, drawing_id, words)
        section = build_section(program, onto)
        geo = measure(section, centrelines, d.su, d.sv)
        ppu = choose_scale(geo, d.target_px, cfg)
        clash = overlaps(geo, ppu, cfg.overlap_px)
        if not clash:
            break
        rejected.append([f"{a}/{b}:{k}" for a, b, k in clash])
    else:
        raise ValueError(f"{drawing_id}: parts overlap in every attempt: {rejected[-1]}")
    t_sample = time.perf_counter()
    n = d.dpi / 25.4 / ppu  # drawing units per paper mm
    sheets = set(centrelines)
    looks = part_looks(section, geo, d, ppu, sheets)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        doc = draft(section, geo, looks, centrelines, words, s, d, n, rng, Path(tmp) / "sheet.dxf")
    t_draft = time.perf_counter()
    margin = rng.randint(*cfg.margin_px)
    info = dxf_ingest.render_info(doc, dpi=ppu * 25.4, margin_px=margin, max_px=16384)
    base_size = (info.width, info.height)
    if cfg.zoom != 1.0:
        info = dxf_ingest.render_info(doc, dpi=ppu * 25.4 * cfg.zoom, margin_px=round(margin * cfg.zoom),
                                      max_px=round(16384 * cfg.zoom))
    info = dataclasses.replace(info, px_per_mm=d.dpi / 25.4 * cfg.zoom)  # lineweights plot at the scan dpi
    render = dxf_ingest.render_image(doc, info)
    t_render = time.perf_counter()
    k = info.px_per_unit
    rings, widths, widened = {}, {}, {}
    for pid, g in geo.items():
        px = to_pixels(g.rings, info)
        widths[pid] = g.width * k
        widened[pid] = g.narrow * k < cfg.min_mask_px
        if widened[pid]:  # min-width rule: grow sub-pixel regions to one pixel
            delta = (cfg.min_mask_px - g.narrow * k) / 2.0 + 0.01
            px = [np.asarray(offset_ring(r, delta if _is_outer(r, px) else -delta)) for r in px]
        rings[pid] = px
    clean, rings, placement = place(render, rings, cfg, rng, base_size)
    image, damage = degrade(clean, nprng, cfg)
    instances = []
    for pid, part in section.parts.items():
        # the stored (rounded) width decides the flag, so the two never disagree
        width = round(float(max(widths[pid], cfg.min_mask_px) if widened[pid] else widths[pid]), 3)
        instances.append(Instance(
            part_id=pid, class_id=part.class_id, rings=[np.asarray(r) for r in keyhole([list(map(tuple, r))
                                                                                        for r in rings[pid]])],
            min_width_px=width, thin=bool(width < cfg.thin_px), widened=widened[pid],
            mode=looks[pid].mode, linetype=looks[pid].linetype))
    align = alignment(clean, instances)
    record = {
        "drawing_id": drawing_id, "index": index, "seed": seed,
        "template": {"id": s.template.id, "version": s.template.version},
        "style": {"id": d.style.id, "version": d.style.version},
        "attempts": {"sample": s.attempts, "overlap_rejects": rejected},
        "choices": s.choices,
        "values": {k_: round(v, 6) for k_, v in s.env.items()},
        "drafting": {"su": round(d.su, 4), "sv": round(d.sv, 4), "dpi": round(d.dpi, 2),
                     "px_per_mm_u": round(k * d.su, 4), "px_per_mm_v": round(k * d.sv, 4),
                     "paper_scale": round(n, 3), "target_px": round(d.target_px, 3),
                     "thinnest_px": round(min(widths.values()), 3), "font": d.font,
                     "capped": info.capped, "labels": sorted(words)},
        "sheet": {"render": list(render.size), "size": list(clean.size), **placement, "margin_px": info.margin_px},
        "damage": damage,
        "parts": [{"id": i.part_id, "class_id": i.class_id, "min_width_px": i.min_width_px, "thin": i.thin,
                   "widened": i.widened, "mode": i.mode, "linetype": i.linetype,
                   "holes": sum(1 for loop in section.parts[i.part_id].loops
                                if section_cut._area2(loop) < 0)} for i in instances],
        "alignment": align,
        "seconds": {"sample": round(t_sample - started, 4), "draft": round(t_draft - t_sample, 4),
                    "render": round(t_render - t_draft, 4),
                    "total": round(time.perf_counter() - started, 4)},
    } | ({"zoom": cfg.zoom} if cfg.zoom != 1.0 else {})
    return Drawing(drawing_id, image, clean, instances, record)


def alignment(clean: Image.Image, instances: list[Instance], ink_level: int = 160) -> dict:
    """How well each mask sits on its ink: the share of the mask's boundary
    pixels with ink within 1 and 2 px (Chebyshev), on the sheet before
    photometric damage. Dashed lines and hatch gaps lower it; a wrong
    transform drops it toward the background ink density."""
    ink = np.asarray(clean) < ink_level
    out = {}
    for inst in instances:
        box = ring_box(inst.rings, pad=3, limit=(ink.shape[1], ink.shape[0]))
        if box[2] == 0 or box[3] == 0:
            continue
        mask = rasterize(inst.rings, box)
        edge = _boundary(mask)
        total = int(np.count_nonzero(edge))
        if not total:
            continue
        x, y, w, h = box
        near = ink[y:y + h, x:x + w]
        out[inst.part_id] = {
            "r1": round(float(np.count_nonzero(edge & _dilate(near, 1)) / total), 4),
            "r2": round(float(np.count_nonzero(edge & _dilate(near, 2)) / total), 4),
            "boundary_px": total, "mode": inst.mode, "linetype": inst.linetype, "thin": inst.thin}
    return out


# ---------------------------------------------------------------- tiles

def tile_origins(size: int, tile: int, stride: int) -> list[int]:
    """Tile starts covering [0, size) with the last tile flush to the end."""
    if size <= tile:
        return [0]
    count = math.ceil((size - tile) / stride) + 1
    return [round(i * (size - tile) / (count - 1)) for i in range(count)]


@dataclass
class Tile:
    x0: int
    y0: int
    image: Image.Image
    annotations: list[dict]


def tiles(drawing: Drawing, cfg: Config, rng: random.Random) -> list[Tile]:
    """Cut the sheet into overlapping tiles; each instance is clipped to the
    tile and kept when the piece is at least `cfg.min_remnant_px2` (and, with
    `cfg.drop_empty_masks`, fills a pixel). Tiles with ink but no instance (text,
    dimensions) are kept as clutter at `cfg.keep_empty_p`; blank tiles are
    dropped."""
    W, H = drawing.image.size
    clean = np.asarray(drawing.clean)
    out = []
    for y0 in tile_origins(H, cfg.tile, cfg.stride):
        for x0 in tile_origins(W, cfg.tile, cfg.stride):
            x1, y1 = x0 + cfg.tile, y0 + cfg.tile
            anns = []
            for inst in drawing.instances:
                ann = _clip_instance(inst, x0, y0, x1, y1, cfg)
                if ann:
                    anns.append(ann)
            if not anns:
                if not np.any(clean[y0:y1, x0:x1] < 128) or rng.random() >= cfg.keep_empty_p:
                    continue
            out.append(Tile(x0, y0, drawing.image.crop((x0, y0, x1, y1)), anns))
    return out


def fills_a_pixel(poly, size: int) -> bool:
    """True when a polygon, rounded as an annotation stores it, has a pixel in
    its mask. The scorer's port of pycocotools decides: training and scoring
    both see that mask, and a mask with no pixel can never be matched. Its
    upsampling differs from `rasterize` on slivers under a pixel wide."""
    from training.eval_seg import rle_from_polygon  # only dataset builds need this, and training/ is not app code

    flat = [round(float(c), 2) for p in poly for c in p]
    return any(rle_from_polygon(flat, size, size)[1::2])


def _clip_instance(inst: Instance, x0, y0, x1, y1, cfg: Config) -> dict | None:
    polys, area, truncated = [], 0.0, False
    for ring in inst.rings:
        rx0, ry0 = ring.min(axis=0)
        rx1, ry1 = ring.max(axis=0)
        if rx1 <= x0 or ry1 <= y0 or rx0 >= x1 or ry0 >= y1:
            truncated = True
            continue
        if rx0 < x0 or ry0 < y0 or rx1 > x1 or ry1 > y1:
            truncated = True
            piece = clip_ring(ring, x0, y0, x1, y1)
        else:
            piece = [tuple(p) for p in ring]
        if len(piece) < 3:
            continue
        a = abs(ring_area(piece))
        if a < cfg.min_remnant_px2:
            continue
        poly = [(px - x0, py - y0) for px, py in piece]
        if cfg.drop_empty_masks and not fills_a_pixel(poly, cfg.tile):
            continue
        polys.append(poly)
        area += a
    if not polys:
        return None
    pts = np.concatenate([np.asarray(p) for p in polys])
    bx0, by0 = pts.min(axis=0)
    bx1, by1 = pts.max(axis=0)
    return {"class_id": inst.class_id,
            "segmentation": [[round(float(c), 2) for p in poly for c in p] for poly in polys],
            "bbox": [round(float(bx0), 2), round(float(by0), 2), round(float(bx1 - bx0), 2),
                     round(float(by1 - by0), 2)],
            "area": round(area, 2), "iscrowd": 0,
            "attributes": {"thin": inst.thin, "min_width_px": inst.min_width_px, "part_id": inst.part_id,
                           "truncated": truncated}}
