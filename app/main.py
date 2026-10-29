"""Setout backend: upload -> spec -> build -> verify -> artifacts.

Single-process v0: builds run in-process but move through real persisted
job states (queued -> running -> done/failed) with per-stage progress, so
the viewer shows genuine progress and failures preserve intermediates.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cad_sandbox, guide, library, meta_client, review
from . import generate as ai
from .geometry import build_assembly, export_glb, export_scene_json
from .seed import door_sill_d3, door_subsill_d4
from .spec import DetailSpec, Provenance
from .store import (build_dir, db, figure_path, find_document_by_sha,
                    get_document, store_pdf, var_root)
from .verify import summarize, verify_all

app = FastAPI(title="Setout", version="0.1.0")

# Public demo mode (SETOUT_PUBLIC=1): read-only, source drawings never served
# (they are third-party documents), and the AI guide is rate-limited so a
# public link cannot drain the API budget.
PUBLIC = os.environ.get("SETOUT_PUBLIC") == "1"
GUIDE_PER_HOUR = int(os.environ.get("SETOUT_GUIDE_PER_HOUR", "60"))
_guide_calls: list[float] = []


@app.middleware("http")
async def public_mode(request: Request, call_next):
    if PUBLIC:
        path, method = request.url.path, request.method
        drawing = path.startswith("/api/documents") or path.endswith(("/thumb", "/drawing"))
        if drawing or (method in ("POST", "PATCH") and path != "/api/guide"):
            return JSONResponse({"detail": "read-only public demo"}, status_code=403)
        if path == "/api/guide" and method == "POST":
            now = time.time()
            _guide_calls[:] = [t for t in _guide_calls if now - t < 3600]
            if len(_guide_calls) >= GUIDE_PER_HOUR:
                return JSONResponse({"detail": "guide is busy, try again later"}, status_code=429)
            _guide_calls.append(now)
    return await call_next(request)

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


def _load_spec(detail_id: str) -> DetailSpec | None:
    """Seed specs (human transcriptions) first, then AI-generated library."""
    if detail_id in SPECS:
        return SPECS[detail_id]()
    return ai.load_library_spec(detail_id)


@app.get("/api/specs")
async def list_specs() -> dict:
    seeds = [{"detail_id": k, "title": (s := v()).title,
              "crop_id": s.source.crop_id, "sha256": s.source.sha256,
              "origin": s.origin} for k, v in SPECS.items()]
    return {"specs": seeds + ai.list_library()}


@app.get("/api/specs/{detail_id}")
async def get_spec(detail_id: str) -> dict:
    """The spec builds use: saved user corrections if any, else seed/library."""
    try:
        return _active_spec(detail_id).model_dump()
    except KeyError:
        raise HTTPException(404, "unknown detail")


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
    try:
        spec = _active_spec(detail_id)  # build on earlier corrections
    except KeyError:
        raise HTTPException(404, "unknown detail")
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
    spec = _load_spec(detail_id)
    if spec is None:
        raise KeyError(detail_id)
    return spec


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
    if _load_spec(req.detail_id) is None:
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


# ---------------------------------------------------------------- library

@app.get("/api/library")
async def get_library() -> dict:
    entries = [library.entry(f(), "human") for f in SPECS.values()]
    for row in ai.list_library():
        spec = ai.load_library_spec(row["detail_id"])
        if spec is not None:
            entries.append(library.entry(spec, "ai"))
    return {"details": entries, "components": library.component_index(entries)}


@app.get("/api/specs/{detail_id}/scene")
async def get_scene(detail_id: str) -> dict:
    """Build + verify on demand (fast: pure-Python meshes) for the viewer page."""
    try:
        spec = _active_spec(detail_id)
    except KeyError:
        raise HTTPException(404, "unknown detail")
    meshes = build_assembly(spec)
    verify_all(spec, meshes, None)
    return export_scene_json(spec, meshes)


@app.get("/api/specs/{detail_id}/thumb")
@app.get("/api/specs/{detail_id}/drawing")
async def get_drawing(detail_id: str):
    """The source drawing, upright (as SAM saw it; regions are relative to it)."""
    spec = _load_spec(detail_id)
    png = ai.drawing_png(spec) if spec else None
    if png is None:
        raise HTTPException(404, "source drawing not uploaded on this server")
    return Response(png, media_type="image/png")


PREVIEW_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _preview_path(detail_id: str) -> Path | None:
    if not PREVIEW_ID.match(detail_id):
        return None
    return ai.library_dir() / "previews" / f"{detail_id}.jpg"


@app.get("/api/specs/{detail_id}/preview")
async def get_preview(detail_id: str):
    """Library card image: a render of our own 3D, never the source drawing,
    so it is served in public mode too."""
    p = _preview_path(detail_id)
    if p is None or not p.is_file():
        raise HTTPException(404, "no preview yet")
    return FileResponse(p, media_type="image/jpeg")


@app.post("/api/specs/{detail_id}/preview")
async def save_preview(detail_id: str, request: Request) -> dict:
    """The viewer posts one JPEG render after the build-up finishes."""
    p = _preview_path(detail_id)
    if p is None or _load_spec(detail_id) is None:
        raise HTTPException(404, "unknown detail")
    data = await request.body()
    if not data.startswith(b"\xff\xd8\xff") or len(data) > 1_000_000:
        raise HTTPException(400, "expected a JPEG under 1 MB")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return {"bytes": len(data)}


class ReviewRequest(BaseModel):
    edits: list[dict] = []  # [{name, value}]
    accept: list[str] = []
    labels: list[dict] = []  # [{part_id, label|null}]
    publish: bool = False
    reviewer: str = ""


@app.get("/api/specs/{detail_id}/review")
async def get_review(detail_id: str) -> dict:
    try:
        return review.get(detail_id)
    except KeyError:
        raise HTTPException(404, "only AI-generated details have a review")


@app.post("/api/specs/{detail_id}/review")
async def post_review(detail_id: str, req: ReviewRequest) -> dict:
    try:
        return review.apply(detail_id, req.edits, req.accept, req.labels,
                            req.publish, req.reviewer)
    except KeyError:
        raise HTTPException(404, "only AI-generated details have a review")
    except ValueError as e:
        raise HTTPException(400, str(e)[:300])


class GuideRequest(BaseModel):
    detail_id: str
    question: str
    history: list[dict] = []


def _run_guide(job_id: str, req: GuideRequest) -> None:
    job = GEN_JOBS[job_id]
    try:
        job["state"] = "running"
        job["progress"].append("thinking")
        job["result"] = guide.ask(_active_spec(req.detail_id), req.question, req.history)
        job.update(state="done", detail_id=req.detail_id)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        job.update(state="failed", error=str(e)[:500])


@app.post("/api/guide")
async def ask_guide(req: GuideRequest) -> dict:
    if not meta_client.configured():
        raise HTTPException(503, "AI not configured: set META_API_KEY in .env")
    if _load_spec(req.detail_id) is None:
        raise HTTPException(404, "unknown detail")
    job_id = uuid.uuid4().hex[:12]
    GEN_JOBS[job_id] = {"id": job_id, "state": "queued", "progress": [], "result": None,
                        "detail_id": None, "error": None, "gate_notes": []}
    threading.Thread(target=_run_guide, args=(job_id, req), daemon=True).start()
    return GEN_JOBS[job_id]


# ---------------------------------------------------------------- AI generation

GEN_JOBS: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    doc_id: str
    crop_id: str


@app.get("/api/ai/status")
async def ai_status() -> dict:
    return {"configured": meta_client.configured(),
            "model": meta_client.default_model(),
            "cad_sandbox": cad_sandbox.available(), "public": PUBLIC}


def _run_generate(job_id: str, req: GenerateRequest) -> None:
    job = GEN_JOBS[job_id]

    def progress(msg: str) -> None:
        job["progress"].append(msg)

    try:
        job["state"] = "running"
        rec = ai.generate(req.doc_id, req.crop_id, progress)
        job.update(state="done", detail_id=rec["detail_id"],
                   gate_notes=rec["gate_notes"], seconds=rec["seconds"])
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        job.update(state="failed", error=str(e)[:500])
        job["progress"].append(f"FAILED: {str(e)[:200]}")


@app.post("/api/generate")
async def start_generate(req: GenerateRequest) -> dict:
    if not get_document(req.doc_id):
        raise HTTPException(404, "unknown document")
    if not figure_path(req.doc_id, req.crop_id):
        raise HTTPException(404, "unknown figure")
    if not meta_client.configured():
        raise HTTPException(503, "AI not configured: set META_API_KEY in .env")
    job_id = uuid.uuid4().hex[:12]
    GEN_JOBS[job_id] = {"id": job_id, "state": "queued", "progress": [],
                        "detail_id": None, "error": None, "gate_notes": []}
    threading.Thread(target=_run_generate, args=(job_id, req), daemon=True).start()
    return GEN_JOBS[job_id]


class RemodelRequest(BaseModel):
    part_id: str
    brief: str | None = None


def _run_remodel(job_id: str, detail_id: str, req: RemodelRequest) -> None:
    job = GEN_JOBS[job_id]
    try:
        job["state"] = "running"
        ai.remodel_part(detail_id, req.part_id, req.brief, job["progress"].append)
        job.update(state="done", detail_id=detail_id)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        job.update(state="failed", error=str(e)[:500])
        job["progress"].append(f"FAILED: {str(e)[:200]}")


@app.post("/api/specs/{detail_id}/cad")
async def remodel_part(detail_id: str, req: RemodelRequest) -> dict:
    if not meta_client.configured() or not cad_sandbox.available():
        raise HTTPException(503, "AI or CAD sandbox not configured")
    job_id = uuid.uuid4().hex[:12]
    GEN_JOBS[job_id] = {"id": job_id, "state": "queued", "progress": [],
                        "detail_id": None, "error": None, "gate_notes": []}
    threading.Thread(target=_run_remodel, args=(job_id, detail_id, req), daemon=True).start()
    return GEN_JOBS[job_id]


class CritiqueRequest(BaseModel):
    renders: list[str]  # data:image/... URLs captured by the viewer


def _run_critique(job_id: str, detail_id: str, req: CritiqueRequest) -> None:
    job = GEN_JOBS[job_id]
    try:
        job["state"] = "running"
        job["result"] = ai.critique(detail_id, req.renders, job["progress"].append)
        job.update(state="done", detail_id=detail_id)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        job.update(state="failed", error=str(e)[:500])
        job["progress"].append(f"FAILED: {str(e)[:200]}")


@app.post("/api/specs/{detail_id}/critique")
async def critique(detail_id: str, req: CritiqueRequest) -> dict:
    if not meta_client.configured():
        raise HTTPException(503, "AI not configured: set META_API_KEY in .env")
    if not req.renders or len(req.renders) > 3 or sum(len(r) for r in req.renders) > 8_000_000:
        raise HTTPException(400, "send 1-3 renders under 8 MB total")
    job_id = uuid.uuid4().hex[:12]
    GEN_JOBS[job_id] = {"id": job_id, "state": "queued", "progress": [], "result": None,
                        "detail_id": None, "error": None, "gate_notes": []}
    threading.Thread(target=_run_critique, args=(job_id, detail_id, req), daemon=True).start()
    return GEN_JOBS[job_id]


def _run_locate(job_id: str, detail_id: str) -> None:
    job = GEN_JOBS[job_id]
    try:
        job["state"] = "running"
        job["result"] = ai.locate_on_drawing(detail_id, job["progress"].append)
        job.update(state="done", detail_id=detail_id)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        job.update(state="failed", error=str(e)[:500])


@app.post("/api/specs/{detail_id}/locate")
async def locate_parts(detail_id: str) -> dict:
    """SAM 3.1: find each part on the (upright) source drawing."""
    if not meta_client.configured():
        raise HTTPException(503, "AI not configured: set META_API_KEY in .env")
    job_id = uuid.uuid4().hex[:12]
    GEN_JOBS[job_id] = {"id": job_id, "state": "queued", "progress": [], "result": None,
                        "detail_id": None, "error": None, "gate_notes": []}
    threading.Thread(target=_run_locate, args=(job_id, detail_id), daemon=True).start()
    return GEN_JOBS[job_id]


@app.get("/api/generate/{job_id}")
async def get_generate(job_id: str) -> dict:
    if job_id not in GEN_JOBS:
        raise HTTPException(404, "job not found: it may have been lost when the "
                                 "server restarted (AI jobs live in memory); start it again")
    return GEN_JOBS[job_id]


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
