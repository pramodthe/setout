"""Canonical component specification, version 1.

One schema drives generators, viewer, verification and evaluation.
Millimetres in the spec; conversion to viewer/GLB units is explicit
(see app/units.py) and tested.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SPEC_VERSION = "1.0"


class Provenance(str, Enum):
    DRAWING = "drawing"  # value read from the source drawing
    MANUFACTURER_DOC = "manufacturer_doc"  # value from a manufacturer document
    USER_INPUT = "user_input"  # explicit user correction/input
    # A value derived from a part a model or archetype filled in (the drawing
    # does not show it). Shown ghosted; never counts as verified.
    INFERRED = "inferred"
    ASSUMPTION = "assumption"  # modelling assumption; never counts as verified


# Part-level provenance: "drawing" when the drawing shows the part, "inferred"
# when a model or an archetype slot filled it in (ghosted, never a verified
# PASS), "user_input" once a reviewer confirms an inferred part.
PartProvenance = Literal["drawing", "inferred", "user_input"]


class ParamRole(str, Enum):
    PHYSICAL = "physical"  # real-world dimension of the component
    ILLUSTRATIVE = "illustrative"  # viewing-only extent, excluded from accuracy
    DISPLAY = "display"  # display exaggeration (e.g. membrane drawn thicker)


class Param(BaseModel):
    name: str
    value: float
    unit: str = "mm"
    provenance: Provenance
    role: ParamRole = ParamRole.PHYSICAL
    source_quote: str | None = None  # exact drawing/doc wording, if any
    tolerance_mm: float | None = None  # per-parameter check tolerance
    note: str | None = None


class GeneratorRef(BaseModel):
    id: str  # closed set, see app.geometry.REGISTRY
    version: str


class ProductRef(BaseModel):
    ref: str | None = None  # manufacturer product code, or null unresolved
    system: str | None = None
    note: str | None = None


class SourceRegion(BaseModel):
    """Indicative region on a source crop (fractional 0..1 coords)."""

    crop_id: str
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 1.0
    y1: float = 1.0
    kind: Literal["indicative", "sam_mask", "human_box"] = "indicative"


class PathPoint(BaseModel):
    """One vertex of a sheet/generator profile path (section plane, mm)."""

    x: float
    y: float
    z: float | None = None  # only for 3D paths (sweep)
    provenance: Provenance = Provenance.ASSUMPTION
    note: str | None = None


class Component(BaseModel):
    id: str
    type: str  # normalised type, e.g. "membrane", "sealant_bead"
    class_id: str | None = None  # ontology component class (app/ontology.py), once classified
    source_label: str | None = None  # EXACT drawing label; null when none
    # zone: drawn but not built (a clearance, set-out line or datum); shown
    # see-through and never treated as a physical part.
    representation: Literal["solid", "sheet", "coating", "annotation", "zone"]
    generator: GeneratorRef
    params: list[Param] = Field(default_factory=list)
    product: ProductRef | None = None
    path: list[PathPoint] = Field(default_factory=list)
    install_after: list[str] = Field(default_factory=list)
    source_regions: list[SourceRegion] = Field(default_factory=list)
    color: str = "#999999"
    note: str | None = None
    provenance: PartProvenance = "drawing"
    confidence: float | None = Field(default=None, ge=0, le=1)  # existence; inferred parts only, None = unmeasured
    inferred_by: str | None = None  # "archetype:<assembly>@<ontology version>:<slot>" or "model:<id>@<version>:<slot>"

    def param(self, name: str) -> Param:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(f"{self.id} has no param {name!r}")


class Relation(BaseModel):
    source: str
    target: str
    kind: Literal[
        "supported_by",
        "bonded_to",
        "covers",
        "laps_over",
        "terminates_against",
        "sealed_to",
        "installed_before",
        # Detail world model kinds (ontology/relations.yaml). Reported
        # UNVERIFIED by the verifier until it has a geometric check for them.
        "hosted_in",
        "fixed_to",
        "abuts",
        "repeated_along",
    ]


class Unresolved(BaseModel):
    id: str
    description: str
    impacts: list[str] = Field(default_factory=list)  # component ids or "build"
    blocks_geometry: bool = False


class SourceDoc(BaseModel):
    filename: str
    sha256: str
    page_index: int
    crop_id: str
    page_width_pt: float
    page_height_pt: float
    rotation_deg: float = 0.0
    coord_note: str = (
        "Section plane: +x toward exterior, +y up, origin at interior FFL "
        "on the step face. +z along the sill (illustrative extent)."
    )


class CheckResult(BaseModel):
    id: str
    stage: Literal["A", "B", "C"]
    description: str
    status: Literal["PASS", "FAIL", "UNVERIFIED", "N/A"]
    expected: Any = None
    actual: Any = None
    tolerance: Any = None
    evidence: str | None = None


class DetailSpec(BaseModel):
    spec_version: str = SPEC_VERSION
    detail_id: str
    title: str
    origin: str  # e.g. "human-transcription-v1" — never claim model output
    source: SourceDoc
    units: Literal["mm"] = "mm"
    context: dict[str, Any] = Field(default_factory=dict)
    components: list[Component] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)
    verification: list[CheckResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> DetailSpec:
        ids = [c.id for c in self.components]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate component ids: {dupes}")
        known = set(ids)
        for c in self.components:
            for dep in c.install_after:
                if dep not in known:
                    raise ValueError(f"{c.id}.install_after -> unknown {dep!r}")
            if c.id in c.install_after:
                raise ValueError(f"{c.id} depends on itself")
        for r in self.relations:
            if r.source not in known:
                raise ValueError(f"relation source unknown: {r.source!r}")
            if r.target not in known:
                raise ValueError(f"relation target unknown: {r.target!r}")
        for u in self.unresolved:
            for imp in u.impacts:
                if imp != "build" and imp not in known:
                    raise ValueError(f"unresolved {u.id} impacts unknown {imp!r}")
        cycle = find_install_cycle(self)
        if cycle:
            raise ValueError(f"install dependency cycle: {' -> '.join(cycle)}")
        return self

    def component(self, cid: str) -> Component:
        for c in self.components:
            if c.id == cid:
                return c
        raise KeyError(f"no component {cid!r}")

    def install_order(self) -> list[str]:
        """Topological order of component ids (dependencies first)."""
        order: list[str] = []
        visited: dict[str, int] = {}  # 0=unseen 1=open 2=done

        def visit(cid: str) -> None:
            state = visited.get(cid, 0)
            if state == 2:
                return
            visited[cid] = 1
            for dep in self.component(cid).install_after:
                visit(dep)
            visited[cid] = 2
            order.append(cid)

        for c in self.components:
            visit(c.id)
        return order


def find_install_cycle(spec: DetailSpec) -> list[str] | None:
    """Return a dependency cycle path if install_after has one, else None."""
    by_id = {c.id: c for c in spec.components}
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(cid: str) -> list[str] | None:
        color[cid] = 1
        stack.append(cid)
        for dep in by_id[cid].install_after:
            if dep not in by_id:
                continue
            if color.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if color.get(dep, 0) == 0:
                hit = visit(dep)
                if hit:
                    return hit
        stack.pop()
        color[cid] = 2
        return None

    for c in spec.components:
        if color.get(c.id, 0) == 0:
            hit = visit(c.id)
            if hit:
                return hit
    return None
