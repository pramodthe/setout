"""Why the agent could not finish a real threshold section, and the fixes.

Each test pins one cause seen on a live run. The fixture is our own: a section
whose levels are chains of named datums, the way a real program writes them.
No network: Muse and Modal are stubbed.
"""
import copy
import json

import pytest

from app import cad_components, cad_sandbox, completion, database as d, ontology
from app.agent import INSTRUCTIONS
from app.agent_tools import Tools, completion_mode, model_checks, model_view
from app.model_gateway import ModelGateway
from app.native_program import _slot_claimed, anchor_placements, prepare
from app.program_edits import apply_edits
from app.program_schema import program_schema
from app.repository import REVIEW_TAIL, BudgetExceeded, billed_tokens, tokens_left
from app.verification_v3 import envelope_problem, slot_checks, spatial_checks
from app.worker import CONTINUE_REQUEST, Worker, continue_if_cut_short
from test_agent_v3 import FakeRenderer, response_json, source_repo  # noqa: F401
from test_api_v3 import client  # noqa: F401

READ = {"drawing_type": "section", "rotation_deg": 0, "title": "Terrace door",
        "elements": [{"label_verbatim": "Liquid applied layer", "kind": "label", "where": "left", "category": "layer"},
                     {"label_verbatim": "Door bottom track", "kind": "label", "where": "centre", "category": "object"},
                     {"label_verbatim": "Bead of mastic", "kind": "label", "where": "right", "category": "object"},
                     {"label_verbatim": "Floor line", "kind": "label", "where": "right", "category": "zone"}],
        "dimensions": [{"id": "d0", "value_mm": 75, "quote_verbatim": "75mm step down", "applies_to": "floor levels"}]}
SOURCE = {"filename": "own.pdf", "sha256": "a" * 64, "page_index": 0, "crop_id": "p0_x",
          "page_width_pt": 300, "page_height_pt": 300, "rotation_deg": 0}
# A chain of datums, as a real program writes a level.
LEVEL = ("structural_slab_top_level + liquid_applied_layer_thickness + bottom_tray_thickness_allowance + "
         "levelling_packer_stack_thickness + bedding_mastic_compressed_thickness")


def _dims(**expressions):
    return [{"name": k, "expr": v} for k, v in expressions.items()]


def _param(name, value, provenance="assumption", **extra):
    return {"name": name, "value": value, "unit": "mm", "kind": "exact", "provenance": provenance, **extra}


def threshold(placement=None):
    """A section as the writer leaves it: what the cut shows, levels as chains."""
    placed = placement or {"x": "0", "y": "0", "z": "0"}
    return {"version": "3.0", "title": "Terrace door", "kind": "section", "situation": [], "assembly_class": "door_threshold",
            "params": [_param("step_down", 75, "drawing", source_quote="75mm step down", source_dimension_id="d0"),
                       _param("structural_slab_top_level", 200), _param("liquid_applied_layer_thickness", 2),
                       _param("bottom_tray_thickness_allowance", 3), _param("levelling_packer_stack_thickness", 12),
                       _param("bedding_mastic_compressed_thickness", 6), _param("track_section_height", 70),
                       _param("track_section_width", 130), _param("track_centre_offset", 260), _param("slab_depth", 150),
                       _param("slab_length", 500), _param("run_length", 300)],
            "parts": [
                {"id": "slab", "type": "slab", "label": None, "class_id": "slab", "shape": "box", "role": "substrate",
                 "dims": _dims(x0="0", x1="slab_length", y0="structural_slab_top_level - slab_depth",
                               y1="structural_slab_top_level", z0="0", z1="run_length"), "install_after": [], "features": []},
                {"id": "layer", "type": "membrane", "label": "Liquid applied layer", "class_id": "membrane", "shape": "box",
                 "role": "covered_layer", "dims": _dims(x0="0", x1="slab_length", y0="structural_slab_top_level",
                 y1="structural_slab_top_level + liquid_applied_layer_thickness", z0="0", z1="run_length"),
                 "install_after": ["slab"], "features": []},
                {"id": "track", "type": "door_sill", "label": "Door bottom track", "class_id": "door_sill", "shape": "cad",
                 "role": "object", "form": "shaped", "cad_brief": "extruded door track with fins",
                 "dims": _dims(x0="track_centre_offset - 0.5 * track_section_width",
                               x1="track_centre_offset + 0.5 * track_section_width", y0=LEVEL,
                               y1=LEVEL + " + track_section_height", z0="0", z1="run_length"),
                 "param_bindings": {"width": "track_section_width", "depth": "run_length", "height": "track_section_height"},
                 "placement": placed, "install_after": ["layer"], "features": []},
                {"id": "bottom_tray", "type": "sub_sill", "label": None, "class_id": "sub_sill", "shape": "cad", "role": "object",
                 "form": "block", "cad_brief": "bent tray under the track",
                 "dims": _dims(x0="track_centre_offset - track_section_width", x1="track_centre_offset + track_section_width",
                               y0="structural_slab_top_level + liquid_applied_layer_thickness",
                               y1="structural_slab_top_level + liquid_applied_layer_thickness + bottom_tray_thickness_allowance",
                               z0="0", z1="run_length"),
                 "param_bindings": {"width": "track_section_width", "depth": "run_length", "height": "bottom_tray_thickness_allowance"},
                 "placement": placed, "install_after": ["layer"], "features": []}],
            "relations": [{"source": "layer", "target": "slab", "kind": "covers", "constraints": {
                "axis": "y", "source_face": "min", "target_face": "max", "min_gap": 0, "max_gap": 1, "min_coverage": 0.9}}],
            "unresolved": []}


def _prepared(raw, base=None, read=READ):
    return prepare(raw, SOURCE, "door", read, base)


def _usage(total, cached, output):
    return {"input_tokens": total, "output_tokens": output, "input_tokens_details": {"cached_tokens": cached}}


def _claim(repo, kind="generate_detail", payload=None, budget=None):
    job = repo.enqueue(kind, payload or {"figure_id": "figure"}, budget=budget)
    return job, repo.claim("threshold-test", only_ids=[job["id"]])


def _read_response(payload):
    purpose = payload["text"]["format"]["name"]
    assert purpose == "read", purpose
    return response_json(json.loads(json.dumps(READ)))


def _written(repo, raw=None):
    """A worker's tools with a first accepted version of the fixture."""
    job, fence = _claim(repo)
    tools = Tools(repo, fence, ModelGateway(repo, fence, _read_response))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": raw or threshold()})
    assert written.get("version_id"), written
    return job, tools, written


def _set_consumed(repo, job, **consumed):
    with d.transaction(repo.engine) as con:
        con.execute(d.runs.update().where(d.runs.c.job_id == job["id"]).values(consumed=consumed))


# ------------------------------------------------------------------ A. budget

def test_the_budget_counts_uncached_input_plus_output(source_repo):
    assert billed_tokens(_usage(37892, 30065, 584)) == 8411
    assert billed_tokens({"input_tokens": 100, "output_tokens": 5}) == 105  # no cache reported
    job, fence = _claim(source_repo)
    gateway = ModelGateway(source_repo, fence, lambda payload: {"output": [], "usage": _usage(30000, 28000, 500)})
    gateway.call({"model": "m", "input": [], "max_output_tokens": 4096}, "agent", "turn-0")
    run = source_repo.run(job["id"])
    assert run["consumed"]["tokens"] == 2500 and run["consumed"]["cached_tokens"] == 28000
    assert run["reserved"]["tokens"] == 0 and tokens_left(run) == 320_000 - 2500  # one reading of the budget


def test_a_response_without_usage_keeps_its_reservation(source_repo):
    job, fence = _claim(source_repo)
    ModelGateway(source_repo, fence, lambda payload: {"output": []}).call(
        {"model": "m", "input": [], "max_output_tokens": 4096}, "agent", "turn-0")
    assert source_repo.run(job["id"])["consumed"]["tokens"] > 4096


