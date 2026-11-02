"""A drawing to a 3D scene built by Muse in a sandbox. The model and the sandbox are stubbed: the scene is its list of code steps."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import database as d, studio
from app.main import app
from app.runtime import repository
from app.worker import Worker
from goal import blender_live


def png(shade=255):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (shade, shade, shade)).save(out, "PNG")
    return out.getvalue()


class Muse:
    """Plays a script of tool calls, one list a turn."""
    def __init__(self, *turns, items=(), ticks=()):
        self.turns, self.payloads, self.items, self.ticks, self.checked = list(turns), [], list(items), list(ticks), []

    def __call__(self, payload):
        form = payload.get("text", {}).get("format", {}).get("name")
        if form:
            self.checked.append(payload)
            if form == "studio_features":
                answer = {"detail": "a slab", "items": [{"id": k, "what": what, "importance": "must"} for k, what in enumerate(self.items)]}
            else:
                state = self.ticks.pop(0) if self.ticks else "right"
                answer = {"ticks": [{"id": k, "state": state, "note": "" if state == "right" else "add it"} for k in range(len(self.items))], "not_in_source": []}
            return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}], "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}
        self.payloads.append(payload)
        calls = self.turns.pop(0) if self.turns else [("finish", {"what": "done", "unsure": []})]
        output = [{"type": "function_call", "call_id": f"c{len(self.payloads)}-{k}", "name": name, "arguments": json.dumps(args)} for k, (name, args) in enumerate(calls)]
        return {"status": "completed", "output": output, "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}


@pytest.fixture
def sandbox(monkeypatch):
    """Records what ran, and fails a step that says so."""
    runs = []

    def run(steps, views, export=False):
        runs.append(list(steps))
        error = next((f"failed: {s}" for s in steps if "boom" in s), None)
        parts = [{"name": f"part {k}", "part": f"part {k}", "group": "g", "material": "concrete", "from": [0, 0, 0], "to": [1, 1, 1], "triangles": 12, "step": k + 1} for k in range(len(steps))]
        found = {"scene.json": json.dumps({"error": error, "seconds": 0.1, "parts": parts}).encode(), **{f"{v}.jpg": png(200 - len(steps)) for v in views}}
        if export:
            found["model.glb"] = b"glb"
        return found
    monkeypatch.setattr(blender_live, "sandbox", run)
    monkeypatch.setattr(studio, "ensure_worker", lambda: None)
    monkeypatch.setattr(studio.meta_client, "configured", lambda: True)
    return runs


def work(muse):
    repo = repository()
    while Worker(repo, responder=muse).run_once():
        pass


STEP = ("run_python", {"intent": "the slab", "code": "SLAB = 200\nbox('slab', (0, 0, 0), (1, 1, SLAB), 'concrete', 'structure')"})


def test_a_drawing_becomes_a_scene_of_code_steps(sandbox):
    muse = Muse([STEP], [("finish", {"what": "a slab", "unsure": ["how thick"]})])
    made = studio.start("d.png", png())
    work(muse)
    held = studio.get(made["id"])
    assert held["state"] == "ready" and held["title"] == "a slab" and held["unsure"] == ["how thick"]
    assert held["step_count"] == 1 and held["intents"] == ["the slab"] and held["parts"][0]["step"] == 1
    assert studio.file_of(made["id"], "model.glb")[0] == b"glb"


def test_a_failed_step_is_feedback_and_is_not_kept(sandbox):
    muse = Muse([("run_python", {"intent": "bad", "code": "boom = 1"})], [STEP])
    made = studio.start("d.png", png())
    work(muse)
    assert studio.get(made["id"])["intents"] == ["the slab"]
    told = next(i for i in muse.payloads[1]["input"] if i.get("type") == "function_call_output")
    assert "not kept" in told["output"]


@pytest.mark.parametrize("code", ["import os\nos.system('x')", "open('/etc/passwd')", "().__class__", "import urllib.request", "exec('1')"])
def test_unsafe_code_never_reaches_the_sandbox(sandbox, code):
    muse = Muse([("run_python", {"intent": "bad", "code": code})], [STEP])
    studio.start("d.png", png())
    work(muse)
    assert not any(code in step for run in sandbox for step in run)
    told = next(i for i in muse.payloads[1]["input"] if i.get("type") == "function_call_output")
    assert "rejected" in told["output"]


def test_a_checkpoint_holds_pictures_by_reference(sandbox):
    muse = Muse([STEP])
    studio.start("d.png", png())
    work(muse)
    rows = repository().list(d.steps, d.steps.c.kind == "model")
    assert "data:image" not in json.dumps(rows[-1]["request"])


def test_feedback_carries_on_from_the_scene(sandbox):
    muse = Muse([STEP])
    made = studio.start("d.png", png())
    work(muse)
    muse = Muse([("run_python", {"intent": "the wall", "code": "W = 100"})])
    studio.feedback(made["id"], "add a wall")
    work(muse)
    held = studio.get(made["id"])
    assert held["intents"] == ["the slab", "the wall"]
    assert "add a wall" in json.dumps(muse.payloads[0]["input"][0]) and sandbox[-1][0].startswith("SLAB")
    assert any(item["kind"] == "person" for item in held["feed"])


def test_undo_drops_the_last_step(sandbox):
    muse = Muse([STEP], [("run_python", {"intent": "the wall", "code": "W = 100"})])
    made = studio.start("d.png", png())
    work(muse)
    held = studio.undo(made["id"])
    assert held["intents"] == ["the slab"] and len(sandbox[-1]) == 1


def test_a_scene_being_built_refuses_changes(sandbox):
    made = studio.start("d.png", png())
    with pytest.raises(studio.Busy):
        studio.feedback(made["id"], "change it")
    with pytest.raises(studio.Busy):
        studio.undo(made["id"])
    work(Muse([STEP]))


def test_only_pictures_and_pdfs_are_taken(sandbox):
    with pytest.raises(ValueError):
        studio.start("d.txt", b"not a drawing")


def test_a_budget_stop_keeps_what_was_built(sandbox, monkeypatch):
    from app.repository import BudgetExceeded

    class Stops(Muse):
        def __call__(self, payload):
            if len(self.payloads) == 1:
                raise BudgetExceeded("tokens budget exhausted")
            return super().__call__(payload)
    made = studio.start("d.png", png())
    work(Stops([STEP]))
    held = studio.get(made["id"])
    assert held["state"] == "ready" and held["step_count"] == 1


def test_the_routes(sandbox):
    client = TestClient(app)
    assert client.get("/api/studio/none").status_code == 404
    assert client.post("/api/studio/none/undo").status_code == 404
    assert client.get("/api/studio/none/drawing").status_code == 404
    assert client.post("/api/studio/none/feedback", json={"text": "x"}).status_code == 404
    assert "scenes" in client.get("/api/studio").json()


def test_muse_can_measure_without_adding_a_step(sandbox):
    muse = Muse([STEP], [("list_parts", {})])
    made = studio.start("d.png", png())
    work(muse)
    assert studio.get(made["id"])["step_count"] == 1
    told = [i for i in muse.payloads[2]["input"] if i.get("type") == "function_call_output"][-1]
    assert "part 0" in told["output"]


def test_finish_is_refused_until_the_checker_is_satisfied(sandbox):
    finish = ("finish", {"what": "a slab", "unsure": []})
    fix = ("run_python", {"intent": "the wall", "code": "W = 100"})
    muse = Muse([STEP], [finish], [fix], [finish], items=["a wall on the left"], ticks=["missing", "right"])
    made = studio.start("d.png", png())
    work(muse)
    held = studio.get(made["id"])
    assert held["state"] == "ready" and held["intents"] == ["the slab", "the wall"]
    assert [r["right"] for r in held["checks"]["rounds"]] == [0, 1] and held["checks"]["last"]["passed"]
    told = [i for p in muse.payloads for i in p["input"] if i.get("type") == "function_call_output" and "checker_says" in i["output"]]
    assert told and "a wall on the left" in told[0]["output"]


def test_refusals_stop_and_what_is_left_goes_to_the_person(sandbox):
    finish = ("finish", {"what": "a slab", "unsure": []})
    muse = Muse([STEP], [finish], [finish], [finish], [finish], [finish], items=["a wall"], ticks=["missing"] * 9)
    made = studio.start("d.png", png())
    work(muse)
    held = studio.get(made["id"])
    assert held["state"] == "ready" and len(held["checks"]["rounds"]) == blender_live.REFUSALS + 1
    assert any("a wall" in u for u in held["unsure"])


def test_the_checker_sees_every_side_and_none_of_the_builders_words(sandbox):
    finish = ("finish", {"what": "SECRET", "unsure": []})
    muse = Muse([("run_python", {"intent": "SECRET", "code": "HIDDEN = 1"})], [finish], items=["a wall"])
    studio.start("d.png", png())
    work(muse)
    seen = muse.checked[-1]["input"][0]["content"]
    assert sum(part["type"] == "input_image" for part in seen) == 6
    assert "SECRET" not in json.dumps(muse.checked) and "HIDDEN" not in json.dumps(muse.checked)


def test_an_unanswered_item_is_not_passed_as_seen():
    from goal import blender_check
    features = {"items": [{"id": 1, "what": "a wall", "importance": "must"}, {"id": 2, "what": "a nail", "importance": "should"}]}
    done = blender_check.verdict(features, {"ticks": [{"id": 2, "state": "missing", "note": "x"}], "not_in_source": []})
    assert done["passed"] and done["right"] == 0 and done["rows"][0]["state"] == "cant_tell"
    done = blender_check.verdict(features, {"ticks": [{"id": 1, "state": "wrong", "note": "x"}], "not_in_source": []})
    assert not done["passed"]


def test_running_out_of_turns_still_ends_with_a_check(sandbox, monkeypatch):
    monkeypatch.setattr(blender_live, "TURNS", 2)
    keep_going = ("run_python", {"intent": "more", "code": "M = 1"})

    class Busy(Muse):
        def __call__(self, payload):
            if not payload.get("text"):
                self.turns.append([keep_going])
            return super().__call__(payload)
    muse = Busy(items=["a wall"], ticks=["missing"] * 3)
    made = studio.start("d.png", png())
    work(muse)
    held = studio.get(made["id"])
    assert held["title"] == "a slab" and len(held["checks"]["rounds"]) == 1
    assert any("ran out of turns" in u for u in held["unsure"]) and any("a wall" in u for u in held["unsure"])
