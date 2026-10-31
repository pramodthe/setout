"""Day 2: make schema, agent repairs, and the CLI. The model and Blender are faked."""
import json

import pytest

from app.main import GEN_JOBS
from app.make.agent import build_messages, current_values, generate
from app.make.cli import main
from app.make.db import connect, create_job, get_job, get_setting, put_setting
from app.store import db as store_db
from app.store import var_root

SECRET = "sk-day2secretvalue999"
SCRIPT = 'PARAMS = {"edge": 100, "upturn_h": 100}\n'
TREE = {
    "name": "FloorWasteJunction",
    "type": "collection",
    "children": [
        {"name": "Structure", "type": "collection", "children": [
            {"name": "Substrate", "type": "mesh"},
            {"name": "Screed", "type": "mesh"},
        ]},
        {"name": "Waterproofing", "type": "collection", "children": [
            {"name": "Membrane", "type": "mesh"},
            {"name": "Upturn", "type": "mesh"},
            {"name": "Stainless Angle", "type": "mesh"},
        ]},
        {"name": "Finishes", "type": "collection", "children": [
            {"name": "Tile", "type": "mesh"},
            {"name": "Sealant", "type": "mesh"},
        ]},
    ],
}


def _completion(script, attempt=1, via="tool"):
    return {
        "script": script,
        "summary": "floor waste",
        "via": via,
        "profile": "muse",
        "model": "muse-spark-1.3",
        "event": {
            "profile": "muse",
            "model": "muse-spark-1.3",
            "latency_s": 1.25,
            "tokens": {"prompt": 10, "completion": 20, "total": 30},
            "via": via,
            "attempt": attempt,
        },
    }


def _ok():
    return {
        "ok": True,
        "traceback": "",
        "stdout_tail": "",
        "warnings": [],
        "files": {
            "scene.glb": b"glTF-test",
            "tree.json": json.dumps(TREE).encode(),
            "preview.png": b"\x89PNG\r\n\x1a\n",
        },
    }


def _version(project_id, number):
    con = connect()
    try:
        row = con.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? AND n = ?",
            (project_id, number),
        ).fetchone()
    finally:
        con.close()
    return dict(row)


def _always(script, attempt_holder=None):
    def complete_fn(messages, profile=None, attempt=1):
        if attempt_holder is not None:
            attempt_holder.append(messages)
        return _completion(script, attempt)
    return complete_fn


def test_schema_is_wal_and_leaves_the_document_tables():
    store_db().close()
    con = connect()
    try:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"projects", "messages", "scene_versions", "jobs", "settings", "documents", "builds"} <= names
        columns = [row[1] for row in con.execute("PRAGMA table_info(scene_versions)")]
        for name in ("script", "params", "overrides", "glb_path", "tree_json", "preview_path",
                     "render_path", "profile", "model", "status", "parent_n"):
            assert name in columns
        job_columns = [row[1] for row in con.execute("PRAGMA table_info(jobs)")]
        for name in ("kind", "state", "attempt", "events"):
            assert name in job_columns
    finally:
        con.close()
    put_setting("active_profile", "muse")
    assert get_setting("active_profile") == "muse"
    create_job("job-rebuild", "missing-project", "rebuild")
    assert get_job("job-rebuild")["kind"] == "rebuild"


def test_gen_jobs_stays_in_memory_for_the_other_routes():
    assert isinstance(GEN_JOBS, dict)


