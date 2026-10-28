"""Setout backend: upload -> spec -> build -> verify -> artifacts.

Single-process v0: builds run in-process but move through real persisted
job states (queued -> running -> done/failed) with per-stage progress, so
the viewer shows genuine progress and failures preserve intermediates.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .geometry import build_assembly, export_glb, export_scene_json
from .seed import door_sill_d3, door_subsill_d4
from .spec import DetailSpec, Provenance
from .store import (build_dir, db, figure_path, get_document, store_pdf,
                    var_root)
from .verify import summarize, verify_all

app = FastAPI(title="Setout", version="0.1.0")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


def _set_build(job_id: str, state: str, progress: list[str] | None = None,
               spec: str | None = None, report: str | None = None) -> None:
    con = db()
    if progress is not None:
        con.execute("UPDATE builds SET state=?, progress=? WHERE id=?",
                    (state, json.dumps(progress), job_id))
    else:
        con.execute("UPDATE builds SET state=? WHERE id=?", (state, job_id))
    if spec is not None:
        con.execute("UPDATE builds SET spec=? WHERE id=?", (spec, job_id))
    if report is not None:
        con.execute("UPDATE builds SET report=? WHERE id=?", (report, job_id))
    con.commit()
    con.close()


def _get_build(job_id: str) -> dict | None:
    con = db()
    row = con.execute("SELECT id, detail_id, doc_id, state, progress, report "
                      "FROM builds WHERE id=?", (job_id,)).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row[0], "detail_id": row[1], "doc_id": row[2],
            "state": row[3], "progress": json.loads(row[4] or "[]"),
            "report": json.loads(row[5]) if row[5] else None}


# ---------------------------------------------------------------- documents

@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if not data[:5] == b"%PDF-":
        raise HTTPException(400, "not a PDF (magic check failed)")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "PDF larger than 50 MB")
    return store_pdf(file.filename or "upload.pdf", data)


@app.get("/api/documents/{doc_id}")
async def document(doc_id: str) -> dict:
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(404, "unknown document")
    return doc


@app.get("/api/documents/{doc_id}/figures/{crop_id}")
async def figure(doc_id: str, crop_id: str):
    p = figure_path(doc_id, crop_id)
    if not p:
        raise HTTPException(404, "unknown figure")
    media = "image/jpeg" if p.suffix == ".jpg" else "image/png"
    return FileResponse(p, media_type=media)


# ---------------------------------------------------------------- specs

SPECS = {"conpro-door-sill-D3": door_sill_d3,
         "conpro-subsill-D4": door_subsill_d4}


@app.get("/api/specs")
async def list_specs() -> dict:
    return {"specs": [{"detail_id": k,
                       "title": v().title} for k, v in SPECS.items()]}


@app.get("/api/specs/{detail_id}")
async def get_spec(detail_id: str) -> dict:
    if detail_id not in SPECS:
        raise HTTPException(404, "unknown detail")
    return SPECS[detail_id]().model_dump()


class Correction(BaseModel):
    component_id: str
    param: str | None = None
    value: float | None = None
    source_label: str | None = None


@app.patch("/api/specs/{detail_id}")
async def correct_spec(detail_id: str, fix: Correction) -> dict:
    """User correction: param values and labels. Param edits are recorded
    with USER_INPUT provenance and re-validated; source (drawing) values can
    only be overridden explicitly, never silently."""
    if detail_id not in SPECS:
        raise HTTPException(404, "unknown detail")
    spec = SPECS[detail_id]()
    try:
        comp = spec.component(fix.component_id)
    except KeyError:
        raise HTTPException(404, "unknown component")
    if fix.source_label is not None:
        comp.source_label = fix.source_label
    if fix.param is not None:
        if fix.value is None:
            raise HTTPException(400, "value required with param")
        try:
            p = comp.param(fix.param)
        except KeyError:
            raise HTTPException(404, "unknown param")
        if p.provenance == Provenance.DRAWING:
            p.note = ((p.note or "") + " [USER OVERRIDE of drawing value]"
                      ).strip()
        p.value = fix.value
        p.provenance = Provenance.USER_INPUT
    DetailSpec.model_validate(spec.model_dump())  # re-validate refs/cycles
    # persist the corrected spec for subsequent builds
    var_root().joinpath(f"spec-{detail_id}.json").write_text(
        spec.model_dump_json(indent=2))
    return spec.model_dump()


def _active_spec(detail_id: str) -> DetailSpec:
    override = var_root() / f"spec-{detail_id}.json"
    if override.exists():
        return DetailSpec.model_validate_json(override.read_text())
    return SPECS[detail_id]()


# ---------------------------------------------------------------- builds

class BuildRequest(BaseModel):
    detail_id: str
    doc_id: str | None = None
    use_display_thickness: bool = False


def _run_build(job_id: str, req: BuildRequest, doc_sha: str | None) -> None:
    progress: list[str] = []
    try:
        _set_build(job_id, "running", progress)
        progress.append("validate spec")
        _set_build(job_id, "running", progress)
        spec = _active_spec(req.detail_id)
        progress.append("generate meshes")
        _set_build(job_id, "running", progress)
        meshes = build_assembly(spec,
                                use_display=req.use_display_thickness)
        progress.append("verify")
        _set_build(job_id, "running", progress)
        results = verify_all(spec, meshes, doc_sha)
        report = summarize(results)
        progress.append("export artifacts")
        _set_build(job_id, "running", progress)
        d = build_dir(job_id)
        (d / "spec.json").write_text(spec.model_dump_json(indent=2))
        (d / "scene.json").write_text(json.dumps(
            export_scene_json(spec, meshes), indent=1))
        (d / "model.glb").write_bytes(
            export_glb(meshes, [c.id for c in spec.components]))
        (d / "report.json").write_text(json.dumps(
            {"summary": report,
             "checks": [r.model_dump() for r in results]}, indent=2))
        _set_build(job_id, "done", progress,
                   spec.model_dump_json(), json.dumps(report))
    except Exception as e:  # noqa: BLE001 - job failure must persist + report
        progress.append(f"FAILED: {e}")
        _set_build(job_id, "failed", progress)
        build_dir(job_id).joinpath("error.txt").write_text(
            f"{e}\n\n{traceback.format_exc()}")


@app.post("/api/builds")
async def create_build(req: BuildRequest) -> dict:
    if req.detail_id not in SPECS:
        raise HTTPException(404, "unknown detail")
    doc_sha = None
    if req.doc_id:
        doc = get_document(req.doc_id)
        if not doc:
            raise HTTPException(404, "unknown document")
        doc_sha = doc["sha256"]
    job_id = uuid.uuid4().hex[:12]
    con = db()
    con.execute("INSERT INTO builds(id, detail_id, doc_id, state, progress) "
                "VALUES(?,?,?,?,?)",
                (job_id, req.detail_id, req.doc_id, "queued", "[]"))
    con.commit()
    con.close()
    threading.Thread(target=_run_build, args=(job_id, req, doc_sha),
                     daemon=True).start()
    return {"id": job_id, "state": "queued", "detail_id": req.detail_id,
            "doc_id": req.doc_id, "progress": [], "report": None}


@app.get("/api/builds/{job_id}")
async def get_build(job_id: str) -> dict:
    b = _get_build(job_id)
    if not b:
        raise HTTPException(404, "unknown build")
    d = build_dir(job_id)
    arts = [p.name for p in sorted(d.glob("*")) if p.is_file()]
    b["artifacts"] = arts
    return b


@app.get("/api/builds/{job_id}/artifacts/{name}")
async def artifact(job_id: str, name: str):
    if name not in ("spec.json", "scene.json", "model.glb", "report.json",
                    "error.txt"):
        raise HTTPException(404, "unknown artifact")
    p = build_dir(job_id) / name
    if not p.exists():
        raise HTTPException(404, "artifact not ready")
    media = {"model.glb": "model/gltf-binary"}.get(name, "application/json")
    if name == "error.txt":
        media = "text/plain"
    return FileResponse(p, media_type=media, filename=f"{job_id}-{name}")


# ---------------------------------------------------------------- eval

@app.get("/api/eval/{job_id}")
async def evaluate(job_id: str) -> dict:
    """Stage A/B/C summary for a finished build (see docs/EVAL.md)."""
    b = _get_build(job_id)
    if not b:
        raise HTTPException(404, "unknown build")
    if b["state"] != "done":
        raise HTTPException(409, f"build state is {b['state']}")
    spec = DetailSpec.model_validate_json(
        (build_dir(job_id) / "spec.json").read_text())
    stages: dict[str, Any] = {}
    for stage in ("A", "B", "C"):
        rows = [r for r in spec.verification if r.stage == stage]
        stages[stage] = {
            "checks": [r.model_dump() for r in rows],
            "counts": {s: sum(1 for r in rows if r.status == s)
                       for s in ("PASS", "FAIL", "UNVERIFIED", "N/A")},
        }
    return {"job_id": job_id, "detail_id": b["detail_id"],
            "verdict": (b["report"] or {}).get("verdict"),
            "stages": stages}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    p = DIST / "index.html"
    if p.exists():
        return p.read_text()
    return ("<h1>Setout</h1><p>Web bundle not built. Run "
            "<code>npm run build</code> in <code>web/</code>.</p>")
