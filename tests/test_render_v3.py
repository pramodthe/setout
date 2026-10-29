"""Real Chromium + production viewer integration; SETOUT_BROWSER_TESTS=1."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from app import database as d
from app.render import Renderer
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo

pytestmark = pytest.mark.skipif(os.environ.get("SETOUT_BROWSER_TESTS") != "1", reason="explicit browser integration suite")


def test_real_viewer_renders_exact_version(source_repo, tmp_path):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo)).run_once()
    result = repo.get(d.jobs, job["id"])
    assert result["state"] == "succeeded", result
    version_id = result["result"]["version_id"]
    report = repo.list(d.verifications)[0]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "SETOUT_DATABASE_URL": str(repo.engine.url),
                   "SETOUT_VAR": str(tmp_path), "SETOUT_ARTIFACT_ROOT": str(repo.artifacts.root),
                   "SETOUT_WORKER": "external", "SETOUT_PUBLIC": "0"}
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                                   env=environment, stdout=log, stderr=log)
        try:
            for _ in range(200):
                try:
                    urllib.request.urlopen(base + "/api/ai/status", timeout=1).close()
                    break
                except OSError:
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(log.read())
                    time.sleep(0.05)
            images = Renderer(repo, base).render(version_id, report["manifest_sha"])
            assert len(images) == 6
            assert {i["view"] for i in images if not i["tags"]} == {"iso", "front", "top"}
            assert all(i["version_id"] == version_id and i["manifest_sha"] == report["manifest_sha"] for i in images)
            assert all(repo.artifacts.read(i["artifact"]["id"]).startswith(b"\xff\xd8\xff") for i in images)
            assert len({i["artifact"]["id"] for i in images if not i["tags"]}) == 3
            # The same persisted run must remain reviewable after page reload.
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
                browser = playwright.chromium.launch(headless=True,
                    executable_path=str(chrome) if chrome.is_file() else None)
                try:
                    page = browser.new_page()
                    page.goto(f"{base}/#/jobs/{job['id']}")
                    for visit in range(2):
                        if visit:
                            page.reload()
                        page.get_by_text("succeeded",exact=True).wait_for()
                        page.get_by_text("Run trace (",exact=False).click()
                        page.get_by_text("read_drawing · completed",exact=False).wait_for()
                        page.get_by_text("critic · completed",exact=False).wait_for()
                        assert page.get_by_role("link",name="Open detail").get_attribute("href") == f"#/d/{repo.run(job['id'])['detail_id']}"
                finally:
                    browser.close()
        finally:
            process.terminate()
            process.wait(timeout=15)
