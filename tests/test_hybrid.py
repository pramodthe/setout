"""Parallel building, pinned hardware, isolated checking and safe repair fallback."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import database as d, studio
from app.main import app
from app.model_gateway import ModelGateway
from app.repository import BudgetExceeded, Repository
from app.runtime import repository
from app.worker import Worker
from builder import hybrid, library
from test_studio import png, sandbox  # noqa: F401 - shared sandbox fixture


PLAN = {"title": "Two layers", "detail": "A generic two-layer test assembly", "assumptions": ["Sizes assumed"],
        "parameters": [{"name": "LEVEL", "value": 20}], "outlines": [], "components": [
            {"id": "base", "intent": "the base", "specification": "ends at LEVEL", "source_items": [1], "asset_ids": []},
            {"id": "cap", "intent": "the cap", "specification": "starts at LEVEL", "source_items": [1], "asset_ids": []}]}
CODE = "box('part', (0, 0, 0), (100, 100, P['LEVEL']), 'concrete', 'base')"


class Muse:
    def __init__(self, states=("right",), codes=None, repair=None):
        self.states, self.codes, self.repair = list(states), codes or {}, repair
        self.payloads, self.lock = [], threading.Lock()
        self.live_states = []
        self.barrier = threading.Barrier(2)

    def __call__(self, payload):
        purpose = payload["text"]["format"]["name"]
        text = payload["input"][0]["content"][0]["text"]
        with self.lock:
            self.payloads.append(payload)
        if purpose == "studio_plan":
            answer = copy.deepcopy(PLAN)
        elif purpose == "studio_features":
            answer = {"title": "Layers", "detail": "Two layers", "items": [{"id": 1, "what": "cap on base", "importance": "must"}]}
        elif purpose == "studio_component":
            component = json.loads(text.split("Assigned component:\n")[1].split("\nSelected assets:")[0])
            # Both calls must reach the provider together, not just a mocked executor.
            self.barrier.wait(timeout=5)
            answer = {"code": self.codes.get(component["id"], CODE), "assumptions": []}
        elif purpose == "studio_check":
            self.live_states.append(studio.get(self.scene_id))
            answer = {"ticks": [{"id": 1, "state": self.states.pop(0), "note": "adjust the cap"}], "not_in_source": []}
        elif purpose == "studio_repair":
            answer = self.repair or {"replacements": [{"id": "cap", "code": "# repaired\n" + CODE}], "assumptions": []}
        else:
            raise AssertionError(purpose)
        return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}],
                "usage": {"input_tokens": 100, "output_tokens": 50}}


def build(muse):
    made = studio.start("drawing.png", png(), method="hybrid")
    muse.scene_id = made["id"]
    job = repository().get(d.studio_scenes, made["id"])["job_id"]
    Worker(repository(), responder=muse).run_once(only_ids=[job])
    return repository().get(d.studio_scenes, made["id"])


def test_parallel_components_share_plan_and_export_once(sandbox):
    muse = Muse()
    row = build(muse)
    assert row["state"] == "ready", row["error"]
    assert len(sandbox) == 1  # no individual component renders or duplicate publish
    assert len(row["steps"]) == 2 and row["intents"] == ["the base", "the cap"]
    assert all("P = {'LEVEL': 20}" in step for step in row["steps"])
    assert row["tokens"] == 5 * 150
    assert row["checks"]["build"]["method"] == "hybrid"
    assert row["checks"]["last"]["passed"]
    assert all(s["state"] == "working" and s["live_model"] for s in muse.live_states)
    assert all(p["reasoning"]["effort"] == "low" for p in muse.payloads if p["text"]["format"]["name"] in ("studio_plan", "studio_component"))
    check = next(p for p in muse.payloads if p["text"]["format"]["name"] == "studio_check")
    text = check["input"][0]["content"][0]["text"]
    assert "LEVEL" not in text and "specification" not in text and CODE not in text
    assert check["reasoning"]["effort"] == "medium"
    assert "data:image" not in json.dumps(repository().list(d.steps))


def test_targeted_repair_keeps_other_component_unchanged(sandbox):
    row = build(Muse(states=("wrong", "right")))
    assert row["state"] == "ready" and row["checks"]["last"]["passed"]
    assert len(sandbox) == 2
    assert sandbox[0][0] == sandbox[1][0]
    assert sandbox[0][1] != sandbox[1][1] == row["steps"][1]


def test_failed_repair_restores_steps_preview_and_check(sandbox):
    row = build(Muse(states=("wrong",), repair={"replacements": [{"id": "cap", "code": "boom = 1"}], "assumptions": []}))
    assert row["state"] == "ready"
    assert row["steps"] == sandbox[0]
    assert "boom" not in "\n".join(row["steps"])
    assert row["checks"]["last"]["rows"][0]["state"] == "wrong"
    assert any("earlier checked model" in note for note in row["unsure"])


def test_empty_repair_does_not_render_or_check_again(sandbox):
    row = build(Muse(states=("wrong",), repair={"replacements": [], "assumptions": []}))
    assert row["state"] == "ready" and not row["checks"]["last"]["passed"]
    assert len(sandbox) == 1


def test_worse_visual_repair_restores_the_original_export(sandbox, monkeypatch):
    original = hybrid.checker.check
    calls = []

    def check(*args):
        result, tokens = original(*args)
        calls.append(result)
        if len(calls) == 2:
            result["not_in_source"] = ["an invented large extra part"]
        return result, tokens

    monkeypatch.setattr(hybrid.checker, "check", check)
    row = build(Muse(states=("wrong", "wrong")))
    assert row["state"] == "ready" and row["steps"] == sandbox[0]
    assert row["checks"]["last"]["not_in_source"] == []
    assert len(row["checks"]["rounds"]) == 2
    assert "repaired" not in row["steps"][1]


def test_incomplete_component_response_is_retried_before_rendering():
    prompts = []

    def ask(prompt, *_):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise ValueError("studio_component reached the provider output limit")
        return {"code": CODE, "assumptions": []}, 100

    live = hybrid.Build(png(), ask)
    live.plan = PLAN
    answer, step, assets, spent = live._component(PLAN["components"][0], [], {})
    assert len(prompts) == 2 and "concise code" in prompts[-1]
    assert CODE in step and spent == 100 and assets == []


def test_code_and_assets_are_checked_before_any_sandbox():
    component = {**PLAN["components"][0], "asset_ids": ["p0001"]}
    triangles = [[[0, 0, 0], [1, 0, 0], [0, 1, 2]]]
    code = "place_asset('p0001', 'fixing', (0,0,0), (0,0,1), 'steel', 'base')"
    step, used = hybrid.component_step(PLAN, component, code, {"p0001": triangles})
    assert used == ["p0001"] and repr(triangles) in step
    # The exact mesh, not a mutable id/file reference, survives later library edits.
    triangles[0][0][0] = 900
    assert "900" not in step
    for bad in ("open('file')", "import os", "global P", "from math import *", "unknown_name", code.replace("p0001", "../outside"), code.replace("'p0001'", "P['ID']")):
        with pytest.raises((ValueError, SyntaxError)):
            hybrid.component_step(PLAN, component, bad, {"p0001": triangles})


def test_component_name_analysis_preserves_imports_closures_and_builtins():
    code = "from mathutils import Vector\ndef helper():\n    return Vector((P['LEVEL'], 0, 0))\npoint = helper()\nfor i in range(2):\n    rod(str(i), point, (100, 0, 0), 3, 'steel', 'base')"
    step, _ = hybrid.component_step(PLAN, PLAN["components"][0], code, {})
    assert code.splitlines()[0] in step


def test_invalid_plan_never_reaches_component_generation():
    for change in ({"components": PLAN["components"] * 2}, {"parameters": [{"name": "bad-name", "value": 1}]},
                   {"components": [{**PLAN["components"][0], "asset_ids": ["unknown"]}]}):
        with pytest.raises(ValueError):
            hybrid.validate_plan({**PLAN, **change}, [])


def test_hyphenated_component_ids_remain_valid_python():
    plan = copy.deepcopy(PLAN)
    plan["components"][0]["id"] = "C01-BASE"
    hybrid.validate_plan(plan, [])
    step, _ = hybrid.component_step(plan, plan["components"][0], CODE, {})
    assert "def _component_C01_BASE():" in step
    compile(step, "component", "exec")  # compile only; never execute generated code locally


def test_plan_must_assign_every_independent_source_item():
    with pytest.raises(ValueError, match="missing"):
        hybrid.validate_plan(PLAN, [], {"items": [{"id": 1}, {"id": 2}]})


def test_rounded_path_is_tangent_in_both_directions_and_fits_short_segments():
    import ast
    import math
    from builder import loop
    # This is our pure geometry helper, not generated model code or Blender.
    tree = ast.parse((loop.HERE / "kit.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "rounded_path")
    namespace = {"math": math}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "kit-helper", "exec"), namespace)
    rounded = namespace["rounded_path"]
    for sign in (1, -1):
        path = rounded([(0, 0), (100, 0), (100, sign * 100)], 20)
        assert path[0] == (0, 0) and path[-1] == (100, sign * 100)
        assert path[1] == pytest.approx((80, 0))
        assert path[-2] == pytest.approx((100, sign * 20))
        assert all(math.dist(p, (80, sign * 20)) == pytest.approx(20) for p in path[1:-1])
    short = rounded([(0, 0), (2, 0), (2, 2)], 100)
    assert all(0 <= x <= 2 and -1e-9 <= y <= 2 for x, y in short)
    assert rounded([(0, 0), (1, 0), (2, 0)], 10) == [(0, 0), (1, 0), (2, 0)]


def test_invalid_sheet_sections_cannot_silently_fill_with_triangles():
    import ast
    from builder import loop
    tree = ast.parse((loop.HERE / "kit.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_simple_outline")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "kit-helper", "exec"), namespace)
    simple = namespace["_simple_outline"]
    assert simple([(0, 0), (10, 0), (10, 10), (8, 10), (8, 2), (0, 2)])
    assert not simple([(0, 0), (10, 10), (0, 10), (10, 0)])
    assert not simple([(0, 0), (10, 0), (5, 0), (5, 10), (0, 10)])


def test_upload_selects_hybrid_and_rejects_unknown_method(sandbox):
    client = TestClient(app)
    response = client.post("/api/studio", files={"file": ("d.png", png(), "image/png")}, data={"method": "hybrid"})
    assert response.status_code == 202
    assert repository().get(d.jobs, response.json()["job_id"])["payload"]["method"] == "hybrid"
    assert client.post("/api/studio", files={"file": ("d.png", png(), "image/png")}, data={"method": "other"}).status_code == 422


def test_parallel_reservations_cannot_spend_checker_allowance(tmp_path):
    repo = Repository("sqlite:///" + str(tmp_path / "db"), tmp_path / "artifacts")
    job = repo.enqueue("studio_build", {}, budget={"tokens": 100})
    fence = repo.claim("worker")
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        try:
            repo.reserve(fence, {"tokens": 60}, token_floor=30)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [False, True]
    assert repo.run(job["id"])["reserved"]["tokens"] == 60


def test_concurrent_gateway_checkpoints_replay_without_new_calls(tmp_path):
    repo = Repository("sqlite:///" + str(tmp_path / "db"), tmp_path / "artifacts")
    job = repo.enqueue("studio_build", {})
    fence = repo.claim("worker")
    barrier, sent = threading.Barrier(3), []

    def respond(payload):
        sent.append(payload)
        barrier.wait(timeout=5)
        return {"output": [], "usage": {"input_tokens": 10, "output_tokens": 20}}

    def call(k):
        return ModelGateway(repo, fence, respond).call({"max_output_tokens": 4096}, "studio_component", str(k), reserve_tokens=50_000)

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = list(pool.map(call, range(3)))
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(call, range(3))) == first
    assert len(sent) == 3
    assert repo.run(job["id"])["consumed"]["tokens"] == 90
    assert repo.run(job["id"])["reserved"]["tokens"] == 0