def test_a_turn_is_not_refused_for_input_the_provider_has_cached(source_repo):
    """Seen live: 184k consumed, 117k of it billed, and the gateway refused the
    next turn: its estimate billed the whole conversation plus 8k an image, on
    top of a 100k tail. That, not the agent's own floor, stopped the first run."""
    job, tools, _ = _written(source_repo)
    with d.transaction(source_repo.engine) as con:  # the last turn sent nearly all of this one
        con.execute(d.steps.insert().values(id="turn", run_id=source_repo.run(job["id"])["id"], n=90, kind="model",
                    state="completed", request={"purpose": "agent", "operation_key": "turn-5"},
                    response={"output": []}, tokens=_usage(38000, 30000, 500)))
    _set_consumed(source_repo, job, tokens=226_000)
    assert tokens_left(source_repo.run(job["id"])) == 94_000
    seen = []
    def respond(payload):
        seen.append(payload["max_output_tokens"])
        return {"output": [], "usage": _usage(39000, 38000, 2000)}
    payload = {"model": "m", "max_output_tokens": 32768,
               "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 120_000}]}]}
    ModelGateway(source_repo, tools.fence, respond).call(payload, "agent", "turn-6")
    assert seen and seen[0] >= 8192


class CachedTurns:
    """Reads, writes, then keeps working: turn after turn of mostly cached input,
    the way a patch loop looks to the provider."""
    def __init__(self, turns):
        self.turns, self.calls = turns, 0

    def __call__(self, payload):
        if "text" in payload:
            return _read_response(payload)
        i = self.calls
        self.calls += 1
        name, args = (("read_drawing", {}) if i == 0 else ("write_program", {"program": threshold()}) if i == 1 else
                      ("recall", {"query": "door"}) if i < self.turns else
                      ("finish", {"version_id": None, "verification_id": None, "critic_verdict_id": None,
                                  "status": "needs_review", "summary": "stop", "open_issues": []}))
        return {"output": [{"type": "function_call", "name": name, "call_id": f"c{i}", "arguments": json.dumps(args)}],
                "usage": _usage(38000, 30000, 500)}


def test_the_floor_cannot_stop_a_run_that_has_budget_left(source_repo, monkeypatch):
    """Twelve turns at 38k input: 462k counted the old way, past the whole budget
    and long before any CAD; 110k of it is what the run pays for."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    model = CachedTurns(12)
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert model.calls == 14 and final["result"]["stop_reason"] == "finished"  # its first finish was sent back
    assert source_repo.run(job["id"])["consumed"]["tokens"] == 14 * 8500 + 20  # and the nested read


def test_a_run_keeps_the_model_calls_its_review_needs(source_repo, monkeypatch):
    """Seen live: two runs spent all 32 calls, so the final critic never ran."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    model = CachedTurns(50)
    def respond(payload):
        if "text" in payload and payload["text"]["format"]["name"] == "critic":
            return response_json({"verdict": "fail", "issues": []})
        return model(payload)
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"}, budget={"model_calls": 12})
    Worker(source_repo, responder=respond, renderer=FakeRenderer(source_repo)).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert final["result"]["stop_reason"] == "budget_floor" and source_repo.list(d.critics)
    assert source_repo.run(job["id"])["consumed"]["model_calls"] <= 12


def test_an_agent_turn_leaves_the_review_tail(source_repo):
    job, tools, _ = _written(source_repo)
    _set_consumed(source_repo, job, tokens=320_000 - REVIEW_TAIL - 6000)
    with pytest.raises(BudgetExceeded):
        ModelGateway(source_repo, tools.fence, _read_response).call(
            {"model": "m", "input": [], "max_output_tokens": 32768}, "agent", "turn-1")


def test_the_run_ends_on_a_review_when_only_the_tail_is_left(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    model = CachedTurns(50)
    def respond(payload):
        if "text" in payload and payload["text"]["format"]["name"] == "critic":
            return response_json({"verdict": "fail", "issues": []})
        return model(payload)
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"}, budget={"tokens": 150_000, "model_calls": 32})
    Worker(source_repo, responder=respond, renderer=FakeRenderer(source_repo)).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert final["state"] == "needs_review" and final["result"]["stop_reason"] == "budget_floor"
    assert source_repo.list(d.critics)
    assert final["result"]["continued_by"]  # and the run the floor stopped carries on


def test_the_final_critic_fits_in_the_tail(source_repo):
    """Five images and the evidence must fit the tail, or the review never runs."""
    def respond(payload):
        if payload["text"]["format"]["name"] == "read":
            return _read_response(payload)
        return response_json({"verdict": "fail", "issues": []})
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, respond), renderer=FakeRenderer(source_repo))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": threshold()})
    _set_consumed(source_repo, job, tokens=320_000 - REVIEW_TAIL)
    rendered = tools.run("render", "render", {"version_id": written["version_id"]})
    assert tools.run("critic", "critic", rendered["critic_args"])["verdict"] == "fail"


def test_a_fully_cached_run_still_stops_at_its_hard_limit(source_repo):
    """Cached input costs the budget nothing, so model_calls is what bounds it."""
    calls = []
    def respond(payload):
        calls.append(payload)
        return {"output": [{"type": "function_call", "name": "recall", "call_id": f"c{len(calls)}",
                            "arguments": json.dumps({"query": "door"})}], "usage": _usage(5000, 5000, 0)}
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"}, budget={"model_calls": 3})
    Worker(source_repo, responder=respond).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert len(calls) == 3 and final["state"] == "needs_review"
    assert final["error"]["code"] == "budget_exhausted" and final["result"]["stop_reason"] == "budget_exhausted"
    assert source_repo.run(job["id"])["consumed"]["tokens"] == 0


# --------------------------------------------------------------- B. tool results

def test_a_tool_result_carries_what_is_not_passing_plus_counts(source_repo):
    job, tools, written = _written(source_repo)
    stored = source_repo.list(d.steps, d.steps.c.call_id == "write")[0]["output"]
    view = model_view("write_program", stored)
    statuses = {c["id"]: c["status"] for c in stored["checks"]}
    assert "PASS" in statuses.values() and {"FAIL", "UNVERIFIED"} & set(statuses.values())  # the store keeps them all
    assert all(c["status"] not in ("PASS", "N/A") for c in view["checks"]) and "passed_check_ids" not in view
    assert all(c["status"] == "FAIL" or c.get("required") or c.get("missing") for c in view["checks"])
    assert "assembly-class-unset" not in {c["id"] for c in view["checks"]} and "B-interference" in view["informational"]
    assert sum(view["check_counts"].values()) == len(stored["checks"]) and view["check_counts"]["PASS"] > 0
    assert all("stage" not in c and None not in c.values() for c in view["checks"])
    assert len(json.dumps(view["checks"])) < len(json.dumps(stored["checks"]))
    report = source_repo.get(d.verifications, written["verification_id"])
    assert len(report["checks"]) == len(stored["checks"])  # the full checks stay in the database


def test_a_rejected_patch_on_a_working_version_does_not_echo_the_program(source_repo):
    job, tools, written = _written(source_repo)
    rejected = tools.run("bad", "patch_program", {"edits": [{"op": "replace", "path": "/parts/@slab/role", "value": "object"}]})
    assert rejected["error"]["proposal"]["parts"]  # the record keeps it
    view = model_view("patch_program", rejected)
    assert "proposal" not in json.dumps(view) and "unchanged" in view["error"]["note"]
    assert view["error"]["message"] == rejected["error"]["message"]


def test_a_rejected_first_write_names_the_parts_at_fault_and_keeps_the_rest_server_side(source_repo):
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response))
    tools.run("read", "read_drawing", {})
    candidate = threshold()
    candidate["parts"][0]["role"] = "object"
    rejected = tools.run("w1", "write_program", {"program": candidate})
    view = model_view("write_program", rejected)["error"]
    assert "proposal" not in view and [p["id"] for p in view["named_parts"]] == ["slab"]
    assert view["part_ids"] == [p["id"] for p in candidate["parts"]] and "structural_slab_top_level" in view["parameter_names"]
    assert "patch_program" in view["note"] and "write_program again" in view["note"]
    fixed = tools.run("p1", "patch_program", {"edits": [{"op": "replace", "path": "/parts/@slab/role", "value": "substrate"}]})
    assert fixed.get("version_id"), fixed  # the patch resumed from the stored proposal


def test_a_rejection_that_names_no_part_shows_the_proposal_because_nothing_else_can(source_repo):
    """The model never saw this program: write_program authors in a nested call."""
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response))
    tools.run("read", "read_drawing", {})
    candidate = threshold()
    next(dim for dim in candidate["parts"][0]["dims"] if dim["name"] == "x1")["expr"] = "1 +"
    view = model_view("write_program", tools.run("w1", "write_program", {"program": candidate}))["error"]
    assert "1 +" in view["message"] and view["proposal"]["parts"]


def test_a_conflict_says_where_both_definitions_are_and_shows_them_alone(source_repo):
    """Seen live: told only the name, an agent counted indexes by hand and got them wrong,
    and `@name` cannot reach one of two definitions."""
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response))
    tools.run("read", "read_drawing", {})
    candidate, first = threshold(), 1
    candidate["params"].append(dict(candidate["params"][first], value=999))
    last = len(candidate["params"]) - 1
    view = model_view("write_program", tools.run("w1", "write_program", {"program": candidate}))["error"]
    assert f"{candidate['params'][first]['name']} at /params/{first} and /params/{last}" in view["message"]
    assert "proposal" not in view and [p["value"] for p in view["named_params"]] == [candidate["params"][first]["value"], 999]
    fixed = tools.run("p1", "patch_program", {"edits": [{"op": "remove", "path": f"/params/{last}"}]})
    assert fixed.get("version_id"), fixed


def test_a_patch_cannot_define_a_name_twice():
    """Seen live: 59 added params, eight of them second definitions that took 12 turns to untangle."""
    program, kept = threshold(), 1
    param = program["params"][kept]
    with pytest.raises(ValueError, match=f"params already has {param['name']}: replace /params/@{param['name']}"):
        apply_edits(program, [{"op": "add", "path": "/params/-", "value": dict(param, value=999)}])
    with pytest.raises(ValueError, match="already has"):
        apply_edits(program, [{"op": "add", "path": f"/params/@{param['name']}", "value": dict(param, value=999)}])
    assert apply_edits(program, [{"op": "add", "path": "/params/-", "value": dict(param)}])["params"] == program["params"]
    swapped = [{"op": "remove", "path": f"/params/@{param['name']}"}, {"op": "add", "path": "/params/-", "value": dict(param, value=999)}]
    assert apply_edits(program, swapped)["params"][-1]["value"] == 999
    with pytest.raises(ValueError, match="/parts/@slab/dims already has x1: replace /parts/@slab/dims/@x1"):
        apply_edits(program, [{"op": "add", "path": "/parts/@slab/dims/-", "value": {"name": "x1", "expr": "slab_depth"}}])
    relation = dict(program["relations"][0])  # a list with no names is not touched
    assert len(apply_edits(program, [{"op": "add", "path": "/relations/-", "value": relation}])["relations"]) == len(program["relations"]) + 1


