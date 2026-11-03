"""A drawing to a 3D scene built by Muse in a sandbox. The model and the sandbox are stubbed: the scene is its list of code steps."""
import io
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import database as d, studio
from app.main import app
from app.runtime import repository
from app.worker import Worker
from builder import loop


def png(shade=255):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (shade, shade, shade)).save(out, "PNG")
    return out.getvalue()


class Muse:
    """Plays a script of tool calls, one list a turn."""
    def __init__(self, *turns, items=(), ticks=(), relations=()):
        self.turns, self.payloads, self.items, self.ticks, self.checked, self.relations = list(turns), [], list(items), list(ticks), [], list(relations)

    def __call__(self, payload):
        form = payload.get("text", {}).get("format", {}).get("name")
        if form:
            self.checked.append(payload)
            if form == "studio_features":
                answer = {"detail": "a slab", "items": [{"id": k, "what": what, "importance": "must"} for k, what in enumerate(self.items)]}
            elif form == "studio_contract":
                answer = {"parts": [{"id": 1, "what": "a slab"}, {"id": 2, "what": "a wall"}], "relations": self.relations}
            elif form == "studio_match":
                answer = {"matches": [{"id": 1, "objects": [0]}, {"id": 2, "objects": [1]}]}
            else:
                state = self.ticks.pop(0) if self.ticks else "right"
                answer = state if isinstance(state, dict) else {"ticks": [{"id": k, "state": state, "note": "" if state == "right" else "add it"} for k in range(len(self.items))], "not_in_source": []}
            return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}], "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}
        self.payloads.append(payload)
        calls = self.turns.pop(0) if self.turns else [("finish", {"what": "done", "unsure": []})]
        output = [{"type": "function_call", "call_id": f"c{len(self.payloads)}-{k}", "name": name, "arguments": json.dumps(args)} for k, (name, args) in enumerate(calls)]
        return {"status": "completed", "output": output, "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}


@pytest.fixture
def sandbox(monkeypatch):
    """Records what ran, and fails a step that says so."""
    runs = []

    def run(steps, views, export=False, relations=None, measure=False):
        runs.append(list(steps))
        error = next((f"failed: {s}" for s in steps if "boom" in s), None)
        parts = [{"name": f"part {k}", "part": f"part {k}", "group": "g", "material": "concrete", "from": [0, 0, 0], "to": [1, 1, 1], "triangles": 12, "step": k + 1} for k in range(len(steps))]
        picture = Image.new("RGB", (40, 30), (200 - len(steps),) * 3)
        picture.putpixel((0, 0), tuple(hashlib.sha256(json.dumps(steps).encode()).digest()[:3]))
        out = io.BytesIO()
        picture.save(out, "PNG")
        found = {"scene.json": json.dumps({"error": error, "seconds": 0.1, "parts": parts}).encode(), **{f"{v['view'] if isinstance(v, dict) else v}.jpg": out.getvalue() for v in views}}
        if export:
            found["model.glb"] = b"glb"
        return found
    monkeypatch.setattr(loop, "sandbox", run)
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


def no_export(monkeypatch):
    real = loop.sandbox
    monkeypatch.setattr(loop, "sandbox", lambda steps, views, export=False, relations=None, measure=False: {k: v for k, v in real(steps, views, export).items() if k != "model.glb"})
    return real


def test_undo_with_no_export_clears_the_old_model_and_says_so(sandbox, monkeypatch):
    made = studio.start("d.png", png())
    work(Muse([STEP], [("run_python", {"intent": "the wall", "code": "W = 100"})], [("run_python", {"intent": "the roof", "code": "R = 1"})]))
    real = no_export(monkeypatch)
    held = studio.undo(made["id"])
    assert held["model_artifact_id"] is None and held["state"] == "failed" and held["error"] == studio.NO_MODEL
    assert held["intents"] == ["the slab", "the wall"]
    monkeypatch.setattr(loop, "sandbox", real)
    held = studio.undo(made["id"])
    assert held["model_artifact_id"] and held["state"] == "ready" and held["error"] is None


def test_a_build_with_no_export_keeps_its_work_and_can_be_retried(sandbox, monkeypatch):
    real = no_export(monkeypatch)
    made = studio.start("d.png", png())
    work(Muse([STEP]))
    held = studio.get(made["id"])
    assert held["model_artifact_id"] is None and held["state"] == "failed" and held["error"] == studio.NO_MODEL
    assert held["intents"] == ["the slab"] and held["tokens"] > 0
    monkeypatch.setattr(loop, "sandbox", real)
    studio.feedback(made["id"], "try again")
    work(Muse([("run_python", {"intent": "the wall", "code": "W = 100"})]))
    held = studio.get(made["id"])
    assert held["model_artifact_id"] and held["state"] == "ready" and held["error"] is None


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


def test_unknown_scenes(sandbox):
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
    assert held["state"] == "ready" and len(held["checks"]["rounds"]) == loop.REFUSALS + 1
    assert any("a wall" in u for u in held["unsure"])


def test_the_checker_sees_every_side_and_none_of_the_builders_words(sandbox):
    finish = ("finish", {"what": "SECRET", "unsure": []})
    muse = Muse([("run_python", {"intent": "SECRET", "code": "HIDDEN = 1"})], [finish], items=["a wall"])
    studio.start("d.png", png())
    work(muse)
    seen = forms(muse, "studio_check")[-1]["input"][0]["content"]
    assert sum(part["type"] == "input_image" for part in seen) == 10  # source, five sides, four independent cut-face close-ups
    assert "front detail top left" in seen[0]["text"]
    assert forms(muse, "studio_contract") and forms(muse, "studio_match")
    assert "SECRET" not in json.dumps(muse.checked) and "HIDDEN" not in json.dumps(muse.checked)


def test_an_unanswered_item_is_not_passed_as_seen():
    from builder import checker
    features = {"items": [{"id": 1, "what": "a wall", "importance": "must"}, {"id": 2, "what": "a nail", "importance": "should"}]}
    done = checker.verdict(features, {"ticks": [{"id": 2, "state": "missing", "note": "x"}], "not_in_source": []})
    assert not done["passed"] and done["right"] == 0 and done["rows"][0]["state"] == "cant_tell"
    done = checker.verdict(features, {"ticks": [{"id": 1, "state": "wrong", "note": "x"}], "not_in_source": []})
    assert not done["passed"]


@pytest.mark.parametrize("state", ["wrong", "missing", "cant_tell"])
def test_small_source_details_must_be_checked_too(state):
    from builder import checker
    features = {"items": [{"id": 0, "what": "a curved bend", "importance": "should"}]}
    result = checker.verdict(features, {"ticks": [{"id": 0, "state": state, "note": "inspect the bend"}], "not_in_source": []})
    assert not result["passed"] and "a curved bend" in checker.fix_list(result)


def test_an_invented_part_blocks_finishing_and_reaches_the_repair_list():
    from builder import checker
    result = checker.verdict({"items": []}, {"ticks": [], "not_in_source": ["an extra wall"]})
    assert not result["passed"] and "an extra wall" in checker.fix_list(result)


def ticks(*states):
    return {"ticks": [{"id": k, "state": state, "note": "" if state == "right" else "repair this part"} for k, state in enumerate(states)], "not_in_source": []}


FINISH = ("finish", {"what": "a slab", "unsure": []})


def test_a_worse_repair_is_removed_before_the_next_attempt(sandbox):
    muse = Muse([step(1)], [FINISH], [step(2)], [FINISH], [step(3)], [FINISH],
                items=["slab", "bend"], ticks=[ticks("right", "missing"), ticks("wrong", "missing"), ticks("right", "right")])
    made = studio.start("d.png", png())
    work(muse)
    row = repository().get(d.studio_scenes, made["id"])
    assert row["steps"] == ["PART_1 = 1", "PART_3 = 3"]
    assert row["intents"] == ["part 1", "part 3"] and len(row["parts"]) == 2
    assert [r["right"] for r in row["checks"]["rounds"]] == [1, 0, 2]
    told = [json.loads(i["output"]) for i in muse.payloads[4]["input"] if i.get("type") == "function_call_output"]
    restored = next(r for r in told if r.get("restored"))
    assert restored["steps_kept"] == 1 and restored["intents_kept"] == ["part 1"]
    assert "bend" in restored["checker_says"] and "slab" not in restored["checker_says"]


def test_the_saved_report_and_preview_belong_to_the_restored_model(sandbox, monkeypatch):
    monkeypatch.setattr(loop, "REFUSALS", 1)
    muse = Muse([step(1)], [FINISH], [step(2)], [FINISH], items=["slab", "bend"],
                ticks=[ticks("right", "missing"), ticks("wrong", "missing")])
    made = studio.start("d.png", png())
    work(muse)
    row = repository().get(d.studio_scenes, made["id"])
    assert row["steps"] == ["PART_1 = 1"] and len(row["parts"]) == 1
    assert [r["right"] for r in row["checks"]["rounds"]] == [1, 0]
    assert row["checks"]["last"]["right"] == 1 and not row["checks"]["last"]["passed"]
    preview = Image.open(io.BytesIO(studio.file_of(made["id"], "preview")[0]))
    assert preview.getpixel((1, 1)) == (199, 199, 199)
    assert any("bend" in u for u in row["unsure"]) and not any("slab:" in u for u in row["unsure"])


def test_a_budget_stop_discards_an_unchecked_repair(sandbox):
    from app.repository import BudgetExceeded

    class Stops(Muse):
        def __call__(self, payload):
            if len(self.payloads) >= 3:
                raise BudgetExceeded("tokens budget exhausted")
            return super().__call__(payload)

    muse = Stops([step(1)], [FINISH], [step(2)], items=["slab", "bend"], ticks=[ticks("right", "missing")])
    made = studio.start("d.png", png())
    work(muse)
    row = repository().get(d.studio_scenes, made["id"])
    assert row["state"] == "ready" and row["steps"] == ["PART_1 = 1"]
    assert row["checks"]["last"]["right"] == 1 and any("budget" in u for u in row["unsure"])


def test_an_unchecked_revision_cannot_reuse_the_previous_pass(sandbox):
    from app.repository import BudgetExceeded

    class Stops(Muse):
        def __call__(self, payload):
            if self.payloads:
                raise BudgetExceeded("tokens budget exhausted")
            return super().__call__(payload)

    ident = built()
    assert studio.get(ident)["checks"]["last"]["passed"]
    studio.feedback(ident, "change the shape")
    work(Stops([step(2)]))
    row = studio.get(ident)
    assert row["state"] == "ready" and row["step_count"] == 2
    assert row["checks"]["last"] is None and any("not been checked" in u for u in row["unsure"])


def test_undo_invalidates_the_old_geometry_check(sandbox):
    made = studio.start("d.png", png())
    work(Muse([step(1)], [step(2)], items=["slab"]))
    assert studio.get(made["id"])["checks"]["last"]["passed"]
    held = studio.undo(made["id"])
    assert held["checks"]["last"] is None and held["checks"]["rounds"] == []


def test_a_retry_replays_the_restoration_and_then_repairs_from_it(sandbox):
    muse = Dropped([step(1)], [FINISH], [step(2)], [FINISH], [step(3)], [FINISH], drop_on=5,
                   items=["slab", "bend"], ticks=[ticks("right", "missing"), ticks("wrong", "missing"), ticks("right", "right")])
    made = studio.start("d.png", png())
    work(muse)
    assert retry_now(made["id"])["state"] == "queued"
    work(muse)
    row = repository().get(d.studio_scenes, made["id"])
    assert row["state"] == "ready" and row["steps"] == ["PART_1 = 1", "PART_3 = 3"]
    assert row["checks"]["last"]["passed"]


def test_code_after_finish_in_the_same_response_cannot_change_the_checked_model(sandbox):
    made = studio.start("d.png", png())
    work(Muse([step(1)], [FINISH, step(2)], items=["slab"]))
    assert repository().get(d.studio_scenes, made["id"])["steps"] == ["PART_1 = 1"]


def test_source_inspection_crops_the_original_without_running_blender(sandbox):
    source = Image.new("RGB", (2400, 1200), "white")
    source.paste((255, 0, 0), (1200, 0, 2400, 1200))
    out = io.BytesIO()
    source.save(out, "PNG")
    seen = []

    def show(data, longest=1000):
        seen.append(Image.open(io.BytesIO(data)))
        return {"type": "input_image", "image_url": "test"}

    live = loop.Live(out.getvalue(), None, show=show)
    result, images = live.inspect_source({"region": [0.5, 0, 1, 1]})
    assert "error" not in result and len(images) == 2 and not sandbox
    assert seen[0].size == (1200, 1200) and seen[0].getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.parametrize("region", [None, [], [0, 0, 1], [0, 0, 1, 2], [1, 0, 0, 1], [0, 0, 0, 1], [0, 0, float("nan"), 1]])
def test_source_inspection_rejects_invalid_regions(region):
    result, images = loop.Live(png(), None).inspect_source({"region": region})
    assert "error" in result and not images


def test_close_up_views_keep_the_scene_unchanged(sandbox, monkeypatch):
    real, requests = loop.sandbox, []

    def record(steps, views, **kw):
        requests.append(views)
        return real(steps, views, **kw)

    monkeypatch.setattr(loop, "sandbox", record)
    live = loop.Live(png(), None, steps=["A = 1"])
    result, images = live.look({"views": ["section"], "parts": ["part 0"]})
    assert requests == [[{"view": "section", "parts": ["part 0"]}]]
    assert result["parts"] == ["part 0"] and len(images) == 2 and live.steps == ["A = 1"]
    result, images = live.look({"views": ["section"], "parts": ["unknown"]})
    assert "error" in result and not images


def test_running_out_of_turns_still_ends_with_a_check(sandbox, monkeypatch):
    monkeypatch.setattr(loop, "TURNS", 2)
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


def built(sandbox_runs=None, picture=None):
    made = studio.start("d.png", picture or png())
    work(Muse([STEP], items=["a slab"]))
    return made["id"]


def test_a_new_name_stays_through_a_revision(sandbox):
    ident = built()
    assert studio.rename(ident, "  My detail  ")["title"] == "My detail"
    studio.feedback(ident, "add a wall")
    work(Muse([("run_python", {"intent": "the wall", "code": "W = 100"})], items=["a slab"]))
    assert studio.get(ident)["title"] == "My detail"


def test_a_first_build_still_names_itself(sandbox):
    assert studio.get(built())["title"] != studio.NEW_TITLE


def test_a_scene_needs_a_name(sandbox):
    with pytest.raises(ValueError):
        studio.rename(built(), "   ")


def test_a_deleted_scene_leaves_nothing_behind(sandbox):
    repo = repository()
    ident = built(picture=png(11))
    row = repo.get(d.studio_scenes, ident)
    jobs = [j["id"] for j in repo.list(d.jobs) if j["payload"].get("scene_id") == ident]
    run = repo.run(jobs[0])["id"]
    assert repo.list(d.steps, d.steps.c.run_id == run)
    studio.delete(ident)
    with pytest.raises(KeyError):
        studio.get(ident)
    assert not repo.list(d.jobs, d.jobs.c.id.in_(jobs)) and not repo.list(d.events, d.events.c.job_id.in_(jobs))
    assert not repo.list(d.steps, d.steps.c.run_id == run) and not repo.list(d.runs, d.runs.c.id == run)
    assert not repo.list(d.artifacts, d.artifacts.c.id == row["drawing_artifact_id"])
    assert not (repo.artifacts.root / row["drawing_artifact_id"]).exists()


def test_deleting_a_scene_keeps_the_files_another_scene_uses(sandbox):
    repo = repository()
    first, second = built(picture=png(22)), built(picture=png(22))
    shared = repo.get(d.studio_scenes, first)["drawing_artifact_id"]
    assert repo.get(d.studio_scenes, second)["drawing_artifact_id"] == shared
    studio.delete(first)
    assert studio.file_of(second, "drawing")[0] == png(22) and studio.file_of(second, "model.glb")[0] == b"glb"
    studio.delete(second)
    assert not (repo.artifacts.root / shared).exists()


def test_a_scene_being_built_cannot_be_deleted(sandbox):
    made = studio.start("d.png", png(33))
    with pytest.raises(studio.Busy):
        studio.delete(made["id"])
    assert studio.get(made["id"])["state"] == "working"
    work(Muse([STEP]))


def test_a_scene_through_the_routes(sandbox):
    client = TestClient(app)
    ident = built(picture=png(44))
    assert client.patch(f"/api/studio/{ident}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert client.patch(f"/api/studio/{ident}", json={"title": " "}).status_code == 422
    assert client.delete(f"/api/studio/{ident}").status_code == 204
    assert client.get(f"/api/studio/{ident}").status_code == 404
    assert client.delete(f"/api/studio/{ident}").status_code == 404
    assert client.patch("/api/studio/none", json={"title": "x"}).status_code == 404


class Dropped(Muse):
    """The connection drops once, on the turn it's told to."""
    def __init__(self, *turns, drop_on=None, **kw):
        super().__init__(*turns, **kw)
        self.drop_on = drop_on

    def __call__(self, payload):
        if not payload.get("text") and len(self.payloads) + 1 == self.drop_on:
            self.drop_on = None
            raise ConnectionError("dropped")
        return super().__call__(payload)


def step(n):
    return ("run_python", {"intent": f"part {n}", "code": f"PART_{n} = {n}"})


def retry_now(ident):
    repo = repository()
    job = repo.get(d.studio_scenes, ident)["job_id"]
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job).values(available_at=0))
    return repo.get(d.jobs, job)


