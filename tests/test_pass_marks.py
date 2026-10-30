"""A relation's pass mark belongs to the verifier, and an agent cannot get round it.

Each test pins a way round the rule that a real run took, or that it could have
taken. The fixtures are our own: blocks of named sizes, no drawing. Measured on
built meshes through the same code a version is verified with.
"""
import copy
from typing import get_args

import pytest

from app.geometry import Mesh, _box_tris
from app.native_program import NativeProgram, NativeRelation, prepare
from app.verification_probes import shared_length, solid_spans
from app.verification_v3 import (COVER_FLOOR, CONTACT_FLOOR, LAP_WIDTHS, is_lap, label_check, part_thickness,
                                 spatial_checks)
from test_constraints_v3 import _layer_program

SOURCE = {"filename": "own.pdf", "sha256": "a" * 64, "page_index": 0, "crop_id": "p0_x",
          "page_width_pt": 300, "page_height_pt": 300, "rotation_deg": 0}
READ = {"drawing_type": "section", "rotation_deg": 0, "title": "Blocks", "dimensions": [],
        "elements": [{"label_verbatim": "Wide deck", "kind": "label", "where": "left", "category": "layer"}]}
TOUCHING = {"axis": "y", "source_face": "min", "target_face": "max", "min_gap": 0, "max_gap": 1}


def _box(name, x0, y0, x1, y1, z1=100):
    mesh = Mesh(name)
    _box_tris(mesh, x0, y0, 0, x1, y1, z1)
    return mesh


def _parts(*specs):
    """(id, role) pairs as a program, each installed after the one before."""
    raw = copy.deepcopy(_layer_program().model_dump(mode="json"))
    template = raw["parts"][0]
    raw["parts"], raw["relations"] = [], []
    for i, (ident, role) in enumerate(specs):
        raw["parts"].append({**template, "id": ident, "type": ident, "role": role, "features": [],
                             "install_after": [specs[i - 1][0]] if i else []})
    return raw


def _relation(source, target, kind, **constraints):
    return {"source": source, "target": target, "kind": kind, "constraints": {**TOUCHING, **constraints}}


def _checks(raw, meshes, **options):
    return {c["id"]: c for c in spatial_checks(NativeProgram.model_validate(raw), meshes, **options)}


def _strip_on_deck():
    """A 4 mm strip standing on a deck 550 long: touching, and hiding next to nothing."""
    raw = _parts(("deck", "covered_layer"), ("strip", "finish"))
    return raw, {"deck": _box("deck", 0, 0, 550, 4), "strip": _box("strip", 100, 4, 104, 54)}


# ----------------------------------------------------------- the mark itself

@pytest.mark.parametrize("declared", [0.03, 0.0, None])
def test_a_covers_claim_under_the_floor_fails_whatever_it_declares(declared):
    """Seen live: half the measured relations ended with their mark lowered to almost
    nothing, and a strip 'covering' a sliver of a deck passed."""
    raw, meshes = _strip_on_deck()
    settings = {} if declared is None else {"min_coverage": declared}
    raw["relations"] = [_relation("strip", "deck", "covers", **settings)]
    check = _checks(raw, meshes)["assembly-0-covers"]
    assert check["status"] == "FAIL" and check["coverage"] < COVER_FLOOR
    assert "no declared value lowers it" in check["reason"] and "supported_by" in check["reason"]


def test_an_author_may_ask_for_more_than_the_floor_and_is_held_to_it():
    raw = _parts(("deck", "substrate"), ("layer", "finish"))
    meshes = {"deck": _box("deck", 0, 0, 100, 4), "layer": _box("layer", 0, 4, 60, 8)}
    raw["relations"] = [_relation("layer", "deck", "covers")]
    assert _checks(raw, meshes)["assembly-0-covers"]["status"] == "PASS"  # 60%: over the floor
    raw["relations"] = [_relation("layer", "deck", "covers", min_coverage=0.9)]
    failed = _checks(raw, meshes)["assembly-0-covers"]
    assert failed["status"] == "FAIL" and "declares" in failed["reason"]


def test_the_floors_are_the_values_the_evidence_chose():
    assert CONTACT_FLOOR == COVER_FLOOR == 0.5


def test_a_wide_plate_on_a_small_support_is_a_true_contact():
    """Measured on a seed: a sub-sill on a nut is 9% of the plate. A floor on the
    source's face would fail it, so the floor reads the smaller face."""
    raw = _parts(("nut", "substrate"), ("plate", "finish"))
    meshes = {"nut": _box("nut", 45, 0, 55, 10), "plate": _box("plate", 0, 10, 100, 14)}
    raw["relations"] = [_relation("plate", "nut", "supported_by")]
    check = _checks(raw, meshes)["assembly-0-supported_by"]
    assert check["status"] == "PASS" and check["coverage"] == pytest.approx(0.1) and check["contact"] == 1