def test_a_result_names_the_objects_still_to_model(source_repo):
    job, tools, written = _written(source_repo)
    view = model_view("write_program", written)
    assert view["next_step"].endswith("track, bottom_tray") and "before any more patches" in view["next_step"]
    assert "next_step" not in model_view("write_program", {"checks": [{"id": "cad-envelope-x", "status": "FAIL", "reason": "off"}]})


def test_a_refusal_lists_every_fault_with_where_it_is_so_the_program_need_not_come_back(source_repo):
    """Seen live: 25 of these, reported one at a time and with no location, took 14 turns
    of adding params and fixed none of them."""
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response))
    tools.run("read", "read_drawing", {})
    candidate = threshold()
    dims = {d_["name"]: d_ for d_ in candidate["parts"][0]["dims"]}
    dims["x1"]["expr"], dims["x0"]["expr"] = "slab_length + 25", "-70"
    next(d_ for d_ in candidate["parts"][1]["dims"] if d_["name"] == "x1")["expr"] = "no_such_length"
    view = model_view("write_program", tools.run("w1", "write_program", {"program": candidate}))["error"]
    assert "3 expressions refused" in view["message"]
    for fault in ("slab.x1 = slab_length + 25", "slab.x0 = -70", "layer.x1 = no_such_length"):
        assert fault in view["message"]
    assert "matching units" in view["message"] and "use a named mm value" in view["message"]
    assert "invalid dimensional expression" in view["message"]
    assert "proposal" not in view and [p["id"] for p in view["named_parts"]] == ["slab", "layer"]
    assert [p["name"] for p in view["named_params"]] == ["slab_length"]


def test_a_refusal_has_room_for_a_long_list(source_repo):
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response))
    tools.run("read", "read_drawing", {})
    candidate = threshold()
    for n in range(15):
        extra = copy.deepcopy(candidate["parts"][0])
        extra["id"] = f"copy_{n}"
        for dim in extra["dims"]:
            if dim["name"] in ("x1", "y1"):
                dim["expr"] = "structural_slab_top_level + 25"
        candidate["parts"].append(extra)
    view = model_view("write_program", tools.run("w1", "write_program", {"program": candidate}))["error"]
    assert "30 expressions refused" in view["message"] and view["message"].count("+ 25") == 30


def test_a_rejected_path_coordinate_says_which_part():
    raw = threshold()
    raw["parts"][1].update(shape="sheet", dims=_dims(thickness="liquid_applied_layer_thickness", z0="0", z1="run_length"),
                           path=[{"x": "0", "y": "slab_length + 25"}, {"x": "slab_length", "y": "0"}])
    with pytest.raises(ValueError, match=r"layer path = slab_length \+ 25"):
        _prepared(raw)


class Refused:
    """Writes a program with faults, then sends the patches it is given, one a turn."""
    def __init__(self, program, patches):
        self.program, self.patches, self.turns = program, patches, 0

    def __call__(self, payload):
        if payload.get("text", {}).get("format", {}).get("name") == "read":
            return _read_response(payload)
        self.turns += 1
        if self.turns == 1:
            call = ("read_drawing", {})
        elif self.turns == 2:
            call = ("write_program", {"program": self.program})
        elif self.patches:
            call = ("patch_program", {"edits": self.patches.pop(0)})
        else:
            call = ("finish", {"version_id": None, "verification_id": None, "critic_verdict_id": None,
                               "status": "needs_review", "summary": "stop", "open_issues": []})
        return {"output": [{"type": "function_call", "name": call[0], "call_id": f"call-{self.turns}", "arguments": json.dumps(call[1])}],
                "usage": _usage(100, 0, 10)}


def _faulty(*dims):
    program = threshold()
    for dim in program["parts"][0]["dims"]:
        if dim["name"] in dims:
            dim["expr"] = "structural_slab_top_level + 25"
    return program


def test_the_same_refusal_three_times_ends_a_run_that_has_no_version(source_repo):
    """Seen live: 268k tokens on a program that was never accepted."""
    idle = [{"op": "replace", "path": "/title", "value": "another try"}]
    model = Refused(_faulty("x1"), [idle] * 40)
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    ended = source_repo.get(d.jobs, job["id"])
    assert ended["state"] == "needs_review" and ended["result"]["stop_reason"] == "refused_again"
    assert model.turns == 4 and ended["result"]["version_id"] is None  # the write and two patches
    assert continue_if_cut_short(source_repo, job["id"], limit=2) is None  # nothing to continue from


def test_a_run_with_a_draft_is_not_stopped_by_refused_patches(source_repo):
    """It has a version to keep, a refused patch leaves that version as it was, and the
    repair loop and the budget bound what follows."""
    refused = [{"op": "replace", "path": "/parts/@slab/role", "value": "object"}]
    model = Refused(threshold(), [refused] * 4)
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    assert source_repo.run(job["id"])["working_version_id"] and model.turns >= 6
    assert source_repo.get(d.jobs, job["id"])["result"].get("stop_reason") != "refused_again"


def test_a_run_getting_through_its_faults_is_not_stopped(source_repo):
    """Three refusals in a row, each with less on it: only sameness means nothing moved."""
    model = Refused(_faulty("x1", "y1", "z1"), [
        [{"op": "replace", "path": "/parts/@slab/dims/@x1/expr", "value": "slab_length"}],
        [{"op": "replace", "path": "/parts/@slab/dims/@y1/expr", "value": "structural_slab_top_level"}],
        [{"op": "replace", "path": "/parts/@slab/dims/@z1/expr", "value": "run_length"}]])
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    assert source_repo.run(job["id"])["working_version_id"]


def test_a_repeated_gate_note_reaches_the_model_once(source_repo, monkeypatch):
    """Seen live: 33k characters of the same notes in one run."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    raw = threshold()
    next(d_ for d_ in raw["parts"][0]["dims"] if d_["name"] == "y0")["expr"] = "structural_slab_top_level - slab_depth - step_down"
    patch = {"edits": [{"op": "replace", "path": "/title", "value": "Terrace door, revised"}]}
    script = [("read_drawing", {}), ("write_program", {"program": raw}), ("patch_program", patch), ("patch_program", patch),
              ("finish", {"version_id": None, "verification_id": None, "critic_verdict_id": None, "status": "needs_review",
                          "summary": "stop", "open_issues": []})]
    last = {}
    def respond(payload):
        if "text" in payload:
            return _read_response(payload)
        last["input"] = payload["input"]
        name, args = script.pop(0)
        return {"output": [{"type": "function_call", "name": name, "call_id": f"c{len(script)}", "arguments": json.dumps(args)}],
                "usage": _usage(100, 0, 10)}
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=respond).run_once(only_ids=[job["id"]])
    results = [json.loads(i["output"]) for i in last["input"] if i.get("type") == "function_call_output"]
    notes = [r["gate_notes"] for r in results if "gate_notes" in r]
    assert any("slab.y0" in note for note in notes[0]) and notes[1] == [] and notes[2] == []
    stored = [s["output"] for s in source_repo.list(d.steps, d.steps.c.tool_name == "patch_program")]
    assert all(any("slab.y0" in note for note in s["gate_notes"]) for s in stored)  # the record keeps them


def test_a_critic_has_room_to_think_and_a_cut_off_one_is_not_told_to_finish(source_repo, monkeypatch):
    """Seen live: the critic spent its whole 8192 on reasoning, and the error said
    to finish, so a run at 0 FAILs ended with budget left and no verdict."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    limits = []
    def respond(payload):
        if payload["text"]["format"]["name"] == "read":
            return _read_response(payload)
        limits.append(payload["max_output_tokens"])
        return {"output": [], "status": "incomplete", "usage": _usage(20000, 0, limits[-1])}
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, respond), renderer=FakeRenderer(source_repo))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": threshold()})
    rendered = tools.run("render", "render", {"version_id": written["version_id"]})
    message = tools.run("critic", "critic", rendered["critic_args"])["error"]["message"]
    assert limits == [16384] and "call the tool again" in message and "finish" not in message


def test_the_critic_reads_the_compact_checks(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    prompts = []
    def respond(payload):
        if payload["text"]["format"]["name"] == "read":
            return _read_response(payload)
        prompts.append(payload["input"][0]["content"][0]["text"])
        return response_json({"verdict": "fail", "issues": []})
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, respond), renderer=FakeRenderer(source_repo))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": threshold()})
    rendered = tools.run("render", "render", {"version_id": written["version_id"]})
    tools.run("critic", "critic", rendered["critic_args"])
    assert '"check_counts"' in prompts[0] and '"id":"source-artifacts"' not in prompts[0]  # a passing check


# ---------------------------------------------------------------- C. stop reason

def _stopped(repo, stop_reason, consumed=209_000, reserved=0):
    """A revision that ended with `stop_reason` and a new version that still FAILs."""
    job, tools, written = _written(repo)
    with d.transaction(repo.engine) as con:
        con.execute(d.runs.update().where(d.runs.c.job_id == job["id"]).values(
                    consumed={"tokens": consumed}, reserved={"tokens": reserved}))
    repo.end(tools.fence, "needs_review", {"version_id": written["version_id"], "stop_reason": stop_reason})
    return job