def test_a_retry_rebuilds_the_scene_instead_of_adding_to_it(sandbox):
    scene = studio.start("d.png", png())
    muse = Dropped([step(1)], [step(2)], [step(3)], items=["a slab"], drop_on=3)
    work(muse)
    assert retry_now(scene["id"])["state"] == "queued"
    work(muse)
    repo = repository()
    row = repo.get(d.studio_scenes, scene["id"])
    job = repo.get(d.jobs, row["job_id"])
    assert (job["state"], job["attempts"], row["state"]) == ("succeeded", 2, "ready")
    assert row["steps"] == ["PART_1 = 1", "PART_2 = 2", "PART_3 = 3"]
    assert row["intents"] == ["part 1", "part 2", "part 3"]
    assert len(row["parts"]) == 3


def test_a_retry_does_not_pay_twice_or_repeat_the_feed(sandbox):
    scene = studio.start("d.png", png())
    muse = Dropped([step(1)], [step(2)], [step(3)], items=["a slab"], drop_on=3)
    work(muse)
    retry_now(scene["id"])
    work(muse)
    repo = repository()
    row = repo.get(d.studio_scenes, scene["id"])
    assert len(muse.payloads) == 4  # three steps and finish: the two turns before the drop came from checkpoints
    assert row["tokens"] == repo.run(row["job_id"])["consumed"]["tokens"] + repo.run(row["checks"]["measure_job"])["consumed"]["tokens"]
    said = [f["text"] for f in row["feed"] if f["kind"] == "step"]
    assert said == ["part 1", "part 2", "part 3"]


