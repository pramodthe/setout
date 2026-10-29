"""Detail program (spec v2): a general grammar for construction details.

A program is named parameters (with provenance) plus parts built from a
closed set of shapes whose dimensions are *expressions* of those parameters.
Rooms, junction sections and assemblies are all expressed the same way. A
model may write a program; it never writes geometry. `compile_program`
evaluates the expressions and emits the canonical DetailSpec v1, so the
existing generators, verification and exports are reused unchanged.

Provenance flows through expressions: a dimension computed only from drawing
parameters (and neutral datums) is a drawing value, with the quotes carried
along; any assumption in the expression makes it an assumption.
"""

from __future__ import annotations

import ast
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .spec import (Component, DetailSpec, GeneratorRef, Param, PathPoint,
                   ProductRef, Provenance, Relation, SourceDoc, Unresolved)

PROGRAM_VERSION = "2.0"
GEN = "setout-mesh-v0"

Shape = Literal["box", "sheet", "post", "rod", "wedge", "fillet", "profile",
                "revolve", "sweep", "fixture", "cad", "annotation"]
FIXTURES = ("bath", "shower_tray", "wc", "basin", "vanity", "door")

# dims each shape needs (z0/z1 optional for extruded shapes: default extent)
SHAPE_DIMS: dict[str, tuple[str, ...]] = {
    "box": ("x0", "x1", "y0", "y1"),
    "sheet": ("thickness",),
    "post": ("cx", "cz", "r", "y0", "y1"),
    "rod": ("cx", "cy", "r"),
    "wedge": ("x0", "x1", "y_bottom", "y_top_x0", "fall_ratio"),
    "fillet": ("x", "y", "leg_x", "leg_y"),
    "profile": (),  # closed outline in path (x, y); optional z0/z1
    "revolve": ("cx", "cz"),  # path = (radius, y) profile around a vertical axis
    "sweep": ("r",),  # path = 3D polyline (x, y, z)
    "fixture": ("x0", "x1", "y0", "y1", "z0", "z1"),  # envelope of a named fixture
    "cad": ("x0", "x1", "y0", "y1", "z0", "z1"),  # envelope for AI-modelled CadQuery
    "annotation": (),
}
MIN_PATH = {"sheet": 2, "profile": 3, "revolve": 2, "sweep": 2}
OPTIONAL_DIMS = ("z0", "z1")
GENERATOR = {"box": "box", "sheet": "sheet_follow", "post": "post", "rod": "rod",
             "wedge": "wedge", "fillet": "fillet", "profile": "profile",
             "revolve": "revolve", "sweep": "sweep", "cad": "cad_mesh",
             "annotation": "annotation"}

_RANK = {Provenance.DRAWING: 0, Provenance.MANUFACTURER_DOC: 1,
         Provenance.USER_INPUT: 2, Provenance.ASSUMPTION: 3}
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class ProgParam(BaseModel):
    name: str
    value: float
    unit: Literal["mm", "ratio"] = "mm"
    provenance: Provenance
    source_quote: str | None = None
    kind: Literal["exact", "min", "max", "datum"] = "exact"
    note: str | None = None


class Dim(BaseModel):
    name: str
    expr: str


class PathXY(BaseModel):
    x: str
    y: str
    z: str | None = None  # sweep only


class ProgPart(BaseModel):
    id: str
    type: str
    label: str | None = None  # EXACT drawing label, or null
    shape: Shape
    dims: list[Dim] = Field(default_factory=list)
    path: list[PathXY] = Field(default_factory=list)  # sheet/profile/revolve/sweep
    fixture: str | None = None  # named fixture kind when shape == "fixture"
    cad_brief: str | None = None  # what to model when shape == "cad"
    install_after: list[str] = Field(default_factory=list)
    note: str | None = None

    def dim(self, name: str) -> str | None:
        for d in self.dims:
            if d.name == name:
                return d.expr
        return None


class DetailProgram(BaseModel):
    version: Literal["2.0"] = PROGRAM_VERSION
    detail_id: str
    title: str
    origin: str  # how it was made, e.g. "ai-generated:muse-spark-1.3 (unreviewed)"
    kind: Literal["section", "room", "assembly"] = "assembly"
    situation: list[str] = Field(default_factory=list)
    source: SourceDoc
    params: list[ProgParam] = Field(default_factory=list)
    parts: list[ProgPart] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> DetailProgram:
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError("duplicate parameter names")
        for n in names:
            if not _ID.match(n):
                raise ValueError(f"bad parameter name {n!r}")
        ids = [p.id for p in self.parts]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate part ids")
        for part in self.parts:
            if not _ID.match(part.id):
                raise ValueError(f"bad part id {part.id!r}")
            have = {d.name for d in part.dims}
            missing = [d for d in SHAPE_DIMS[part.shape] if d not in have]
            if missing:
                raise ValueError(f"{part.id} ({part.shape}) missing dims {missing}")
            need = MIN_PATH.get(part.shape, 0)
            if len(part.path) < need:
                raise ValueError(f"{part.id}: {part.shape} needs >= {need} path points")
            if part.shape == "fixture" and part.fixture not in FIXTURES:
                raise ValueError(f"{part.id}: fixture must be one of {FIXTURES}")
            if part.shape == "cad" and not (part.cad_brief or "").strip():
                raise ValueError(f"{part.id}: cad part needs a cad_brief")
        return self


# ------------------------------------------------------------ expressions

_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def expr_names(expr: str) -> set[str]:
    return {n.id for n in ast.walk(_parse(expr)) if isinstance(n, ast.Name)
            and n.id not in ("min", "max")}