@pytest.mark.parametrize("stop_reason", ["budget_floor", "budget_exhausted", "output_limit", "model_stopped", "repairs_left"])
def test_a_run_cut_short_continues_whatever_it_spent(source_repo, stop_reason):
    """Seen live: stopped on the floor at 209k, and 209k + 100k < 320k said it had not."""
    job = _stopped(source_repo, stop_reason)
    follow = continue_if_cut_short(source_repo, job["id"], limit=2)
    assert follow and source_repo.get(d.jobs, follow)["payload"]["requested_changes"] == CONTINUE_REQUEST


def test_a_run_the_agent_finished_is_not_continued_however_much_it_spent(source_repo):
    assert continue_if_cut_short(source_repo, _stopped(source_repo, "finished", consumed=319_000)["id"], limit=2) is None


def test_a_cut_short_run_with_only_unverified_required_checks_still_continues(source_repo):
    job = _stopped(source_repo, "budget_floor")
    run = source_repo.run(job["id"])
    with d.transaction(source_repo.engine) as con:
        con.execute(d.verifications.update().where(d.verifications.c.version_id == run["working_version_id"]).values(
                    checks=[{"id": "assembly-0-covers", "status": "UNVERIFIED", "required": True}]))
    assert continue_if_cut_short(source_repo, job["id"], limit=2)


def test_the_continue_request_fills_slots_before_cad():
    assert CONTINUE_REQUEST.index("complete_assembly") < CONTINUE_REQUEST.index("CAD")
    assert "slot-missing" in CONTINUE_REQUEST


