"""HTTP API for 3D Make. The model and Blender are faked; the queue runs on this thread."""
import io
import json

from fastapi.testclient import TestClient

from app.main import app
from app.make.db import create_job, get_job, get_version, set_job_state
from app.make.queue import process_available, recover_stale

client = TestClient(app)
SCRIPT = 'PARAMS = {"edge": 100}\n'
TREE = {
    "name": "FloorWasteJunction",
    "type": "collection",
    "children": [
        {"name": "Waterproofing", "type": "collection", "children": [
            {"name": "Upturn", "type": "mesh", "location": [0, 0, 0]},
            {"name": "Membrane", "type": "mesh"},
            {"name": "Stainless Angle", "type": "mesh"},
            {"name": "Sealant", "type": "mesh"},
            {"name": "Tile", "type": "mesh"},
        ]},
        {"name": "Structure", "type": "collection", "children": [
            {"name": "Substrate", "type": "mesh"},
            {"name": "Screed", "type": "mesh"},
        ]},
    ],
}


def _completion(script, attempt=1):
    return {
        "script": script,
        "summary": "floor waste",
        "via": "fence",
        "profile": "muse",
        "model": "muse-spark-1.3",
        "event": {
            "profile": "muse",
            "model": "muse-spark-1.3",
            "latency_s": 0.2,
            "tokens": {"prompt": 3, "completion": 4, "total": 7},
            "via": "fence",
            "attempt": attempt,
            "tools_attempted": False,
        },
    }


def _files(script, overrides, timeout):
    tree = json.loads(json.dumps(TREE))
    location = (overrides or {}).get("Upturn", {}).get("location")
    if location:
        for group in tree["children"]:
            for part in group["children"]:
                if part["name"] == "Upturn":
                    part["location"] = location
    return {
        "ok": True,
        "traceback": "",
        "stdout_tail": "",
        "warnings": [],
        "files": {
            "scene.glb": b"glTF-test-bytes",
            "tree.json": json.dumps(tree).encode(),
            "preview.png": b"\x89PNG\r\n\x1a\npreview",
        },
    }


def _complete(messages, profile=None, attempt=1):
    _complete.seen.append(messages)
    return _completion(SCRIPT, attempt)


_complete.seen = []


def _run():
    _complete.seen = []
    return process_available(complete_fn=_complete, runner=_files, render_fn=_render)


def _render(script, overrides, quality, timeout=30):
    return {"ok": True, "traceback": "", "files": {"render.png": b"\x89PNG\r\n\x1a\nrender"}}


