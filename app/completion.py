"""Archetype completion: fill the required slots a drawing leaves empty.

M1 of the detail world model, with no learning. For the program's assembly
class, every required slot with too few parts is filled from the archetype.
A new part carries provenance "inferred" (ghosted, never a verified PASS),
confidence None (unmeasured in M1) and inferred_by
"archetype:<assembly>@<ontology version>:<slot>". It is an exact box even when
its class is an object: a placeholder has no form to model, spends no CAD call
and cannot PASS. Every size it needs is a named parameter with provenance
"assumption" and a note saying the drawing gave no value, unless its class
derives the size from a part already in the program. Placement is written as
expressions over the program's own parameters, anchored on the parts filling
the slot's anchor: side members stand on the anchor's top face at the ends of
its run, the top member spans the side members, the infill fills between them.
A coordinate too long to stay an expression becomes a named assumption whose
note says it is a placeholder position. A slot that cannot be placed safely
becomes a suggestion with the reason, never a guessed part.

Nothing here names a part. It reads slots, classes, placement families and
dimension slots from the ontology (library data), so a new trade is new
YAML, not new code.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field

from .generate import _norm
from .native_program import NativeParam, NativePart, NativeProgram, NativeRelation
from .ontology import Ontology
from .program import evaluate

MEASURES = ("depth", "width", "height", "top", "bottom", "x0", "x1", "z0", "z1")
# Characters. A longer coordinate becomes a named value: two of them must still
# fit one expression (a span x1 - x0) within the gate's limit of 240.
MAX_COORDINATE = 110
# The dimension slots each placement reads from the part's class.
NEEDS = {("fill_opening", "side"): ("width", "depth", "height"),
         ("fill_opening", "top"): ("height", "depth"),
         ("hosted_in_frame", "infill"): ("thickness",)}
ORDER = {None: 0, "side": 1, "top": 2, "infill": 3}  # anchors are placed before what rests on them
BEARING = {"axis": "y", "source_face": "min", "target_face": "max", "min_gap": 0, "max_gap": 0, "min_coverage": 1.0}
_SIMPLE = re.compile(r"^[a-z_][a-z0-9_]*$|^\d+(\.\d+)?$")


@dataclass
class Suggestion:
    slot: str
    classes: tuple[str, ...]
    reason: str  # why it could not be filled safely
    confidence: float | None = None


@dataclass
class Completion:
    parts: list[NativePart] = field(default_factory=list)  # provenance="inferred", confidence, inferred_by set
    params: list[NativeParam] = field(default_factory=list)  # provenance "assumption" (note: no source) unless derived
    relations: list[NativeRelation] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)  # required slots it could not fill
    ontology_version: str = ""

    def as_json(self):
        return {"parts": [p.model_dump(mode="json") for p in self.parts],
                "params": [p.model_dump(mode="json") for p in self.params],
                "relations": [r.model_dump(mode="json") for r in self.relations],
                "suggestions": [asdict(s) for s in self.suggestions],
                "ontology_version": self.ontology_version}


# ----------------------------------------------------------- expressions

def _wrap(expression):
    return expression if _SIMPLE.match(expression.strip()) else f"({expression.strip()})"


def _add(a, b):
    if a.strip() == "0":
        return b.strip()
    return a.strip() if b.strip() == "0" else f"{_wrap(a)} + {_wrap(b)}"


def _sub(a, b):
    return a.strip() if b.strip() == "0" else f"{_wrap(a)} - {_wrap(b)}"


def _extreme(function, expressions):
    """min/max over distinct expressions; a single one needs no call."""
    distinct = list(dict.fromkeys(e.strip() for e in expressions))
    return distinct[0] if len(distinct) == 1 else f"{function}({', '.join(distinct)})"


def _envelope(part):
    """A part's planar envelope as expressions, or None when its shape has
    no readable envelope (sheets, posts, rods, sweeps, revolves)."""
    dims = {d.name: d.expr for d in part.dims}
    if part.shape in ("box", "cad") and {"x0", "x1", "y0", "y1", "z0", "z1"} <= dims.keys():
        return {k: dims[k] for k in ("x0", "x1", "y0", "y1", "z0", "z1")}
    if part.shape == "wedge" and {"x0", "x1", "y_bottom", "y_top_x0", "z0", "z1"} <= dims.keys():
        return {"x0": dims["x0"], "x1": dims["x1"], "y0": dims["y_bottom"], "y1": dims["y_top_x0"],
                "z0": dims["z0"], "z1": dims["z1"]}
    if part.shape == "profile" and {"z0", "z1"} <= dims.keys() and len(part.path) >= 3:
        xs = [p.x or "0" for p in part.path]
        ys = [p.y or "0" for p in part.path]
        return {"x0": _extreme("min", xs), "x1": _extreme("max", xs), "y0": _extreme("min", ys), "y1": _extreme("max", ys),
                "z0": dims["z0"], "z1": dims["z1"]}
    return None


def _measure(envelope, measure):
    e = envelope
    spans = {"depth": ("x0", "x1"), "width": ("z0", "z1"), "height": ("y0", "y1")}
    if measure in spans:
        low, high = spans[measure]
        return _sub(e[high], e[low])
    return e[{"top": "y1", "bottom": "y0"}.get(measure, measure)]


def _value(expression, env):
    """A composed expression's number. Composing a real program's anchors can
    run past the authoring limit, which is exactly when it gets named."""
    return evaluate(expression, env, limit=len(expression))


def _centre(envelope, axis, env):
    return (_value(envelope[axis + "0"], env) + _value(envelope[axis + "1"], env)) / 2


# ---------------------------------------------------------------- filling

class _Filler:
    def __init__(self, program, ont, read):
        self.program, self.ont, self.read = program, ont, read
        self.archetype = ont.assembly(program.assembly_class)
        self.result = Completion(ontology_version=ont.version)
        self.parts = list(program.parts)  # existing parts plus what this completion adds
        self.env = {p.name: p.value for p in program.params}
        self.names = {p.name for p in program.params}
        self.ids = {p.id for p in program.parts}

    def fillers(self, slot):
        return [p for p in self.parts if p.class_id in slot.classes]

    def anchor(self, slot_id):
        """(part, envelope) a slot measures from: of the parts filling it, the
        one with the highest top, since what stands on the slot stands on that."""
        found = [(p, _envelope(p)) for p in self.fillers(self.archetype.slot(slot_id))]
        found = [(p, e) for p, e in found if e]
        return max(found, key=lambda item: _value(item[1]["y1"], self.env)) if found else None

    def members(self, anchor_id, position):
        """Parts of this assembly's slots that share the anchor and position."""
        found = []
        for slot in self.archetype.slots:
            if slot.anchor == anchor_id and slot.position == position:
                found += [p for p in self.fillers(slot) if p not in found]
        return found

    def suggest(self, slot, reason):
        self.result.suggestions.append(Suggestion(slot=slot.id, classes=slot.classes, reason=reason))

    def labelled(self, slot):
        """A READ label that names this slot: the drawing shows the part, so it
        must be authored from the drawing, not inferred."""
        carried = {_norm(p.label) for p in self.program.parts if p.label}
        aliases = [a for c in slot.classes for a in (self.ont.component(c).aliases if self.ont.component(c) else ())]
        for element in (self.read or {}).get("elements", []):
            label = _norm(element.get("label_verbatim", ""))
            if label and label not in carried and element.get("category") in ("layer", "object", None) and any(
                    re.search(r"(?<![a-z0-9])" + re.escape(_norm(alias)) + r"(?![a-z0-9])", label) for alias in aliases):
                return element["label_verbatim"]
        return None

    def unique_name(self, base):
        name, n = base, 1
        while name in self.names:
            n += 1
            name = f"{base}_{n}"
        self.names.add(name)
        return name

    def unique_id(self, base):
        ident, n = base, 1
        while ident in self.ids:
            n += 1
            ident = f"{base}_{n}"
        self.ids.add(ident)
        return ident

    def size(self, slot, cls, name):
        """The expression for one dimension slot: derived from a part already
        in the program when the class says so, else a named placeholder from
        the cited typical range. None when neither is possible."""
        dim = cls.dimension(name)
        if dim is None or dim.unit != "mm":
            return None, f"class {cls.id} declares no mm dimension slot {name!r}"
        if dim.derive_from:
            source_slot, _, measure = dim.derive_from.partition(".")
            target = self.archetype.slot(source_slot)
            if target is None or measure not in MEASURES:
                return None, f"{cls.id}.{name} derives from {dim.derive_from!r}, which names no slot measure"
            source = self.anchor(source_slot)
            if source is None:
                return None, f"{cls.id}.{name} derives from {dim.derive_from}, and no readable part fills {source_slot!r}"
            return _measure(source[1], measure), None
        if dim.typical_min is None:
            return None, f"{cls.id}.{name} has neither a derivation nor a cited typical range"
        value = round((dim.typical_min + dim.typical_max) / 2, 1)
        param = NativeParam(name=self.unique_name(f"{slot.id}_{name}"), value=value, unit="mm", provenance="assumption",
                            kind="exact", note=f"no source on this drawing (archetype placeholder): the {slot.id} {name} "
                            f"is the centre of the typical range {dim.typical_min:g}-{dim.typical_max:g} mm read at {dim.source}")
        self.result.params.append(param)
        self.env[param.name] = value
        return param.name, None

    def coordinate(self, expression, ident, name, anchor_part):
        """An expression short enough to stay linked to the anchor, or a named
        value in its place. The gate limits expressions to 240 characters and a
        chain of datums passes that quickly; a placeholder's position does not
        need the link, and its note says what it lost."""
        expression = expression.strip()
        if len(expression) <= MAX_COORDINATE:
            return expression
        value = round(_value(expression, self.env), 3)
        param = NativeParam(name=self.unique_name(f"{ident}_{name}"), value=value, unit="mm", provenance="assumption",
                            kind="exact", note=f"no source on this drawing: placeholder position of {ident} ({name}), "
                            f"derived from {anchor_part.id} when the slot was filled; it does not follow later changes")
        self.result.params.append(param)
        self.env[param.name] = value
        return param.name

    def sizes(self, slot, cls, needed):
        found = {}
        for name in needed:
            expression, problem = self.size(slot, cls, name)
            if problem:
                return None, problem
            found[name] = expression
        return found, None

    def envelopes(self, slot, anchor, sizes, need):
        """The new parts' envelopes for the slot's placement, with the parts
        each one rests on or sits between."""
        position = slot.position
        # A depth taken from the anchor's own depth ends on the anchor's face.
        x1 = anchor["x1"] if sizes.get("depth") == _measure(anchor, "depth") else _add(anchor["x0"], sizes.get("depth", "0"))
        if position == "side":
            taken = self.members(slot.anchor, "side")
            ends = ["low", "high"]
            for member in taken:
                envelope = _envelope(member)
                if envelope:
                    end = "low" if _centre(envelope, "z", self.env) < _centre(anchor, "z", self.env) else "high"
                    if end in ends:
                        ends.remove(end)
            output = []
            for end in ends[:need]:
                z0 = anchor["z0"] if end == "low" else _sub(anchor["z1"], sizes["width"])
                z1 = _add(anchor["z0"], sizes["width"]) if end == "low" else anchor["z1"]
                output.append(({"x0": anchor["x0"], "x1": x1, "y0": anchor["y1"],
                                "y1": _add(anchor["y1"], sizes["height"]), "z0": z0, "z1": z1}, []))
            return output, None
        sides = [(p, _envelope(p)) for p in self.members(slot.anchor, "side")]
        sides = [(p, e) for p, e in sides if e]
        if len(sides) < 2:
            return None, "the side members are not placed yet; the top member and the infill rest on them"
        sides.sort(key=lambda item: _centre(item[1], "z", self.env))
        if position == "top":
            y0 = _extreme("max", [e["y1"] for _, e in sides])
            return [({"x0": anchor["x0"], "x1": x1, "y0": y0, "y1": _add(y0, sizes["height"]),
                      "z0": _extreme("min", [e["z0"] for _, e in sides]),
                      "z1": _extreme("max", [e["z1"] for _, e in sides])}, [p for p, _ in sides])], None
        tops = [(p, _envelope(p)) for p in self.members(slot.anchor, "top")]
        tops = [(p, e) for p, e in tops if e]
        y1 = tops[0][1]["y0"] if tops else _extreme("min", [e["y1"] for _, e in sides])
        # Centred in the frame depth: a placement choice, not a dimension.
        x0 = _add(anchor["x0"], f"({_sub(_sub(anchor['x1'], anchor['x0']), sizes['thickness'])}) / 2")
        return [({"x0": x0, "x1": _add(x0, sizes["thickness"]), "y0": anchor["y1"], "y1": y1,
                  "z0": sides[0][1]["z1"], "z1": sides[-1][1]["z0"]}, [p for p, _ in sides] + [p for p, _ in tops])], None

    def fill(self, slot):
        need = slot.min - len(self.fillers(slot))
        if need <= 0:
            return
        label = self.labelled(slot)
        if label:
            return self.suggest(slot, f"the drawing labels {label!r}, which names this slot: author it from the drawing")
        drawing_type = (self.read or {}).get("drawing_type")
        if drawing_type not in (None, "section"):
            return self.suggest(slot, f"section_visibility describes sections; this is a {drawing_type} drawing")
        needed = NEEDS.get((slot.placement, slot.position))
        if needed is None or not slot.anchor:
            return self.suggest(slot, f"placement {slot.placement or 'none'} needs coordinates only the drawing or a reviewer gives")
        found = self.anchor(slot.anchor)
        if found is None:
            return self.suggest(slot, f"no readable part fills its anchor slot {slot.anchor!r}")
        anchor_part, anchor = found
        cls = self.ont.component(slot.classes[0])
        sizes, problem = self.sizes(slot, cls, needed)
        if problem:
            return self.suggest(slot, problem)
        envelopes, problem = self.envelopes(slot, anchor, sizes, need)
        if problem:
            return self.suggest(slot, problem)
        for i, (envelope, rests_on) in enumerate(envelopes):
            self.add(slot, cls, envelope, anchor_part, rests_on, i if need > 1 or len(envelopes) > 1 else None)

    def add(self, slot, cls, envelope, anchor_part, rests_on, index):
        ident = self.unique_id(slot.id if index is None else f"{slot.id}_{index + 1}")
        envelope = {name: self.coordinate(value, ident, name, anchor_part) for name, value in envelope.items()}
        tag = f"archetype:{self.archetype.id}@{self.ont.version}:{slot.id}"
        shown = {"cut": "a section across this assembly would cut it", "beyond": "it lies beyond the section plane",
                 "out_of_plane": "it is out of the section plane"}[slot.section_visibility]
        note = (f"inferred from the {self.archetype.id} archetype (slot {slot.id}): the drawing does not show it, {shown}. "
                "A box stands in for its shape and its sizes are named assumptions; confirm or correct in review.")
        is_object = cls.role == "object"
        part = NativePart(
            id=ident, type=cls.id, label=None, class_id=cls.id, shape="box", role=cls.role,
            form=(cls.form or "shaped") if is_object else None,
            dims=[{"name": k, "expr": v} for k, v in envelope.items()],
            install_after=[anchor_part.id] + [p.id for p in rests_on if p.id != anchor_part.id],
            note=note, provenance="inferred", confidence=None, inferred_by=tag)
        self.parts.append(part)
        self.result.parts.append(part)
        supports = rests_on or [anchor_part]
        if slot.position in ("side", "top") and "supported_by" in cls.relations_allowed:
            for other in supports:
                self.result.relations.append(NativeRelation(source=ident, target=other.id, kind="supported_by",
                                                            constraints=dict(BEARING)))
        if slot.position == "infill" and "hosted_in" in cls.relations_allowed:
            for other in rests_on:
                self.result.relations.append(NativeRelation(source=ident, target=other.id, kind="hosted_in"))


