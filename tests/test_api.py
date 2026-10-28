"""API end-to-end: upload -> spec -> build -> artifacts -> eval."""

import io
import os

os.environ.setdefault("SETOUT_VAR", "/tmp/setout-test-var")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(540, 780)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_reject_non_pdf():
    r = client.post("/api/documents",
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_upload_blank_pdf_has_no_figures():
    r = client.post("/api/documents",
                    files={"file": ("blank.pdf", _blank_pdf(),
                                    "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pages"] == 1
    assert body["figures"] == []  # honest: nothing embedded, nothing faked


def test_spec_list_has_both_variants():
    r = client.get("/api/specs")
    assert r.status_code == 200
    ids = {s["detail_id"] for s in r.json()["specs"]}
    assert {"conpro-door-sill-D3", "conpro-subsill-D4"} <= ids


def test_d4_build_passes():
    r = client.post("/api/builds", json={"detail_id": "conpro-subsill-D4"})
    assert r.status_code == 200, r.text
    body = _poll_done(r.json()["id"])
    assert body["state"] == "done", body
    assert body["report"]["failed"] == []
    assert body["report"]["verdict"].startswith("PASS")


def test_spec_roundtrip_and_correction():
    r = client.get("/api/specs/conpro-door-sill-D3")
    assert r.status_code == 200
    n = len(r.json()["components"])
    assert n >= 10
    r = client.patch("/api/specs/conpro-door-sill-D3",
                     json={"component_id": "packer", "param": "h",
                           "value": 10})
    assert r.status_code == 200
    p = [x for x in r.json()["components"] if x["id"] == "packer"][0]
    h = [x for x in p["params"] if x["name"] == "h"][0]
    assert h["value"] == 10 and h["provenance"] == "user_input"


def _poll_done(job: str, tries: int = 100) -> dict:
    import time
    for _ in range(tries):
        r = client.get(f"/api/builds/{job}")
        assert r.status_code == 200
        if r.json()["state"] in ("done", "failed"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError("build did not finish")


def test_build_produces_artifacts_and_report():
    r = client.post("/api/builds", json={"detail_id": "conpro-door-sill-D3"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "queued"
    job = r.json()["id"]
    body = _poll_done(job)
    assert body["state"] == "done", body
    assert body["progress"][0] == "validate spec"
    assert set(("spec.json", "scene.json", "model.glb", "report.json")) \
        <= set(body["artifacts"])
    assert body["report"]["verdict"].startswith("PASS")
    assert body["report"]["failed"] == []
    r = client.get(f"/api/builds/{job}/artifacts/scene.json")
    scene = r.json()
    assert scene["detail_id"] == "conpro-door-sill-D3"
    assert any(s["id"] == "membrane" and s["tri_count"] > 0
               for s in scene["solids"])
    r = client.get(f"/api/eval/{job}")
    assert r.status_code == 200
    assert r.json()["stages"]["B"]["counts"]["FAIL"] == 0
