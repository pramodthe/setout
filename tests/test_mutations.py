"""Mutation tests: each known-bad change to a known-good program must FAIL a
named check, and the completeness tier and the completion gate must follow.

The good program is a floor build-up (slab, 1.5 mm membrane, 10 mm tile)
stored through the authoring gates and verified like any agent draft. The
door threshold cases build their parts from the ontology's own required
slots, so they name slots and classes from library data, never from code."""
import pytest

from app import database as d
from app.native_program import NativeProgram, prepare
from app.repository import outcome_tier, sha
from app.verification_v3 import prepare_verification, slot_checks
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo  # noqa: F401

READ = {"drawing_type": "section", "rotation_deg": 0, "title": "Floor build-up",
        "elements": [{"label_verbatim": label, "kind": "label", "where": "", "category": "layer"}
                     for label in ("Slab", "Membrane", "Tile")],
        "dimensions": [{"value_mm": 150, "quote_verbatim": "150mm", "applies_to": "slab thickness"}]}
THRESHOLD_READ = {"drawing_type": "section", "rotation_deg": 0, "title": "Door threshold", "elements": [], "dimensions": []}
SOURCE = {"filename": "own.pdf", "sha256": "a" * 64, "page_index": 0, "crop_id": "c", "page_width_pt": 1, "page_height_pt": 1}
COVERS = {"axis": "y", "source_face": "min", "target_face": "max", "min_gap": -0.01, "max_gap": 0.01, "min_coverage": 1}
DOOR = "door_threshold"


def param(name, value, **extra):
    return {"name": name, "value": value, "unit": "mm", "kind": "exact", "provenance": "assumption", **extra}


def box(ident, label, role, y0, y1, after=(), features=()):
    dims = [{"name": n, "expr": e} for n, e in (("x0", "0"), ("x1", "run"), ("y0", y0), ("y1", y1))]
    return {"id": ident, "type": ident, "label": label, "shape": "box", "role": role, "dims": dims,
            "install_after": list(after), "features": list(features)}


def sheet(ident, label, role, centre, after=()):
    return {"id": ident, "type": ident, "label": label, "shape": "sheet", "role": role,
            "dims": [{"name": "thickness", "expr": "membrane_t"}],
            "path": [{"x": "0", "y": centre}, {"x": "run", "y": centre}], "install_after": list(after), "features": []}


def covers(source, target, **constraints):
    return {"source": source, "target": target, "kind": "covers", "constraints": {**COVERS, **constraints}}


def layers():
    """Slab, 1.5 mm membrane, 10 mm tile: a floor build-up that verifies clean."""
    return {"title": "Floor build-up", "kind": "section", "situation": [], "unresolved": [],
            "params": [param("slab_t", 150, provenance="drawing", source_quote="150mm", source_dimension_id="d0"),
                       param("membrane_t", 1.5), param("tile_t", 10), param("run", 600)],
            "parts": [box("slab", "Slab", "substrate", "-slab_t", "0",
                          features=[{"id": "thickness", "kind": "extent", "selector": {"axis": "y"},
                                     "expected_expr": "slab_t", "source_dimension_id": "d0"}]),
                      sheet("membrane", "Membrane", "covered_layer", "membrane_t / 2", after=["slab"]),
                      box("tile", "Tile", "finish", "membrane_t", "membrane_t + tile_t", after=["membrane"])],
            "relations": [covers("membrane", "slab"), covers("tile", "membrane")]}


def without_membrane(raw):
    """An agent that leaves the drawn membrane out and beds the tile on the slab."""
    raw["parts"] = [p for p in raw["parts"] if p["id"] != "membrane"]
    tile = raw["parts"][1]
    tile["install_after"] = ["slab"]
    tile["dims"][2]["expr"], tile["dims"][3]["expr"] = "0", "tile_t"
    raw["relations"] = [covers("tile", "slab")]
    return raw


def native(raw):
    return NativeProgram.model_validate({**raw, "detail_id": "t", "origin": "test", "source": SOURCE})


