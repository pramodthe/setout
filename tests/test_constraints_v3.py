"""Regression checks for wrong internal geometry, lost evidence and layer order."""
import copy

import pytest

from app import database as d
from app.agent_tools import Tools
from app.cad_components import check_component, measure_features
from app.native_program import NativeProgram
from app.verification_v3 import spatial_checks, prepare_verification
from app.geometry import Mesh
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo, program
from app.worker import Worker


def test_wrong_bore_does_not_pass_correct_outer_bounds():
    report = {"bounds": [0, 0, 0, 40, 30, 10], "faces": [
        {"kind": "CYLINDER", "radius": 6, "axis": [0, 0, 1], "location": [20, 15, 0], "internal": True}]}
    features = [{"id": "width", "kind": "extent", "selector": {"axis": "x"}, "expected_expr": "w"},
                {"id": "bore", "kind": "hole_diameter", "selector": {"axis": "y"}, "expected_expr": "bore", "tolerance": 0.1}]
    _, checks = measure_features(report, features, {"w": 40, "bore": 10})
    assert [c["status"] for c in checks] == ["PASS", "FAIL"]
    assert checks[1]["actual"] == 12


def test_unsupported_or_ambiguous_measurement_stays_unverified():
    face = {"kind": "CYLINDER", "radius": 5, "axis": [0, 0, 1], "location": [0, 0, 0], "internal": True}
    _, checks = measure_features({"bounds": [0,0,0,40,30,10], "faces": [face, face]}, [
        {"id": "bore", "kind": "hole_diameter", "selector": {"axis": "y"}, "expected_expr": "bore"},
        {"id": "wall", "kind": "wall_thickness", "selector": {}, "expected_expr": "wall"}], {"bore": 10, "wall": 2})
    assert all(c["status"] == "UNVERIFIED" for c in checks)


def test_component_sizes_must_be_named():
    with pytest.raises(ValueError, match="named"):
        check_component('import cadquery as cq\ndef build(p):\n return cq.Workplane("XY").box(p["w"], 20, p["h"])')
    for expression in ('p["w"] + 20', 'hidden'):
        with pytest.raises(ValueError, match="named"):
            check_component('import cadquery as cq\ndef build(p):\n hidden = 20\n return cq.Workplane("XY").box('+expression+', p["d"], p["h"])')
    with pytest.raises(ValueError,match="named"):
        check_component('import cadquery as cq\ndef build(p):\n wall = 10.0\n return cq.Workplane(obj=cq.Solid.makeBox(p["w"], wall, p["h"]))')
    with pytest.raises(ValueError,match="named"):
        check_component('import cadquery as cq\ndef build(p):\n return cq.Workplane("XY").box(length=p["w"], width=20, height=p["h"])')
    for operation in ('.center(20,0)', '.workplane(offset=20)', '.pushPoints([(0,0),(20,20)])', '.rarray(20,p["d"],2,2)'):
        with pytest.raises(ValueError,match="named"):
            check_component('import cadquery as cq\ndef build(p):\n return cq.Workplane("XY")'+operation+'.box(p["w"],p["d"],p["h"])')
    with pytest.raises(ValueError,match="scaling"):
        check_component('import cadquery as cq\ndef build(p):\n return cq.Workplane("XY").box(p["w"],p["d"],p["h"]).scale(p["factor"])')


def test_declared_shaped_object_cannot_be_its_envelope_block():
    from types import SimpleNamespace
    from app.verification_v3 import object_form_check
    bounds = [0,0,0,360,650,800]
    solid = {"bounds":bounds,"volume":360*650*800}
    # The check reads the declaration, never the part's name or trade.
    for name in ("wc_pan", "anchor_bracket", "cladding_clip"):
        part = SimpleNamespace(id="p",type=name,label=None,role="object",form="shaped")
        result = object_form_check(part,solid)
        assert result["id"] == "object-form-p" and result["status"] == "FAIL" and result["required"]
        assert object_form_check(part,{"bounds":bounds,"volume":360*650*800*.4})["status"] == "PASS"
        assert object_form_check(part,{"bounds":bounds})["status"] == "UNVERIFIED"
    block = SimpleNamespace(id="p",type="wc_pan",label=None,role="object",form="block")
    assert object_form_check(block,solid) is None
    layer = SimpleNamespace(id="p",type="tile",label=None,role="finish",form=None)
    assert object_form_check(layer,solid) is None