def test_two_faces_that_barely_overlap_do_not_touch_enough_whatever_the_kind():
    raw = _parts(("low", "substrate"), ("high", "finish"))
    meshes = {"low": _box("low", 0, 0, 100, 4), "high": _box("high", 90, 4, 190, 8)}  # 10% of both
    for kind in ("supported_by", "bonded_to", "sealed_to", "terminates_against", "abuts", "covers"):
        raw["relations"] = [_relation("high", "low", kind)]
        check = _checks(raw, meshes)[f"assembly-0-{kind}"]
        assert check["status"] == "FAIL" and check["contact"] == pytest.approx(0.1), kind


# ------------------------------------------------------------ ways round it

@pytest.mark.parametrize("kind", ["supported_by", "bonded_to", "sealed_to", "terminates_against", "abuts"])
def test_a_kind_swap_does_not_change_what_is_measured(kind):
    """Seen live: covers became terminates_against, then supported_by, on one pair."""
    raw = _parts(("low", "substrate"), ("high", "finish"))
    meshes = {"low": _box("low", 0, 0, 100, 4), "high": _box("high", 0, 4, 100, 8)}
    raw["relations"] = [_relation("high", "low", "covers")]
    covers = _checks(raw, meshes)["assembly-0-covers"]
    raw["relations"] = [_relation("high", "low", kind)]
    assert _checks(raw, meshes)[f"assembly-0-{kind}"]["contact"] == covers["contact"]
    meshes["high"] = _box("high", 95, 4, 195, 8)
    assert _checks(raw, meshes)[f"assembly-0-{kind}"]["status"] == "FAIL"


def test_a_true_kind_clears_a_false_covers_but_not_the_layers_obligation():
    """The honest repair stays open to the agent, and it does not cover the deck.
    Seen live: terminates_against on a sliver ticked off a covered layer."""
    raw, meshes = _strip_on_deck()
    for kind in ("supported_by", "terminates_against", "bonded_to", "sealed_to"):
        raw["relations"] = [_relation("strip", "deck", kind)]
        checks = _checks(raw, meshes)
        assert checks[f"assembly-0-{kind}"]["status"] == "PASS", kind
        coverage = checks["coverage-deck"]
        assert coverage["status"] == "UNVERIFIED" and coverage["required"], kind
        assert coverage["covered_share"] == pytest.approx(4 / 550) and "50%" in coverage["reason"]


def test_a_layer_is_covered_by_what_really_lies_on_it_under_any_name():
    raw = _parts(("deck", "covered_layer"), ("left", "finish"), ("right", "finish"))
    meshes = {"deck": _box("deck", 0, 0, 100, 4), "left": _box("left", 0, 4, 40, 8), "right": _box("right", 50, 4, 90, 8)}
    raw["relations"] = [_relation("left", "deck", "supported_by"), _relation("right", "deck", "bonded_to")]
    check = _checks(raw, meshes)["coverage-deck"]
    assert check["status"] == "PASS" and check["covered_share"] == pytest.approx(0.8)


def test_a_sliver_cannot_shelter_behind_a_true_cover_on_the_same_face():
    """Seen replaying a real version, when the floor was on the sum of a face's covers."""
    raw = _parts(("slab", "substrate"), ("deck", "finish"), ("sliver", "finish"))
    meshes = {"slab": _box("slab", 0, 0, 100, 50), "deck": _box("deck", 0, 50, 100, 54), "sliver": _box("sliver", 40, 50, 50, 54)}
    raw["relations"] = [_relation("deck", "slab", "covers"), _relation("sliver", "slab", "covers")]
    checks = _checks(raw, meshes)
    assert checks["assembly-0-covers"]["status"] == "PASS" and checks["assembly-1-covers"]["status"] == "FAIL"


def test_one_part_named_twice_counts_once_on_a_layer():
    """Seen live: three relations on one pair. Saying it again hides no more of the face."""
    raw = _parts(("deck", "covered_layer"), ("pad", "finish"))
    meshes = {"deck": _box("deck", 0, 0, 100, 4), "pad": _box("pad", 0, 4, 30, 8)}
    raw["relations"] = [_relation("pad", "deck", kind) for kind in ("supported_by", "bonded_to", "terminates_against")]
    assert _checks(raw, meshes)["coverage-deck"]["covered_share"] == pytest.approx(0.3)