def verified(repo, raw, read=READ, parent=None):
    """Store `raw` through the authoring gates as a version of detail "mutant"
    (a successor of `parent` when given) and verify it."""
    document = repo.get(d.documents, "doc")
    source = {**SOURCE, "sha256": document["sha256"], "crop_id": "p0_wall"}
    program, spec, _ = prepare(raw, source, "mutant", read)
    read_id = "read-" + sha(read)[:12]
    known = repo.list(d.reads, d.reads.c.id == read_id)
    with d.transaction(repo.engine) as con:
        if not known:
            con.execute(d.reads.insert().values(id=read_id, figure_id="figure", model="test", response=read,
                                                 content_sha=sha(read), created_at=0))
        made = repo.insert_version(con, detail_id="mutant", expected_parent=parent, program=program.model_dump(mode="json"),
                                   spec=spec.model_dump(mode="json"), figure_id="figure", read_id=read_id)
    return made["version_id"], prepare_verification(repo, made["version_id"])


def fails(report):
    return sorted(c["id"] for c in report["checks"] if c["status"] == "FAIL")


def by_id(report):
    return {c["id"]: c for c in report["checks"]}


# (f) The unmodified program gains no FAIL from the new checks.
def test_the_good_program_verifies_clean(source_repo):
    _, report = verified(source_repo, layers())
    checks = by_id(report)
    assert fails(report) == []
    assert not [i for i, c in checks.items() if c.get("required") and c["status"] != "PASS"]
    assert report["quality"] == {"outcome": "verified", "inferred_parts": []}
    assert report["scene"]["quality"] == report["quality"]
    assert checks["layer-order:slab:membrane"]["status"] == checks["layer-order:membrane:tile"]["status"] == "PASS"
    assert checks["layer-order:membrane:tile"]["probes"] == 9
    assert checks["assembly-0-covers"]["contact_tolerance"] == 0.75  # half the 1.5 mm membrane
    assert checks["assembly-class-unset"]["status"] == "UNVERIFIED" and not checks["assembly-class-unset"]["required"]
    assert checks["source-dimension-d0"]["status"] == "PASS"


# (a) Deleting the membrane: the drawing's evidence for it is lost.
def test_deleting_the_membrane_from_a_verified_detail_fails(source_repo):
    base_id, base = verified(source_repo, layers())
    assert fails(base) == []
    _, report = verified(source_repo, without_membrane(layers()), parent=base_id)
    checks = by_id(report)
    assert "evidence-retention" in fails(report)
    assert checks["evidence-retention"]["lost"] == ["label:Membrane"]
    assert checks["label-coverage"]["missing"] == ["Membrane"]
    assert checks["assembly-0-covers"]["status"] == "PASS"  # the tile does bed on the slab: only the evidence says a layer is gone


def test_a_fresh_program_without_the_drawn_membrane_is_flagged_not_failed(source_repo):
    """No ancestor, no assembly class: the only signal is the READ's layer
    label nobody carries, which label-coverage reports UNVERIFIED and not
    required (a v3 decision). The door_threshold archetype lists its membrane
    slot as optional, so a slot check cannot catch this either. This pins
    today's behaviour; making it a FAIL is a policy decision for the owner."""
    _, report = verified(source_repo, without_membrane(layers()))
    checks = by_id(report)
    assert fails(report) == []
    assert checks["label-coverage"]["status"] == "UNVERIFIED" and checks["label-coverage"]["missing"] == ["Membrane"]
    assert not checks["label-coverage"]["required"]


