"""The day-1 report must not keep keys, auth headers or URL query strings."""
from pathlib import Path

from app.make.redact import redact
from eval.make.muse_bpy_eval import render_report


def test_redacts_env_value_bearer_and_query(monkeypatch):
    secret = "sk-testsecretvalue999"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    raw = (
        "Authorization: Bearer " + secret + "\n"
        "LLM_API_KEY=" + secret + "\n"
        "see https://llm.example.test/v1?api_key=sk-querysecretvalue999&x=1\n"
        "model muse-spark-1.3 profile muse\n"
    )
    cleaned = redact(raw)
    assert secret not in cleaned
    assert "sk-querysecretvalue999" not in cleaned
    assert "api_key=" not in cleaned
    assert "Bearer" not in cleaned
    assert "https://llm.example.test/v1" not in cleaned  # base URL value is scrubbed
    assert "muse-spark-1.3" in cleaned
    assert "profile muse" in cleaned


def test_report_file_keeps_scores_and_drops_secrets(tmp_path, monkeypatch):
    secret = "sk-testsecretvalue999"
    monkeypatch.setenv("LLM_API_KEY", secret)
    sections = [{
        "id": "muse",
        "label": "Muse Spark 1.3",
        "model": "muse-spark-1.3",
        "verdict": "NO-GO",
        "passed": 1,
        "rows": [{
            "prompt_id": "floor_waste",
            "runs": False,
            "objects": 2,
            "collections_present": False,
            "tree_valid": False,
            "repairs": 2,
            "pass": False,
            "error_tail": (
                "Authorization: Bearer " + secret
                + " https://llm.example.test/v1?api_key=sk-querysecretvalue999"
            ),
        }],
    }]
    text = redact(render_report(sections, target="local"))
    path = tmp_path / "day1_report.md"
    path.write_text(text)
    body = path.read_text()
    assert secret not in body
    assert "sk-querysecretvalue999" not in body
    assert "Bearer" not in body
    assert "api_key=" not in body
    assert "muse-spark-1.3" in body
    assert "floor_waste" in body
    assert "NO-GO" in body
    assert "FAIL" in body
    assert "Target: local" in body


def test_env_example_is_empty_and_env_is_ignored():
    example = Path(".env.example").read_text().splitlines()
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                 "ALT_LLM_BASE_URL", "ALT_LLM_API_KEY", "ALT_LLM_MODEL"):
        assert f"{name}=" in example
    ignored = Path(".gitignore").read_text().splitlines()
    assert ".env" in ignored
    assert any(line.startswith("eval/make/out/") for line in ignored)