def _parse(expr: str) -> ast.Expression:
    if len(expr) > 240:
        raise ValueError(f"expression too long: {expr[:40]}…")
    try:
        return ast.parse(expr.strip() or "0", mode="eval")
    except SyntaxError as e:
        raise ValueError(f"bad expression {expr!r}") from e


def evaluate(expr: str, env: dict[str, float]) -> float:
    """Whitelisted arithmetic: numbers, names, + - * /, unary -, min, max."""

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise ValueError(f"unknown parameter {n.id!r} in {expr!r}")
            return env[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, _OPS):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if b == 0:
                raise ValueError(f"division by zero in {expr!r}")
            return a / b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in ("min", "max") and n.args and not n.keywords:
            vals = [ev(a) for a in n.args]
            return min(vals) if n.func.id == "min" else max(vals)
        raise ValueError(f"disallowed syntax in {expr!r}")

    return ev(_parse(expr))


# ------------------------------------------------------------ compile

def _provenance(expr: str, by_name: dict[str, ProgParam]) -> tuple[Provenance, str | None]:
    """Provenance of a computed value: worst of its non-datum inputs.
    A bare literal is an assumption (nothing on the drawing backs it)."""
    names = expr_names(expr)
    inputs = [by_name[n] for n in names if by_name[n].kind != "datum"]
    if not inputs:
        datums = [by_name[n] for n in names]
        if datums and all(d.provenance == Provenance.DRAWING for d in datums):
            return Provenance.DRAWING, None
        return Provenance.ASSUMPTION, None
    worst = max(inputs, key=lambda p: _RANK[p.provenance]).provenance
    quotes = [p.source_quote for p in inputs
              if p.provenance == Provenance.DRAWING and p.source_quote]
    return worst, (" · ".join(dict.fromkeys(quotes)) or None)


def _param(name: str, expr: str, env: dict[str, float],
           by_name: dict[str, ProgParam], unit: str = "mm") -> Param:
    value = evaluate(expr, env)
    prov, quote = _provenance(expr, by_name)
    bare = expr.strip()
    note = None if bare in by_name or _is_number(bare) else f"= {bare}"
    if bare in by_name and by_name[bare].note:
        note = by_name[bare].note
    kind = by_name[bare].kind if bare in by_name else None
    if kind in ("min", "max"):
        note = f"{kind.upper()}: {note or bare}"
    return Param(name=name, value=round(value, 4), unit=unit, provenance=prov,
                 source_quote=quote, note=note)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


COLORS = {"membrane": "#3b82f6", "waterstop": "#9ca3af", "tile": "#d6cfc2",
          "screed": "#6b6964", "adhesive": "#d9d4ca", "wall": "#8f8d87",
          "slab": "#8f8d87", "substrate": "#8f8d87", "sealant": "#4c5158"}


def compile_program(p: DetailProgram) -> DetailSpec:
    by_name = {q.name: q for q in p.params}
    env = {q.name: q.value for q in p.params}
    comps: list[Component] = []
    max_z = 0.0
    for part in p.parts:
        params: list[Param] = []
        for d in part.dims:
            unit = "ratio" if d.name == "fall_ratio" else "mm"
            name = "thickness_physical" if d.name == "thickness" else d.name
            params.append(_param(name, d.expr, env, by_name, unit))
            if d.name == "z1":
                max_z = max(max_z, params[-1].value)
        if part.shape == "sheet":
            t = next(x for x in params if x.name == "thickness_physical")
            params.append(Param(name="thickness_display", value=max(t.value, 4.0),
                                provenance=Provenance.ASSUMPTION,
                                note="display thickness for visibility"))
        path = []
        for pt in part.path:
            coords = [_param("x", pt.x or "0", env, by_name),
                      _param("y", pt.y or "0", env, by_name)]
            if pt.z:
                coords.append(_param("z", pt.z, env, by_name))
            worse = max((c.provenance for c in coords), key=lambda v: _RANK[v])
            path.append(PathPoint(x=coords[0].value, y=coords[1].value,
                                  z=coords[2].value if len(coords) > 2 else None,
                                  provenance=worse))
        rep = {"sheet": "sheet", "annotation": "annotation"}.get(part.shape, "solid")
        color = next((c for k, c in COLORS.items() if k in part.type), "#9aa3ad")
        comps.append(Component(
            id=part.id, type=part.type, source_label=part.label,
            representation=rep,  # type: ignore[arg-type]
            generator=GeneratorRef(
                id=f"fixture_{part.fixture}" if part.shape == "fixture"
                else GENERATOR[part.shape], version=GEN),
            params=params, path=path,
            product=ProductRef(note="unresolved: no system selected"),
            install_after=[a for a in part.install_after if a != part.id],
            color=color,
            note=(f"AI-modelled in CadQuery (sandboxed, illustrative): {part.cad_brief}"
                  + (f" — {part.note}" if part.note else ""))
            if part.shape == "cad" else part.note))
    drawing = [q.model_dump(mode="json") for q in p.params
               if q.provenance == Provenance.DRAWING and q.kind != "datum"]
    for q in drawing:
        q["used_by"] = sorted(
            {part.id for part in p.parts
             for e in [d.expr for d in part.dims]
             + [c for pt in part.path for c in (pt.x, pt.y, pt.z) if c]
             if q["name"] in expr_names(e)})
    return DetailSpec(
        detail_id=p.detail_id, title=p.title, origin=p.origin, source=p.source,
        context={"program_version": p.version, "kind": p.kind,
                 "situation": p.situation,
                 "illustrative_extent_z_mm": max_z or 600,
                 "illustrative_extent_provenance": "assumption",
                 "drawing_params": drawing, "contact_checks": []},
        components=comps, relations=p.relations, unresolved=p.unresolved)