def test_running_out_of_cad_calls_ends_in_a_review_that_says_so(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    calls = iter([("read_drawing", {}), ("write_program", {"program": threshold()})])
    state = {}
    def respond(payload):
        purpose = payload.get("text", {}).get("format", {}).get("name")
        if purpose == "read":
            return _read_response(payload)
        if purpose == "critic":
            return response_json({"verdict": "fail", "issues": []})
        if purpose == "cad":
            return response_json({"code": 'import cadquery as cq\ndef build(p):\n return cq.Workplane("XY").box(p["width"],p["depth"],p["height"],centered=False)'})
        for item in payload["input"]:
            if item.get("type") == "function_call_output" and "version_id" in json.loads(item["output"]):
                state["version"] = json.loads(item["output"])["version_id"]
        name, args = next(calls, ("model_component", {"version_id": state.get("version"), "part_key": "track", "brief": "track"}))
        return {"output": [{"type": "function_call", "name": name, "call_id": f"c{len(payload['input'])}",
                            "arguments": json.dumps(args)}], "usage": _usage(100, 0, 10)}
    def built(code, params, features, measurement_params=None):
        raise cad_sandbox.CadCodeError("sandbox refused")
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"}, budget={"cad_calls": 1})
    Worker(source_repo, responder=respond, renderer=FakeRenderer(source_repo), cad_builder=built).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert final["state"] == "needs_review" and final["result"]["stop_reason"] == "budget_exhausted"
    assert source_repo.list(d.critics)  # the draft was reviewed before the run stopped
    assert final["result"]["continued_by"]


def test_a_run_may_not_give_up_on_what_it_can_fix_while_budget_remains(source_repo, monkeypatch):
    """Seen live: a run ended on its first failing critic with 71k tokens left."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    answer = {"version_id": written["version_id"], "verification_id": None, "critic_verdict_id": None,
              "status": "needs_review", "summary": "giving up", "open_issues": []}
    refused = tools.run("f1", "finish", answer)
    assert refused["error"]["code"] == "repairs_left" and "cad-track" in refused["error"]["repairs"]
    assert source_repo.get(d.jobs, job["id"])["state"] == "running"


def test_a_finish_sent_back_with_nothing_done_ends_rather_than_spins(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    answer = {"version_id": written["version_id"], "verification_id": None, "critic_verdict_id": None,
              "status": "needs_review", "summary": "giving up", "open_issues": []}
    tools.run("f1", "finish", answer)
    ended = tools.run("f2", "finish", answer)
    final = source_repo.get(d.jobs, job["id"])
    assert "error" not in ended and final["state"] == "needs_review" and final["result"]["stop_reason"] == "finished"
    assert continue_if_cut_short(source_repo, job["id"], limit=2) is None


def test_a_finish_is_sent_back_a_bounded_number_of_times_however_much_changes(source_repo, monkeypatch):
    from app.repository import REPAIR_REFUSALS
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    answers = []
    for i in range(REPAIR_REFUSALS + 1):
        version = source_repo.run(job["id"])["working_version_id"]
        answers.append(tools.run(f"f{i}", "finish", {"version_id": version, "verification_id": None, "critic_verdict_id": None,
                                                     "status": "needs_review", "summary": "stop", "open_issues": []}))
        if "error" in answers[-1]:
            tools.run(f"p{i}", "patch_program", {"edits": [{"op": "replace", "path": "/params/@slab_length/value", "value": 500 + 10 * i}]})
    assert ["error" in answer for answer in answers] == [True] * REPAIR_REFUSALS + [False]


def test_a_run_with_nothing_left_to_fix_ends_at_once(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    monkeypatch.setattr(tools, "_repairs", lambda run: [])
    ended = tools.run("f1", "finish", {"version_id": written["version_id"], "verification_id": None, "critic_verdict_id": None,
                                       "status": "needs_review", "summary": "done what can be", "open_issues": []})
    assert "error" not in ended and ended["stop_reason"] == "finished"


def test_what_is_for_a_reviewer_does_not_hold_a_run_back(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    with d.transaction(source_repo.engine) as con:
        con.execute(d.verifications.update().where(d.verifications.c.version_id == written["version_id"]).values(checks=[
            {"id": "coverage-layer", "status": "UNVERIFIED", "required": True},
            {"id": "anchored-track", "status": "UNVERIFIED", "required": True},
            {"id": "assembly-0-covers", "status": "UNVERIFIED", "required": True},
            {"id": "label-verbatim", "status": "UNVERIFIED", "required": False}]))
    assert tools._repairs(source_repo.run(job["id"])) == ["anchored-track", "assembly-0-covers"]


def test_a_critics_errors_are_repairs_and_what_needs_a_human_is_not(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    run = source_repo.run(job["id"])
    with d.transaction(source_repo.engine) as con:
        con.execute(d.verifications.update().where(d.verifications.c.version_id == written["version_id"]).values(checks=[]))
        con.execute(d.steps.insert().values(id="crit", run_id=run["id"], n=90, kind="tool", state="completed", tool_name="critic",
                    call_id="crit", output={"issues": [{"id": "shape-mismatch:track", "severity": "error"},
                                                       {"id": "a-note", "severity": "warning"},
                                                       {"id": "needs-eyes", "severity": "error", "needs_human": True}]}))
    assert tools._repairs(source_repo.run(job["id"])) == ["shape-mismatch:track"]


def test_a_run_out_of_budget_with_repairs_left_continues_as_a_revision(source_repo, monkeypatch):
    """71k left against a 64k tail is no room for one more repair, so it must go on."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    _set_consumed(source_repo, job, tokens=249_000, model_calls=20)
    ended = tools.run("f1", "finish", {"version_id": written["version_id"], "verification_id": None, "critic_verdict_id": None,
                                       "status": "needs_review", "summary": "out of room", "open_issues": []})
    final = source_repo.get(d.jobs, job["id"])
    assert "error" not in ended and final["result"]["stop_reason"] == "repairs_left" and final["result"]["repairs"]
    follow = continue_if_cut_short(source_repo, job["id"], limit=2)
    assert source_repo.get(d.jobs, follow)["kind"] == "revise_detail"


def test_out_of_cad_calls_what_needs_cad_goes_on_in_a_new_run(source_repo, monkeypatch):
    """A new run has a new CAD budget: sending the finish back could only waste turns."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    monkeypatch.setattr(tools, "_repairs", lambda run: ["shape-mismatch:track", "cad-bottom_tray"])
    _set_consumed(source_repo, job, cad_calls=12)
    ended = tools.run("f1", "finish", {"version_id": written["version_id"], "verification_id": None, "critic_verdict_id": None,
                                       "status": "needs_review", "summary": "no more CAD", "open_issues": []})
    assert "error" not in ended and ended["stop_reason"] == "repairs_left"


def test_out_of_cad_calls_what_a_patch_can_fix_is_still_sent_back(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    monkeypatch.setattr(tools, "_repairs", lambda run: ["shape-mismatch:track", "anchored-track"])
    _set_consumed(source_repo, job, cad_calls=12)
    refused = tools.run("f1", "finish", {"version_id": written["version_id"], "verification_id": None, "critic_verdict_id": None,
                                         "status": "needs_review", "summary": "no more CAD", "open_issues": []})
    assert refused["error"]["code"] == "repairs_left"


def test_repairs_named_by_a_critic_continue_though_every_check_passes(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    with d.transaction(source_repo.engine) as con:
        con.execute(d.verifications.update().where(d.verifications.c.version_id == written["version_id"]).values(
                    checks=[{"id": "all-good", "status": "PASS"}]))
    source_repo.end(tools.fence, "needs_review", {"version_id": written["version_id"], "stop_reason": "repairs_left",
                                                  "repairs": ["shape-mismatch:track"]})
    assert continue_if_cut_short(source_repo, job["id"], limit=2)


def test_a_chain_of_continuations_still_ends_at_its_limit(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job = _stopped(source_repo, "repairs_left")
    assert continue_if_cut_short(source_repo, job["id"], limit=0) is None


def test_a_truncated_turn_records_why_and_continues(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    turns = iter([("read_drawing", {}), ("write_program", {"program": threshold()})])
    def respond(payload):
        if "text" in payload:
            return _read_response(payload)
        call = next(turns, None)
        if call is None:
            return {"output": [], "status": "incomplete", "usage": _usage(100, 0, 10)}
        return {"output": [{"type": "function_call", "name": call[0], "call_id": call[0], "arguments": json.dumps(call[1])}],
                "usage": _usage(100, 0, 10)}
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=respond).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert final["result"]["stop_reason"] == "output_limit" and final["result"]["continued_by"]


# ------------------------------------------------------- D. completion on real programs

def _complete(raw, read=READ):
    native, _, _ = _prepared(raw, read=read)
    return native, completion.archetype_completion(native, ontology.load(), read)


def test_completion_passes_the_gate_on_long_anchor_expressions():
    """Seen live: `expression too long` on composed anchors, then the agent
    retyped bare numbers and hit `use a named mm value`."""
    native, result = _complete(threshold())
    assert [p.id for p in result.parts] == ["jamb_1", "jamb_2", "head", "leaf"] and result.suggestions == []
    longest = max(len(d.expr) for p in result.parts for d in p.dims)
    assert len(LEVEL) > completion.MAX_COORDINATE >= longest
    base = native.model_dump(mode="json")
    patched, spec, _ = _prepared(apply_edits(base, completion.edits(result)), base)
    assert [p.provenance for p in patched.parts][-4:] == ["inferred"] * 4
    components = {c.id: {p.name: p.value for p in c.params} for c in spec.components}
    top = 200 + 2 + 3 + 12 + 6 + 70
    assert components["jamb_1"]["y0"] == top == components["jamb_2"]["y0"] and components["jamb_1"]["y1"] == top + 2010
    assert components["head"]["y0"] == top + 2010 and components["leaf"]["y1"] == top + 2010


def test_a_named_coordinate_is_an_assumption_that_says_it_is_a_placeholder():
    _, result = _complete(threshold())
    named = [p for p in result.params if "placeholder position" in (p.note or "")]
    assert {"jamb_1_y0", "leaf_y0"} <= {p.name for p in named}
    for param in result.params:
        assert param.provenance.value == "assumption" and "no source" in param.note and param.source_quote is None
    assert "track" in next(p for p in named if p.name == "jamb_1_y0").note


def test_short_coordinates_stay_linked_to_the_anchor():
    _, result = _complete(threshold())
    x0 = next(d.expr for d in result.parts[0].dims if d.name == "x0")
    assert x0 == "track_centre_offset - 0.5 * track_section_width"


def test_the_frame_stands_on_the_highest_part_of_the_threshold_slot():
    """Two parts fill the threshold slot: a tray under the track. The jambs stand
    on the track whichever comes first in the program."""
    raw = threshold()
    raw["parts"][2], raw["parts"][3] = raw["parts"][3], raw["parts"][2]
    assert raw["parts"][2]["id"] == "bottom_tray"
    native, result = _complete(raw)
    assert result.parts[0].install_after == ["track"]
    assert {r.target for r in result.relations if r.source == "jamb_1"} == {"track"}
    x0, x1 = (next(d.expr for d in result.parts[0].dims if d.name == name) for name in ("x0", "x1"))
    assert x0 == "track_centre_offset - 0.5 * track_section_width" and x1 == "track_centre_offset + 0.5 * track_section_width"


def test_an_anchor_too_long_to_read_as_one_expression_still_places():
    """A profile's envelope is a min/max over its whole outline, past the limit."""
    raw = threshold()
    track = next(p for p in raw["parts"] if p["id"] == "track")
    outline = [{"x": f"slab_length - {i} * bottom_tray_thickness_allowance",
                "y": f"structural_slab_top_level + {i} * bedding_mastic_compressed_thickness"} for i in range(12)]
    outline.append({"x": "slab_length", "y": "structural_slab_top_level + 20 * bedding_mastic_compressed_thickness"})
    track.update(role="finish", shape="profile", label=None, form=None, cad_brief=None, param_bindings={}, path=outline,
                 dims=_dims(z0="0", z1="run_length"))
    native, _, _ = prepare(raw, SOURCE, "door", dict(READ, elements=[]))
    assert len(completion._envelope(native.parts[2])["x1"]) > 240
    result = completion.archetype_completion(native, ontology.load(), dict(READ, elements=[]))
    assert [p.id for p in result.parts] == ["jamb_1", "jamb_2", "head", "leaf"]
    base = native.model_dump(mode="json")
    prepare(apply_edits(base, completion.edits(result)), SOURCE, "door", dict(READ, elements=[]), base)


# ----------------------------------------------------------- E. runs before CAD, mode on

def test_the_default_mode_applies_and_suggest_and_off_remain(monkeypatch):
    monkeypatch.delenv("SETOUT_COMPLETION", raising=False)
    assert completion_mode() == "on"
    for mode in ("suggest", "off", "on"):
        monkeypatch.setenv("SETOUT_COMPLETION", mode)
        assert completion_mode() == mode


class Author:
    """An agent that reads, writes once and then wants to start CAD. It never
    calls complete_assembly itself."""
    def __init__(self, stop_after=3):
        self.turns, self.inputs, self.stop_after = [], [], stop_after

    def __call__(self, payload):
        purpose = payload.get("text", {}).get("format", {}).get("name")
        if purpose == "read":
            return _read_response(payload)
        if purpose == "critic":
            return response_json({"verdict": "fail", "issues": []})
        self.inputs.append(payload["input"])
        i = len(self.turns)
        self.turns.append(i)
        if i == 0:
            call = ("read_drawing", {})
        elif i == 1:
            call = ("write_program", {"program": threshold()})
        else:
            call = ("finish", {"version_id": None, "verification_id": None, "critic_verdict_id": None,
                               "status": "needs_review", "summary": "stop", "open_issues": []})
        return {"output": [{"type": "function_call", "name": call[0], "call_id": f"call-{i}", "arguments": json.dumps(call[1])}],
                "usage": _usage(100, 0, 10)}


def test_completion_runs_after_the_first_accepted_version_with_no_model_turn(source_repo, monkeypatch):
    monkeypatch.delenv("SETOUT_COMPLETION", raising=False)
    model = Author()
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    run = source_repo.run(job["id"])
    steps = source_repo.list(d.steps, d.steps.c.run_id == run["id"], order=d.steps.c.n)
    tools = [s["tool_name"] for s in steps if s["kind"] == "tool"]
    assert tools[:3] == ["read_drawing", "write_program", "complete_assembly"] and len(model.turns) == 4  # a finish sent back
    final = source_repo.get(d.versions, run["working_version_id"])
    assert [p["id"] for p in final["program"]["parts"] if p["provenance"] == "inferred"] == ["jamb_1", "jamb_2", "head", "leaf"]
    report = source_repo.list(d.verifications, d.verifications.c.version_id == final["id"])[-1]
    assert report["quality"]["outcome"] == "complete_with_inferred"
    # The next turn saw it, in the compact view, and knows the new version.
    notes = [item for item in model.inputs[2] if item.get("role") == "user" and "complete_assembly ran" in json.dumps(item)]
    assert len(notes) == 1 and final["id"] in notes[0]["content"][0]["text"]
    assert '"inferred_parts":["jamb_1","jamb_2","head","leaf"]' in notes[0]["content"][0]["text"]


def test_a_replayed_run_rebuilds_the_same_conversation_around_the_note(source_repo, monkeypatch):
    monkeypatch.delenv("SETOUT_COMPLETION", raising=False)
    model = Author()
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    first = []
    def stops_on_turn_three(payload):
        if "text" not in payload and len(model.turns) == 2:
            first.append(payload["input"])
            raise TimeoutError("worker lost")
        return model(payload)
    Worker(source_repo, responder=stops_on_turn_three).run_once(only_ids=[job["id"]])
    assert source_repo.get(d.jobs, job["id"])["state"] == "queued"
    with d.transaction(source_repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(available_at=0))
    second = []
    def resumed(payload):
        if "text" not in payload:
            second.append(payload["input"])
        return model(payload)
    Worker(source_repo, responder=resumed).run_once(only_ids=[job["id"]])
    assert second[0] == first[0]
    assert sum(1 for s in source_repo.list(d.operations) if s["call_id"].startswith("auto-complete-")) == 1


def test_suggest_mode_leaves_applying_to_the_agent(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "suggest")
    model = Author()
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    assert not [o for o in source_repo.list(d.operations) if o["tool_name"] == "complete_assembly"]


def test_a_rejected_completion_is_fed_back_once_per_version(source_repo, monkeypatch):
    monkeypatch.delenv("SETOUT_COMPLETION", raising=False)
    def refuse(*args, **kwargs):
        return [{"op": "replace", "path": "/parts/@slab/role", "value": "object"}]
    monkeypatch.setattr(completion, "edits", refuse)
    model = Author()
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model).run_once(only_ids=[job["id"]])
    attempts = [o for o in source_repo.list(d.operations) if o["call_id"].startswith("auto-complete-")]
    assert len(attempts) == 1 and attempts[0]["result"]["error"]["code"] == "invalid_tool_input"
    assert "proposal" not in json.dumps(model.inputs[2][-1])  # no echo of a program the model already has