def test_material_words_ignore_position_words():
    from app.verification_v3 import material_tokens
    assert material_tokens("membrane_wall_upturn") == {"membrane"}
    assert material_tokens("Insulation board, lower") == {"insulation","board"}
    assert material_tokens("wall") == {"wall"}


def _layer_program():
    raw = program()
    raw.update(detail_id="layers", origin="test", source={"filename": "own.pdf", "sha256": "a"*64,
               "page_index": 0, "crop_id": "p0_x", "page_width_pt": 100, "page_height_pt": 100})
    raw["parts"] = []
    raw["relations"] = []
    for i, (name, role) in enumerate((("membrane", "covered_layer"), ("adhesive", "covered_layer"), ("tile", "finish"))):
        part = copy.deepcopy(program()["parts"][0])
        part.update(id=name, type=name, role=role, features=[], install_after=[] if i == 0 else [["membrane", "adhesive"][i-1]])
        raw["parts"].append(part)
        if i:
            raw["relations"].append({"source": name, "target": ["membrane", "adhesive"][i-1], "kind": "covers",
                "constraints": {"axis": "y", "source_face": "min", "target_face": "max", "min_gap": -0.01, "max_gap": 0.01, "min_coverage": 1}})
    return NativeProgram.model_validate(raw)


def _mesh(name, y0, y1, z=0):
    return Mesh(name, positions=[0,y0,z,100,y0,z,100,y1,z+100])


def test_displaced_membrane_and_indirect_install_order_fail():
    native = _layer_program()
    meshes = {"membrane": _mesh("membrane", 0, 1), "adhesive": _mesh("adhesive", 1, 6), "tile": _mesh("tile", 6, 16)}
    assert all(c["status"] == "PASS" for c in spatial_checks(native, meshes))
    meshes["membrane"] = _mesh("membrane", 0, 1, z=300)
    assert any(c["status"] == "FAIL" for c in spatial_checks(native, meshes))
    meshes["membrane"] = _mesh("membrane", 0, 1)
    native.parts[1].install_after = []
    native.parts[0].install_after = ["tile"]
    assert any(c["status"] == "FAIL" for c in spatial_checks(native, meshes))


def test_removing_source_evidence_does_not_clear_required_checks(source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo)).run_once()
    version_id = repo.run(job["id"])["working_version_id"]
    detail_id = repo.get(d.versions, version_id)["detail_id"]
    repo.enqueue("revise_detail", {"detail_id": detail_id, "base_version_id": version_id})
    fence = repo.claim("edit")
    modified = program()
    modified["params"][0]["provenance"] = "assumption"
    modified["parts"][0]["label"] = None
    result = Tools(repo, fence).run("edit", "patch_program", {"expected_version_id": version_id, "program": modified})
    report = prepare_verification(repo, result["version_id"])
    statuses = {c["id"]: c["status"] for c in report["checks"]}
    assert statuses["evidence-retention"] == "FAIL"
    assert statuses["source-dimension-d0"] == "UNVERIFIED"
    # An unchanged follow-up patch must not erase the lost-evidence obligation.
    second = Tools(repo, fence).run("again", "patch_program", {"edits":[
        {"op":"replace", "path":"/title", "value":"Wall revised again"}]})
    checks = prepare_verification(repo, second["version_id"])["checks"]
    assert next(c for c in checks if c["id"] == "evidence-retention")["status"] == "FAIL"


