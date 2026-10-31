"""BLENDER_TARGET selects Modal or local, and Modal failure falls back to local."""
from app.make.blender_backend import ModalUnavailable, blender_target, render, run


def test_unset_or_unknown_target_is_local(monkeypatch):
    monkeypatch.delenv("BLENDER_TARGET", raising=False)
    assert blender_target() == "local"
    monkeypatch.setenv("BLENDER_TARGET", "nope")
    assert blender_target() == "local"
    monkeypatch.setenv("BLENDER_TARGET", "modal")
    assert blender_target() == "modal"


def test_modal_target_uses_the_remote_result(monkeypatch):
    monkeypatch.setenv("BLENDER_TARGET", "modal")
    calls = {}

    def remote(name, *args):
        calls["name"] = name
        calls["args"] = args
        return {"ok": True, "files": {}, "traceback": ""}

    monkeypatch.setattr("app.make.blender_backend._modal", remote)

    def boom(*args, **kwargs):
        raise AssertionError("local blender was started")

    monkeypatch.setattr("app.make.worker.run", boom)
    result = run("import bpy\n", {}, timeout=30)
    assert result["ok"] is True
    assert result["backend"] == "modal"
    assert calls["name"] == "run"
    assert calls["args"][0] == "import bpy\n"


def test_modal_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("BLENDER_TARGET", "modal")

    def remote(name, *args):
        raise ModalUnavailable("lookup failed")

    monkeypatch.setattr("app.make.blender_backend._modal", remote)
    monkeypatch.setattr(
        "app.make.worker.run",
        lambda *args, **kwargs: {"ok": True, "traceback": "", "files": {}, "warnings": []},
    )
    result = run("import bpy\n", {}, timeout=30)
    assert result["backend"] == "local"
    assert result["ok"] is True
    assert any("local Blender" in item for item in result["warnings"])


def test_render_uses_the_same_switch(monkeypatch):
    monkeypatch.setenv("BLENDER_TARGET", "modal")
    monkeypatch.setattr(
        "app.make.blender_backend._modal",
        lambda name, *args: {"ok": True, "files": {"render.png": b"png"}},
    )
    result = render("import bpy\n", {}, "draft", timeout=30)
    assert result["backend"] == "modal"
    assert result["files"]["render.png"] == b"png"


def test_modal_app_env_has_no_secrets():
    text = open("deploy/blender_app.py").read()
    assert "Secret" not in text
    assert "LLM_API_KEY" not in text
    assert "api_key" not in text
    assert "block_network=True" in text