def test_a_program_with_no_assembly_class_is_left_alone(source_repo, monkeypatch):
    monkeypatch.delenv("SETOUT_COMPLETION", raising=False)
    raw = threshold()
    raw["assembly_class"] = None
    job, tools, written = _written(source_repo, raw)
    assert tools.completion_call(source_repo.run(job["id"]), []) is None


def test_a_revision_is_handed_the_read_so_it_spends_no_turn_asking_for_it(source_repo, monkeypatch):
    """Seen live: each continuation spent two uncached turns re-reading."""
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    job, tools, written = _written(source_repo)
    source_repo.end(tools.fence, "needs_review", {"version_id": written["version_id"], "stop_reason": "finished"})
    version = source_repo.get(d.versions, written["version_id"])
    seen = []
    def respond(payload):
        seen.append(payload["input"][0]["content"])
        return {"output": [{"type": "function_call", "name": "finish", "call_id": "end", "arguments": json.dumps(
            {"version_id": None, "verification_id": None, "critic_verdict_id": None, "status": "needs_review",
             "summary": "stop", "open_issues": []})}], "usage": _usage(100, 0, 10)}
    revise = source_repo.enqueue("revise_detail", {"detail_id": version["detail_id"], "base_version_id": version["id"],
                                                   "requested_changes": "x"})
    Worker(source_repo, responder=respond).run_once(only_ids=[revise["id"]])
    texts = [item.get("text", "") for item in seen[0]]
    assert any(text.startswith("The drawing's READ") and "Door bottom track" in text for text in texts)
    assert any(text.startswith("Current verification") and '"check_counts"' in text for text in texts)


def test_a_bad_ontology_does_not_kill_the_run(source_repo, monkeypatch):
    from app import agent_tools
    job, tools, written = _written(source_repo)
    monkeypatch.setattr(agent_tools, "ontology_or_error", lambda: (None, "unreadable"))
    assert tools.completion_call(source_repo.run(job["id"]), []) is None


def test_a_slot_the_completion_cannot_read_does_not_kill_the_run(source_repo, monkeypatch):
    job, tools, written = _written(source_repo)
    def broken(*args):
        raise ValueError("no readable anchor")
    monkeypatch.setattr(completion, "archetype_completion", broken)
    assert tools.completion_call(source_repo.run(job["id"]), []) is None


def test_the_instructions_put_completion_before_cad():
    assert "before any CAD work" in INSTRUCTIONS and "Once the drawn parts verify" not in INSTRUCTIONS
    assert "provenance=drawing, inferred_by=null, label=null" in INSTRUCTIONS  # drawn but unlabelled is not inferred
    assert "THE VERIFIER SETS THE PASS MARK" in INSTRUCTIONS and "min_coverage is of the target" not in INSTRUCTIONS


# ------------------------------------------------------- F. inferred objects are boxes

def _with_ghosts():
    native, result = _complete(threshold())
    base = native.model_dump(mode="json")
    return apply_edits(base, completion.edits(result)), base


def test_only_an_inferred_object_may_be_a_primitive():
    raw, base = _with_ghosts()
    assert _prepared(raw, base)[0].parts[-1].shape == "box"
    drawn = copy.deepcopy(raw)
    next(p for p in drawn["parts"] if p["id"] == "leaf").update(provenance="drawing", inferred_by=None)
    with pytest.raises(ValueError, match="objects require parametric CAD"):
        _prepared(drawn, base)
    profile = copy.deepcopy(raw)  # an inferred object may be a box, not any primitive: a box is what confirmation can read
    next(p for p in profile["parts"] if p["id"] == "leaf").update(
        shape="profile", dims=_dims(z0="0", z1="run_length"),
        path=[{"x": "0", "y": "0"}, {"x": "slab_length", "y": "0"}, {"x": "0", "y": "slab_depth"}])
    with pytest.raises(ValueError, match="objects require parametric CAD"):
        _prepared(profile, base)
    confirmed_box = copy.deepcopy(raw)
    next(p for p in confirmed_box["parts"] if p["id"] == "leaf").update(provenance="user_input")
    with pytest.raises(ValueError, match="objects require parametric CAD"):
        prepare(confirmed_box, SOURCE, "door", READ, base, human=True)


def test_an_inferred_box_spends_no_cad_call_raises_no_cad_fail_and_never_passes(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "on")
    job, tools, written = _written(source_repo)
    applied = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    ids = {c["id"]: c for c in applied["checks"]}
    assert "cad-jamb_1" not in ids and "cad-leaf" not in ids
    assert {i for i in ids if i.startswith("cad-")} == {"cad-track", "cad-bottom_tray"}  # drawn objects still owe CAD
    assert ids["slot-inferred:jamb"]["status"] == "UNVERIFIED" and not ids["slot-inferred:jamb"]["required"]
    assert not [c for c in applied["checks"] if c["status"] == "PASS" and any(k in c["id"] for k in ("jamb", "head", "leaf"))
                and not c["id"].startswith(("B-mesh", "B-built"))]  # only that the mesh exists
    refused = tools.run("model", "model_component", {"version_id": applied["version_id"], "part_key": "leaf", "brief": "x"})
    assert "stays a box" in refused["error"]["message"]
    assert source_repo.run(job["id"])["consumed"].get("cad_calls", 0) == 0


def test_an_inferred_parts_relations_are_never_a_pass_or_a_block(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "on")
    job, tools, written = _written(source_repo)
    applied = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    program = source_repo.get(d.versions, applied["version_id"])["program"]
    inferred = {p["id"] for p in program["parts"] if p["provenance"] == "inferred"}
    touched = [i for i, r in enumerate(program["relations"]) if {r["source"], r["target"]} & inferred]
    assert touched
    checks = {c["id"]: c for c in applied["checks"]}
    for i in touched:
        kind = program["relations"][i]["kind"]
        assert checks[f"assembly-{i}-{kind}"]["status"] == "UNVERIFIED" and not checks[f"assembly-{i}-{kind}"]["required"]
    assert not [i for i in checks if i.startswith("layer-order:") and inferred & set(i.split(":"))]


def test_a_confirmed_box_becomes_a_labelled_cad_placeholder_that_fails_until_modelled(client, source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "on")
    job, tools, written = _written(source_repo)
    applied = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    response = client.post(f"/api/versions/{applied['version_id']}/reviews",
                           json={"reviewer": "Expert", "action": "correct", "data": {"confirm_inferred": ["jamb_1"]}})
    assert response.status_code == 200, response.text
    confirmed = source_repo.get(d.versions, response.json()["version_id"])
    parts = {p["id"]: p for p in confirmed["program"]["parts"]}
    jamb = parts["jamb_1"]
    assert jamb["provenance"] == "user_input" and jamb["shape"] == "cad" and jamb["inferred_by"].endswith(":jamb")
    assert jamb["cad_brief"].startswith("Door jamb") and jamb["param_bindings"] and "placeholder" in jamb["note"]
    assert parts["jamb_2"]["shape"] == "box" and parts["jamb_2"]["provenance"] == "inferred"
    from app.verification_v3 import prepare_verification
    report = prepare_verification(source_repo, confirmed["id"])
    assert next(c for c in report["checks"] if c["id"] == "cad-jamb_1")["status"] == "FAIL"
    solid = next(s for s in report["scene"]["solids"] if s["id"] == "jamb_1")
    assert solid["placeholder"] == "pending" and solid["provenance"] == "user_input"
    assert "placeholder" in next(c for c in confirmed["spec"]["components"] if c["id"] == "jamb_1")["note"]


def test_the_critic_is_told_a_ghost_box_is_not_a_fidelity_error():
    from app.agent_tools import RETENTION_RULE
    assert "plain box on purpose" in RETENTION_RULE


# ------------------------------------------------------------------- G. class-role

def _labelled(role, label, class_id, shape="box"):
    raw = threshold()
    part = raw["parts"][1]
    part.update(role=role, label=label, class_id=class_id, shape=shape)
    if role == "object":
        part.update(form="shaped", cad_brief="a bead", param_bindings={"width": "slab_length"})
    return prepare(raw, SOURCE, "door", READ)[0]


def test_a_class_role_the_drawing_overrules_is_unverified_and_names_the_disagreement():
    """Seen live: the READ made a bead an object, the ontology calls sealant a
    finish, and the check could never pass: it burned patches."""
    program = _labelled("object", "Bead of mastic", "sealant", shape="cad")
    check = {c["id"]: c for c in slot_checks(program, READ)}["class-role:layer"]
    assert check["status"] == "UNVERIFIED" and not check["required"]
    assert "Bead of mastic" in check["reason"] and "'finish'" in check["reason"] and "'object'" in check["reason"]


def test_a_class_role_an_author_can_fix_still_fails():
    # Unlabelled: nothing but the author chose this role.
    program = _labelled("covered_layer", None, "sealant")
    assert {c["id"]: c for c in slot_checks(program, READ)}["class-role:layer"]["status"] == "FAIL"
    # Labelled, but the READ's category allows the class's role: a choice within layers.
    program = _labelled("covered_layer", "Liquid applied layer", "sealant")
    assert {c["id"]: c for c in slot_checks(program, READ)}["class-role:layer"]["status"] == "FAIL"


def test_a_class_role_with_no_reading_at_hand_still_fails():
    program = _labelled("object", "Bead of mastic", "sealant", shape="cad")
    assert {c["id"]: c for c in slot_checks(program)}["class-role:layer"]["status"] == "FAIL"


# ----------------------------------------------------------------- H. inferred_by

