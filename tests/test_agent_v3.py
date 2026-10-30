"""Full native-tool loop with scripted provider and real database checkpoints."""
import io
import json

from PIL import Image, ImageDraw
import pytest

from app import database as d
from app.agent_tools import Tools
from app.repository import Repository, sha
from app.worker import Worker

READ = {"drawing_type": "section", "rotation_deg": 0, "title": "Wall",
        "elements": [{"label_verbatim": "Wall", "kind": "label", "where": "center"}],
        "dimensions": [{"value_mm": 150, "quote_verbatim": "150mm", "applies_to": "wall height"}]}


def program():
    return {"version": "3.0", "title": "Wall", "kind": "section", "situation": ["Residential"],
            "params": [{"name": "height", "value": 150, "unit": "mm", "kind": "exact", "provenance": "drawing",
                        "source_quote": "150mm", "source_dimension_id": "d0"},
                       {"name": "width", "value": 100, "unit": "mm", "kind": "exact", "provenance": "assumption"},
                       {"name": "depth", "value": 20, "unit": "mm", "kind": "exact", "provenance": "assumption"}],
            "parts": [{"id": "wall", "type": "wall", "label": "Wall", "shape": "box", "role": "substrate",
                       "dims": [{"name": name, "expr": expression} for name, expression in
                                [("x0", "0"), ("x1", "width"), ("y0", "0"), ("y1", "height"), ("z0", "0"), ("z1", "depth")]],
                       "install_after": [], "features": [{"id": "height", "kind": "extent", "selector": {"axis": "y"},
                         "expected_expr": "height", "source_dimension_id": "d0"}]}], "relations": [], "unresolved": []}


@pytest.fixture
def source_repo(tmp_path):
    repo = Repository("sqlite:///" + str(tmp_path / "v3.db"), tmp_path / "artifacts")
    output = io.BytesIO()
    Image.new("RGB", (300, 300), "white").save(output, "PNG")
    crop = repo.artifacts.put(output.getvalue(), "image/png")
    pdf_output = io.BytesIO()
    Image.new("RGB", (300, 300), "white").save(pdf_output, "PDF")
    pdf_bytes = pdf_output.getvalue()
    pdf = repo.artifacts.put(pdf_bytes, "application/pdf")
    with d.transaction(repo.engine) as con:
        repo.artifact(con, crop)
        repo.artifact(con, pdf)
        con.execute(d.documents.insert().values(id="doc", sha256=pdf["sha256"], filename="own.pdf", pages=1, artifact_id=pdf["id"]))
        con.execute(d.figures.insert().values(id="figure", document_id="doc", crop_key="p0_wall", page=0,
                    width=300, height=300, rotation_deg=0, artifact_id=crop["id"]))
    return repo


def response_json(value):
    return {"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(value)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 10}}


class ScriptedModel:
    def __init__(self):
        self.agent_calls = 0

    def __call__(self, payload):
        if "text" in payload:
            purpose = payload["text"]["format"]["name"]
            if purpose == "read":
                return response_json(json.loads(json.dumps(READ)))
            if purpose == "critic":
                return response_json({"verdict": "pass", "issues": []})
            pytest.fail(f"unexpected nested call {purpose}")
        outputs = {item["call_id"]: json.loads(item["output"]) for item in payload["input"] if item.get("type") == "function_call_output"}
        i = self.agent_calls
        self.agent_calls += 1
        version = outputs.get("call-1", {}).get("version_id")
        calls = [("read_drawing", {}),
                 ("write_program", {"expected_version_id": None, "program": program()}),
                 ("build_and_verify", {"version_id": version}),
                 ("render", {"version_id": version}),
                 ("critic", {"version_id": version, "verification_id": outputs.get("call-2", {}).get("verification_id"),
                             "render_ids": [r["render_id"] for r in outputs.get("call-3", {}).get("renders", [])]}),
                 ("finish", {"version_id": version, "verification_id": outputs.get("call-2", {}).get("verification_id"),
                             "critic_verdict_id": outputs.get("call-4", {}).get("critic_verdict_id"),
                             "status": "done", "summary": "Measured wall", "open_issues": []})]
        if i >= len(calls):
            pytest.fail(f"agent did not complete: {outputs}")
        name, args = calls[i]
        return {"output": [{"type": "function_call", "name": name, "call_id": f"call-{i}", "arguments": json.dumps(args)}],
                "usage": {"input_tokens": 10, "output_tokens": 10}}


class FakeRenderer:
    def __init__(self, repo):
        self.repo = repo

    def render(self, version_id, manifest_sha):
        output = io.BytesIO()
        image = Image.new("RGB", (200, 200), "gray")
        ImageDraw.Draw(image).rectangle([40,30,160,170],fill="white",outline="black",width=3)
        image.save(output,"JPEG")
        return [{"version_id": version_id, "manifest_sha": manifest_sha, "profile_sha": sha("physical"),
                 "viewer_sha": sha("test-viewer"), "view": view, "tags": tags,
                 "artifact": self.repo.artifacts.put(output.getvalue(), "image/jpeg")}
                for view in ("iso", "front", "top") for tags in (False, True)]


def test_agent_completes_with_version_pinned_evidence(source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    worker = Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo))
    assert worker.run_once()
    result = repo.get(d.jobs, job["id"])
    assert result["state"] == "succeeded", result
    run = repo.run(job["id"])
    assert result["result"]["version_id"] == run["working_version_id"]
    assert len(repo.list(d.operations)) == 6
    assert len(repo.list(d.critics)) == 1
    assert all(s["state"] == "completed" for s in repo.list(d.steps))


