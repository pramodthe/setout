"""Settings store the key on the server and never send it back."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.make.db import connect, get_setting
from app.make.queue import process_available

client = TestClient(app)
SECRET = "muse-settings-key-abc123xyz"
SCRIPT = "import math\n"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    monkeypatch.setattr("app.make.llm.load_dotenv", lambda *args, **kwargs: None)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                 "ALT_LLM_BASE_URL", "ALT_LLM_API_KEY", "ALT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    from app.make.db import delete_settings_prefix
    delete_settings_prefix("make.llm.")
    yield
    delete_settings_prefix("make.llm.")


def _save(**extra):
    body = {
        "profile": "muse",
        "base_url": "https://llm.example.test/v1",
        "model": "muse-spark-1.3",
    }
    body.update(extra)
    response = client.put("/api/make/settings", json=body)
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    return response.json()


def test_the_key_is_stored_and_only_a_boolean_comes_back():
    body = _save(api_key=SECRET)
    muse = next(item for item in body["profiles"] if item["id"] == "muse")
    assert muse["api_key_set"] is True
    assert muse["base_url"] == "https://llm.example.test/v1"
    assert muse["model"] == "muse-spark-1.3"
    assert muse["ready"] is True
    assert "api_key" not in muse
    assert get_setting("make.llm.muse.api_key") == SECRET
    listed = client.get("/api/make/settings/models")
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert "llm.example.test" not in listed.text
    muse_chip = next(item for item in listed.json() if item["id"] == "muse")
    assert muse_chip["ready"] is True
    again = client.get("/api/make/settings")
    assert SECRET not in again.text
    junk = client.put(
        "/api/make/settings",
        content=SECRET.encode(),
        headers={"content-type": "application/json"},
    )
    assert junk.status_code == 400
    assert SECRET not in junk.text


def test_a_blank_key_keeps_the_saved_one_and_clear_removes_it():
    _save(api_key=SECRET)
    kept = _save()
    muse = next(item for item in kept["profiles"] if item["id"] == "muse")
    assert muse["api_key_set"] is True
    assert get_setting("make.llm.muse.api_key") == SECRET
    cleared = _save(clear_api_key=True)
    muse = next(item for item in cleared["profiles"] if item["id"] == "muse")
    assert muse["api_key_set"] is False
    assert get_setting("make.llm.muse.api_key") is None


def test_connection_error_does_not_contain_the_key(monkeypatch):
    class Completions:
        def __init__(self, key):
            self.key = key

        def create(self, **kwargs):
            raise RuntimeError(f"rejected {self.key}")

    class Fake:
        def __init__(self, *, base_url, api_key, max_retries=0, timeout=None):
            self.chat = self
            self.completions = Completions(api_key)

    monkeypatch.setattr("app.make.llm.OpenAI", Fake)
    response = client.post("/api/make/settings/test", json={
        "profile": "muse",
        "base_url": "https://llm.example.test/v1?api_key=" + SECRET,
        "model": "muse-spark-1.3",
        "api_key": SECRET,
    })
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert SECRET not in response.text
    assert "api_key=" not in response.text


def test_saved_key_never_reaches_job_rows_sse_or_other_tables():
    _save(api_key=SECRET)
    project = client.post("/api/make/projects", json={"title": "Key"}).json()["id"]
    sent = client.post(f"/api/make/projects/{project}/messages", json={"text": "a box", "mode": "create"})
    assert sent.status_code == 200, sent.text
    job_id = sent.json()["job_id"]

    def complete(messages, profile=None, attempt=1):
        return {
            "script": SCRIPT,
            "summary": "box",
            "via": "fence",
            "profile": "muse",
            "model": "muse-spark-1.3",
            "event": {
                "profile": "muse",
                "model": "muse-spark-1.3",
                "latency_s": 0.1,
                "tokens": {"prompt": 1, "completion": 1, "total": 2},
                "via": "fence",
                "attempt": attempt,
                "tools_attempted": False,
            },
        }

    def runner(script, overrides, timeout):
        return {"ok": False, "traceback": f"build failed {SECRET}", "warnings": [SECRET], "files": {}}

    assert process_available(complete_fn=complete, runner=runner) >= 1
    with client.stream("GET", f"/api/make/jobs/{job_id}/events") as stream:
        body = "".join(stream.iter_text())
    assert SECRET not in body
    detail = client.get(f"/api/make/projects/{project}")
    assert SECRET not in detail.text
    with connect() as con:
        for table in ("jobs", "messages", "scene_versions", "projects"):
            for row in con.execute(f"SELECT * FROM {table}"):
                blob = " ".join("" if value is None else str(value) for value in row)
                assert SECRET not in blob, table
        stored = con.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("make.llm.muse.api_key",),
        ).fetchone()
    assert stored[0] == SECRET
    assert SECRET not in json.dumps(detail.json())