def archetype_completion(program: NativeProgram, ont: Ontology, read=None) -> Completion:
    """Propose the parts that fill the program's empty required slots.

    Requires `program.assembly_class`; a program that names no archetype has
    no slots to fill. `read` (the READ pass) keeps a labelled part from being
    inferred and limits inference to section drawings."""
    if not program.assembly_class or ont.assembly(program.assembly_class) is None:
        raise ValueError("set assembly_class to the archetype this drawing shows; one of: " + ", ".join(ont.assembly_ids()))
    filler = _Filler(program, ont, read)
    for slot in sorted(ont.required_slots(program.assembly_class), key=lambda s: ORDER.get(s.position, 0)):
        filler.fill(slot)
    return filler.result


def cad_placeholder(part, ont: Ontology):
    """The fields that make a reviewer-confirmed box object a CAD part with no
    model yet. A confirmed part is real, so the rule for objects applies again:
    it needs CadQuery. Until it has it, the verifier and the viewer treat it as
    every unbuilt object: a placeholder shape, labelled, that FAILs. The sizes
    stay the assumptions they were."""
    dims = {d["name"]: d["expr"] for d in part["dims"]}
    missing = [name for name in ("x0", "x1", "y0", "y1", "z0", "z1") if name not in dims]
    if missing:
        raise ValueError(f"{part['id']}: a placeholder needs its full envelope before it can be confirmed; missing {missing}")
    cls = ont.component(part.get("class_id"))
    return {"shape": "cad", "cad_brief": f"{cls.name}: {cls.definition}" if cls else part["type"],
            "param_bindings": {"width": _sub(dims["x1"], dims["x0"]), "depth": _sub(dims["z1"], dims["z0"]),
                               "height": _sub(dims["y1"], dims["y0"])},
            "placement": {"x": dims["x0"], "y": dims["y0"], "z": dims["z1"]},
            "note": ((part.get("note") or "") + " A reviewer confirmed the part exists; its shape is still a "
                     "placeholder until it is modelled.").strip()}


def edits(completion: Completion):
    """The proposal as patch_program edits, so the normal write path gates it."""
    return ([{"op": "add", "path": "/params/-", "value": p.model_dump(mode="json")} for p in completion.params] +
            [{"op": "add", "path": "/parts/-", "value": p.model_dump(mode="json")} for p in completion.parts] +
            [{"op": "add", "path": "/relations/-", "value": r.model_dump(mode="json")} for r in completion.relations])


def apply(program, completion: Completion):
    """A copy of the raw program with the proposal appended."""
    result = copy.deepcopy(program)
    result["params"] = result.get("params", []) + [p.model_dump(mode="json") for p in completion.params]
    result["parts"] = result.get("parts", []) + [p.model_dump(mode="json") for p in completion.parts]
    result["relations"] = result.get("relations", []) + [r.model_dump(mode="json") for r in completion.relations]
    return result