def test_prompt_writes_version_one_and_logs_the_call(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    result = generate("a floor waste", complete_fn=_always(SCRIPT), runner=lambda *a, **k: _ok())
    assert result["status"] == "done"
    assert result["version"] == 1
    assert result["attempts"] == 1
    assert result["parts"] == 7
    folder = var_root() / "make" / result["project_id"] / "1"
    assert (folder / "scene.glb").read_bytes().startswith(b"glTF")
    assert (folder / "preview.png").is_file()
    assert json.loads((folder / "tree.json").read_text())["name"] == "FloorWasteJunction"
    job = get_job(result["job_id"])
    events = json.loads(job["events"])
    call = next(event for event in events if event.get("via") == "tool")
    assert call["profile"] == "muse"
    assert call["model"] == "muse-spark-1.3"
    assert call["latency_s"] == 1.25
    assert call["tokens"]["total"] == 30
    assert call["attempt"] == 1
    blob = json.dumps(events)
    assert SECRET not in blob
    assert "base_url" not in call
    version = _version(result["project_id"], 1)
    assert version["source"] == "prompt"
    assert version["status"] == "done"
    assert json.loads(version["params"])["upturn_h"] == 100
    assert SECRET not in (version["script"] or "")


def test_traceback_is_repaired_and_not_logged_with_the_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    seen = []
    ran = {"n": 0}

    def complete_fn(messages, profile=None, attempt=1):
        seen.append(messages[-1]["content"])
        return _completion(SCRIPT, attempt)

    def runner(script, overrides, timeout):
        ran["n"] += 1
        if ran["n"] == 1:
            return {"ok": False, "traceback": f"NameError: missing\nLLM_API_KEY={SECRET}",
                    "warnings": [], "files": {}, "stdout_tail": ""}
        return _ok()

    result = generate("a floor waste", complete_fn=complete_fn, runner=runner)
    assert result["status"] == "done"
    assert result["attempts"] == 2
    assert ran["n"] == 2
    assert "NameError" in seen[1]
    assert SECRET not in seen[1]
    assert SECRET not in json.dumps(json.loads(get_job(result["job_id"])["events"]))


def test_bbox_warning_is_sent_back_for_repair():
    ran = {"n": 0}
    seen = []

    def complete_fn(messages, profile=None, attempt=1):
        seen.append(messages[-1]["content"])
        return _completion(SCRIPT, attempt)

    def runner(script, overrides, timeout):
        ran["n"] += 1
        result = _ok()
        if ran["n"] == 1:
            result["warnings"] = ["Slab extends beyond 2000 mm of the origin"]
        return result

    result = generate("a slab", complete_fn=complete_fn, runner=runner)
    assert result["status"] == "done"
    assert result["attempts"] == 2
    assert "2 m" in seen[1]
    assert "extends beyond" in seen[1]


def test_ast_rejection_does_not_start_blender():
    ran = {"n": 0}

    def complete_fn(messages, profile=None, attempt=1):
        if attempt == 1:
            return _completion("import os\nprint(os.environ)", attempt)
        return _completion(SCRIPT, attempt)

    def runner(script, overrides, timeout):
        ran["n"] += 1
        assert "import os" not in script
        return _ok()

    result = generate("a box", complete_fn=complete_fn, runner=runner)
    assert result["status"] == "done"
    assert ran["n"] == 1
    assert result["attempts"] == 2


def test_feedback_bakes_overrides_and_writes_the_next_version():
    first = generate("a floor waste", complete_fn=_always(SCRIPT), runner=lambda *a, **k: _ok())
    with connect() as con:
        con.execute(
            "UPDATE scene_versions SET params = ?, overrides = ? WHERE project_id = ? AND n = 1",
            (json.dumps({"upturn_h": 100}),
             json.dumps({"Upturn": {"dims": {"upturn_h": 100}, "location": [0, 0, 52]}}),
             first["project_id"]),
        )
        con.commit()
    seen = []

    def complete_fn(messages, profile=None, attempt=1):
        seen.append(messages[-1]["content"])
        return _completion(SCRIPT.replace("100", "150"), attempt)

    second = generate(
        "make the upturn 150mm and add sealant bead",
        project_id=first["project_id"],
        mode="feedback",
        complete_fn=complete_fn,
        runner=lambda *a, **k: _ok(),
    )
    assert second["version"] == 2
    assert second["status"] == "done"
    body = seen[0]
    assert "Current Blender script" in body
    assert "tree.json" in body
    assert "upturn_h" in body
    assert "sealant bead" in body
    assert "150mm" in body
    row = _version(first["project_id"], 2)
    assert row["source"] == "feedback"
    assert row["parent_n"] == 1
    assert (var_root() / "make" / first["project_id"] / "1" / "preview.png").is_file()
    assert (var_root() / "make" / first["project_id"] / "2" / "preview.png").is_file()
    baked = current_values({"upturn_h": 100}, {"Upturn": {"dims": {"upturn_h": 150}}})
    assert baked["upturn_h"] == 150


def test_injected_error_is_repaired_within_three_attempts():
    ran = {"n": 0}

    def complete_fn(messages, profile=None, attempt=1):
        return _completion(SCRIPT, attempt)

    def mutate(script):
        return script + "\nraise RuntimeError('injected build error')\n"

    def runner(script, overrides, timeout):
        ran["n"] += 1
        if "injected build error" in script:
            return {"ok": False, "traceback": "RuntimeError: injected build error",
                    "warnings": [], "files": {}, "stdout_tail": ""}
        return _ok()

    result = generate("a floor waste", complete_fn=complete_fn, runner=runner, mutate_script=mutate)
    assert result["status"] == "done"
    assert result["attempts"] == 2
    assert ran["n"] == 2
    assert result["attempts"] <= 4


def test_cli_prompt_and_feedback(monkeypatch, capsys):
    captured = {}

    def fake_generate(text, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return {
            "project_id": "proj1",
            "version": 1 if kwargs["mode"] == "prompt" else 2,
            "status": "done",
            "attempts": 1,
            "latencies": [0.5],
            "parts": 7,
            "model": "muse-spark-1.3",
            "warnings": [],
            "error": None,
            "artifact_dir": "/tmp/make/proj1/1",
        }

    monkeypatch.setattr("app.make.cli.generate", fake_generate)
    assert main(["bathroom floor waste"]) == 0
    assert captured["kwargs"]["mode"] == "prompt"
    out = capsys.readouterr().out
    assert "project proj1" in out
    assert "version 1" in out
    assert SECRET not in out
    assert main(["--project", "proj1", "--feedback", "make the upturn 150mm and add sealant bead"]) == 0
    assert captured["kwargs"]["mode"] == "feedback"
    assert captured["kwargs"]["project_id"] == "proj1"
    assert "150mm" in captured["text"]


def test_cli_feedback_needs_a_project():
    with pytest.raises(SystemExit) as caught:
        main(["--feedback", "taller"])
    assert caught.value.code == 2


def test_build_messages_for_a_new_prompt_is_just_the_text():
    messages = build_messages("prompt", "a sill", None)
    assert messages[-1]["content"] == "a sill"
    assert "tree.json" not in messages[-1]["content"]