def test_a_layers_end_face_is_not_its_covered_face():
    """A deck under the end of an upright hides that end, not the upright's broad face."""
    raw = _parts(("deck", "finish"), ("upright", "covered_layer"))
    meshes = {"deck": _box("deck", 0, 0, 100, 4), "upright": _box("upright", 50, 4, 54, 204)}
    raw["relations"] = [{"source": "deck", "target": "upright", "kind": "covers", "constraints": {
        "axis": "y", "source_face": "max", "target_face": "min", "min_gap": 0, "max_gap": 1}}]
    raw["parts"][0]["install_after"], raw["parts"][1]["install_after"] = ["upright"], []
    checks = _checks(raw, meshes)
    assert checks["assembly-0-covers"]["status"] == "PASS"  # true of the end face, and worth nothing
    assert checks["coverage-upright"]["status"] == "UNVERIFIED" and checks["coverage-upright"]["covered_share"] < 0.05


def test_faces_that_look_the_same_way_do_not_touch():
    """Every end of an extruded section is coplanar, so these would always measure 100%."""
    raw = _parts(("low", "substrate"), ("high", "finish"))
    meshes = {"low": _box("low", 0, 0, 100, 100), "high": _box("high", 10, 10, 90, 90)}
    raw["relations"] = [{"source": "high", "target": "low", "kind": "bonded_to", "constraints": {
        "axis": "z", "source_face": "min", "target_face": "min", "min_gap": 0, "max_gap": 1}}]
    check = _checks(raw, meshes)["assembly-0-bonded_to"]
    assert check["status"] == "FAIL" and "same way" in check["reason"]


def test_a_part_with_no_measured_relation_stays_open():
    """Deleting a failing relation clears its FAIL, so the part must then owe one."""
    raw = _parts(("low", "substrate"), ("high", "finish"), ("loose", "finish"))
    meshes = {"low": _box("low", 0, 0, 100, 4), "high": _box("high", 0, 4, 100, 8), "loose": _box("loose", 300, 0, 310, 10)}
    raw["relations"] = [_relation("high", "low", "supported_by")]
    checks = _checks(raw, meshes)
    assert checks["anchored-loose"]["status"] == "UNVERIFIED" and checks["anchored-loose"]["required"]
    assert "anchored-low" not in checks and "anchored-high" not in checks


def test_an_unmeasured_kind_does_not_anchor_a_part():
    raw = _parts(("low", "substrate"), ("high", "finish"))
    meshes = {"low": _box("low", 0, 0, 100, 4), "high": _box("high", 0, 4, 100, 8)}
    raw["relations"] = [{"source": "high", "target": "low", "kind": "repeated_along", "constraints": {}}]
    checks = _checks(raw, meshes)
    assert checks["anchored-high"]["status"] == "UNVERIFIED" and checks["assembly-0-repeated_along"]["required"]


def test_a_single_part_owes_no_relation():
    raw = _parts(("only", "substrate"))
    assert not [c for c in _checks(raw, {"only": _box("only", 0, 0, 10, 10)}) if c.startswith("anchored-")]


def test_a_zone_or_ghost_owes_no_relation_and_a_ghost_cannot_anchor():
    raw = _parts(("low", "substrate"), ("high", "finish"), ("mark", "zone"), ("ghost", "finish"))
    raw["parts"][2]["install_after"] = []
    raw["parts"][3].update(provenance="inferred", class_id="door_jamb", inferred_by="x")
    raw["relations"] = [_relation("high", "ghost", "supported_by")]
    meshes = {key: _box(key, 0, i * 10, 100, i * 10 + 10) for i, key in enumerate(("low", "high", "mark", "ghost"))}
    checks = _checks(raw, meshes)
    assert {"anchored-low", "anchored-high"} <= set(checks)
    assert not {"anchored-mark", "anchored-ghost"} & set(checks)


def test_a_role_changed_after_the_first_draft_keeps_the_obligation():
    """A covered layer renamed a finish would owe nothing, and class_id need not stop it."""
    raw, meshes = _strip_on_deck()
    raw["parts"][0]["role"] = "finish"
    raw["relations"] = [_relation("strip", "deck", "supported_by")]
    assert "coverage-deck" not in _checks(raw, meshes)
    assert _checks(raw, meshes, covered_before={"deck"})["coverage-deck"]["status"] == "UNVERIFIED"


def test_only_a_first_draft_role_its_class_agrees_with_binds_later_drafts():
    """Seen live: a sealant was covered_layer for one draft, against its own class, and
    the repair must not leave it owing a cover for ever."""
    from app.verification_v3 import first_draft_layers
    first = {"parts": [{"id": "layer", "role": "covered_layer", "class_id": "membrane"},
                       {"id": "bead", "role": "covered_layer", "class_id": "sealant"},
                       {"id": "plain", "role": "covered_layer", "class_id": None},
                       {"id": "tile", "role": "finish", "class_id": None}]}
    assert first_draft_layers(first) == {"layer", "plain"}
    assert first_draft_layers({}) == set()


