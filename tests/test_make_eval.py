"""Day-1 scorer: a mocked completion returns the reference script."""
import json

import pytest

from app.make.floor_waste_script import SCRIPT
from app.make.llm import clear_tool_cache
from eval.make.muse_bpy_eval import PROMPTS, _one_prompt, profile_verdict, score_build


@pytest.fixture(autouse=True)
def _clear():
    clear_tool_cache()


def _tree(meshes=6, collections=("Structure", "Waterproofing")):
    children = []
    per = max(1, meshes // len(collections))
    remaining = meshes
    for name in collections:
        take = per if name != collections[-1] else remaining
        remaining -= take
        children.append({
            "name": name,
            "type": "collection",
            "children": [{"name": f"{name}-{index}", "type": "mesh"} for index in range(take)],
        })
    return {
        "name": "FloorWasteJunction",
        "type": "collection",
        "children": children,
    }


def _ok_result(meshes=6):
    return {
        "ok": True,
        "traceback": "",
        "stdout_tail": "",
        "files": {"tree.json": json.dumps(_tree(meshes)).encode()},
    }


def _completion(script, attempt=1):
    return {
        "script": script,
        "summary": "reference",
        "via": "tool",
        "profile": "muse",
        "model": "muse-spark-1.3",
        "event": {
            "profile": "muse",
            "model": "muse-spark-1.3",
            "latency_s": 0.1,
            "tokens": {"prompt": 1, "completion": 1, "total": 2},
            "via": "tool",
            "attempt": attempt,
        },
    }


def test_scorer_passes_a_scene_with_five_named_objects():
    scored = score_build(_ok_result())
    assert scored["runs"] is True
    assert scored["objects"] >= 5
    assert scored["collections_present"] is True
    assert scored["tree_valid"] is True
    assert scored["pass"] is True
    thin = score_build(_ok_result(meshes=4))
    assert thin["pass"] is False
    broken = score_build({"ok": True, "files": {"tree.json": b"not-json"}})
    assert broken["tree_valid"] is False
    assert broken["pass"] is False


def test_mocked_llm_returns_the_reference_script(monkeypatch):
    seen = []

    def fake_complete(messages, profile=None, *, client=None, attempt=1):
        return _completion(SCRIPT, attempt)

    def runner(script, overrides, timeout):
        seen.append(script)
        return _ok_result()

    monkeypatch.setattr("eval.make.muse_bpy_eval.complete", fake_complete)
    info = {"id": "muse", "label": "Muse Spark 1.3", "model": "muse-spark-1.3"}
    rows = [_one_prompt(prompt, info, runner, 30) for prompt in PROMPTS]
    assert seen == [SCRIPT] * len(PROMPTS)
    assert all(row["pass"] for row in rows)
    assert all(row["repairs"] == 0 for row in rows)
    assert profile_verdict(rows) == "GO"
    assert profile_verdict(rows[:2]) == "NO-GO"


def test_repairs_are_counted_and_the_error_tail_is_redacted(monkeypatch):
    secret = "sk-testsecretvalue999"
    calls = {"n": 0}

    def fake_complete(messages, profile=None, *, client=None, attempt=1):
        return _completion(SCRIPT, attempt)

    def runner(script, overrides, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": False,
                "traceback": f"boom LLM_API_KEY={secret}",
                "stdout_tail": "",
                "files": {},
            }
        return _ok_result()

    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setattr("eval.make.muse_bpy_eval.complete", fake_complete)
    info = {"id": "muse", "label": "Muse Spark 1.3", "model": "muse-spark-1.3"}
    row = _one_prompt(PROMPTS[0], info, runner, 30)
    assert row["pass"] is True
    assert row["repairs"] == 1
    assert calls["n"] == 2

    def always_fail(script, overrides, timeout):
        return {"ok": False, "traceback": f"Authorization: Bearer {secret}", "stdout_tail": "", "files": {}}

    failed = _one_prompt(PROMPTS[0], info, always_fail, 30)
    assert failed["pass"] is False
    assert failed["repairs"] == 3
    assert secret not in failed["error_tail"]
    assert "Bearer" not in failed["error_tail"]
