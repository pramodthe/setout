"""A section to a planned assembly: read, confirm, build, keep and reuse. The model and the catalogue search are stubbed."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import assemblies, database as d, store
from app.main import app
from app.runtime import repository
from app.worker import Worker
from goal import engine, part_search


PLAN = {"assembly": "two blocks on a slab", "direction_of_run": "wall runs into the page", "unsure": ["which block"],
        "parts": [{"id": "slab", "name": "slab", "query": "slab", "category": "concrete", "form": "solid", "seen": "drawn", "regions": [], "path": [], "spacing_mm": 0, "reason": "big box"},
                  {"id": "unit", "name": "brick", "query": "brick", "category": "brick", "form": "units", "seen": "drawn", "regions": [], "path": [], "spacing_mm": 0, "reason": "small boxes"}],
        "relations": [{"subject": "unit", "relation": "sits_on", "target": "slab", "second_target": "", "confidence": 0.9}]}
HIT = {"id": 7, "category": "brick", "name": "brick", "size": [0.2, 0.1, 0.06], "copies": 50, "doubtful": False, "score": 0.7}


def sized(picture):
    from goal import regions
    return sorted(regions.with_ink(picture)[0], key=lambda r: -r.area)


class Muse:
    """Answers each kind of call, and remembers the prompts it was given."""
    def __init__(self, check_fails=False):
        self.prompts, self.check_fails = [], check_fails

    def __call__(self, payload):
        name = payload["text"]["format"]["name"]
        self.prompts.append(payload["input"][0]["content"][0]["text"])
        if name == "assembly_plan":
            answer = json.loads(json.dumps(PLAN))
            found = self.regions
            answer["parts"][0]["regions"] = [found[0].id]
            answer["parts"][1]["regions"] = [r.id for r in found[1:]]
        elif name == "assembly_check":
            if self.check_fails:
                return {"status": "incomplete", "output": [], "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}
            answer = {"parts": [], "relations": []}
        elif name == "assembly_learn":
            answer = {"changes": [{"id": "unit", "category": "block", "why": "same hatching"}, {"id": "ghost", "category": "tile", "why": "not a part"}]}
        else:
            answer = {"choices": [{"part": "unit", "component": 7, "bond": "stack", "spacing_mm": 0, "every": 1, "why": "the right size"}]}
        return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}], "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}


@pytest.fixture
def drawn(tmp_path, monkeypatch):
    image = Image.new("L", (600, 500), 255)
    pen = ImageDraw.Draw(image)
    pen.rectangle((40, 300, 560, 460), outline=0, width=3)
    for y in (156, 230):
        pen.rectangle((60, y, 260, y + 70), outline=0, width=3)
    path = tmp_path / "section.png"
    image.save(path)
    mesh = np.zeros((1, 3, 3))
    mesh[0] = [[0, 0, 0], [0.2, 0, 0], [0.2, 0.1, 0.06]]
    parts = tmp_path / "parts"
    parts.mkdir()
    np.save(parts / "7.npy", mesh)
    monkeypatch.setattr(engine, "STORE", tmp_path)
    monkeypatch.setattr(engine, "search", lambda queries, keep=8: [[dict(HIT)] for _ in queries])
    monkeypatch.setattr(part_search, "embed", lambda **_: {"texts": np.array([[1.0, 0.0]])})
    monkeypatch.setattr(assemblies, "figure_path", lambda *_: path)
    monkeypatch.setattr("goal.choose.sheet", lambda _: b"\x89PNG")
    monkeypatch.setattr(assemblies, "ensure_worker", lambda: None)
    return path


def work(muse, picture):
    from goal import plan
    muse.regions = sorted(plan.regions_of(str(picture))[0], key=lambda r: -r.area)
    repo = repository()
    while Worker(repo, responder=muse).run_once():
        pass


def test_nothing_is_built_until_a_person_confirms(drawn):
    muse = Muse()
    made = assemblies.start("doc", "crop")
    work(muse, drawn)
    held = assemblies.get(made["id"])
    assert held["state"] == "awaiting_confirmation" and held["result"] is None and held["scene_artifact_id"] is None
    assert held["choices"]["unit"]["component"] == 7 and held["tokens"] == 450
    with pytest.raises(KeyError):
        assemblies.scene(made["id"])


def test_a_confirmed_plan_is_built_with_what_muse_chose(drawn):
    muse = Muse()
    made = assemblies.start("doc", "crop")
    work(muse, drawn)
    assemblies.confirm(made["id"], "A reviewer", None, [])
    work(muse, drawn)
    held = assemblies.get(made["id"])
    assert held["state"] == "done" and held["confirmed_by"] == "A reviewer"
    assert held["result"]["broken_after"] == 0
    scene = json.loads(assemblies.scene(made["id"]))
    assert {s["id"] for s in scene["solids"]} >= {"slab", "unit"}
    assert all(s["provenance"] != "drawing" for s in scene["solids"]) and scene["verification"] == []


def test_a_correction_becomes_a_lesson_and_reaches_the_next_reading(drawn):
    muse = Muse()
    made = assemblies.start("doc", "crop")
    work(muse, drawn)
    assemblies.correct(made["id"], "A reviewer", "these are blocks, not bricks")
    work(muse, drawn)
    plans = [p for p in muse.prompts if "Plan how it is built" in p]
    assert "these are blocks, not bricks" in plans[-1] and "Believe them" in plans[-1]
    assert "these are blocks, not bricks" not in plans[0]
    other = assemblies.start("doc", "other")
    work(muse, drawn)
    assert assemblies.get(other["id"])["state"] == "awaiting_confirmation"
    plans = [p for p in muse.prompts if "Plan how it is built" in p]
    assert "Lessons from people" in plans[-1] and "Believe them" not in plans[-1]


def test_changing_a_part_chooses_it_again(drawn):
    muse = Muse()
    made = assemblies.start("doc", "crop")
    work(muse, drawn)
    before = sum("Choose one candidate" in p for p in muse.prompts)
    assemblies.confirm(made["id"], "A reviewer", "a brick wall on a slab", [{"id": "unit", "category": "block"}])
    work(muse, drawn)
    held = assemblies.get(made["id"])
    assert held["plan"]["assembly"] == "a brick wall on a slab" and held["state"] == "done"
    assert next(p for p in held["plan"]["parts"] if p["id"] == "unit")["query"] == "block"
    assert sum("Choose one candidate" in p for p in muse.prompts) == before + 1
    lessons = repository().list(d.memories, d.memories.c.scope == assemblies.LESSON_SCOPE)
    assert sum("a brick wall on a slab" in m["text"] for m in lessons) == 2


def test_only_a_good_detail_is_compared_and_it_can_only_change_a_part(drawn):
    muse = Muse()
    first = assemblies.start("doc", "crop")
    work(muse, drawn)
    assemblies.confirm(first["id"], "A reviewer", None, [])
    work(muse, drawn)
    assemblies.start("doc", "two")
    work(muse, drawn)
    assert not any("details a person confirmed" in p for p in muse.prompts)
    assemblies.judge(first["id"], "A reviewer", "good", "")
    third = assemblies.start("doc", "three")
    work(muse, drawn)
    held = assemblies.get(third["id"])
    plans = [p for p in muse.prompts if "Plan how it is built" in p]
    assert "a person confirmed" not in plans[-1]  # the reading stays Muse's own
    assert any("details a person confirmed" in p for p in muse.prompts)
    assert next(p for p in held["plan"]["parts"] if p["id"] == "unit")["category"] == "block"
    assert first["id"] in held["plan"]["compared_with"] and len(held["plan"]["learned"]) == 1
    assert "Choices a person confirmed" in [p for p in muse.prompts if "Choose one candidate" in p][-1]


def test_a_plan_survives_its_check_failing(drawn):
    muse = Muse(check_fails=True)
    made = assemblies.start("doc", "crop")
    work(muse, drawn)
    held = assemblies.get(made["id"])
    assert held["state"] == "awaiting_confirmation" and any("did not finish" in u for u in held["plan"]["unsure"])


def test_a_lesson_stays_out_of_the_verified_agent(drawn):
    muse = Muse()
    made = assemblies.start("doc", "crop")
    work(muse, drawn)
    assemblies.correct(made["id"], "A reviewer", "a note")
    lesson = repository().list(d.memories, d.memories.c.scope == assemblies.LESSON_SCOPE)[0]
    assert lesson["scope"] not in ("global", "detail", "type", "manufacturer")


def test_a_wrong_stage_is_refused(drawn):
    muse = Muse()
    made = assemblies.start("doc", "crop")
    with pytest.raises(assemblies.WrongState):
        assemblies.confirm(made["id"], "A reviewer", None, [])
    with pytest.raises(assemblies.WrongState):
        assemblies.judge(made["id"], "A reviewer", "good", "")
    work(muse, drawn)
    with pytest.raises(ValueError):
        assemblies.confirm(made["id"], " ", None, [])
    with pytest.raises(ValueError):
        assemblies.confirm(made["id"], "A reviewer", None, [{"id": "ghost"}])


def test_the_routes(drawn, monkeypatch):
    client = TestClient(app)
    assert client.get("/api/assemblies/none").status_code == 404
    assert client.post("/api/assemblies/none/confirm", json={"reviewer": "A"}).status_code == 404
    assert client.get("/api/catalogue/999999/thumb").status_code == 404
    assert "assemblies" in client.get("/api/assemblies").json()
