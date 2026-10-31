"""CAD comes before patching because the harness runs it, and the budget fits that.

Scripted models, no network. Seen live: five turns of patches before any CAD, then a
turn per object; 30 of 32 calls gone at half the token budget.
"""
import json

import pytest

from app import cad_sandbox, database as d
from app.agent import INSTRUCTIONS
from app.agent_tools import AUTO_CAD, auto_cad_view
from app.model_gateway import ModelGateway
from app.repository import DEFAULT_BUDGET, REVIEW_CALLS, REVIEW_TAIL
from app.worker import Worker
from test_agent_v3 import FakeRenderer, response_json, source_repo  # noqa: F401
from test_threshold_run import LOCAL, _builder, _read_response, _usage, threshold


class Agentic:
    """Reads, writes, and then stops: anything else in a run is the harness's doing."""
    def __init__(self, after=None, code=LOCAL, write=None):
        self.after = after or []
        self.code, self.write = code, write or threshold()
        self.turns, self.inputs, self.cad = 0, [], []

    def __call__(self, payload):
        purpose = payload.get("text", {}).get("format", {}).get("name")
        if purpose == "read":
            return _read_response(payload)
        if purpose == "cad":
            self.cad.append(payload)
            return response_json({"code": self.code, "envelope_problem": ""})
        if purpose == "critic":
            return response_json({"verdict": "fail", "issues": []})
        self.inputs.append(payload["input"])
        i = self.turns
        self.turns += 1
        script = [("read_drawing", {}), ("write_program", {"program": self.write})] + self.after
        name, args = script[i] if i < len(script) else ("finish", {
            "version_id": None, "verification_id": None, "critic_verdict_id": None, "status": "needs_review",
            "summary": "stop", "open_issues": []})
        return {"output": [{"type": "function_call", "name": name, "call_id": f"call-{i}", "arguments": json.dumps(args)}],
                "usage": _usage(100, 0, 10)}


@pytest.fixture
def auto_cad(monkeypatch):
    monkeypatch.setenv("SETOUT_AUTO_CAD", "on")
    monkeypatch.setenv("SETOUT_COMPLETION", "off")


def _run(repo, model, **options):
    job = repo.enqueue("generate_detail", {"figure_id": "figure"}, **options)
    Worker(repo, responder=model, renderer=FakeRenderer(repo), cad_builder=_builder()).run_once(only_ids=[job["id"]])
    return job, repo.run(job["id"])


def _tool_steps(repo, run):
    return [s for s in repo.list(d.steps, d.steps.c.run_id == run["id"], order=d.steps.c.n) if s["kind"] == "tool"]


def test_every_object_is_modelled_before_the_agents_next_turn(source_repo, auto_cad):
    model = Agentic()
    job, run = _run(source_repo, model)
    names = [s["tool_name"] for s in _tool_steps(source_repo, run)]
    assert names[:4] == ["read_drawing", "write_program", "model_component", "model_component"]
    assert model.turns == 4  # read, write, a finish sent back and the one that ends: none for either object
    built = source_repo.get(d.versions, run["working_version_id"])["bindings"]
    assert all(binding.get("build_id") for binding in built.values()) and set(built) == {"track", "bottom_tray"}


def test_the_agent_sees_what_the_harness_built_once(source_repo, auto_cad):
    model = Agentic()
    job, run = _run(source_repo, model)
    notes = [item for item in model.inputs[-1] if item.get("role") == "user" and "ran by itself" in json.dumps(item)]
    assert len(notes) == 2 and "ran by itself on track" in notes[0]["content"][0]["text"]
    first, last = (json.loads(note["content"][0]["text"].split(": ", 1)[1]) for note in notes)
    assert "checks" not in first and "check_counts" in last  # only the last of the chain repeats the verification
    assert first["version_id"] and last["version_id"] == run["working_version_id"]


