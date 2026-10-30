"""The ontology loader: the contract API over ontology/**, and its validation."""
import re
import shutil
from pathlib import Path

import pytest
import yaml

from app import ontology
from app.native_program import NativePart, NativeRelation

ROOT = Path(__file__).resolve().parent.parent / "ontology"


def test_ontology_loads_with_the_contract_api():
    ont = ontology.load()
    assert re.match(r"^\d{4}\.\d{2}\.\d{2}$", ont.version)
    assert {"door_jamb", "door_head", "door_leaf", "door_sill", "membrane", "slab"} <= set(ont.component_ids())
    assert ont.assembly_ids() == ["door_threshold"]
    assert ont.component("door_jamb").role == "object" and ont.component("membrane").role == "covered_layer"
    assert ont.component("no_such_class") is None and ont.assembly("no_such_assembly") is None
    required = ont.required_slots("door_threshold")
    assert [s.id for s in required] == ["threshold", "substrate", "jamb", "head", "leaf"]
    assert all(s.min >= 1 for s in required) and ont.required_slots("no_such_assembly") == []
    jamb = ont.slot_for("door_threshold", "door_jamb")
    assert jamb.id == "jamb" and jamb.min == jamb.max == 2 and jamb.section_visibility == "beyond"
    assert jamb.placement == "fill_opening" and jamb.anchor == "threshold" and jamb.position == "side"
    assert ont.slot_for("door_threshold", "door_head").section_visibility == "out_of_plane"
    assert ont.slot_for("door_threshold", "no_such_class") is None
    # The seven existing relation kinds stay; the new ones are the ones the archetype uses.
    assert set(ont.relations) == {"supported_by", "bonded_to", "covers", "laps_over", "terminates_against", "sealed_to",
                                  "installed_before", "hosted_in", "fixed_to", "abuts", "repeated_along"}


def test_ontology_data_agrees_with_the_program_grammar():
    ont = ontology.load()
    roles = set(NativePart.model_fields["role"].annotation.__args__)
    shapes = set(NativePart.model_fields["shape"].annotation.__args__)
    kinds = set(NativeRelation.model_fields["kind"].annotation.__args__)
    for ident in ont.component_ids():
        cls = ont.component(ident)
        assert cls.role in roles and (cls.shape_hint is None or cls.shape_hint in shapes)
        assert cls.form is None or cls.role == "object"
        assert set(cls.relations_allowed) <= kinds
        for dim in cls.dimension_slots:
            # A typical range is a prior read from a public page, never a memory.
            assert (dim.typical_min is None) == (dim.typical_max is None)
            if dim.typical_min is not None:
                assert dim.source.startswith("http") and dim.typical_min <= dim.typical_max
    for assembly_id in ont.assembly_ids():
        for slot in ont.assembly(assembly_id).slots:
            assert all(ont.component(c) for c in slot.classes)
            assert slot.section_visibility in ontology.VISIBILITY
    # Every file says whether the owner has reviewed it.
    for path in list(ROOT.glob("*/*.yaml")) + [ROOT / "relations.yaml"]:
        assert yaml.safe_load(path.read_text())["status"] in ontology.STATUS, path


def _copy(tmp_path, name):
    root = tmp_path / name
    shutil.copytree(ROOT, root)
    return root


def _rewrite(path, old, new):
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1))


@pytest.mark.parametrize("case", ["range_without_source", "unknown_slot_class", "bad_role", "bad_visibility",
                                  "duplicate_id", "missing_status", "unknown_relation", "bad_anchor"])
def test_bad_ontology_data_is_rejected(tmp_path, case):
    root = _copy(tmp_path, case)
    jamb, assembly = root / "classes" / "door_jamb.yaml", root / "assemblies" / "door_threshold.yaml"
    if case == "range_without_source":
        _rewrite(jamb, "source: https://archipro.com.au/article/standard-door-sizes-australia", "source: null")
    elif case == "unknown_slot_class":
        _rewrite(assembly, "classes: [door_jamb]", "classes: [door_jamb, door_ghost]")
    elif case == "bad_role":
        _rewrite(jamb, "role: object", "role: fixture")
    elif case == "bad_visibility":
        _rewrite(assembly, "section_visibility: beyond", "section_visibility: hidden")
    elif case == "duplicate_id":
        (root / "classes" / "door_jamb_copy.yaml").write_text(jamb.read_text())
    elif case == "missing_status":
        _rewrite(jamb, "status: draft\n", "")
    elif case == "unknown_relation":
        _rewrite(jamb, "relations_allowed: [supported_by,", "relations_allowed: [glued_by,")
    elif case == "bad_anchor":
        _rewrite(assembly, "anchor: threshold\n    position: side", "anchor: nowhere\n    position: side")
    with pytest.raises(ValueError):
        ontology.load(root)
