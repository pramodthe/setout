"""Held-out correction contract with a scripted provider, not a fidelity claim."""
import copy

from app import database as d
from app.agent_tools import Tools
from app.model_gateway import ModelGateway
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo, program, READ, response_json
from test_api_v3 import client


def test_accepted_correction_changes_held_out_attribution_without_copying_values(client,source_repo):
    repo = source_repo
    training = repo.enqueue("generate_detail",{"figure_id":"figure"})
    Worker(repo,responder=ScriptedModel(),renderer=FakeRenderer(repo)).run_once()
    version = repo.get(d.versions,repo.run(training["id"])["working_version_id"])
    corrected = copy.deepcopy(version["program"])
    corrected["params"][1]["value"] = 300
    lesson = "For wall width attribution, measure the x extent and apply the current drawing's printed value, never this example's value."
    review = client.post(f"/api/versions/{version['id']}/reviews",json={"reviewer":"Test reviewer",
        "action":"correct","data":{"program":corrected,"lesson":lesson}})
    assert review.status_code == 200,review.text
    fixed = repo.get(d.versions,review.json()["version_id"])
    assert fixed["program"]["params"][1]["provenance"] == "user_input"
    memories = repo.list(d.memories)
    assert {m["state"] for m in memories} == {"proposed"}
    memory = next(m for m in memories if m["text"] == lesson)
    derived = next(m for m in memories if m["text"] != lesson)
    # Without the reviewer's words the diff still teaches a concrete lesson.
    assert derived["scope"] == "type" and "width" in derived["text"] and "300" in derived["text"]
    assert "explicit assumption" in derived["text"]
    accepted = client.post(f"/api/versions/{fixed['id']}/reviews",json={"reviewer":"Test reviewer",
        "action":"activate_memory","data":{"memory_id":memory["id"]}})
    assert accepted.status_code == 200,accepted.text
    axes = {}
    for mode in ("off","on"):
        job = repo.enqueue("generate_detail",{"figure_id":"figure","memory_mode":mode})
        fence = repo.claim("held-out-"+mode,only_ids=[job["id"]])
        def responder(payload):
            if payload["text"]["format"]["name"] == "read":
                held_out = copy.deepcopy(READ)
                held_out["dimensions"] = [{"value_mm":75,"quote_verbatim":"75mm","applies_to":"wall width"}]
                return response_json(held_out)
            candidate = program()
            candidate["params"][0].update(value=75,source_quote="75mm")
            text = payload["input"][0]["content"][0]["text"]
            if lesson in text:
                candidate["parts"][0]["features"][0]["selector"]["axis"] = "x"
                for dim in candidate["parts"][0]["dims"]:
                    if dim["name"] == "x1": dim["expr"] = "height"
                    if dim["name"] == "y1": dim["expr"] = "width"
            return response_json(candidate)
        tools = Tools(repo,fence,ModelGateway(repo,fence,responder))
        tools.run("read","read_drawing",{})
        recalled = tools.run("recall","recall",{"query":"wall width"})
        brief = "Model the wall. " + " ".join(m["text"] for m in recalled["memories"])
        result = tools.run("write","write_program",{"brief":brief})
        assert result.get("version_id"),result
        candidate = repo.get(d.versions,result["version_id"])["program"]
        axes[mode] = candidate["parts"][0]["features"][0]["selector"]["axis"]
        source = next(p for p in candidate["params"] if p["provenance"] == "drawing")
        assert (source["value"],source["source_quote"]) == (75,"75mm")
        assert not any(p["value"] == 300 and p["provenance"] == "drawing" for p in candidate["params"])
        repo.end(fence,"needs_review",{"reason":"scripted held-out attribution test"})
    assert axes == {"off":"y","on":"x"}


def test_component_recall_is_bounded_and_finds_an_exact_revision(source_repo):
    from app.repository import sha
    repo = source_repo
    original_brief = "A named parametric fixture. " * 500
    with d.transaction(repo.engine) as con:
        for index in range(12):
            ident = str(index).zfill(32)
            con.execute(d.components.insert().values(id=ident,family_key="fixture",code="example",
                code_sha=sha("example"),param_schema={"w":{"unit":"mm"}},feature_schema=[],brief=original_brief,created_at=index))
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    fence = repo.claim("bounded-recall")
    tools = Tools(repo,fence)
    general = tools.run("general","recall",{"query":"fixture"})
    assert len(general["components"]) == 8
    assert all(len(item["brief"]) <= 600 for item in general["components"])
    ident = "0"*32
    exact = tools.run("exact","recall",{"query":"component "+ident+" parameter schema"})
    assert [item["id"] for item in exact["components"]] == [ident]
    assert repo.get(d.components,ident)["brief"] == original_brief


def test_repeated_failures_and_human_accepted_critic_repairs_propose_once(source_repo):
    from app.repository import uid
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    Worker(repo,responder=ScriptedModel(),renderer=FakeRenderer(repo)).run_once()
    parent = repo.get(d.versions,repo.run(job["id"])["working_version_id"])
    with d.transaction(repo.engine) as con:
        child = repo.insert_version(con,detail_id=parent["detail_id"],expected_parent=parent["id"],
            program=parent["program"],spec=parent["spec"],figure_id=parent["figure_id"],read_id=parent["drawing_read_id"])
        failure = {"id":"cad-shape","status":"FAIL","reason":"wrong bore"}
        # A previous version failed the same independently measured requirement.
        report = repo.list(d.verifications,d.verifications.c.version_id == parent["id"])[0]
        con.execute(d.verifications.insert().values(**{**report,"id":uid(),"checks":[failure],"created_at":1}))
        repo.note_repeated_failures(con,child["version_id"],[failure])
        repo.note_repeated_failures(con,child["version_id"],[failure])
        critic = repo.list(d.critics,d.critics.c.version_id == parent["id"])[0]
        con.execute(d.critics.insert().values(**{**critic,"id":uid(),"verdict":"fail",
            "issues":[{"id":"bore","reason":"bore absent"}],"created_at":10**12}))
        report_id = uid()
        con.execute(d.verifications.insert().values(**{**report,"id":report_id,"version_id":child["version_id"]}))
        con.execute(d.critics.insert().values(**{**critic,"id":uid(),"version_id":child["version_id"],
            "verification_id":report_id,"verdict":"pass","issues":[],"created_at":10**12}))
        repo.review_event(con,child["version_id"],"Test reviewer","accept",{})
        repo.review_event(con,child["version_id"],"Test reviewer","accept",{})
    memories = repo.list(d.memories)
    assert sorted(m["kind"] for m in memories) == ["failure","lesson"]
    assert all(m["state"] == "proposed" and m["source_version_id"] == child["version_id"] for m in memories)
    assert next(m for m in memories if m["kind"] == "lesson")["source_event_id"]


def test_correction_lessons_keep_printed_values_with_their_detail():
    from app.lessons import correction_lessons
    old = program()
    new = copy.deepcopy(old)
    new["params"][0]["value"] = 160
    new["params"][2]["value"] = 25
    new["parts"][0]["label"] = None
    lessons = {scope: (key, text, tags) for scope, key, text, tags in correction_lessons(old, new, "detail-1")}
    key, text, tags = lessons["detail"]
    assert key == "detail-1" and "printed value height from 150 to 160" in text and "re-read" in text
    assert "label None" in text and "height" in tags
    key, text, tags = lessons["type"]
    assert key == "section" and "depth" in text and "25 mm instead of 20 mm" in text
    assert "height" not in text
    assert correction_lessons(old, copy.deepcopy(old), "detail-1") == []
