"""The web app delegates Blender work to Modal, with no local process fallback."""
import json
import struct
import subprocess
from types import SimpleNamespace

import pytest

from app import presentation


def embedded_glb():
    data = json.dumps({"asset": {"version": "2.0"}}).encode()
    data += b" " * (-len(data) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20 + len(data), len(data), 0x4E4F534A) + data


def test_modal_is_the_default():
    import os
    from unittest.mock import patch
    with patch.dict(os.environ):
        os.environ.pop("SETOUT_RENDERER", None)
        assert presentation.enabled()


def remote(monkeypatch, stream):
    import modal
    calls = []

    def lookup(app, function):
        calls.append((app, function))
        return SimpleNamespace(remote_gen=stream)

    monkeypatch.setenv("SETOUT_RENDERER", "modal")
    monkeypatch.setattr(modal.Function, "from_name", lookup)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("The web app must not launch local Blender"))
    return calls


def test_rendering_uploads_the_glb_and_joins_modal_output_chunks(monkeypatch):
    source = embedded_glb()

    def stream(data):
        assert data == source
        yield "model.glb", b"first"
        yield "model.glb", b"second"
        yield "render.jpg", b"image"
        yield "render.json", b'{"backend":"modal"}'

    calls = remote(monkeypatch, stream)
    found = presentation.render_assets(source)
    assert calls == [("setout-presentation", "finish_model")]
    assert found["model.glb"] == b"firstsecond"
    assert json.loads(found["render.json"])["backend"] == "modal"


def test_modal_failure_does_not_fall_back_to_local_blender(monkeypatch):
    def fail(_):
        raise ConnectionError("Modal unavailable")

    remote(monkeypatch, fail)
    with pytest.raises(ConnectionError, match="Modal unavailable"):
        presentation.render_assets(embedded_glb())


@pytest.mark.parametrize("outputs", [[], [("unexpected.py", b"no")]])
def test_partial_or_unexpected_outputs_are_not_published(monkeypatch, outputs):
    remote(monkeypatch, lambda _: iter(outputs))
    with pytest.raises(ValueError):
        presentation.render_assets(embedded_glb())


def test_old_local_setting_cannot_launch_blender(monkeypatch):
    monkeypatch.setenv("SETOUT_RENDERER", "local")
    assert not presentation.enabled()
    with pytest.raises(ValueError, match="disabled"):
        presentation.render_assets(embedded_glb())
