"""V3 authoring grammar; legacy fixture programs remain readable separately."""
from __future__ import annotations

import copy
import math
import ast
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .generate import gate, _norm, _numbers
from .program import DetailProgram, ProgPart, ProgParam, compile_program, expr_names
from .spec import Provenance


def expression_unit(expression, units):
    """Reject hidden physical literals and incompatible arithmetic in v3."""
    def infer(node):
        if isinstance(node, ast.Expression):
            return infer(node.body)
        if isinstance(node, ast.Name):
            return units[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return "zero" if node.value == 0 else "ratio"
        if isinstance(node, ast.UnaryOp):
            return infer(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max"):
            found = {infer(arg) for arg in node.args} - {"zero"}
            if len(found) > 1:
                raise ValueError("min/max arguments have incompatible units")
            return next(iter(found), "zero")
        if isinstance(node, ast.BinOp):
            left, right = infer(node.left), infer(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if left == "zero":
                    return right
                if right == "zero":
                    return left
                if left == right:
                    return left
                raise ValueError("physical offsets must be named values with matching units")
            if isinstance(node.op, ast.Mult):
                if left == "mm" and right == "mm":
                    raise ValueError("area expressions cannot supply a length")
                return "mm" if "mm" in (left, right) else "ratio"
            if isinstance(node.op, ast.Div):
                if right == "mm":
                    if left != "mm":
                        raise ValueError("inverse lengths are unsupported")
                    return "ratio"
                return left
        raise ValueError("unsupported dimensional expression")
    try:
        return infer(ast.parse(expression, mode="eval"))
    except (SyntaxError, KeyError) as error:
        raise ValueError(f"invalid dimensional expression: {expression!r}") from error


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["extent", "hole_diameter", "face_distance", "wall_thickness", "radius", "angle", "slope_ratio"]
    selector: dict
    expected_expr: str
    source_dimension_id: str | None = None
    comparison: Literal["eq", "min", "max"] = "eq"
    unit: Literal["mm", "ratio", "deg"] = "mm"
    tolerance: float = Field(default=0.01, ge=0, le=0.1)

    @model_validator(mode="after")
    def measurement_units(self):
        expected_unit = "ratio" if self.kind == "slope_ratio" else "deg" if self.kind == "angle" else "mm"
        if self.unit != expected_unit:
            raise ValueError(f"{self.kind} measurements require unit={expected_unit}")
        self.tolerance = min(self.tolerance, 1e-6 if self.unit == "ratio" else .1)
        return self


class NativePart(ProgPart):
    shape: Literal["box", "sheet", "post", "rod", "wedge", "fillet", "profile", "revolve", "sweep", "cad", "annotation"]
    # zone: drawn but not built (clearance, circulation space, set-out line,
    # datum). An exact primitive, shown see-through, never in a physical check.
    role: Literal["substrate", "covered_layer", "finish", "object", "zone"]
    # Objects only. shaped: the drawing shows a form beyond its envelope
    # (openings, cavities, bowls, flanges, legs, profiles), so the measured
    # solid may not fill its box. block: the manufactured item really is a
    # rectangular block. Declared by the author; checks never guess it from names.
    form: Literal["shaped", "block"] | None = None
    param_bindings: dict[str, str] = Field(default_factory=dict)
    features: list[Feature] = Field(default_factory=list)
    placement: dict[str, str] = Field(default_factory=lambda: {"x": "0", "y": "0", "z": "0"})


class NativeParam(ProgParam):
    source_dimension_id: str | None = None


class NativeRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    kind: Literal["supported_by", "bonded_to", "covers", "laps_over", "terminates_against", "sealed_to", "installed_before",
                  # ontology/relations.yaml kinds; the verifier reports them UNVERIFIED until it measures them
                  "hosted_in", "fixed_to", "abuts", "repeated_along"]
    constraints: dict = Field(default_factory=dict)


class NativeProgram(DetailProgram):
    version: Literal["3.0"] = "3.0"
    params: list[NativeParam]
    parts: list[NativePart]
    relations: list[NativeRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def physical_contract(self):
        names = {p.name for p in self.params}
        units = {p.name: p.unit for p in self.params}
        ids = {p.id for p in self.parts}
        if any(not math.isfinite(p.value) for p in self.params):
            raise ValueError("parameters must be finite")
        for part in self.parts:
            if part.fixture:
                raise ValueError("fixtures cannot be authored in v3")
            if part.role == "object" and part.shape != "cad":
                raise ValueError(f"{part.id}: objects require parametric CAD")
            if part.role != "object" and part.shape == "cad":
                raise ValueError(f"{part.id}: layers/substrates use exact primitives, not CAD")
            if part.form and part.role != "object":
                raise ValueError(f"{part.id}: form is declared only on objects")
            if part.role == "zone" and part.shape not in ("box", "sheet", "wedge", "profile"):
                raise ValueError(f"{part.id}: a zone is an exact primitive (box, sheet, wedge or profile)")
            if len({f.id for f in part.features}) != len(part.features):
                raise ValueError(f"{part.id}: duplicate feature IDs")
            for feature in part.features:
                other = feature.selector.get("other_part")
                if other and other not in ids:
                    raise ValueError(f"{part.id}: feature refers to unknown part")
            for expression in list(part.param_bindings.values()) + [f.expected_expr for f in part.features]:
                if not expr_names(expression) <= names:
                    raise ValueError(f"{part.id}: unknown parameter binding")
            for dimension in part.dims:
                want = "ratio" if dimension.name == "fall_ratio" else "mm"
                if expression_unit(dimension.expr, units) not in (want, "zero"):
                    raise ValueError(f"{part.id}.{dimension.name}: use a named {want} value")
            for point in part.path:
                for expression in (point.x, point.y, point.z):
                    if expression and expression_unit(expression, units) not in ("mm", "zero"):
                        raise ValueError(f"{part.id}: path coordinates require named lengths")
        return self


def normalize_identifiers(raw):
    def ident(value):
        value = re.sub(r"[^a-z0-9_]","_",value.lower())[:48] or "part"
        return value if value[0].isalpha() else "p_"+value
    names = {p["name"]:ident(p["name"]) for p in raw["params"]}
    parts = {p["id"]:ident(p["id"]) for p in raw["parts"]}
    class Rename(ast.NodeTransformer):
        def visit_Name(self,node):
            return ast.copy_location(ast.Name(id=names.get(node.id,node.id),ctx=node.ctx),node)
    def expression(value):
        try:
            return ast.unparse(Rename().visit(ast.parse(value,mode="eval"))) if value else value
        except SyntaxError as error:
            raise ValueError("invalid dimensional expression: "+str(value)) from error
    unique = {}
    for param in raw["params"]:
        param["name"] = names[param["name"]]
        if param["name"] in ("min","max"):
            raise ValueError("min/max are reserved expression function names")
        if param["name"] in unique and unique[param["name"]] != param:
            raise ValueError("conflicting parameter definitions after normalization: "+param["name"])
        unique[param["name"]] = param
    raw["params"] = list(unique.values())
    for part in raw["parts"]:
        part["id"] = parts[part["id"]]
        part["install_after"] = [parts.get(key,key) for key in part["install_after"]]
        for dim in part["dims"]:
            dim["expr"] = expression(dim["expr"])
        for point in part["path"]:
            for key,value in point.items():
                point[key] = expression(value)
        part["param_bindings"] = {key:expression(value) for key,value in part["param_bindings"].items()}
        part["placement"] = {key:expression(value) for key,value in part["placement"].items()}
        for feature in part["features"]:
            feature["expected_expr"] = expression(feature["expected_expr"])
            selector = feature["selector"]
            if selector.get("other_part"):
                selector["other_part"] = parts.get(selector["other_part"],selector["other_part"])
            if selector.get("center"):
                selector["center"] = [expression(str(value)) for value in selector["center"]]
    for relation in raw.get("relations",[]):
        for key in ("source","target"):
            relation[key] = parts.get(relation[key],relation[key])
    return raw


def kept_bindings(parent_program, parent_bindings, program):
    """The parent's CAD bindings that still describe `program`: the part keeps
    its CAD contract and every value its component reads is unchanged. Any
    other object is rebuilt by the next CAD call."""
    old_parts = {p["id"]: p for p in parent_program.get("parts", [])}
    old_env = {p["name"]: p["value"] for p in parent_program.get("params", [])}
    new_env = {p.name: p.value for p in program.params}
    contract = ("shape", "type", "role", "cad_brief", "param_bindings", "features")
    kept = {}
    for part in program.parts:
        if part.id not in parent_bindings:
            continue
        old, current = old_parts.get(part.id, {}), part.model_dump(mode="json")
        expressions = list(part.param_bindings.values()) + [f.expected_expr for f in part.features]
        referenced = set().union(*(expr_names(e) for e in expressions)) if expressions else set()
        if (all(current.get(k) == old.get(k) for k in contract)
                and all(old_env.get(k) == new_env.get(k) for k in referenced)):
            kept[part.id] = parent_bindings[part.id]
    return kept


def check_roles(program, read, human=False):
    """Roles follow the drawing reading, never a vocabulary of our own.

    The READ pass classifies each printed label as a layer, a manufactured
    object or other. A part carrying that label must agree; unclassified
    labels (older reads) are not checked. Every object the agent authors
    declares its form; a reviewer's value correction does not have to."""
    category = {_norm(e["label_verbatim"]): e.get("category") for e in read.get("elements", [])
                if e.get("label_verbatim")}
    for part in program.parts:
        found = category.get(_norm(part.label or ""))
        if found == "object" and part.role != "object":
            raise ValueError(f"{part.id}: the drawing reading classifies {part.label!r} as a manufactured "
                             "object; keep the label and change the part to role=object with parametric CAD")
        if found == "layer" and part.role in ("object", "zone"):
            raise ValueError(f"{part.id}: the drawing reading classifies {part.label!r} as a layer; keep the "
                             "label and change the part to an exact primitive with role substrate, covered_layer or finish")
        if found == "zone" and part.role != "zone":
            raise ValueError(f"{part.id}: the drawing reading classifies {part.label!r} as a zone that is not "
                             "built (a clearance, set-out line or datum); keep the label and use role=zone")
        if part.role == "object" and part.form is None and not human:
            raise ValueError(f"{part.id}: objects declare form=shaped or form=block")
    zones = {part.id for part in program.parts if part.role == "zone"}
    for part in program.parts:
        if zones & set(part.install_after) or (part.id in zones and part.install_after):
            raise ValueError(f"{part.id}: zones are not installed; leave them out of install_after")
    for relation in program.relations:
        if {relation.source, relation.target} & zones:
            raise ValueError(f"{relation.source}->{relation.target}: a zone is not built, so it has no physical "
                             "relations; describe clearances with features or a note instead")
        # Seen 29 Oct: min_coverage 90 meant 90%, and every relation FAILed at
        # full coverage. Reject it rather than guess the unit. (Authoring only:
        # stored versions keep loading.)
        coverage = relation.constraints.get("min_coverage")
        if coverage is not None and not 0 <= coverage <= 1:
            raise ValueError(f"{relation.source}->{relation.target}: min_coverage is a fraction from 0 to 1 "
                             f"(0.9 means 90%), not {coverage:g}")


def _slot_claimed(inferred_by):
    """The slot an inferred_by string names: the last segment of
    "<kind>:<id>@<version>:<slot>"; None when it has no such shape."""
    pieces = (inferred_by or "").split(":")
    return pieces[2] if len(pieces) == 3 and all(pieces) and "@" in pieces[1] else None


def check_provenance(program, base=None, human=False):
    """Part-level provenance is a claim about the drawing, gated like a value.

    `inferred` says the drawing does not show the part and a required slot of
    the program's assembly class calls for it. The claim is valid only when
    `inferred_by` names such a slot, the part carries a class the slot takes,
    and no drawing or reviewer-confirmed part already fills it. An invalid
    claim is rejected with its reason, never relabelled: calling a hidden part
    `drawing` would say the drawing shows it. `user_input` is a reviewer's
    confirmation, which an agent can neither grant nor withdraw."""
    previous = {p["id"]: p for p in (base or {}).get("parts", [])}
    if not human:
        for part in program.parts:
            before = previous.get(part.id, {}).get("provenance", "drawing")
            if part.provenance == "user_input" and before != "user_input":
                raise ValueError(f"{part.id}: only a reviewer can confirm a part (provenance user_input)")
            if part.provenance == "drawing" and before != "drawing":
                raise ValueError(f"{part.id} was {before}; calling it drawing would claim the drawing shows it. "
                                 "Keep the provenance, or leave the part for a reviewer to confirm")
        for ident, old in previous.items():
            if old.get("provenance") == "user_input" and not any(
                    p.id == ident and p.provenance == "user_input" for p in program.parts):
                raise ValueError(f"{ident} is a part a reviewer confirmed; keep it and its provenance unchanged. "
                                 "Only a reviewer can change it.")
    for part in program.parts:
        if part.provenance == "drawing" and part.inferred_by:
            raise ValueError(f"{part.id}: inferred_by is set but provenance is drawing; a part the drawing shows was not inferred")
    inferred = [p for p in program.parts if p.provenance == "inferred"]
    if not inferred:
        return
    from . import ontology
    known = ontology.load()
    archetype = known.assembly(program.assembly_class) if program.assembly_class else None
    if archetype is None:
        raise ValueError("an inferred part needs assembly_class set to the archetype whose required slot it fills; "
                         "choose one of: " + ", ".join(known.assembly_ids()))
    required = {s.id: s for s in known.required_slots(archetype.id)}
    for part in inferred:
        slot = required.get(_slot_claimed(part.inferred_by) or "")
        kind, _, rest = (part.inferred_by or "").partition(":")
        if slot is None or (kind == "archetype" and rest.split("@")[0] != archetype.id):
            raise ValueError(f"{part.id}: inferred_by must name a required slot of {archetype.id} as "
                             f"archetype:{archetype.id}@{known.version}:<slot>; required slots: " + ", ".join(required))
        if part.class_id not in slot.classes:
            raise ValueError(f"{part.id}: slot {slot.id!r} takes class_id {list(slot.classes)}, not {part.class_id!r}")
        shown = [p.id for p in program.parts if p.provenance != "inferred" and p.class_id in slot.classes]
        if len(shown) >= (slot.max if slot.max is not None else slot.min):
            raise ValueError(f"{part.id}: slot {slot.id!r} is already filled by {shown}, which the drawing shows or a "
                             "reviewer confirmed; an inferred part cannot duplicate it")


def prepare(raw, source, detail_id, read, base=None, human=False):
    raw = copy.deepcopy(raw)
    raw["version"] = "3.0"
    raw["detail_id"] = detail_id
    raw["source"] = source
    raw["origin"] = "ai-generated:v3 (unreviewed)" if not human else "user-revised:v3"
    for part in raw.get("parts", []):
        if part.get("role") != "object":
            part["form"] = None  # form describes objects only; a layer's is ignored
        if part.get("provenance", "drawing") != "inferred":
            part["confidence"] = None  # existence confidence belongs to inferred parts only
    raw["parts"] = [NativePart.model_validate(p).model_dump(mode="json") for p in raw.get("parts", [])]
    raw["params"] = [NativeParam.model_validate(p).model_dump(mode="json") for p in raw.get("params", [])]
    raw = normalize_identifiers(raw)
    previous = {p["name"]: p for p in (base or {}).get("params", [])}
    source_dimensions = {item.get("id", f"d{i}"): item for i, item in enumerate(read.get("dimensions", []))}
    native_notes = []
    for param in raw.get("params", []):
        old = previous.get(param["name"])
        if human and old and param["value"] != old["value"]:
            param["provenance"] = "user_input"
        elif param["provenance"] in ("user_input", "manufacturer_doc") and (
                not old or (param["value"], param["provenance"]) != (old["value"], old["provenance"])):
            raise ValueError("an agent cannot manufacture user or manufacturer evidence")
        if param["provenance"] == "drawing":
            evidence = source_dimensions.get(param.get("source_dimension_id"))
            quote = " ".join((param.get("source_quote") or "").lower().split())
            if (not evidence or abs(param["value"]-evidence["value_mm"]) > 1e-6 or
                    not quote or quote not in " ".join(evidence["quote_verbatim"].lower().split()) or
                    not any(abs(n-evidence["value_mm"]) <= 1e-6 for n in _numbers(evidence["quote_verbatim"]))):
                param["provenance"] = "assumption"
                param["note"] = ((param.get("note") or "") + " [gate: no matching READ dimension/value/quote]").strip()
                native_notes.append(f"{param['name']}: no exact READ dimension match -> assumption")
            elif re.search(r"\bmin(?:imum)?\b|at least|not less than", evidence["quote_verbatim"].lower()):
                param["kind"] = "min"
            elif re.search(r"\bmax(?:imum)?\b|at most|not more than", evidence["quote_verbatim"].lower()):
                param["kind"] = "max"
    if not human:
        # Nor can it overwrite, relabel or drop one: those values come from a
        # reviewer or a manufacturer, and only a reviewer changes them.
        current = {p["name"]: p for p in raw.get("params", [])}
        for name, old in previous.items():
            new = current.get(name)
            if old["provenance"] in ("user_input", "manufacturer_doc") and (
                    not new or (new["value"], new["provenance"]) != (old["value"], old["provenance"])):
                raise ValueError(f"{name} is a reviewer's value ({old['value']:g}, {old['provenance']}); "
                                 "keep it and its provenance unchanged. Only a reviewer can change it.")
    for part in raw["parts"]:
        for feature in part["features"]:
            evidence = source_dimensions.get(feature.get("source_dimension_id"))
            if evidence:
                text = evidence["quote_verbatim"].lower()
                minimum = bool(re.search(r"\bmin(?:imum)?\b|at least|not less than", text))
                maximum = bool(re.search(r"\bmax(?:imum)?\b|at most|not more than", text))
                feature["comparison"] = "min" if minimum and not maximum else "max" if maximum and not minimum else "eq"
    # Apply the original quote/label gates without its illustrative-only CAD cap.
    cad = {part["id"] for part in raw.get("parts", []) if part["shape"] == "cad"}
    for part in raw.get("parts", []):
        if part["id"] in cad:
            part["shape"] = "box"
    gated, notes = gate(raw, read)
    notes = native_notes + notes
    for part in gated["parts"]:
        if part["id"] in cad:
            original = next(p for p in raw["parts"] if p["id"] == part["id"])
            part["shape"] = "cad"
            part["cad_brief"] = original.get("cad_brief") or original.get("type")
    program = NativeProgram.model_validate(gated)
    check_roles(program, read, human)
    check_provenance(program, base, human)
    compiled_input = program.model_dump(mode="json")
    compiled_input["version"] = "2.0"
    spec = compile_program(DetailProgram.model_validate(compiled_input))
    if any(not math.isfinite(p.value) for c in spec.components for p in c.params):
        raise ValueError("compiled dimensions must be finite")
    spec.context["program_version"] = "3.0"
    for part, component in zip(program.parts, spec.components):
        if part.role == "zone":
            component.representation = "zone"
    for parameter in spec.context.get("drawing_params", []):
        for part in program.parts:
            expressions = list(part.param_bindings.values()) + list(part.placement.values())
            if any(parameter["name"] in expr_names(expression) for expression in expressions):
                if part.id not in parameter["used_by"]:
                    parameter["used_by"].append(part.id)
    # A printed number is not evidence for every unrelated dimension that
    # happens to reuse it. Only the declared feature owner/peer inherits its
    # drawing provenance; other applications remain explicit assumptions.
    owners = {}
    for part in program.parts:
        for feature in part.features:
            if feature.source_dimension_id:
                owners.setdefault(feature.source_dimension_id, set()).add(part.id)
                if feature.selector.get("other_part"):
                    owners[feature.source_dimension_id].add(feature.selector["other_part"])
    drawing = {p.name: p for p in program.params if p.provenance == Provenance.DRAWING and p.kind != "datum"}
    for part, component in zip(program.parts, spec.components):
        for dimension in part.dims:
            source_names = expr_names(dimension.expr) & drawing.keys()
            if any(part.id not in owners.get(drawing[name].source_dimension_id, set()) for name in source_names):
                name = "thickness_physical" if dimension.name == "thickness" else dimension.name
                parameter = component.param(name)
                parameter.provenance = Provenance.ASSUMPTION
                parameter.note = ((parameter.note or "") + " [source value application to this part is unconfirmed]").strip()
                notes.append(f"{part.id}.{name}: source application unconfirmed -> assumption")
    from .geometry import build_assembly
    preflight = spec.model_copy(deep=True)
    for component in preflight.components:
        if component.generator.id == "cad_mesh":
            component.generator.id = "box"
    meshes = build_assembly(preflight)
    if not any(mesh.tri_count for mesh in meshes.values()):
        raise ValueError("program produced no drawable geometry")
    return program, spec, notes