def test_the_schema_offers_every_required_slot_as_inferred_by():
    known = ontology.load()
    offered = program_schema()["properties"]["parts"]["items"]["properties"]["inferred_by"]
    choices = offered["anyOf"][0]["enum"]
    assert offered["anyOf"][1] == {"type": "null"} and len(choices) == len(known.required_slots("door_threshold"))
    assert f"archetype:door_threshold@{known.version}:jamb" in choices
    assert {_slot_claimed(choice) for choice in choices} == {s.id for s in known.required_slots("door_threshold")}


def test_every_offered_inferred_by_passes_the_gate_it_was_offered_for():
    known = ontology.load()
    for choice in program_schema()["properties"]["parts"]["items"]["properties"]["inferred_by"]["anyOf"][0]["enum"]:
        slot = known.assembly("door_threshold").slot(_slot_claimed(choice))
        raw = threshold()
        raw["parts"] = [p for p in raw["parts"] if p["class_id"] not in slot.classes]
        for part in raw["parts"]:
            part["install_after"] = [a for a in part["install_after"] if a in {p["id"] for p in raw["parts"]}]
        raw["relations"] = []
        cls = known.component(slot.classes[0])
        raw["parts"].append({"id": "ghost", "type": cls.id, "label": None, "class_id": cls.id, "shape": "box", "role": cls.role,
                             "form": (cls.form or "shaped") if cls.role == "object" else None, "provenance": "inferred",
                             "inferred_by": choice, "dims": _dims(x0="0", x1="slab_length", y0="0", y1="slab_depth", z0="0", z1="run_length"),
                             "install_after": [], "features": []})
        prepare(raw, SOURCE, "door", dict(READ, elements=[]))


# ---------------------------------------------------------------- I. CAD feedback

ABSOLUTE = 'import cadquery as cq\ndef build(p):\n    return cq.Workplane("XY").box(p["width"],p["depth"],p["height"],centered=False).translate((75.0,-140.0,210.0))'
LOCAL = 'import cadquery as cq\ndef build(p):\n    return cq.Workplane("XY").box(p["width"],p["depth"],p["height"],centered=False)'


def test_the_cad_gate_says_which_call_passed_what_and_the_way_out():
    with pytest.raises(cad_sandbox.CadCodeError) as caught:
        cad_components.check_component(ABSOLUTE)
    message = str(caught.value)
    assert "physical sizes must use named p parameters" in message  # the gate itself is unchanged
    assert "line 3" in message and "(75.0, -140.0, 210.0)" in message and "translate()" in message
    assert "placement" in message and "0.5 * p['width']" in message
    with pytest.raises(cad_sandbox.CadCodeError) as caught:
        cad_components.check_component('import cadquery as cq\ndef build(p):\n    w = p["w"] + 5\n    return cq.Workplane("XY").box(w,w,w)')
    assert "physical offsets must use named p parameters" in str(caught.value) and "p['w'] + 5" in str(caught.value)
    cad_components.check_component(LOCAL)  # sizes from p, no position: still accepted


@pytest.mark.parametrize("code", [
    'import cadquery as cq\ndef build(p):\n    return cq.Workplane("XY").box(10,p["d"],p["h"])',
    'import cadquery as cq\ndef build(p):\n    return cq.Workplane("XY").box(p["w"],p["d"],p["h"]).translate((1,0,0))',
    'import cadquery as cq\ndef build(p):\n    return cq.Workplane("XY").circle(2.5).extrude(p["h"])'])
def test_the_gate_itself_is_not_loosened(code):
    with pytest.raises(cad_sandbox.CadCodeError):
        cad_components.check_component(code)


def _anchored(raw, bound=()):
    raw = copy.deepcopy(raw)
    anchor_placements(raw, bound)
    return {p["id"]: p["placement"] for p in raw["parts"] if p["shape"] == "cad"}


def test_a_zero_placement_away_from_the_origin_is_anchored_on_the_envelope_corner():
    """Seen live: placement 0,0,0 asked build(p) for absolute coordinates: the
    gate refused them twice, and a solid at the origin failed its envelope."""
    native = _prepared(threshold())[0]
    track = next(p for p in native.parts if p.id == "track")
    assert track.placement == {"x": track.dim("x0"), "y": track.dim("y0"), "z": track.dim("z1")}
    assert {"x", "y", "z"} == set(track.placement)
    placed = _anchored(threshold(), bound=("track",))
    assert placed["track"] == {"x": "0", "y": "0", "z": "0"}  # a built model keeps the placement it was built for
    explicit = _anchored(threshold({"x": "slab_length", "y": "0", "z": "0"}))
    assert explicit["track"] == {"x": "slab_length", "y": "0", "z": "0"}  # the author's placement stands


def test_a_part_around_the_origin_keeps_its_zero_placement():
    raw = threshold()
    next(p for p in raw["parts"] if p["id"] == "track")["dims"] = _dims(
        x0="0 - track_section_width", x1="track_section_width", y0="0", y1="track_section_height", z0="0", z1="run_length")
    assert _anchored(raw)["track"] == {"x": "0", "y": "0", "z": "0"}


def _cad_tools(repo, responses, built=None, raw=None):
    """A first accepted version, and a CAD writer that answers from `responses`."""
    prompts = []
    def respond(payload):
        purpose = payload["text"]["format"]["name"]
        if purpose == "read":
            return _read_response(payload)
        prompts.append(payload)
        return response_json({"code": responses.pop(0)})
    job, fence = _claim(repo)
    tools = Tools(repo, fence, ModelGateway(repo, fence, respond), cad_builder=built)
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": raw or threshold()})
    return job, tools, written, prompts


def _box_mesh(bounds):
    x0, y0, z0, x1, y1, z1 = bounds
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z1), (x1, y1, z1)]
    return {"positions": [v for corner in corners[:3] + corners[3:] + corners[:1] for v in corner]}


def _builder(params_seen=None):
    def built(code, params, features, measurement_params=None):
        cad_components.check_component(code)
        if params_seen is not None:
            params_seen.append(params)
        bounds = [0, 0, -params["depth"], params["width"], params["height"], 0]
        return {"step": b"step", "stl": b"stl", "mesh": _box_mesh(bounds), "measured": {"bounds": bounds, "volume": 1, "faces": []},
                "checks": [], "toolchain_sha": "t", "input_sha": "i"}
    return built


def test_code_the_gate_refuses_is_retried_once_with_the_reason_and_spends_no_cad_call(source_repo):
    job, tools, written, prompts = _cad_tools(source_repo, [ABSOLUTE, LOCAL], _builder())
    result = tools.run("model", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "the track"})
    assert result.get("placeholder") is None, result
    assert len(prompts) == 2
    retry = prompts[1]["input"][0]["content"][0]["text"]
    assert "This code was rejected" in retry and "translate()" in retry and ABSOLUTE in retry
    assert source_repo.run(job["id"])["consumed"]["cad_calls"] == 1


def test_code_refused_twice_is_a_placeholder_with_the_reason_and_no_cad_call(source_repo):
    job, tools, written, prompts = _cad_tools(source_repo, [ABSOLUTE, ABSOLUTE, LOCAL], _builder())
    result = tools.run("model", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "the track"})
    assert "translate()" in result["placeholder"]
    assert source_repo.run(job["id"])["consumed"].get("cad_calls", 0) == 0
    check = next(c for c in result["checks"] if c["id"] == "cad-track")
    assert check["status"] == "FAIL" and "translate()" in check["reason"]  # the agent reads the same reason
    tools.run("again", "model_component", {"version_id": result["version_id"], "part_key": "track", "brief": "again"})
    assert "The previous attempt failed: physical sizes" in prompts[2]["input"][0]["content"][0]["text"]


def test_the_cad_writer_sees_the_drawing_and_a_frame_it_can_reach(source_repo):
    job, tools, written, prompts = _cad_tools(source_repo, [LOCAL], _builder())
    tools.run("model", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "the track"})
    content = prompts[0]["input"][0]["content"]
    assert any(item["type"] == "input_image" for item in content)
    text = content[0]["text"]
    assert "Required LOCAL CadQuery [xmin,ymin,zmin,xmax,ymax,zmax]: [0.0,0.0,0.0,130.0,300.0,70.0]" in text
    assert "start from (0,0,0)" in text and "never translate to a world position" in text


def test_a_part_with_a_model_keeps_its_placement_through_a_patch(source_repo, monkeypatch):
    from app import agent_tools
    job, tools, written, prompts = _cad_tools(source_repo, [LOCAL], _builder())
    built = tools.run("model", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "the track"})
    seen, real = [], agent_tools.prepare
    def spying(*args, **kwargs):
        seen.append(kwargs["bound"])
        return real(*args, **kwargs)
    monkeypatch.setattr(agent_tools, "prepare", spying)
    tools.run("patch", "patch_program", {"edits": [{"op": "replace", "path": "/params/@slab_length/value", "value": 510}]})
    assert seen == [{"track"}], built


def test_a_solid_off_its_envelope_is_told_the_placement_shift():
    actual, expected = [0, 0, -120, 140, 8, 0], [70, 200, 0, 210, 208, 120]
    problem = envelope_problem(actual, expected)
    assert "right size" in problem and "placement" in problem and "'x': 70" in problem and "'z': 120" in problem
    assert "do not remodel" in problem


def test_a_solid_of_the_wrong_size_is_told_so():
    problem = envelope_problem([0, 0, 0, 90, 8, 120], [0, 0, 0, 140, 8, 120])
    assert "[90, 8, 120]" in problem and "[140, 8, 120]" in problem and "param_bindings" in problem