def test_stale_critic_cannot_approve_a_successor(source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo)).run_once()
    original = repo.run(job["id"])["working_version_id"]
    version = repo.get(d.versions, original)
    revision = repo.enqueue("revise_detail", {"detail_id": version["detail_id"], "base_version_id": original})
    fence = repo.claim("revision-worker")
    tools = Tools(repo, fence)
    changed = program()
    changed["params"][1]["value"] = 120
    successor = tools.run("patch", "patch_program", {"expected_version_id": original, "program": changed})
    assert successor.get("version_id"), successor
    critic = repo.list(d.critics)[0]
    result = tools.run("finish", "finish", {"version_id": successor["version_id"], "status": "done", "summary": "",
                       "verification_id": critic["verification_id"], "critic_verdict_id": critic["id"], "open_issues": []})
    assert result["error"]["code"] == "not_ready"
    assert repo.get(d.jobs, revision["id"])["state"] == "running"


def test_bad_tool_json_is_feedback_and_does_not_kill_the_run(source_repo):
    calls = []
    def responder(payload):
        calls.append(payload)
        if len(calls) == 1:
            output = {"type":"function_call","name":"read_drawing","call_id":"bad","arguments":"not-json"}
        else:
            output = {"type":"function_call","name":"finish","call_id":"stop","arguments":json.dumps({
                "status":"needs_review","version_id":None,"verification_id":None,"critic_verdict_id":None,
                "summary":"Input needs review","open_issues":["source needs review"]})}
        return {"output":[output],"usage":{"input_tokens":10,"output_tokens":10}}
    job = source_repo.enqueue("generate_detail", {"figure_id":"figure"})
    Worker(source_repo, responder=responder).run_once()
    assert source_repo.get(d.jobs, job["id"])["state"] == "needs_review"
    bad = source_repo.list(d.operations, d.operations.c.call_id == "bad")[0]
    assert bad["result"]["error"]["code"] == "invalid_tool_input"


def test_tool_schema_has_no_dangling_nested_references(source_repo):
    job = source_repo.enqueue("test", {})
    fence = source_repo.claim("schema-test")
    definitions = Tools(source_repo, fence).definitions()
    assert "$ref" not in json.dumps(definitions)
    writer = next(tool for tool in definitions if tool["name"] == "write_program")
    from app.program_schema import program_schema
    fields = program_schema()["properties"]
    assert "brief" in writer["parameters"]["properties"]
    assert not {"source","detail_id","origin"} & fields.keys()


def test_program_schema_offers_the_ontology_and_gated_part_provenance():
    from app import ontology
    from app.program_schema import program_schema
    known = ontology.load()
    schema = program_schema()
    assembly = schema["properties"]["assembly_class"]["anyOf"]
    assert assembly[0]["enum"] == known.assembly_ids() and assembly[1] == {"type": "null"}
    part = schema["properties"]["parts"]["items"]["properties"]
    assert part["class_id"]["anyOf"][0]["enum"] == known.component_ids() and "door_jamb" in part["class_id"]["anyOf"][0]["enum"]
    assert part["provenance"] == {"type": "string", "enum": ["drawing", "inferred"]}  # never a reviewer's confirmation
    assert {"confidence", "inferred_by"} <= part.keys()
    revising = program_schema(part_provenance=("drawing", "inferred", "user_input"))
    assert revising["properties"]["parts"]["items"]["properties"]["provenance"]["enum"] == ["drawing", "inferred", "user_input"]
    from app.agent import INSTRUCTIONS
    assert "complete_assembly" in INSTRUCTIONS and "never a verified PASS" in INSTRUCTIONS


def test_serialized_programs_and_small_patches_are_verified_atomically(source_repo):
    from app.model_gateway import ModelGateway
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id":"figure"})
    fence = repo.claim("patch-test")
    tools = Tools(repo, fence, ModelGateway(repo, fence, ScriptedModel()))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program":json.dumps(json.dumps(program()))})
    assert written["verification_id"]
    original = repo.get(d.versions, written["version_id"])
    updated = tools.run("patch", "patch_program", {"edits":[
        {"op":"replace", "path":"/params/@width/value", "value":120}]})
    assert updated["verification_id"] != written["verification_id"]
    assert repo.get(d.versions, original["id"])["program"] == original["program"]
    assert repo.get(d.versions, updated["version_id"])["program"]["params"][1]["value"] == 120
    assert repo.get(d.verifications, updated["verification_id"])["version_id"] == updated["version_id"]
    refused = tools.run("identity", "patch_program", {"edits":[
        {"op":"replace", "path":"/source/sha256", "value":"fake"}]})
    assert refused["error"]["code"] == "invalid_tool_input"
    bad_index = tools.run("bad-index","patch_program",{"edits":[
        {"op":"replace","path":"/parts/99/label","value":"Wall"}]})
    assert bad_index["error"]["code"] == "invalid_tool_input"
    bad_part = tools.run("bad-part","model_component",{"version_id":updated["version_id"],"part_key":"missing","brief":"unused"})
    assert bad_part["error"]["code"] == "invalid_tool_input"
    assert repo.get(d.jobs,job["id"])["state"] == "running"


