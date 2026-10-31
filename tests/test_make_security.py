"""Worker rejection and timeout. Blender is only required for the in-process loop."""
from pathlib import Path
import stat

import pytest

from app.make.harness import restricted_globals
from app.make.worker import run, scrub_env


def test_open_socket_os_and_import_never_start_blender(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("blender was started")

    monkeypatch.setattr("app.make.worker.subprocess.Popen", boom)
    scripts = [
        "open('/etc/passwd').read()",
        "import socket\nsocket.socket().connect(('127.0.0.1', 9))",
        "import os\nprint(os.environ)",
        "__import__('os')",
    ]
    for source in scripts:
        result = run(source, timeout=5)
        assert result["ok"] is False
        assert result["files"] == {}
        assert result["blocked"] == "ast"
        assert result["traceback"]


def test_restricted_exec_blocks_open_os_and_socket():
    namespace = restricted_globals({})
    with pytest.raises(NameError):
        exec("open('/etc/passwd')", namespace)  # noqa: S102 - the assertion is the missing builtin
    with pytest.raises(ImportError):
        exec("import os\nos.environ", namespace)  # noqa: S102
    with pytest.raises(ImportError):
        exec("import socket\nsocket.socket()", namespace)  # noqa: S102
    exec("import math\nassert math.floor(1.2) == 1", namespace)  # noqa: S102


def test_scrub_env_drops_credential_names(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-testsecretvalue999")
    monkeypatch.setenv("PATH", "/usr/bin")
    cleaned = scrub_env()
    assert "LLM_API_KEY" not in cleaned
    assert "sk-testsecretvalue999" not in " ".join(cleaned.values())
    assert cleaned["PATH"] == "/usr/bin"


def test_worker_kills_a_hung_process_at_the_timeout(tmp_path):
    binary = tmp_path / "fakeblender"
    binary.write_text("#!/bin/sh\nwhile true; do sleep 0.2; done\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    result = run("while True:\n    pass\n", timeout=2, preview=False, blender_bin=str(binary))
    assert result["ok"] is False
    assert result["files"] == {}
    assert "killed at timeout" in result["traceback"]


def test_modal_app_has_no_secrets_and_demo_app_is_separate():
    worker = Path("deploy/blender_app.py").read_text()
    demo = Path("deploy/modal_app.py").read_text()
    assert 'modal.App("setout-blender"' in worker
    assert "block_network=True" in worker
    assert "max_inputs=1" in worker
    assert "single_use_containers=True" in worker
    assert "memory=4096" in worker
    assert "cpu=2" in worker
    assert "timeout=120" in worker
    assert "timeout=600" in worker
    assert "Secret.from_name" not in worker
    assert "secrets=" not in worker
    assert "from app.make.blender_pin import" in worker
    assert 'modal.App("setout-demo"' in demo
    assert "setout-blender" not in demo


def test_render_settings_follow_the_quality_switch(monkeypatch):
    from app.make.render_settings import PREVIEW, render_settings

    monkeypatch.delenv("RENDER_GPU", raising=False)
    draft = render_settings("draft")
    assert draft["engine"] == "BLENDER_EEVEE_NEXT"
    assert draft["width"] == 1280 and draft["height"] == 960
    high = render_settings("high")
    assert high["engine"] == "CYCLES"
    assert high["samples"] == 64
    assert high["device"] == "CPU"
    monkeypatch.setenv("RENDER_GPU", "1")
    gpu = render_settings("high")
    assert gpu["samples"] == 256
    assert gpu["device"] == "GPU"
    assert PREVIEW["width"] == 800 and PREVIEW["height"] == 600