# (b) Swapping two layers: the relations still say tile over membrane.
def test_swapping_two_layers_fails_the_order_probe(source_repo):
    raw = layers()
    tile = raw["parts"][2]
    tile["dims"][2]["expr"], tile["dims"][3]["expr"] = "0", "tile_t"
    raw["parts"][1]["path"] = [{"x": "0", "y": "tile_t + membrane_t / 2"}, {"x": "run", "y": "tile_t + membrane_t / 2"}]
    _, report = verified(source_repo, raw)
    checks = by_id(report)
    assert "layer-order:membrane:tile" in fails(report)
    probe = checks["layer-order:membrane:tile"]
    assert probe["order"] == ["membrane", "tile"] and probe["violations"] == probe["probes"] == 9
    assert probe["example"]["first"] == pytest.approx([10, 11.5]) and probe["example"]["second"] == pytest.approx([0, 10])
    assert checks["layer-order:slab:membrane"]["status"] == "PASS"  # order, not contact: the slab is still below
    assert "assembly-1-covers" in fails(report)  # the interface measurement agrees


# (c) A 1.5 mm layer floating 1.8 mm above its substrate, inside the old 2 mm band.
def test_a_thin_layer_floating_under_the_old_tolerance_fails(source_repo):
    raw = layers()
    raw["params"].append(param("gap", 1.8))
    raw["parts"][1]["path"] = [{"x": "0", "y": "gap + membrane_t / 2"}, {"x": "run", "y": "gap + membrane_t / 2"}]
    raw["parts"][2]["dims"][2]["expr"], raw["parts"][2]["dims"][3]["expr"] = "gap + membrane_t", "gap + membrane_t + tile_t"
    raw["relations"][0] = covers("membrane", "slab", max_gap=2.0)  # the old flat band, declared by the author
    _, report = verified(source_repo, raw)
    checks = by_id(report)
    assert "assembly-0-covers" in fails(report)
    assert checks["assembly-0-covers"]["min_gap"] == pytest.approx(1.8)
    assert checks["assembly-0-covers"]["contact_tolerance"] == 0.75 and checks["assembly-0-covers"]["gap_band"] == [-0.01, 0.75]
    assert checks["layer-order:slab:membrane"]["status"] == "PASS"  # still the right order, just not in contact
    raw["params"][-1]["value"] = 0  # the same declaration passes once the membrane really beds on the slab
    _, report = verified(source_repo, raw)
    assert fails(report) == [] and checks["assembly-0-covers"]["gap_band"] == [-0.01, 0.75]


# An inferred part's measured sizes are never a verified PASS, even when a
# printed dimension backs them, and an empty slot outranks inferred ones.
def test_inferred_parts_are_not_verified_evidence(source_repo):
    ont = pytest.importorskip("app.ontology").load()
    slot = next((s for s in ont.required_slots(DOOR) if ont.component(s.classes[0]).role != "object"), None)
    if slot is None:
        pytest.skip(f"every required slot of {DOOR} is an object: CAD placeholders measure no features")
    component = ont.component(slot.classes[0])
    raw = layers()
    raw["assembly_class"] = DOOR
    raw["parts"][0].update(role=component.role, class_id=component.id, provenance="inferred", confidence=0.5,
                           inferred_by=f"archetype:{DOOR}@{ont.version}:{slot.id}")
    _, report = verified(source_repo, raw)
    checks = by_id(report)
    assert checks["feature-slab-thickness"]["status"] == "UNVERIFIED" and not checks["feature-slab-thickness"]["required"]
    assert checks["source-dimension-d0"]["status"] == "UNVERIFIED"  # the printed 150 mm proves nothing about an inferred part
    assert checks[f"slot-inferred:{slot.id}"]["status"] == "UNVERIFIED" and checks[f"slot-inferred:{slot.id}"]["parts"] == ["slab"]
    assert report["quality"]["inferred_parts"] == ["slab"]
    assert report["quality"]["outcome"] == "incomplete"  # the other required slots are still empty
    assert report["scene"]["quality"] == report["quality"]


# ---------------------------------------------------------------- the door threshold

@pytest.fixture
def ont():
    ontology = pytest.importorskip("app.ontology")
    library = ontology.load()
    if library.assembly(DOOR) is None:
        pytest.skip(f"{DOOR} is not in the ontology")
    return library