def test_an_object_with_no_cad_contract_is_left_to_the_agent(source_repo, auto_cad):
    raw = threshold()
    next(p for p in raw["parts"] if p["id"] == "track")["param_bindings"] = {}
    model = Agentic(write=raw)
    job, run = _run(source_repo, model)
    assert [s["arguments"]["part_key"] for s in _tool_steps(source_repo, run) if s["tool_name"] == "model_component"] == ["bottom_tray"]


def test_a_failed_object_gets_one_try_then_it_is_the_agents(source_repo, auto_cad):
    calls = []
    def refused(code, params, features, measurement_params=None):
        calls.append(params)
        raise cad_sandbox.CadCodeError("sandbox refused")
    model = Agentic()
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=model, renderer=FakeRenderer(source_repo), cad_builder=refused).run_once(only_ids=[job["id"]])
    run = source_repo.run(job["id"])
    attempts = [s for s in _tool_steps(source_repo, run) if s["tool_name"] == "model_component"]
    assert len(attempts) == 2 and len(calls) == 2  # one each, with the agent's turn straight after
    bindings = source_repo.get(d.versions, run["working_version_id"])["bindings"]
    assert all("placeholder" in binding for binding in bindings.values())


def test_a_part_whose_contract_changed_is_tried_again(source_repo, auto_cad):
    patch = {"edits": [{"op": "replace", "path": "/params/@track_section_width/value", "value": 140}]}
    model = Agentic(after=[("patch_program", patch)])
    job, run = _run(source_repo, model)
    tracks = [s for s in _tool_steps(source_repo, run) if s["call_id"].startswith(AUTO_CAD + "track")]
    assert len(tracks) == 2 and tracks[0]["call_id"] != tracks[1]["call_id"]