def test_a_reviewer_can_still_accept_an_exposed_layer():
    raw, meshes = _strip_on_deck()
    raw["relations"] = [_relation("strip", "deck", "supported_by")]
    check = _checks(raw, meshes, exposed={"deck"})["coverage-deck"]
    assert check["status"] == "N/A" and not check["required"]


# ------------------------------------------------- every kind against what is not true of it

KINDS = get_args(NativeRelation.model_fields["kind"].annotation)
# What each kind claims. A new kind fails test_every_kind_is_classified until it is added here.
CONTACT_ONLY = ("supported_by", "bonded_to", "sealed_to", "terminates_against", "abuts", "laps_over")
HIDES = ("covers",)
SOLID = ("fixed_to", "hosted_in")
NO_CLAIM_OF_CONTACT = ("installed_before", "repeated_along")


def _foot_on_end_of_a_sheet():
    """A foot 100 long whose last 19 mm rests on the end of a sheet, the shape of a
    FAIL a live run reached. In a section both run the same length along the sill."""
    raw = _parts(("sheet", "substrate"), ("foot", "object"))
    raw["parts"][1].update(shape="cad", form="shaped", cad_brief="a foot", param_bindings={"width": "width"})
    meshes = {"sheet": _box("sheet", 0, 0, 200, 2), "foot": _box("foot", 181, 2, 281, 6)}
    return raw, meshes, {"foot": [([0, -1, 0], [231, 2, 50]), ([0, 1, 0], [231, 6, 50])]}, "foot", "sheet"


def _floating_in_a_bent_plate():
    """Inside the plate's bounding box, touching nothing."""
    raw = _parts(("plate", "substrate"), ("part", "finish"))
    plate = Mesh("plate")
    _box_tris(plate, 0, 0, 0, 200, 5, 100)
    _box_tris(plate, 0, 0, 0, 5, 200, 100)
    return raw, {"plate": plate, "part": _box("part", 80, 80, 120, 120)}, {}, "part", "plate"


def _beside_its_target():
    raw = _parts(("block", "substrate"), ("part", "finish"))
    return raw, {"block": _box("block", 0, 0, 100, 50), "part": _box("part", 120, 0, 220, 50)}, {}, "part", "block"


def _resting_on_a_surface():
    """All of a small part's face on a wide one: a true contact, and nothing more."""
    raw = _parts(("slab", "substrate"), ("part", "finish"))
    return raw, {"slab": _box("slab", 0, 0, 500, 50), "part": _box("part", 200, 50, 240, 90)}, {}, "part", "slab"


HOSTILE = {"foot on the end of a sheet": _foot_on_end_of_a_sheet, "floating in a bent plate": _floating_in_a_bent_plate,
           "beside its target": _beside_its_target, "resting on a surface": _resting_on_a_surface}
# Every way of declaring a relation that might change what is measured.
DECLARED = {"faces": dict(TOUCHING), "faces, asking nothing": {**TOUCHING, "min_coverage": 0.0}, "no constraints": {},
            "axis x": {"axis": "x"}, "axis y": {"axis": "y"}, "axis z": {"axis": "z"}}


def _verdict(case, kind, declared):
    raw, meshes, planes, source, target = HOSTILE[case]()
    raw["relations"] = [{"source": source, "target": target, "kind": kind, "constraints": copy.deepcopy(declared)}]
    checks = _checks(raw, meshes, cad_planes=planes)
    return checks[f"assembly-0-{kind}"], checks, source


def test_every_kind_is_classified():
    assert sorted(KINDS) == sorted(CONTACT_ONLY + HIDES + SOLID + NO_CLAIM_OF_CONTACT)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("case", ["foot on the end of a sheet", "floating in a bent plate", "beside its target"])