def archetype(ont, visibilities, inferred=()):
    """A program filling the assembly's required slots whose section
    visibility is in `visibilities`, from library data alone. Slots named in
    `inferred` are filled by inferred parts, as archetype completion does."""
    raw = {"title": "Door threshold", "kind": "section", "situation": [], "unresolved": [], "assembly_class": DOOR,
           "params": [param("size", 100), param("run", 600)], "parts": [], "relations": []}
    for slot in ont.required_slots(DOOR):
        if slot.section_visibility not in visibilities:
            continue
        component = ont.component(slot.classes[0])
        for k in range(slot.min):
            ident = f"{slot.id}_{k}"
            part = {"id": ident, "type": ident, "label": None, "role": component.role, "class_id": component.id,
                    "shape": "box", "install_after": [], "features": [],
                    "dims": [{"name": n, "expr": e} for n, e in (("x0", "0"), ("x1", "size"), ("y0", "0"),
                                                                 ("y1", "size"), ("z0", "0"), ("z1", "run"))]}
            if component.role == "object":
                part.update(shape="cad", form=component.form or "block", cad_brief=component.name)
            if slot.id in inferred:
                part.update(provenance="inferred", confidence=0.5, inferred_by=f"archetype:{DOOR}@{ont.version}:{slot.id}")
            raw["parts"].append(part)
    return raw


# (d) Extrude only: the cut parts alone are an incomplete threshold.
def test_extrude_only_threshold_is_incomplete(ont, source_repo):
    hidden = [s for s in ont.required_slots(DOOR) if s.section_visibility != "cut"]
    assert hidden, f"{DOOR} declares no required slot beyond the cut"
    checks = {c["id"]: c for c in slot_checks(native(archetype(ont, {"cut"})))}
    for slot in hidden:
        missing = checks[f"slot-missing:{slot.id}"]
        assert missing["status"] == "FAIL" and missing["required"]
        assert missing["found"] == [] and missing["min"] == slot.min and missing["section_visibility"] == slot.section_visibility
    assert "slot-missing:jamb" in checks  # the spec's own example of the missing door
    assert not [i for i in checks if i.startswith(("class-", "assembly-"))]
    # Through the whole verifier: the prism is an incomplete version.
    _, report = verified(source_repo, archetype(ont, {"cut"}), read=THRESHOLD_READ)
    assert {f"slot-missing:{s.id}" for s in hidden} <= set(fails(report))
    assert report["quality"] == {"outcome": "incomplete", "inferred_parts": []}


# (e) Deleting a jamb (or any required part) from a complete program.
def test_deleting_a_required_part_from_a_complete_threshold_fails(ont):
    complete = native(archetype(ont, {"cut", "beyond", "out_of_plane"}))
    assert [c for c in slot_checks(complete) if c["status"] == "FAIL"] == []
    for slot in ont.required_slots(DOOR):
        fillers = [p for p in complete.parts if p.class_id in slot.classes]
        one_short = complete.model_copy(update={"parts": [p for p in complete.parts if p is not fillers[0]]})
        checks = {c["id"]: c for c in slot_checks(one_short)}
        assert checks[f"slot-missing:{slot.id}"]["status"] == "FAIL"
        assert checks[f"slot-missing:{slot.id}"]["found"] == [p.id for p in fillers[1:]]
    assert "jamb" in {s.id for s in ont.required_slots(DOOR)}


def test_inferred_parts_complete_the_threshold_without_verifying_it(ont, source_repo):
    hidden = {s.id for s in ont.required_slots(DOOR) if s.section_visibility != "cut"}
    raw = archetype(ont, {"cut", "beyond", "out_of_plane"}, inferred=hidden)
    _, report = verified(source_repo, raw, read=THRESHOLD_READ)
    checks = by_id(report)
    assert not [i for i in checks if i.startswith("slot-missing:")]
    for slot in hidden:
        assert checks[f"slot-inferred:{slot}"]["status"] == "UNVERIFIED" and not checks[f"slot-inferred:{slot}"]["required"]
        assert checks[f"slot-inferred:{slot}"]["parts"]
    inferred = [p["id"] for p in raw["parts"] if p.get("provenance") == "inferred"]
    assert report["quality"] == {"outcome": "complete_with_inferred", "inferred_parts": inferred}
    # The same parts shown by the drawing: complete, but the objects are CAD
    # placeholders that FAIL, so no tier applies and nothing is promoted.
    _, report = verified(source_repo, archetype(ont, {"cut", "beyond", "out_of_plane"}), read=THRESHOLD_READ)
    assert fails(report) and all(i.startswith("cad-") for i in fails(report))
    assert report["quality"] == {"outcome": None, "inferred_parts": []}