def test_a_built_solid_off_its_envelope_reports_the_shift_in_its_check(source_repo):
    raw = threshold({"x": "slab_length", "y": "0", "z": "0"})
    job, tools, written, prompts = _cad_tools(source_repo, [LOCAL], _builder(), raw)
    result = tools.run("model", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "the track"})
    check = next(c for c in result["checks"] if c["id"] == "cad-envelope-track")
    assert check["status"] == "FAIL" and "add {" in check["reason"]


# ------------------------------------------------------------------- the renderer

class DeadBrowser:
    def __init__(self, error):
        self.error = error

    def render(self, version_id, manifest_sha):
        raise self.error


def test_a_failed_render_is_feedback_not_a_lost_run(source_repo):
    """Seen live: a browser that died between renders ended a run at 0 FAILs as
    failed, with no review and no continuation."""
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response),
                  renderer=DeadBrowser(RuntimeError("browser has been closed")))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": threshold()})
    result = tools.run("render", "render", {"version_id": written["version_id"]})
    assert "the renderer failed: browser has been closed" in result["error"]["message"]
    assert source_repo.get(d.jobs, job["id"])["state"] == "running"


def test_a_final_review_with_no_render_still_ends_as_a_draft_for_review(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    model = CachedTurns(50)
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"}, budget={"model_calls": 8})
    Worker(source_repo, responder=model, renderer=DeadBrowser(RuntimeError("gone"))).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert final["state"] == "needs_review" and final["result"]["stop_reason"] == "budget_floor"
    assert final["result"]["version_id"] == source_repo.run(job["id"])["working_version_id"]


def test_a_renderer_budget_stop_is_still_a_budget_stop(source_repo):
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, _read_response),
                  renderer=DeadBrowser(BudgetExceeded("run deadline reached")))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": threshold()})
    with pytest.raises(BudgetExceeded):
        tools.run("render", "render", {"version_id": written["version_id"]})


def test_a_browser_that_died_while_idle_is_replaced_once(monkeypatch):
    from app import render
    class Dead:
        def new_page(self, **options):
            raise RuntimeError("Target page, context or browser has been closed")
    class Alive:
        def new_page(self, **options):
            return "page"
    render._local.browser = Dead()
    launched = []
    def fresh():
        current = getattr(render._local, "browser", None)
        if current is None:
            launched.append(1)
            render._local.browser = Alive()
        return render._local.browser
    monkeypatch.setattr(render, "_browser", fresh)
    assert render._open_page(viewport={}) == "page" and launched == [1]
    render._local.browser = Dead()
    monkeypatch.setattr(render, "_browser", lambda: Dead())
    with pytest.raises(RuntimeError):  # a second failure is the real error
        render._open_page(viewport={})
    render._local.browser = None


# -------------------------------------------- item 10 and the source dimension claims

def test_a_sheet_laid_on_its_surface_is_half_embedded_a_real_gap_not_a_verifier_bug():
    """Seen live: assembly-0-covers measured minus half the sheet's thickness. A
    sheet is centred on its path, so a path on the slab's top leaves half of it inside."""
    from app.geometry import build_assembly
    raw = threshold()
    layer = raw["parts"][1]
    layer.update(shape="sheet", path=[{"x": "0", "y": "structural_slab_top_level"}, {"x": "slab_length", "y": "structural_slab_top_level"}],
                 dims=_dims(thickness="liquid_applied_layer_thickness", z0="0", z1="run_length"))
    raw["parts"] = raw["parts"][:2]
    def measured(raw):
        native, spec, _ = prepare(raw, SOURCE, "door", dict(READ, elements=[]))
        return {c["id"]: c for c in spatial_checks(native, build_assembly(spec))}["assembly-0-covers"]
    assert measured(raw)["status"] == "FAIL" and measured(raw)["min_gap"] == pytest.approx(-1)
    for point in layer["path"]:
        point["y"] = "structural_slab_top_level + 0.5 * liquid_applied_layer_thickness"
    assert measured(raw)["status"] == "PASS" and measured(raw)["min_gap"] == pytest.approx(0)
    assert "centred on its path" in INSTRUCTIONS


def test_a_vertical_layer_bonded_to_a_face_it_does_not_touch_measures_nothing_a_real_miss():
    """Seen live: assembly-1-bonded_to had coverage 0: the upstand stood on the
    slab's top, and the relation named the slab's far end face."""
    from app.geometry import build_assembly
    raw = threshold()
    raw["parts"] = raw["parts"][:2]
    raw["parts"][1].update(shape="sheet", path=[{"x": "track_centre_offset", "y": "structural_slab_top_level"},
                                                {"x": "track_centre_offset", "y": "structural_slab_top_level + slab_depth"}],
                           dims=_dims(thickness="liquid_applied_layer_thickness", z0="0", z1="run_length"), role="finish")
    raw["relations"] = [{"source": "layer", "target": "slab", "kind": "bonded_to", "constraints": {
        "axis": "x", "source_face": "min", "target_face": "max", "min_gap": 0, "max_gap": 1, "min_coverage": 0.9}}]
    native, spec, _ = prepare(raw, SOURCE, "door", dict(READ, elements=[]))
    check = {c["id"]: c for c in spatial_checks(native, build_assembly(spec))}["assembly-0-bonded_to"]
    assert check["status"] == "FAIL" and check["coverage"] == 0 and check["min_gap"] is None


def _bent_sheet(claim):
    raw = threshold()
    raw["parts"] = raw["parts"][:2]
    raw["parts"][1].update(shape="sheet", dims=_dims(thickness="liquid_applied_layer_thickness", z0="0", z1="run_length"), features=[claim],
                           path=[{"x": "0", "y": "structural_slab_top_level"}, {"x": "slab_length", "y": "structural_slab_top_level"},
                                 {"x": "slab_length", "y": "structural_slab_top_level + slab_depth"}])
    raw["relations"] = []
    return raw


def _sheet_check(repo, claim):
    job, tools, written = _written(repo, _bent_sheet(claim))
    return {c["id"]: c for c in written["checks"]}


def test_a_thickness_claimed_on_a_bent_sheet_is_measured_through_the_sheet(source_repo):
    """Seen live: an extent across the bends read 18.8 mm against a 2 mm membrane, and the
    critic took that for a thick slab."""
    check = _sheet_check(source_repo, {"id": "t", "kind": "extent", "selector": {"axis": "y"},
                                       "expected_expr": "liquid_applied_layer_thickness", "tolerance": 0.05})["feature-layer-t"]
    assert check["status"] == "PASS" and check["actual"] == pytest.approx(2)


def test_any_other_extent_of_a_bent_sheet_is_still_its_whole_span(source_repo):
    check = _sheet_check(source_repo, {"id": "t", "kind": "extent", "selector": {"axis": "y"}, "expected_expr": "slab_depth"})["feature-layer-t"]
    assert check["status"] == "FAIL" and check["actual"] > 150


def test_a_sheet_with_no_straight_run_says_it_cannot_measure_a_thickness():
    from types import SimpleNamespace
    from app.geometry import Mesh, _box_tris
    from app.verification_v3 import sheet_thickness_claim
    mesh = Mesh("layer")
    _box_tris(mesh, 0, 0, 0, 10, 10, 10)
    part = SimpleNamespace(dim=lambda name: "2", path=[SimpleNamespace(x="0", y="0"), SimpleNamespace(x="10", y="10")])
    feature = SimpleNamespace(kind="extent", selector={"axis": "y"})
    result = sheet_thickness_claim(part, mesh, feature, {"expected": 2, "tolerance": 0.05, "status": "FAIL", "actual": 10}, {})
    assert result["status"] == "UNVERIFIED" and "no straight run" in result["reason"]


def test_a_feature_expecting_another_number_loses_its_claim_on_a_printed_dimension():
    """Seen live: every feature claimed d0, so the printed dimension had no single
    owner and stayed UNVERIFIED forever."""
    raw = threshold()
    raw["parts"][0]["features"] = [
        {"id": "wide", "kind": "extent", "selector": {"axis": "x"}, "expected_expr": "slab_length", "source_dimension_id": "d0"},
        {"id": "step", "kind": "extent", "selector": {"axis": "y"}, "expected_expr": "step_down", "source_dimension_id": "d0"}]
    native, _, notes = _prepared(raw)
    claims = {f.id: f.source_dimension_id for f in native.parts[0].features}
    assert claims == {"wide": None, "step": "d0"} and any("wide" in note and "claim dropped" in note for note in notes)


def test_model_checks_tally_every_check():
    checks = [{"id": "a", "status": "PASS"}, {"id": "b", "status": "FAIL", "reason": "gap", "description": "gap"},
              {"id": "c", "status": "N/A"}, {"id": "d", "status": "UNVERIFIED", "description": "d"}]
    view = model_checks(checks)
    assert view["check_counts"] == {"FAIL": 1, "N/A": 1, "PASS": 1, "UNVERIFIED": 1}
    assert view["checks"] == [{"id": "b", "status": "FAIL", "reason": "gap"}] and view["informational"] == ["d"]
    # A check that lists work to do stays in full, required or not.
    labels = {"id": "label-coverage", "status": "UNVERIFIED", "required": False, "missing": ["Bead of mastic"]}
    assert model_checks([labels])["checks"] == [{"id": "label-coverage", "status": "UNVERIFIED", "required": False,
                                                 "missing": ["Bead of mastic"]}]