def test_a_retried_revision_keeps_the_scene_it_started_from(sandbox):
    scene = studio.start("d.png", png())
    work(Muse([step(1)], [step(2)]))
    studio.feedback(scene["id"], "add more")
    muse = Dropped([step(3)], [step(4)], drop_on=2)
    work(muse)
    retry_now(scene["id"])
    work(muse)
    row = repository().get(d.studio_scenes, scene["id"])
    assert row["steps"] == ["PART_1 = 1", "PART_2 = 2", "PART_3 = 3", "PART_4 = 4"]


def test_a_busy_provider_leaves_the_scene_working_until_the_retry(sandbox):
    import urllib.error

    class Busy(Muse):
        busy = True

        def __call__(self, payload):
            if self.busy and not payload.get("text") and len(self.payloads) == 1:
                self.busy = False
                raise urllib.error.HTTPError("https://model", 503, "busy", {}, None)
            return super().__call__(payload)
    scene = studio.start("d.png", png())
    muse = Busy([step(1)], [step(2)])
    work(muse)
    assert studio.get(scene["id"])["state"] == "working"
    with pytest.raises(studio.Busy):
        studio.delete(scene["id"])
    retry_now(scene["id"])
    work(muse)
    held = studio.get(scene["id"])
    assert held["state"] == "ready" and held["step_count"] == 2