# ---------------------------------------------------------------- tiers and the completion gate

def test_outcome_tiers():
    clean = [{"id": "x", "status": "PASS", "required": True},
             {"id": "assembly-class-unset", "status": "UNVERIFIED", "required": False}]
    assert outcome_tier(clean, []) == "verified"
    assert outcome_tier(clean + [{"id": "assembly-0-covers", "status": "FAIL", "required": True}], []) is None
    assert outcome_tier(clean + [{"id": "coverage-m", "status": "UNVERIFIED", "required": True}], []) is None
    assert outcome_tier(clean, ["jamb_0"]) == "complete_with_inferred"
    assert outcome_tier(clean + [{"id": "cad-jamb_0", "status": "FAIL", "required": True}], ["jamb_0"]) == "complete_with_inferred"
    assert outcome_tier(clean + [{"id": "slot-missing:jamb", "status": "FAIL", "required": True}], ["head_0"]) == "incomplete"


def _finished(repo):
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo)).run_once(only_ids=[job["id"]])
    return repo.list(d.critics)[0]


def _errors(repo, critic, **kw):
    with d.transaction(repo.engine) as con:
        return repo.completion_errors(con, critic["version_id"], critic["verification_id"], critic["id"], **kw)


def _set(repo, table, ident, **values):
    with d.transaction(repo.engine) as con:
        con.execute(table.update().where(table.c.id == ident).values(**values))


def test_completion_follows_the_outcome_tier(source_repo):
    critic = _finished(source_repo)
    assert _errors(source_repo, critic) == []
    record = source_repo.get(d.verifications, critic["verification_id"])
    quality = record["quality"]
    _set(source_repo, d.verifications, record["id"],
         quality={**quality, "outcome": "complete_with_inferred", "inferred_parts": ["jamb_0"]})
    assert _errors(source_repo, critic) == []
    assert _errors(source_repo, critic, open_issues=["membrane termination unresolved"]) == ["inferred_parts_not_in_open_issues:jamb_0"]
    assert _errors(source_repo, critic, open_issues=["jamb_0: inferred from the door_threshold archetype; confirm on site"]) == []
    _set(source_repo, d.verifications, record["id"], quality={**quality, "outcome": "incomplete", "inferred_parts": []})
    assert _errors(source_repo, critic) == ["incomplete"]
    _set(source_repo, d.verifications, record["id"], quality={**quality, "outcome": None, "inferred_parts": []})
    assert _errors(source_repo, critic) == []  # no tier: the ordinary FAIL and required rules decide


def test_completion_derives_the_tier_from_older_records(source_repo):
    critic = _finished(source_repo)
    record = source_repo.get(d.verifications, critic["verification_id"])
    quality = {"scene_artifact_id": record["quality"]["scene_artifact_id"]}  # written before outcomes existed
    _set(source_repo, d.verifications, record["id"], quality=quality,
         checks=record["checks"] + [{"id": "slot-missing:jamb", "status": "FAIL", "required": True}])
    assert _errors(source_repo, critic) == ["slot-missing:jamb", "incomplete"]
    _set(source_repo, d.verifications, record["id"], quality=quality, checks=record["checks"])
    version = source_repo.get(d.versions, critic["version_id"])
    program = version["program"]
    program["parts"][0]["provenance"] = "inferred"
    _set(source_repo, d.versions, version["id"], program=program)
    assert _errors(source_repo, critic) == []
    assert _errors(source_repo, critic, open_issues=[]) == ["inferred_parts_not_in_open_issues:wall"]