@pytest.mark.parametrize("declared", DECLARED)
def test_no_kind_passes_what_is_not_there(case, kind, declared):
    """Renaming a relation, or declaring it differently, must never clear a measurement."""
    found, checks, source = _verdict(case, kind, DECLARED[declared])
    if kind == "installed_before":  # a sequence, which says nothing about contact
        assert f"anchored-{source}" in checks
    else:
        assert found["status"] != "PASS", found
        assert found["required"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("declared", DECLARED)
def test_a_part_resting_on_a_surface_is_a_contact_and_nothing_more(kind, declared):
    found, checks, source = _verdict("resting on a surface", kind, DECLARED[declared])
    if kind == "installed_before":
        assert f"anchored-{source}" in checks
    elif kind in CONTACT_ONLY and "source_face" in DECLARED[declared]:
        assert found["status"] == "PASS" and found["contact"] == 1
    else:
        assert found["status"] != "PASS", found  # covers hides 8% of the slab; solids share nothing


@pytest.mark.parametrize("kind", SOLID)
def test_a_part_that_rests_is_told_to_say_so(kind):
    found, _, _ = _verdict("resting on a surface", kind, {})
    assert found["status"] == "FAIL" and "contact kind" in found["reason"] and "supported_by" in found["reason"]


# ------------------------------------------------------------------------- a true lap

def _lap(under, over, roles=("covered_layer", "finish")):
    raw = _parts(("under", roles[0]), ("over", roles[1]))
    raw["relations"] = [_relation("over", "under", "laps_over")]
    return raw, {"under": _box("under", *under), "over": _box("over", *over)}


def test_two_layers_may_lap_with_a_small_share_of_their_faces():
    raw, meshes = _lap((0, 0, 1000, 2), (850, 2, 1850, 4))
    check = _checks(raw, meshes)["assembly-0-laps_over"]
    assert check["status"] == "PASS" and check["contact"] == pytest.approx(0.15)


@pytest.mark.parametrize("over", [(999, 2, 1999, 4), (985, 2, 1985, 4)], ids=["1 mm", "15 mm"])
def test_a_sliver_between_two_sheets_is_not_a_lap(over):
    """Under ten times the thinner sheet. A sanity bar, not a lap width from any rule."""
    raw, meshes = _lap((0, 0, 1000, 2), over)
    check = _checks(raw, meshes)["assembly-0-laps_over"]
    assert check["status"] == "FAIL" and "smaller face" in check["reason"]
    assert LAP_WIDTHS == 10


@pytest.mark.parametrize("roles", [("substrate", "finish"), ("object", "finish"), ("covered_layer", "object"), ("zone", "finish")])
def test_only_layers_by_role_may_lap(roles):
    """In a section every part has the same length along the sill, so this is the bar."""
    raw, meshes = _lap((0, 0, 1000, 2), (850, 2, 1850, 4), roles)
    for part in raw["parts"]:
        if part["role"] == "object":
            part.update(shape="cad", form="shaped", cad_brief="x", param_bindings={"width": "width"})
    planes = {p["id"]: [([0, -1, 0], [500, 2, 50]), ([0, 1, 0], [500, 2, 50])] for p in raw["parts"] if p["role"] == "object"}
    program = NativeProgram.model_validate(raw)
    assert not is_lap(program, program.relations[0], meshes, 1)
    if "zone" not in roles:
        assert _checks(raw, meshes, cad_planes=planes)["assembly-0-laps_over"]["status"] == "FAIL"


def test_a_layer_wholly_on_a_wider_layer_is_contact_not_a_lap():
    raw, meshes = _lap((0, 0, 1000, 2), (100, 2, 300, 4))
    program = NativeProgram.model_validate(raw)
    assert not is_lap(program, program.relations[0], meshes, 1)
    assert _checks(raw, meshes)["assembly-0-laps_over"]["contact"] == 1


def test_a_bent_sheets_lap_is_judged_by_the_sheet_not_its_box():
    """Its box is far taller than the sheet: read as thickness, no lap could ever pass."""
    raw = _parts(("under", "covered_layer"), ("over", "finish"))
    raw["params"].append({"name": "sheet_t", "value": 2, "unit": "mm", "kind": "exact", "provenance": "assumption"})
    raw["parts"][0].update(shape="sheet", path=[{"x": "0", "y": "0"}, {"x": "width", "y": "0"}, {"x": "width", "y": "height"}],
                           dims=[{"name": "thickness", "expr": "sheet_t"}, {"name": "z0", "expr": "0"}, {"name": "z1", "expr": "depth"}])
    raw["relations"] = [_relation("over", "under", "laps_over")]
    program = NativeProgram.model_validate(raw)
    bent = Mesh("under")
    _box_tris(bent, 0, -1, 0, 100, 1, 100)
    _box_tris(bent, 99, 1, 0, 100, 150, 100)
    assert is_lap(program, program.relations[0], {"under": bent, "over": _box("over", 60, 1, 260, 21)}, 1)
    assert not is_lap(program, program.relations[0], {"under": bent, "over": _box("over", 90, 1, 290, 21)}, 1)  # 10 mm


# ---------------------------------------------------------------------- a true fixing

def _fixing(screw, target=(0, 100, 200, 104)):
    raw = _parts(("plate", "substrate"), ("screw", "finish"))
    raw["relations"] = [{"source": "screw", "target": "plate", "kind": "fixed_to", "constraints": {}}]
    return raw, {"plate": _box("plate", *target), "screw": screw}


def _headed_screw(x, top, bottom, head=30, shank=6):
    screw = Mesh("screw")
    _box_tris(screw, x - shank / 2, bottom, 47, x + shank / 2, top - 6, 53)
    _box_tris(screw, x - head / 2, top - 6, 35, x + head / 2, top, 65)
    return screw


def test_a_fixing_that_passes_through_what_it_fixes_passes():
    raw, meshes = _fixing(_box("screw", 50, 60, 56, 110, z1=6))
    check = _checks(raw, meshes)["assembly-0-fixed_to"]
    assert check["status"] == "PASS" and check["embedded"] == pytest.approx(4)


def test_a_screw_with_a_head_wider_than_its_shank_is_found_by_its_shank():
    """A fixed grid of lines through the head's box misses a thin shank."""
    raw, meshes = _fixing(_headed_screw(100, 110, 60))
    check = _checks(raw, meshes)["assembly-0-fixed_to"]
    assert check["status"] == "PASS" and check["embedded"] == pytest.approx(4)


def test_two_screws_in_one_part_are_found_though_nothing_lies_between_them():
    """Their box is mostly air, and a line through its middle meets no screw."""
    screws = Mesh("screw")
    _box_tris(screws, 10, 60, 47, 14, 310, 53)
    _box_tris(screws, 210, 60, 47, 214, 310, 53)
    raw, meshes = _fixing(screws, (0, 100, 400, 104))
    check = _checks(raw, meshes)["assembly-0-fixed_to"]
    assert check["status"] == "PASS" and check["embedded"] == pytest.approx(4)


def test_a_fixing_sunk_no_deeper_than_the_contact_tolerance_is_only_touching():
    raw, meshes = _fixing(_box("screw", 50, 103, 56, 150, z1=6))
    check = _checks(raw, meshes)["assembly-0-fixed_to"]
    assert check["status"] == "FAIL" and check["embedded"] == 0


def test_a_long_plate_sunk_into_a_slab_is_touching_not_fixed():
    """Along its own length it shares a lot of solid; through its thickness, 1 mm."""
    raw, meshes = _fixing(_box("screw", 20, 103, 180, 110), (0, 100, 200, 104))
    check = _checks(raw, meshes)["assembly-0-fixed_to"]
    assert check["status"] == "FAIL" and check["embedded"] == 0


def test_a_line_through_a_shared_edge_is_one_crossing():
    assert solid_spans([1, 1, 5]) == [(1, 5)]
    assert solid_spans([1, 5, 8, 8, 8, 12]) == [(1, 5), (8, 12)]
    assert shared_length([(0, 10)], [(4, 6), (8, 20)]) == 4


def test_a_fixing_that_stops_short_of_the_plate_or_misses_it_fails():
    raw, meshes = _fixing(_box("screw", 50, 40, 56, 90, z1=6))
    assert _checks(raw, meshes)["assembly-0-fixed_to"]["status"] == "FAIL"
    meshes["screw"] = _box("screw", 500, 60, 506, 110, z1=6)
    assert _checks(raw, meshes)["assembly-0-fixed_to"]["status"] == "FAIL"


def test_a_fixing_on_an_unbuilt_object_is_not_measured_on_its_placeholder():
    raw = _parts(("plate", "substrate"), ("screw", "object"))
    raw["parts"][1].update(shape="cad", form="shaped", cad_brief="a screw", param_bindings={"width": "width"})
    raw["relations"] = [{"source": "screw", "target": "plate", "kind": "fixed_to", "constraints": {}}]
    meshes = {"plate": _box("plate", 0, 100, 200, 104), "screw": _box("screw", 50, 60, 56, 110, z1=6)}
    check = _checks(raw, meshes, cad_planes={})["assembly-0-fixed_to"]
    assert check["status"] == "UNVERIFIED" and check["required"] and "once both parts are built" in check["reason"]


def test_a_fixing_installed_before_what_it_fixes_fails():
    raw, meshes = _fixing(_box("screw", 50, 60, 56, 110, z1=6))
    raw["parts"][1]["install_after"], raw["parts"][0]["install_after"] = [], ["screw"]
    assert "installed before" in _checks(raw, meshes)["assembly-0-fixed_to"]["reason"]


# ---------------------------------------------------------------------- a true opening

def test_a_part_in_a_channel_is_hosted_in_it():
    raw = _parts(("frame", "substrate"), ("leaf", "finish"))
    raw["relations"] = [{"source": "leaf", "target": "frame", "kind": "hosted_in", "constraints": {}}]
    frame = Mesh("frame")
    _box_tris(frame, 0, 0, 0, 10, 100, 100)
    _box_tris(frame, 90, 0, 0, 100, 100, 100)
    _box_tris(frame, 0, 0, 0, 100, 10, 100)
    check = _checks(raw, {"frame": frame, "leaf": _box("leaf", 40, 20, 60, 80)})["assembly-0-hosted_in"]
    assert check["status"] == "PASS" and check["contact"] == 1


def test_a_part_set_into_a_solid_is_hosted_in_it():
    raw = _parts(("block", "substrate"), ("rod", "finish"))
    raw["relations"] = [{"source": "rod", "target": "block", "kind": "hosted_in", "constraints": {}}]
    assert _checks(raw, {"block": _box("block", 0, 0, 100, 100), "rod": _box("rod", 40, 40, 60, 60, z1=50)})[
        "assembly-0-hosted_in"]["status"] == "PASS"


# ---------------------------------------------------------- shaped objects

def _bent_plate():
    """A plate on two supports, dropping past them to a drip at one end and turning
    up at the other: its lowest and highest points are not where it meets anything."""
    plate = Mesh("plate")
    _box_tris(plate, 0, 20, 0, 200, 24, 100)      # the flat run
    _box_tris(plate, 0, 0, 0, 4, 20, 100)         # the drip, below the run
    _box_tris(plate, 196, 24, 0, 200, 80, 100)    # the turn-up, above it
    return plate


def _shaped(role_kinds):
    raw = _parts(("pad", "substrate"), ("plate", "object"), ("foot", "object"))
    for part in raw["parts"][1:]:
        part.update(shape="cad", form="shaped", cad_brief="x", param_bindings={"width": "width"})
    raw["relations"] = [_relation("plate", "pad", role_kinds[0]), _relation("foot", "plate", role_kinds[1])]
    return raw


# Flat faces as a trusted measurement reports them: outward normal, a point on the face.
PLATE_PLANES = [([0, -1, 0], [100, 20, 50]), ([0, -1, 0], [2, 0, 50]), ([0, 1, 0], [100, 24, 50]), ([0, 1, 0], [198, 80, 50])]
FOOT_PLANES = [([0, -1, 0], [100, 24, 50]), ([0, 1, 0], [100, 40, 50])]


def test_a_bent_plate_meets_its_support_on_its_flat_run_not_its_lowest_point():
    """Better shapes must not read worse: measured at its extreme, the plate's drip
    would hang in the air and the true contact would be a FAIL."""
    raw = _shaped(("supported_by", "supported_by"))
    meshes = {"pad": _box("pad", 50, 10, 150, 20), "plate": _bent_plate(), "foot": _box("foot", 60, 24, 140, 40)}
    checks = _checks(raw, meshes, cad_planes={"plate": PLATE_PLANES, "foot": FOOT_PLANES})
    resting = checks["assembly-0-supported_by"]
    assert resting["status"] == "PASS" and resting["contact"] == 1 and resting["min_gap"] == pytest.approx(0)
    carried = checks["assembly-1-supported_by"]  # the foot stands on the run, under the turn-up's height
    assert carried["status"] == "PASS" and carried["contact"] == 1 and carried["max_gap"] == pytest.approx(0)


@pytest.mark.parametrize("kind", ["supported_by", "bonded_to", "sealed_to", "terminates_against"])
def test_the_kind_does_not_choose_the_plane_a_shaped_object_is_measured_on(kind):
    """Seen live: kinds were swapped to supported_by, the only one measured at the foot."""
    raw = _shaped((kind, "supported_by"))
    meshes = {"pad": _box("pad", 50, 10, 150, 20), "plate": _bent_plate(), "foot": _box("foot", 60, 24, 140, 40)}
    check = _checks(raw, meshes, cad_planes={"plate": PLATE_PLANES, "foot": FOOT_PLANES})[f"assembly-0-{kind}"]
    assert check["status"] == "PASS" and check["contact"] == 1


def test_a_shaped_object_that_does_not_reach_shows_its_real_gap():
    raw = _shaped(("supported_by", "supported_by"))
    meshes = {"pad": _box("pad", 50, 5, 150, 15), "plate": _bent_plate(), "foot": _box("foot", 60, 24, 140, 40)}
    check = _checks(raw, meshes, cad_planes={"plate": PLATE_PLANES, "foot": FOOT_PLANES})["assembly-0-supported_by"]
    assert check["status"] == "FAIL" and "5 to 5 mm apart" in check["reason"]


def test_a_solid_with_no_flat_face_that_way_is_not_measured():
    raw = _shaped(("supported_by", "supported_by"))
    meshes = {"pad": _box("pad", 50, 10, 150, 20), "plate": _bent_plate(), "foot": _box("foot", 60, 24, 140, 40)}
    up_only = [plane for plane in PLATE_PLANES if plane[0][1] > 0]
    check = _checks(raw, meshes, cad_planes={"plate": up_only, "foot": FOOT_PLANES})["assembly-0-supported_by"]
    assert check["status"] == "UNVERIFIED" and check["required"] and "B-Rep plane" in check["reason"]


# ------------------------------------------------------------------ authoring gates

def _raw():
    raw = {"version": "3.0", "title": "Blocks", "kind": "section", "situation": [], "assembly_class": "door_threshold",
           "params": [{"name": "width", "value": 100, "unit": "mm", "kind": "exact", "provenance": "assumption"}],
           "parts": [{"id": "plate", "type": "sub_sill", "label": None, "class_id": "sub_sill", "shape": "cad", "role": "object",
                      "form": "shaped", "cad_brief": "a bent plate", "install_after": [], "features": [],
                      "dims": [{"name": n, "expr": e} for n, e in (("x0", "0"), ("x1", "width"), ("y0", "0"), ("y1", "width"),
                                                                   ("z0", "0"), ("z1", "width"))],
                      "param_bindings": {"width": "width"}}], "relations": [], "unresolved": []}
    return raw


def test_a_shaped_object_cannot_be_redeclared_a_block_to_clear_its_form_check():
    """Seen live: a flat plate failed object-form, and the patch was form=block."""
    first = prepare(_raw(), SOURCE, "d", READ)[0].model_dump(mode="json")
    lowered = _raw()
    lowered["parts"][0]["form"] = "block"
    with pytest.raises(ValueError, match="declared form=shaped"):
        prepare(lowered, SOURCE, "d", READ, first)
    prepare(lowered, SOURCE, "d", READ, first, human=True)  # a reviewer may settle it
    prepare(lowered, SOURCE, "d", READ)  # and a first draft may simply say block


def test_a_part_cannot_be_related_to_itself():
    raw = _raw()
    raw["relations"] = [{"source": "plate", "target": "plate", "kind": "bonded_to", "constraints": dict(TOUCHING)}]
    with pytest.raises(ValueError, match="cannot be measured against itself"):
        prepare(raw, SOURCE, "d", READ)


def _inferred(part, slot="sub_sill"):
    from app import ontology
    known = ontology.load()
    part.update(provenance="inferred", shape="box", inferred_by=f"archetype:door_threshold@{known.version}:{slot}")


def test_a_part_the_drawing_prints_cannot_be_called_inferred():
    """A ghost is never measured, so this would take a part out of verification."""
    raw = _raw()
    raw["parts"][0]["label"] = "Wide deck"
    raw["parts"][0].update(role="finish", form=None, class_id=None)
    _inferred(raw["parts"][0])
    with pytest.raises(ValueError, match="printed on the drawing"):
        prepare(raw, SOURCE, "d", READ)


def test_a_part_drawn_in_an_earlier_version_cannot_become_inferred():
    first = prepare(_raw(), SOURCE, "d", READ)[0].model_dump(mode="json")
    later = _raw()
    _inferred(later["parts"][0])
    with pytest.raises(ValueError, match="take it out of verification"):
        prepare(later, SOURCE, "d", READ, first)


# ------------------------------------------------------------------ what a version says

def test_the_replayed_shape_of_a_lowered_mark_is_reported_with_its_numbers():
    """A FAIL says what was measured, so the agent cannot mistake it for a tolerance."""
    raw, meshes = _strip_on_deck()
    raw["relations"] = [_relation("strip", "deck", "covers", min_coverage=0.01)]
    check = _checks(raw, meshes)["assembly-0-covers"]
    assert "0.7%" in check["reason"] and check["coverage"] == pytest.approx(4 / 550) and check["contact"] == 1


def test_no_label_is_called_verbatim_on_a_models_word_alone():
    read = {"elements": [{"label_verbatim": "A label"}]}
    assert label_check({"elements": []}) is None
    assert label_check(read)["status"] == "UNVERIFIED" and "not confirmed" in label_check(read)["reason"]
    proofread = {**read, "label_check": {"method": "proofread", "corrected": [], "unconfirmed": []}}
    assert label_check(proofread)["status"] == "UNVERIFIED" and "no text layer" in label_check(proofread)["reason"]
    assert not label_check(proofread)["required"]
    checked = {**read, "label_check": {"method": "text_layer", "corrected": [], "unconfirmed": []}}
    assert label_check(checked)["status"] == "PASS"
    partial = {**read, "label_check": {"method": "text_layer", "corrected": [], "unconfirmed": ["A label"]}}
    assert label_check(partial)["status"] == "UNVERIFIED" and label_check(partial)["unconfirmed"] == ["A label"]