def test_the_feed_never_shows_a_tools_raw_output(sandbox):
    scene = studio.start("d.png", png())
    work(Muse([step(1)], [("list_parts", {})]))
    row = repository().get(d.studio_scenes, scene["id"])
    assert not [f for f in row["feed"] if "->" in f["text"] or "list_parts" in f["text"]]
    assert {"kind": "tool", "text": "Measuring the parts"} in row["feed"]


TWO_STEPS = ([STEP], [("run_python", {"intent": "the wall", "code": "W = 100"})])
ON = {"a": 2, "relation": "on", "b": 1, "confidence": 0.9}


def measuring(monkeypatch, measured):
    """The sandbox answers with this measurement, and records what it was asked to measure."""
    real, asked = loop.sandbox, []

    def run(steps, views, export=False, relations=None, measure=False):
        found = real(steps, views, export, relations)
        scene = json.loads(found["scene.json"])
        asked.append(relations)
        if not measure:
            return found
        found["scene.json"] = json.dumps({**scene, "measured": {**measured, "relations": [False] * len(relations["checks"]) if relations and measured.get("fail") else [True] * len(relations["checks"]) if relations else None}}).encode()
        return found
    monkeypatch.setattr(loop, "sandbox", run)
    return asked


def forms(muse, name):
    return [p for p in muse.checked if p["text"]["format"]["name"] == name]