def test_inferred_parts_spend_no_cad(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_AUTO_CAD", "on")
    monkeypatch.delenv("SETOUT_COMPLETION", raising=False)
    model = Agentic()
    job, run = _run(source_repo, model)
    steps = _tool_steps(source_repo, run)
    assert [s["tool_name"] for s in steps[:3]] == ["read_drawing", "write_program", "complete_assembly"]  # slots first
    modelled = [s["arguments"]["part_key"] for s in steps if s["tool_name"] == "model_component"]
    assert modelled == ["track", "bottom_tray"]


def test_the_harness_stops_at_the_cad_budget_and_the_agent_carries_on(source_repo, auto_cad):
    """Out of CAD calls is no reason to end the run: the rest of the work is the agent's."""
    model = Agentic()
    job, run = _run(source_repo, model, budget={"cad_calls": 1})
    assert len([s for s in _tool_steps(source_repo, run) if s["tool_name"] == "model_component"]) == 1
    assert model.turns >= 3  # the agent still got the turn it finished with
    assert source_repo.get(d.jobs, job["id"])["result"]["summary"] == "stop"  # its own finish, not a budget stop


def test_it_can_be_switched_off(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    model = Agentic()
    job, run = _run(source_repo, model)
    assert not [s for s in _tool_steps(source_repo, run) if s["tool_name"] == "model_component"]


def test_a_replayed_run_rebuilds_the_same_conversation(source_repo, auto_cad):
    model = Agentic()
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    first = []
    def stops_after_cad(payload):
        if "text" not in payload and model.turns == 2:
            first.append(payload["input"])
            raise TimeoutError("worker lost")
        return model(payload)
    worker = dict(renderer=FakeRenderer(source_repo), cad_builder=_builder())
    Worker(source_repo, responder=stops_after_cad, **worker).run_once(only_ids=[job["id"]])
    assert source_repo.get(d.jobs, job["id"])["state"] == "queued"
    with d.transaction(source_repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(available_at=0))
    second = []
    def resumed(payload):
        if "text" not in payload:
            second.append(payload["input"])
        return model(payload)
    Worker(source_repo, responder=resumed, **worker).run_once(only_ids=[job["id"]])
    assert second[0] == first[0]
    assert len(model.cad) == 2  # nothing built twice


def test_only_the_last_of_a_chain_carries_the_checks():
    output = {"version_id": "v", "component_id": "c", "component_checks": [], "checks": [{"id": "x", "status": "FAIL"}]}
    assert "checks" not in auto_cad_view(output, final=False) and "checks" in auto_cad_view(output, final=True)


def test_the_instructions_leave_cad_to_the_harness():
    assert "THE HARNESS MODELS EVERY OBJECT BY ITSELF" in INSTRUCTIONS
    assert "Use model_component or reuse_component for each CAD object" not in INSTRUCTIONS
    assert "call recall only to look for something else" in INSTRUCTIONS


# -------------------------------------------------------------------- the budget

def test_a_program_has_room_to_come_back_whole(source_repo):
    """Seen live: 30.9k of 32768, and a program cut off is lost whole."""
    seen = []
    def respond(payload):
        seen.append(payload["max_output_tokens"])
        return response_json({})
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    fence = source_repo.claim("room-test", only_ids=[job["id"]])
    ModelGateway(source_repo, fence, respond).json("write", [], {"type": "object"}, "program", "call")
    assert seen == [49152]


def test_a_cheap_run_reaches_its_tokens_before_its_calls_stop_it():
    """Seen live: a patching run at 5.4k tokens and 20 s a call stopped on 32 calls with
    half its tokens left. At 48 calls the deadline does not get there first."""
    paid_per_call, seconds_per_call = 5400, 20
    agent_calls = DEFAULT_BUDGET["model_calls"] - REVIEW_CALLS
    assert agent_calls == 47 and DEFAULT_BUDGET["tokens"] == 320_000
    assert agent_calls * paid_per_call <= DEFAULT_BUDGET["tokens"] - REVIEW_TAIL
    assert DEFAULT_BUDGET["seconds"] >= DEFAULT_BUDGET["model_calls"] * seconds_per_call
    assert DEFAULT_BUDGET["tool_calls"] > agent_calls


def test_a_run_of_nothing_but_cache_hits_still_stops_at_the_hard_limit(source_repo):
    calls = []
    def respond(payload):
        calls.append(payload)
        return {"output": [{"type": "function_call", "name": "recall", "call_id": f"c{len(calls)}",
                            "arguments": json.dumps({"query": "door"})}], "usage": _usage(5000, 5000, 0)}
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=respond).run_once(only_ids=[job["id"]])
    final = source_repo.get(d.jobs, job["id"])
    assert len(calls) == DEFAULT_BUDGET["model_calls"] and final["state"] == "needs_review"
    assert source_repo.run(job["id"])["consumed"]["tokens"] == 0


def test_the_review_keeps_room_for_a_late_turn_and_the_close_ups():
    turn, draft, retry, critic, locate, check, objects = 1, 1, 1, 1, 1, 1, 7  # seven objects seen
    assert REVIEW_CALLS >= turn + draft + retry + critic + locate + check + objects


def test_the_tail_holds_what_a_final_review_really_costs():
    """Seen live: 26k for the critic and 5.3k an object, and seven objects."""
    assert REVIEW_TAIL >= 26_000 + 7 * 5_300


def test_the_final_review_stays_inside_the_budget_with_close_ups_to_find(source_repo, monkeypatch):
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    monkeypatch.setenv("SETOUT_CLOSE_UP", "on")
    purposes = []
    model = Agentic()
    def respond(payload):
        purpose = payload.get("text", {}).get("format", {}).get("name")
        purposes.append(purpose)
        if purpose == "locate":
            return response_json({"parts": []})
        if purpose == "critic":
            return response_json({"verdict": "fail", "issues": [], "missing_parts": []})
        if purpose is None:
            model.turns += 1
            if model.turns > 2:
                return {"output": [{"type": "function_call", "name": "recall", "call_id": f"c{model.turns}",
                                    "arguments": json.dumps({"query": "door"})}], "usage": _usage(100, 0, 10)}
            model.turns -= 1
        return model(payload)
    job, run = _run(source_repo, respond, budget={"model_calls": 20})
    assert source_repo.list(d.critics) and run["consumed"]["model_calls"] <= 20