def test_structured_authoring_checkpoints_program_and_returns_editable_draft(source_repo):
    from app.model_gateway import ModelGateway
    from app.program_schema import program_schema
    raw = program()
    raw["parts"][0]["param_bindings"] = []
    def responder(payload):
        purpose = payload["text"]["format"]["name"]
        return response_json(READ if purpose == "read" else raw)
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id":"figure"})
    fence = repo.claim("author-test")
    tools = Tools(repo,fence,ModelGateway(repo,fence,responder))
    tools.run("read","read_drawing",{})
    result = tools.run("author","author_program",{"brief":"Model wall from its printed height"})
    assert result["program"]["parts"][0]["id"] == "wall"
    assert result["verification_id"]
    assert any(s["request"].get("purpose") == "program" for s in repo.list(d.steps) if s["kind"] == "model")
    assert "title" in program_schema()["properties"]


def test_corrupt_render_cannot_finish_or_publish(source_repo):
    from app import database as d
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    Worker(repo,responder=ScriptedModel(),renderer=FakeRenderer(repo)).run_once()
    verdict = repo.list(d.critics)[0]
    rendered = repo.get(d.renders,verdict["render_ids"][0])
    repo.artifacts.resolve(rendered["artifact_id"]).write_bytes(b"corrupt")
    with d.transaction(repo.engine) as con:
        errors = repo.completion_errors(con,verdict["version_id"],verdict["verification_id"],verdict["id"])
    assert "missing_corrupt_or_blank_evidence" in errors


def test_blank_render_is_an_error_before_it_can_be_critiqued(source_repo):
    from app.render import validate_image
    image = io.BytesIO()
    Image.new("RGB",(1280,960),"white").save(image,"JPEG")
    with pytest.raises(ValueError,match="blank"):
        validate_image(image.getvalue())


def test_budget_tail_is_spent_on_exact_version_render_and_critique(source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    Worker(repo,responder=ScriptedModel(),renderer=FakeRenderer(repo)).run_once()
    version = repo.get(d.versions,repo.run(job["id"])["working_version_id"])
    revised = repo.enqueue("revise_detail",{"detail_id":version["detail_id"],"base_version_id":version["id"]})
    with d.transaction(repo.engine) as con:
        con.execute(d.runs.update().where(d.runs.c.job_id == revised["id"]).values(consumed={"tokens":285000}))
    provider = ScriptedModel()
    Worker(repo,responder=provider,renderer=FakeRenderer(repo)).run_once()
    assert provider.agent_calls == 0
    final = repo.get(d.jobs,revised["id"])
    assert final["state"] == "succeeded",final
    assert final["result"]["version_id"] == version["id"]
    assert final["result"]["stop_reason"] == "budget_floor"
    assert repo.run(revised["id"])["consumed"]["tokens"] > 285000


def test_resume_replays_the_same_initial_request_after_a_draft_exists(source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail",{"figure_id":"figure"})
    model = ScriptedModel()
    requests = []
    def interrupted(payload):
        if "text" not in payload:
            requests.append(payload["input"][0])
            if model.agent_calls == 2:
                raise TimeoutError("simulated worker/provider interruption")
        return model(payload)
    Worker(repo,responder=interrupted,renderer=FakeRenderer(repo)).run_once()
    assert repo.run(job["id"])["working_version_id"]
    assert repo.get(d.jobs,job["id"])["state"] == "queued"
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(available_at=0))
    def resumed(payload):
        if "text" not in payload:requests.append(payload["input"][0])
        return model(payload)
    Worker(repo,responder=resumed,renderer=FakeRenderer(repo)).run_once()
    assert repo.get(d.jobs,job["id"])["state"] == "succeeded"
    assert all(request == requests[0] for request in requests)


def test_structured_object_is_found_after_narration():
    from app.model_gateway import structured
    narrated = {"output": [{"type": "reasoning"}] + [
        {"type": "message", "content": [{"type": "output_text", "text": "I'll start by reading the drawing."}]}] * 3 + [
        {"type": "message", "content": [{"type": "output_text", "text": '{"title": "Wall"}'}]}]}
    assert structured(narrated) == {"title": "Wall"}
    assert structured(response_json({"a": 1})) == {"a": 1}
    with pytest.raises(json.JSONDecodeError):
        structured({"output": [{"type": "message", "content": [{"type": "output_text", "text": "no object"}]}]})