def _project():
    response = client.post("/api/make/projects", json={"title": "Floor waste"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_models_are_booleans_only():
    response = client.get("/api/make/settings/models")
    assert response.status_code == 200
    body = response.json()
    muse = next(item for item in body if item["id"] == "muse")
    assert muse["label"] == "Muse Spark 1.3"
    assert isinstance(muse["ready"], bool)
    assert isinstance(muse["caps"]["vision"], bool)
    assert isinstance(muse["caps"]["tools"], bool)
    assert "api_key" not in response.text
    assert "base_url" not in response.text
    assert "sk-" not in response.text


def test_message_returns_before_the_job_runs_and_then_streams_done():
    project = _project()
    response = client.post(f"/api/make/projects/{project}/messages", json={
        "text": "bathroom floor waste junction",
        "mode": "create",
    })
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert get_job(job_id)["state"] == "queued"
    assert _run() == 1
    with client.stream("GET", f"/api/make/jobs/{job_id}/events") as stream:
        body = "".join(stream.iter_text())
    assert "event: status" in body
    assert "event: attempt" in body
    assert "event: log" in body
    assert 'event: done' in body
    assert '"version": 1' in body
    glb = client.get(f"/api/make/projects/{project}/versions/1/scene.glb")
    assert glb.status_code == 200
    assert glb.content.startswith(b"glTF")
    tree = client.get(f"/api/make/projects/{project}/versions/1/tree.json")
    assert tree.json()["name"] == "FloorWasteJunction"


def test_feedback_and_override_create_the_next_versions():
    project = _project()
    first = client.post(f"/api/make/projects/{project}/messages", json={"text": "floor waste", "mode": "create"})
    _run()
    second = client.post(f"/api/make/projects/{project}/messages", json={
        "text": "make the upturn 150mm and add sealant bead",
        "mode": "feedback",
    })
    assert second.status_code == 200
    _run()
    detail = client.get(f"/api/make/projects/{project}").json()
    assert [item["n"] for item in detail["versions"]] == [1, 2]
    assert detail["versions"][1]["source"] == "feedback"
    assert detail["versions"][1]["parent_n"] == 1
    patched = client.patch(f"/api/make/projects/{project}/versions/2/overrides", json={
        "overrides": [{"name": "Upturn", "location": [10, 20, 30]}],
    })
    assert patched.status_code == 200, patched.text
    _run()
    tree = client.get(f"/api/make/projects/{project}/versions/3/tree.json").json()
    upturn = next(part for group in tree["children"] for part in group["children"] if part["name"] == "Upturn")
    assert upturn["location"] == [10, 20, 30]
    version = get_version(project, 3)
    assert version["source"] == "override"
    assert version["parent_n"] == 2
    assert first.status_code == 200


def test_a_later_override_keeps_earlier_dimension_edits():
    project = _project()
    created = client.post(f"/api/make/projects/{project}/messages", json={"text": "floor waste", "mode": "create"})
    assert created.status_code == 200
    _run()
    height = client.patch(f"/api/make/projects/{project}/versions/1/overrides", json={
        "overrides": [{"name": "Upturn", "dims": {"upturn_hgt": 160}}],
    })
    assert height.status_code == 200, height.text
    _run()
    moved = client.patch(f"/api/make/projects/{project}/versions/2/overrides", json={
        "overrides": [{"name": "Upturn", "location": [10, 20, 30]}],
    })
    assert moved.status_code == 200, moved.text
    _run()
    stored = json.loads(get_version(project, 3)["overrides"])
    assert stored["Upturn"]["dims"]["upturn_hgt"] == 160
    assert stored["Upturn"]["location"] == [10, 20, 30]


def test_attachment_png_reaches_the_model_payload():
    project = _project()
    png = _png()
    uploaded = client.post(
        f"/api/make/projects/{project}/attachments",
        files={"file": ("sketch.png", png, "application/octet-stream")},
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment_id = uploaded.json()["id"]
    sent = client.post(f"/api/make/projects/{project}/messages", json={
        "text": "use the sketch",
        "attachments": [attachment_id],
        "mode": "create",
    })
    assert sent.status_code == 200
    assert _run() == 1
    content = _complete.seen[-1][1]["content"]
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    events = json.loads(get_job(sent.json()["job_id"])["events"])
    images = next(event for event in events if event.get("kind") == "images")
    assert images["count"] == 1
    assert images["vision"] is True


def test_pdf_page_is_rasterized_and_junk_is_rejected():
    project = _project()
    pdf = client.post(
        f"/api/make/projects/{project}/attachments?page=1",
        files={"file": ("page.pdf", _pdf(), "application/pdf")},
    )
    assert pdf.status_code == 200, pdf.text
    junk = client.post(
        f"/api/make/projects/{project}/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert junk.status_code == 400
    huge = b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024)
    too_big = client.post(
        f"/api/make/projects/{project}/attachments",
        files={"file": ("big.png", huge, "image/png")},
    )
    assert too_big.status_code == 413


def test_render_is_enqueued_and_use_blender_chains_one():
    project = _project()
    created = client.post(f"/api/make/projects/{project}/messages", json={"text": "floor waste", "use_blender": True})
    assert _run() == 2
    assert get_version(project, 1)["render_path"]
    again = client.post(f"/api/make/projects/{project}/versions/1/render", json={"quality": "draft"})
    assert again.status_code == 200
    assert get_job(again.json()["job_id"])["kind"] == "render"
    png = client.get(f"/api/make/projects/{project}/versions/1/render.png")
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")
    detail = client.get(f"/api/make/projects/{project}").json()
    assert detail["versions"][0]["has_render"] is True
    assert created.status_code == 200


def test_stale_running_job_is_requeued_once_then_failed():
    project = _project()
    create_job("stale-job", project, "generate")
    set_job_state("stale-job", "running", attempt=1)
    assert recover_stale() == 1
    assert get_job("stale-job")["state"] == "queued"
    set_job_state("stale-job", "running", attempt=1)
    recover_stale()
    assert get_job("stale-job")["state"] == "failed"


def _png() -> bytes:
    from PIL import Image
    image = Image.new("RGB", (8, 8), (10, 20, 200))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _pdf() -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(120, 120)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