def test_the_contract_is_read_once_and_a_revision_keeps_it(sandbox):
    muse = Muse(*TWO_STEPS, items=["a"], ticks=["missing", "right"], relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    assert len(forms(muse, "studio_contract")) == 1
    first = studio.get(made["id"])["checks"]["features"]["contract"]
    again = Muse([("run_python", {"intent": "more", "code": "M = 1"})], items=["a"])
    studio.feedback(made["id"], "add a wall")
    work(again)
    assert not forms(again, "studio_contract") and studio.get(made["id"])["checks"]["features"]["contract"] == first


def test_a_scene_from_before_contracts_gets_one_on_its_next_revision(sandbox):
    muse = Muse(*TWO_STEPS, items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    repo = repository()
    checks = repo.get(d.studio_scenes, made["id"])["checks"]
    del checks["features"]["contract"]
    studio._set(repo, made["id"], checks=checks)
    again = Muse(items=["a"], relations=[ON])
    studio.feedback(made["id"], "tidy it")
    work(again)
    assert len(forms(again, "studio_contract")) == 1


def test_matching_never_sees_the_builders_names(sandbox, monkeypatch):
    real = loop.sandbox

    def named(steps, views, export=False, relations=None, measure=False):
        found = real(steps, views, export, relations)
        scene = json.loads(found["scene.json"])
        for row in scene["parts"]:
            row["name"], row["material"] = f"builders word {row['name']}", "builders material"
        found["scene.json"] = json.dumps(scene).encode()
        return found
    monkeypatch.setattr(loop, "sandbox", named)
    muse = Muse(*TWO_STEPS, items=["a"], relations=[ON])
    studio.start("d.png", png())
    work(muse)
    sent = forms(muse, "studio_match")[0]["input"][0]["content"][0]["text"]
    assert "builders" not in sent and '"number": 0' in sent


def test_a_build_is_matched_once_however_many_rounds_it_took(sandbox):
    finish = [("finish", {"what": "a", "unsure": []})]
    muse = Muse(*TWO_STEPS, finish, finish, [("run_python", {"intent": "more", "code": "M = 1"})], items=["a"], ticks=["missing"] * 5, relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    assert len(studio.get(made["id"])["checks"]["rounds"]) >= 3 and len(forms(muse, "studio_match")) == 1


def test_a_scene_is_ready_before_it_is_measured(sandbox):
    muse = Muse([STEP], items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    worker = Worker(repository(), responder=muse)
    worker.run_once()
    held = studio.get(made["id"])
    assert held["state"] == "ready" and held["measuring"] and not forms(muse, "studio_contract") and not forms(muse, "studio_match")
    worker.run_once()
    held = studio.get(made["id"])
    assert not held["measuring"] and "measured" in held["checks"] and len(forms(muse, "studio_contract")) == 1


def test_a_measuring_job_that_fails_leaves_the_scene_as_it_was(sandbox, monkeypatch):
    muse = Muse([STEP], items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    worker = Worker(repository(), responder=muse)
    worker.run_once()
    ready = studio.get(made["id"])

    def broken(*_):
        raise ValueError("studio_match reached the provider output limit")
    monkeypatch.setattr(studio, "measure", broken)
    monkeypatch.setitem(studio.JOBS, "studio_measure", broken)
    worker.run_once()
    held = studio.get(made["id"])
    assert held["state"] == "ready" and not held["measuring"] and held["model_artifact_id"] == ready["model_artifact_id"] and held["checks"]["last"]["passed"]


def test_measuring_an_old_model_is_dropped(sandbox):
    muse = Muse([STEP], items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    worker = Worker(repository(), responder=muse)
    worker.run_once()
    studio._set(repository(), made["id"], model_artifact_id=studio._keep(repository(), b"newer", "model/gltf-binary"))
    worker.run_once()
    assert "measured" not in studio.get(made["id"])["checks"] and not forms(muse, "studio_match")


def test_an_undo_measures_the_model_it_leaves(sandbox):
    muse = Muse(*TWO_STEPS, items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    held = studio.undo(made["id"])
    assert held["measuring"] and "measured" not in held["checks"]
    work(muse)
    assert "measured" in studio.get(made["id"])["checks"]


def test_a_failed_step_costs_nothing_to_measure_when_there_is_no_model(sandbox, monkeypatch):
    no_export(monkeypatch)
    muse = Muse([STEP], items=["a"])
    made = studio.start("d.png", png())
    work(muse)
    assert not studio.get(made["id"])["measuring"] and not forms(muse, "studio_match")


def test_relations_decide_nothing_about_the_verdict(sandbox, monkeypatch):
    measuring(monkeypatch, {"fail": True, "loose": [], "overlaps": []})
    muse = Muse(*TWO_STEPS, items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    last = studio.get(made["id"])["checks"]["last"]
    assert last["passed"] and studio.get(made["id"])["checks"]["measured"]["rows"][0]["state"] == "fails"


def outputs(muse):
    return [i["output"] for p in muse.payloads for i in p["input"] if i.get("type") == "function_call_output"]


def test_the_builder_never_hears_what_measuring_found(sandbox, monkeypatch):
    """Told a passing model had faults, Muse rebuilt it and the checker then failed it: 4 of 14 failed relations were misreadings. The person decides."""
    measuring(monkeypatch, {"fail": True, "loose": [["grate"]], "overlaps": [{"a": "kerb", "b": "screed", "mm3": 9, "share": 0.29}]})
    muse = Muse([STEP], [("list_parts", {})], [("finish", {"what": "a", "unsure": []})], items=["a"], ticks=["missing", "right"], relations=[ON])
    studio.start("d.png", png())
    work(muse)
    assert not any(word in said for said in outputs(muse) for word in ("grate", "kerb", "measured", "a wall"))


def test_a_scene_that_passes_is_not_sent_back_by_measuring(sandbox, monkeypatch):
    measuring(monkeypatch, {"fail": True, "loose": [["grate"]], "overlaps": []})
    muse = Muse([STEP], items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    held = studio.get(made["id"])
    assert len(held["checks"]["rounds"]) == 1 and held["checks"]["measured"]["loose"] == [["grate"]]


def test_a_failed_measurement_costs_the_build_nothing(sandbox, monkeypatch):
    measuring(monkeypatch, {"error": "no solid"})
    muse = Muse(*TWO_STEPS, items=["a"], relations=[ON])
    made = studio.start("d.png", png())
    work(muse)
    held = studio.get(made["id"])
    assert held["state"] == "ready" and held["checks"]["last"]["passed"] and held["checks"]["measured"] == {}


def test_a_person_can_strike_out_a_misread_relation(sandbox, monkeypatch):
    asked = measuring(monkeypatch, {"loose": [], "overlaps": []})
    wall = {"a": 2, "relation": "against", "b": 1, "confidence": 0.9}
    muse = Muse(*TWO_STEPS, items=["a"], relations=[ON, wall])
    made = studio.start("d.png", png())
    work(muse)
    client = TestClient(app)
    held = client.post(f"/api/studio/{made['id']}/relations/0", json={"struck": True}).json()
    assert held["checks"]["measured"]["rows"][0]["struck"] is True
    studio.feedback(made["id"], "again")
    work(Muse([("run_python", {"intent": "more", "code": "M = 1"})], items=["a"]))
    assert [c["relation"] for c in [a for a in asked if a][-1]["checks"]] == ["against"]
    assert client.post(f"/api/studio/{made['id']}/relations/9", json={"struck": True}).status_code == 404


def test_only_sure_relations_are_held_against_a_model():
    from builder import checker
    contract = {"parts": [{"id": 1, "what": "a slab"}, {"id": 2, "what": "a wall"}], "relations": [{"a": 2, "relation": "on", "b": 1, "confidence": 0.5}, {"a": 2, "relation": "against", "b": 1, "confidence": checker.SURE}]}
    assert [c["relation"] for c in checker.relations(contract, {"1": ["s"], "2": ["w"]})["checks"]] == ["against"]


def test_an_overlap_the_contract_expects_is_not_a_fault():
    from builder import checker
    contract = {"parts": [{"id": 1, "what": "a slab"}, {"id": 2, "what": "a pipe"}, {"id": 3, "what": "a grate"}], "relations": [{"a": 2, "relation": "through", "b": 1, "confidence": 0.9}]}
    overlap = lambda a, b: {"a": a, "b": b, "mm3": 9, "share": 0.5}  # noqa: E731
    found = checker.reckon(contract, {"1": ["slab"], "2": ["pipe"], "3": ["rim", "bar"]}, {"relations": [True], "overlaps": [overlap("slab", "pipe"), overlap("rim", "bar"), overlap("slab", "kerb")], "loose": []})
    assert found["overlaps"] == [overlap("slab", "kerb")]


class Dropping:
    """Modal, dropping the first calls the way it does when it moves a container."""
    def __init__(self, drops, message="Input aborted due to internal failure (e.g. preemption). Retries are disabled"):
        import modal
        self.drops, self.calls, self.error = drops, 0, modal.exception.RemoteError(message)

    def remote_gen(self, *_):
        self.calls += 1
        if self.calls <= self.drops:
            raise self.error
        yield "scene.json", json.dumps({"error": None, "seconds": 0.1, "parts": []}).encode()


def test_a_dropped_sandbox_call_is_made_again(monkeypatch):
    import modal
    held = Dropping(2)
    monkeypatch.setattr(modal.Function, "from_name", lambda *_: held)
    monkeypatch.setattr(loop.time, "sleep", lambda _: None)
    assert json.loads(loop.sandbox(["A = 1"], ())["scene.json"])["error"] is None and held.calls == 3


def test_a_sandbox_that_keeps_failing_lets_the_job_retry(monkeypatch):
    import modal
    held = Dropping(9)
    monkeypatch.setattr(modal.Function, "from_name", lambda *_: held)
    monkeypatch.setattr(loop.time, "sleep", lambda _: None)
    with pytest.raises(ConnectionError, match="kept failing"):
        loop.sandbox(["A = 1"], ())
    assert held.calls == loop.AGAIN + 1


def test_a_real_failure_in_the_sandbox_is_not_retried(monkeypatch):
    import modal
    held = Dropping(9, "ValueError: something in the function itself")
    monkeypatch.setattr(modal.Function, "from_name", lambda *_: held)
    with pytest.raises(modal.exception.RemoteError):
        loop.sandbox(["A = 1"], ())
    assert held.calls == 1


def test_a_model_that_forgot_its_reasoning_carries_on_without_it(sandbox, monkeypatch):
    class Forgetful(Muse):
        def __call__(self, payload):
            if not payload.get("text") and any(i.get("type") == "reasoning" for i in payload["input"]):
                import urllib.error
                raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":{"message":"Referenced reasoning item rs_1 was not found or has expired."}}'))
            answer = super().__call__(payload)
            if not payload.get("text"):
                answer["output"] = [{"type": "reasoning", "id": f"rs_{len(self.payloads)}", "summary": []}, *answer["output"]]
            return answer
    muse = Forgetful([STEP], [("run_python", {"intent": "the wall", "code": "W = 100"})], items=["a"])
    held = studio.start("d.png", png())
    work(muse)
    scene = studio.get(held["id"])
    assert scene["state"] == "ready" and scene["intents"] == ["the slab", "the wall"]
