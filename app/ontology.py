"""The ontology: component classes, assembly archetypes and relation kinds.

Library data, not engine code. It lives in `ontology/` as YAML written in
our own words (classes, assemblies, relations.yaml, VERSION) and is loaded,
validated and cached here. Engine checks read class ids, roles and slots
from it; they never name a part. A new trade is new files, not code.

Every file carries `status: draft` until the owner reviews it. A typical
dimension range is recorded only with the public URL it was read from, and
is a prior for placeholders: it is never written as a drawing value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import get_args

import yaml

ROOT = Path(__file__).resolve().parent.parent / "ontology"
ROLES = ("substrate", "covered_layer", "finish", "object", "zone")
SHAPES = ("box", "sheet", "post", "rod", "wedge", "fillet", "profile", "revolve", "sweep", "cad", "annotation")
FORMS = ("shaped", "block")
UNITS = ("mm", "ratio", "deg")
VISIBILITY = ("cut", "beyond", "out_of_plane")
STATUS = ("draft", "reviewed")
# The spec's closed, trade-agnostic function set.
FUNCTIONS = ("water_barrier", "drainage", "air_seal", "vapour_control", "thermal", "support", "fixing",
             "movement_joint", "finish", "protection", "operation", "opening_frame", "datum")
# The spec's placement families. app/completion.py places `fill_opening` and
# `hosted_in_frame` members from an anchor slot; the others are suggested only.
PLACEMENTS = ("extrude_along_run", "follow_layer", "repeat_at_spacing", "fill_opening",
              "hosted_in_frame", "posed_object", "fillet_at_corner")
POSITIONS = ("side", "top", "infill")  # a member's place within its opening
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_URL = re.compile(r"^https?://[^\s]+$")


@dataclass(frozen=True)
class Slot:
    id: str  # e.g. "jamb"
    classes: tuple[str, ...]  # accepted component class ids
    min: int  # 0 = optional
    max: int | None  # None = unbounded
    section_visibility: str  # "cut" | "beyond" | "out_of_plane": what a typical section of this assembly shows of it
    placement: str | None  # placement family id (PLACEMENTS), or None
    description: str
    # Placement data for app/completion.py: the slot whose parts the placement
    # is measured from, and the member's place in that opening.
    anchor: str | None = None
    position: str | None = None


@dataclass(frozen=True)
class DimensionSlot:
    name: str  # e.g. "height"
    unit: str  # "mm" | "ratio" | "deg"
    typical_min: float | None  # None unless `source` is a public URL. Never invented.
    typical_max: float | None
    source: str | None  # public URL the range was read from
    derive_from: str | None  # "<slot>.<measure>" over the assembly's other slots, e.g. "threshold.depth"


@dataclass(frozen=True)
class ComponentClass:
    id: str
    name: str
    definition: str  # our own words
    function: str
    role: str  # NativePart.role: substrate | covered_layer | finish | object | zone
    shape_hint: str | None  # preferred NativePart.shape, or None
    form: str | None  # "shaped" | "block" for objects, else None
    relations_allowed: tuple[str, ...]
    dimension_slots: tuple[DimensionSlot, ...]
    drawing_appearance: dict  # measured statistics only; may be empty
    aliases: tuple[str, ...]  # label words used for matching READ labels; never used by engine checks

    def dimension(self, name):
        return next((d for d in self.dimension_slots if d.name == name), None)


@dataclass(frozen=True)
class AssemblyClass:
    id: str
    name: str
    definition: str
    slots: tuple[Slot, ...]
    sequence_hints: tuple[str, ...]  # free text, e.g. "frame before leaf"

    def slot(self, slot_id):
        return next((s for s in self.slots if s.id == slot_id), None)


class Ontology:
    def __init__(self, version, components, assemblies, relations):
        self.version = version
        self._components = components
        self._assemblies = assemblies
        self.relations = relations  # kind -> definition, from relations.yaml

    def component(self, class_id):
        return self._components.get(class_id)

    def assembly(self, assembly_id):
        return self._assemblies.get(assembly_id)

    def component_ids(self):
        return sorted(self._components)

    def assembly_ids(self):
        return sorted(self._assemblies)

    def required_slots(self, assembly_id):
        archetype = self.assembly(assembly_id)
        return [s for s in archetype.slots if s.min >= 1] if archetype else []

    def slot_for(self, assembly_id, class_id):
        archetype = self.assembly(assembly_id)
        return next((s for s in archetype.slots if class_id in s.classes), None) if archetype else None


# ------------------------------------------------------------------ loading

def _text(data, key, where, required=True):
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {key} must be a non-empty string")
    return value.strip()


def _ident(data, key, where):
    value = _text(data, key, where)
    if not _ID.match(value):
        raise ValueError(f"{where}: {key} {value!r} is not snake_case")
    return value


def _strings(data, key, where):
    value = data.get(key) or []
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f"{where}: {key} must be a list of strings")
    return tuple(v.strip() for v in value)


def _choice(data, key, choices, where, required=True):
    value = data.get(key)
    if value is None and not required:
        return None
    if value not in choices:
        raise ValueError(f"{where}: {key} must be one of {choices}, not {value!r}")
    return value


def _read(path):
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise ValueError(f"{path.name}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping")
    _choice(data, "status", STATUS, path.name)  # every file says whether the owner has reviewed it
    return data


def _dimension(data, where):
    name = _ident(data, "name", where)
    where = f"{where}.{name}"
    low, high = data.get("typical_min"), data.get("typical_max")
    source = _text(data, "source", where, required=False)
    if (low is None) != (high is None):
        raise ValueError(f"{where}: typical_min and typical_max come together")
    if low is not None:
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (low, high)) or low > high:
            raise ValueError(f"{where}: typical range must be two numbers, min <= max")
        if not source or not _URL.match(source):
            raise ValueError(f"{where}: a typical range needs the public URL it was read from")
    if source and not _URL.match(source):
        raise ValueError(f"{where}: source must be a public URL")
    return DimensionSlot(name=name, unit=_choice(data, "unit", UNITS, where),
                         typical_min=None if low is None else float(low),
                         typical_max=None if high is None else float(high),
                         source=source, derive_from=_text(data, "derive_from", where, required=False))


def _component(data, where, relations):
    ident = _ident(data, "id", where)
    role = _choice(data, "role", ROLES, where)
    form = _choice(data, "form", FORMS, where, required=False)
    if form and role != "object":
        raise ValueError(f"{where}: form is declared only on objects")
    allowed = _strings(data, "relations_allowed", where)
    unknown = sorted(set(allowed) - set(relations))
    if unknown:
        raise ValueError(f"{where}: relations_allowed names unknown kinds {unknown}")
    dims = tuple(_dimension(d, where) for d in data.get("dimension_slots") or [])
    if len({d.name for d in dims}) != len(dims):
        raise ValueError(f"{where}: duplicate dimension slot names")
    appearance = data.get("drawing_appearance") or {}
    if not isinstance(appearance, dict):
        raise ValueError(f"{where}: drawing_appearance must be a mapping")
    return ComponentClass(id=ident, name=_text(data, "name", where), definition=_text(data, "definition", where),
                          function=_choice(data, "function", FUNCTIONS, where), role=role,
                          shape_hint=_choice(data, "shape_hint", SHAPES, where, required=False), form=form,
                          relations_allowed=allowed, dimension_slots=dims, drawing_appearance=appearance,
                          aliases=_strings(data, "aliases", where))


def _slot(data, where, components):
    ident = _ident(data, "id", where)
    where = f"{where}.{ident}"
    classes = _strings(data, "classes", where)
    unknown = sorted(set(classes) - set(components))
    if not classes or unknown:
        raise ValueError(f"{where}: classes must name known component classes (unknown: {unknown})")
    low, high = data.get("min", 0), data.get("max")
    if not isinstance(low, int) or isinstance(low, bool) or low < 0:
        raise ValueError(f"{where}: min must be an integer >= 0")
    if high is not None and (not isinstance(high, int) or isinstance(high, bool) or high < max(low, 1)):
        raise ValueError(f"{where}: max must be null or an integer >= max(min, 1)")
    return Slot(id=ident, classes=classes, min=low, max=high,
                section_visibility=_choice(data, "section_visibility", VISIBILITY, where),
                placement=_choice(data, "placement", PLACEMENTS, where, required=False),
                description=_text(data, "description", where),
                anchor=_text(data, "anchor", where, required=False),
                position=_choice(data, "position", POSITIONS, where, required=False))


def _assembly(data, where, components):
    ident = _ident(data, "id", where)
    slots = tuple(_slot(s, where, components) for s in data.get("slots") or [])
    ids = [s.id for s in slots]
    if not slots or len(set(ids)) != len(ids):
        raise ValueError(f"{where}: slots must be a non-empty list with unique ids")
    for slot in slots:
        if slot.anchor is not None and (slot.anchor not in ids or slot.anchor == slot.id):
            raise ValueError(f"{where}.{slot.id}: anchor must name another slot of this assembly")
    return AssemblyClass(id=ident, name=_text(data, "name", where), definition=_text(data, "definition", where),
                         slots=slots, sequence_hints=_strings(data, "sequence_hints", where))


def _relations(path):
    from .native_program import NativeRelation  # the kinds a program can store
    storable = set(get_args(NativeRelation.model_fields["kind"].annotation))
    data = _read(path)
    kinds = {}
    for entry in data.get("relations") or []:
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name}: each relation is a mapping")
        ident = _ident(entry, "id", path.name)
        if ident in kinds:
            raise ValueError(f"{path.name}: duplicate relation {ident}")
        if ident not in storable:
            raise ValueError(f"{path.name}: {ident} is not a relation kind the program grammar stores")
        kinds[ident] = _text(entry, "definition", f"{path.name}.{ident}")
    if not kinds:
        raise ValueError(f"{path.name}: no relations")
    return kinds


def _collection(folder, build, *args):
    items = {}
    for path in sorted(folder.glob("*.yaml")):
        item = build(_read(path), path.name, *args)
        if item.id != path.stem:
            raise ValueError(f"{path.name}: id {item.id!r} must match the file name")
        if item.id in items:
            raise ValueError(f"{path.name}: duplicate id {item.id}")
        items[item.id] = item
    if not items:
        raise ValueError(f"{folder}: no .yaml files")
    return items


@lru_cache(maxsize=None)
def _load(root):
    root = Path(root)
    version = (root / "VERSION").read_text().strip() if (root / "VERSION").is_file() else ""
    if not version or len(version.split()) != 1:
        raise ValueError("ontology/VERSION must hold one version token")
    relations = _relations(root / "relations.yaml")
    components = _collection(root / "classes", _component, relations)
    assemblies = _collection(root / "assemblies", _assembly, components)
    return Ontology(version, components, assemblies, relations)


def load(root=None):
    """The validated ontology (cached per root). Raises ValueError on bad data."""
    return _load(str(root or ROOT))


def clear_cache():
    _load.cache_clear()