def test_numeric_substring_cannot_claim_a_different_drawing_value(source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id":"figure"})
    model = ScriptedModel()
    from app.model_gateway import ModelGateway
    fence = repo.claim("source-gate")
    tools = Tools(repo, fence, ModelGateway(repo, fence, model))
    tools.run("read", "read_drawing", {})
    raw = program()
    raw["params"][0].update(value=1, source_quote="1")
    result = tools.run("write", "write_program", {"program":raw})
    version = repo.get(d.versions, result["version_id"])
    assert version["program"]["params"][0]["provenance"] == "assumption"
    report = prepare_verification(repo, version["id"])
    assert next(c for c in report["checks"] if c["id"] == "source-dimension-d0")["status"] == "UNVERIFIED"


def _read(category):
    return {"elements": [{"label_verbatim": "LABELLED ITEM", "kind": "", "where": "", "category": category}]}


def test_layers_cannot_be_reclassified_to_skip_coverage_or_cad_policy():
    from app.native_program import check_roles
    program = _layer_program()
    program.parts[0].label = "Labelled item"
    with pytest.raises(ValueError, match="as a layer"):
        check_roles(program.model_copy(update={"parts": [program.parts[0].model_copy(update={
            "role": "object", "shape": "cad", "form": "shaped"})]}), _read("layer"))
    check_roles(program, _read("layer"))


def test_dimension_units_reject_hidden_physical_offsets():
    from app.native_program import expression_unit
    assert expression_unit("height / 2", {"height":"mm"}) == "mm"
    with pytest.raises(ValueError, match="matching units"):
        expression_unit("height + 200", {"height":"mm"})


@pytest.mark.parametrize("kind",["sanitary_fixture","cladding_clip","anchor","pipe_flashing"])
def test_manufactured_objects_cannot_be_primitive_finishes(kind):
    from app.native_program import check_roles
    program = _layer_program()
    part = program.parts[2].model_copy(update={"type": kind, "label": "Labelled item"})
    with pytest.raises(ValueError,match="manufactured object"):
        check_roles(program.model_copy(update={"parts": [part]}), _read("object"))
    # Roles come from the drawing reading, never from our own vocabulary:
    # with no classification the part's name alone decides nothing.
    check_roles(program.model_copy(update={"parts": [part]}), _read(None))


def test_objects_declare_their_form_unless_a_reviewer_is_correcting_values():
    from app.native_program import check_roles
    program = _layer_program()
    part = program.parts[2].model_copy(update={"role": "object", "shape": "cad", "form": None})
    with pytest.raises(ValueError, match="form=shaped or form=block"):
        check_roles(program.model_copy(update={"parts": [part]}), {})
    check_roles(program.model_copy(update={"parts": [part]}), {}, human=True)
    with pytest.raises(ValueError, match="only on objects"):
        raw = program.model_dump(mode="json")
        raw["parts"][2]["form"] = "block"
        NativeProgram.model_validate(raw)


def test_sloped_interfaces_measure_contact_and_coverage_without_bounding_box_shortcuts():
    from app.geometry import _prism
    from app.interfaces import measure_interface
    screed, membrane = Mesh("screed"),Mesh("membrane")
    _prism(screed, [(0,0),(100,0),(100,10),(0,20)],0,100)
    _prism(membrane, [(0,20),(100,10),(100,11),(0,21)],0,100)
    measured = measure_interface(membrane,screed,1,"min","max")
    assert measured["coverage"] == pytest.approx(1)
    assert measured["min_gap"] == pytest.approx(0)
    assert measured["max_gap"] == pytest.approx(0)
    membrane.positions = [v+5 if i%3 == 1 else v for i,v in enumerate(membrane.positions)]
    moved = measure_interface(membrane,screed,1,"min","max")
    assert moved["min_gap"] == pytest.approx(5)
    flat = Mesh("flat")
    _prism(flat, [(0,20),(100,20),(100,21),(0,21)],0,100)
    detached = measure_interface(flat,screed,1,"min","max")
    assert detached["min_gap"] == pytest.approx(0)
    assert detached["max_gap"] == pytest.approx(10)


def test_cad_support_uses_the_measured_foot_face_not_the_overhanging_body():
    from app.geometry import _box_tris
    from app.interfaces import measure_interface,matches_brep_planes
    body,floor = Mesh("fixture"),Mesh("floor")
    _box_tris(body,0,0,0,100,20,100)
    _box_tris(body,10,20,10,90,30,90)
    _box_tris(body,-50,30,-50,150,50,150)
    _box_tris(floor,-500,-20,-500,500,0,500)
    planes = [([0,1,0],[50,0,50])]
    assert matches_brep_planes(body,1,"min",planes,extremal=True)
    measured = measure_interface(body,floor,1,"min","max",False,source_extremal=True)
    assert measured == {"coverage":1,"min_gap":0,"max_gap":0}
    body.positions = [value+5 if i%3 == 1 else value for i,value in enumerate(body.positions)]
    assert measure_interface(body,floor,1,"min","max",False,source_extremal=True)["min_gap"] == pytest.approx(5)


def test_countertop_support_uses_cad_top_rim_without_internal_faces():
    from app.geometry import _box_tris
    from app.interfaces import measure_interface, matches_brep_planes
    cabinet,counter = Mesh("cabinet"),Mesh("counter")
    # Dense recessed body faces must not enter the top contact or its coverage.
    for i in range(100):
        _box_tris(cabinet,0,i,0,100,i+0.5,100)
    _box_tris(cabinet,0,100,0,10,120,100)
    _box_tris(cabinet,90,100,0,100,120,100)
    _box_tris(counter,0,120,0,100,130,100)
    assert matches_brep_planes(cabinet,1,"max",[([0,1,0],[0,120,0])],extremal=True)
    measured = measure_interface(counter,cabinet,1,"min","max",False,target_extremal=True)
    assert measured == {"coverage":pytest.approx(.2),"min_gap":0,"max_gap":0}
    counter.positions = [v+5 if i%3 == 1 else v for i,v in enumerate(counter.positions)]
    assert measure_interface(counter,cabinet,1,"min","max",False,target_extremal=True)["max_gap"] == pytest.approx(5)


def test_identifier_normalization_preserves_expression_and_relation_references(source_repo):
    from app.model_gateway import ModelGateway
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    fence = repo.claim("normalize")
    tools = Tools(repo,fence,ModelGateway(repo,fence,ScriptedModel()))
    tools.run("read","read_drawing",{})
    candidate = program()
    candidate["params"][0]["name"] = "Printed_Height"
    candidate["parts"][0]["id"] = "Wall"
    candidate["parts"][0]["dims"][3]["expr"] = "0 + Printed_Height"
    candidate["parts"][0]["features"][0]["expected_expr"] = "Printed_Height"
    result = tools.run("write","write_program",{"program":candidate})
    assert result.get("version_id"),result
    version = repo.get(d.versions,result["version_id"])
    assert version["program"]["parts"][0]["id"] == "wall"
    assert next(c for c in result["checks"] if c["id"] == "source-dimension-d0")["status"] == "PASS"


@pytest.mark.parametrize("human_accepted",[False,True])
def test_human_interfaces_are_protected_while_ai_proposals_can_be_repaired(source_repo,human_accepted):
    from app.model_gateway import ModelGateway
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    fence = repo.claim("interfaces")
    tools = Tools(repo,fence,ModelGateway(repo,fence,ScriptedModel()))
    tools.run("read","read_drawing",{})
    candidate = program()
    tile = copy.deepcopy(candidate["parts"][0])
    tile.update(id="tile",type="tile",role="finish",label=None,features=[],install_after=["wall"])
    tile["dims"][0]["expr"] = "width"
    tile["dims"][1]["expr"] = "width+depth"
    candidate["parts"].append(tile)
    candidate["relations"] = [{"source":"tile","target":"wall","kind":"covers","constraints":{
        "axis":"x","source_face":"min","target_face":"max","min_gap":-.01,"max_gap":.01,"min_coverage":1}}]
    first = tools.run("write","write_program",{"program":candidate})
    if human_accepted:
        with d.transaction(repo.engine) as con:
            repo.review_event(con,first["version_id"],"Test reviewer","accept",{})
    changed = tools.run("change","patch_program",{"edits":[{"op":"remove","path":"/relations/0"}]})
    retained = next(c for c in changed["checks"] if c["id"] == "assembly-requirements-retained")
    assert retained["status"] == ("FAIL" if human_accepted else "PASS")


def test_component_reuse_refuses_changed_binding_units_before_cad(source_repo):
    from app.model_gateway import ModelGateway
    from app.repository import sha
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    fence = repo.claim("unit-check")
    tools = Tools(repo,fence,ModelGateway(repo,fence,ScriptedModel()))
    tools.run("read","read_drawing",{})
    candidate = program()
    candidate["parts"][0].update(type="custom_object",role="object",form="block",shape="cad",cad_brief="Custom parametric object",param_bindings={"size":"height"})
    written = tools.run("write","write_program",{"program":candidate})
    code = 'import cadquery as cq\ndef build(p):\n return cq.Workplane("XY").box(p["size"],p["size"],p["size"])'
    with d.transaction(repo.engine) as con:
        con.execute(d.components.insert().values(id="ratio-component",family_key="custom_object",code=code,code_sha=sha(code),
            param_schema={"size":{"unit":"ratio"}},feature_schema=[],brief="Dimensionless binding",created_at=d.now(con)))
    result = tools.run("reuse","reuse_component",{"version_id":written["version_id"],"part_key":"wall","component_id":"ratio-component"})
    assert result["error"]["code"] == "invalid_tool_input"
    assert "unit mismatch" in result["error"]["message"]
    assert repo.run(job["id"])["consumed"].get("cad_calls",0) == 0


def test_label_coverage_lists_drawing_parts_nobody_carries():
    from app.verification_v3 import label_coverage
    program = _layer_program()
    program.parts[0].label = "MEMBRANE"
    read = {"elements": [{"label_verbatim": "MEMBRANE", "category": "layer"},
                         {"label_verbatim": "TILE", "category": "layer"},
                         {"label_verbatim": "SHOWER AREA", "category": "other"}]}
    result = label_coverage(program, read)
    assert result["status"] == "UNVERIFIED" and not result["required"] and result["missing"] == ["TILE"]
    program.parts[2].label = "TILE"
    assert label_coverage(program, read)["status"] == "PASS"
    assert label_coverage(program, {"elements": [{"label_verbatim": "TILE"}]}) is None


def test_authoring_schema_offers_only_drawing_labels_and_agent_provenance():
    from app.program_schema import program_schema
    schema = program_schema(["WALL", "WALL", "SLAB", ""])
    part = schema["properties"]["parts"]["items"]["properties"]
    assert part["label"] == {"anyOf": [{"type": "string", "enum": ["SLAB", "WALL"]}, {"type": "null"}]}
    assert schema["properties"]["params"]["items"]["properties"]["provenance"]["enum"] == ["drawing", "assumption"]
    assert program_schema([])["properties"]["parts"]["items"]["properties"]["label"] == {"type": "null"}


def test_coverage_given_as_a_percentage_is_rejected_when_authoring():
    from app.native_program import check_roles
    program = _layer_program()
    program.relations[0].constraints["min_coverage"] = 90
    with pytest.raises(ValueError, match="fraction from 0 to 1"):
        check_roles(program, {})
    NativeProgram.model_validate(program.model_dump(mode="json"))  # stored versions still load
