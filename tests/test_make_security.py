"""Worker rejection, timeout, output cap, and credential scrubbing."""
from pathlib import Path
import stat

import pytest

from app.make.ast_check import check
from app.make.db import put_setting
from app.make.harness import restricted_globals
from app.make.redact import redact
from app.make.worker import MAX_STDOUT, _read_files, run, scrub_env
from eval.make.muse_bpy_eval import render_report


def test_ast_allowlist_rejects_imports_calls_open_and_getattr():
    rejected = [
        "import os",
        "import subprocess",
        "import socket",
        "from os import path",
        "__import__('os')",
        "eval('1')",
        "exec('1')",
        "open('/etc/passwd')",
        "open('../secret')",
        "getattr(obj, '__class__')",
        "getattr(obj, name)",
        "getattr(obj, 'eval')",
        "getattr(obj, '__' + 'class__')",
    ]
    for source in rejected:
        result = check(source)
        assert not result.ok, source
    assert check("import math\nimport bpy\n").ok
    assert check("getattr(obj, 'location')").ok


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


def test_stdout_is_capped_and_a_printed_key_is_redacted(tmp_path, monkeypatch):
    secret = "sk-testsecretvalue999"
    monkeypatch.setenv("LLM_API_KEY", secret)
    binary = tmp_path / "fakeblender"
    binary.write_text("#!/bin/sh\npython3 -c 'print(\"x\"*200000); print(\"sk-testsecretvalue999\")'\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    result = run("import math\n", timeout=10, preview=False, blender_bin=str(binary))
    assert secret not in result["stdout_tail"]
    assert len(result["stdout_tail"]) <= MAX_STDOUT


def test_an_oversized_artifact_is_not_returned(tmp_path, monkeypatch):
    monkeypatch.setattr("app.make.worker.MAX_ARTIFACT", 8)
    (tmp_path / "scene.glb").write_bytes(b"0123456789")
    (tmp_path / "tree.json").write_bytes(b"{}")
    files, oversized = _read_files(tmp_path, preview=False, mode="preview")
    assert oversized == ["scene.glb"]
    assert "scene.glb" not in files
    assert files["tree.json"] == b"{}"


def test_a_saved_key_is_redacted_from_the_eval_report(monkeypatch):
    from app.make.db import delete_settings_prefix
    secret = "muse-settings-key-abc123xyz"
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    put_setting("make.llm.muse.api_key", secret)
    try:
        text = redact(render_report([{
        "id": "muse",
        "label": "Muse Spark 1.3",
        "model": "muse-spark-1.3",
        "verdict": "NO-GO",
        "passed": 0,
        "rows": [{
            "prompt_id": "floor_waste",
            "runs": False,
            "objects": 0,
            "collections_present": False,
            "tree_valid": False,
            "repairs": 0,
            "pass": False,
            "error_tail": "provider said " + secret,
        }],
    }], target="local"))
        assert secret not in text
        assert "floor_waste" in text
    finally:
        delete_settings_prefix("make.llm.")


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
