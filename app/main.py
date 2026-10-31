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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cad_sandbox, guide, library, meta_client
from . import generate as ai
from .geometry import build_assembly, export_glb, export_scene_json
from .seed import SEEDS
from .spec import DetailSpec, Provenance
from .store import (build_dir, db, figure_path, find_document_by_sha,
                    get_document, store_pdf, var_root)
from .verify import summarize, verify_all

@asynccontextmanager
async def lifespan(_app):
    # A restarted server resumes queued and interrupted jobs straight away
    # (embedded worker only; SETOUT_WORKER=external leaves it to app.worker).
    if os.environ.get("SETOUT_PUBLIC") != "1":
        from .runtime import ensure_worker
        ensure_worker()
        if os.environ.get("SETOUT_MAKE_WORKER", os.environ.get("SETOUT_WORKER", "embedded")) != "external":
            from .make.queue import ensure_worker as ensure_make_worker
            ensure_make_worker()
    yield


app = FastAPI(title="Setout", version="0.1.0", lifespan=lifespan)
from .v3_api import router as v3_router
from .make.router import router as make_router
app.include_router(v3_router)
app.include_router(make_router)

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
        if path.startswith(("/api/jobs", "/api/runs", "/api/artifacts", "/api/versions", "/api/details",
                            "/api/components", "/api/memories", "/api/eval/datasets")):
            return JSONResponse({"detail": "private generation and source evidence"}, status_code=403)
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
    # Off the event loop: rendering a sheet set takes long enough to stall every
    # other request and job stream.
    return _with_detail_ids(await run_in_threadpool(store_pdf, file.filename or "upload.pdf", data))


def _with_detail_ids(doc: dict) -> dict:
    """Each drawing's AI detail id, so the workbench can find its running job."""
    return {**doc, "figures": [{**f, "detail_id": ai_detail_id(doc["sha256"], f["crop_id"])}
                               for f in doc["figures"]]}


@app.get("/api/documents/{doc_id}")
async def document(doc_id: str) -> dict:
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(404, "unknown document")
    return _with_detail_ids(doc)


@app.get("/api/documents/{doc_id}/figures/{crop_id}")
async def figure(doc_id: str, crop_id: str):
    p = figure_path(doc_id, crop_id)
    if not p:
        raise HTTPException(404, "unknown figure")
    media = "image/jpeg" if p.suffix == ".jpg" else "image/png"
    return FileResponse(p, media_type=media)


# ---------------------------------------------------------------- specs

# Two kinds of detail. Seeds are hand-transcribed reference specs in code,
# corrected through PATCH /api/specs. Everything AI-made is an immutable
# version in the database, changed only through jobs and reviews.
SPECS = SEEDS


def _load_spec(detail_id: str) -> DetailSpec | None:
    if detail_id in SPECS:
        return SPECS[detail_id]()
    native = _database_version(detail_id)
    return DetailSpec.model_validate(native["spec"]) if native else None


def _database_version(detail_id: str) -> dict | None:
    from . import database as tables
    from .runtime import repository
    if detail_id in SPECS:
        return None  # an older import of a seed never shadows the code
    repo = repository()
    try:
        detail = repo.get(tables.details, detail_id)
        ident = detail["published_version_id"] if PUBLIC else detail["current_version_id"]
        return repo.get(tables.versions, ident) if ident else None
    except KeyError:
        return None


@app.get("/api/specs")
async def list_specs() -> dict:
    seeds = [{"detail_id": k, "title": (s := v()).title,
              "crop_id": s.source.crop_id, "sha256": s.source.sha256,
              "origin": s.origin} for k, v in SPECS.items()]
    entries = {row["detail_id"]: row for row in seeds}
    from . import database as tables
    from .runtime import repository
    for detail in repository().list(tables.details):
        version = _database_version(detail["id"])
        if version:
            spec = DetailSpec.model_validate(version["spec"])
            entries[detail["id"]] = {"detail_id": detail["id"], "title": spec.title, "origin": spec.origin,
                                      "crop_id": spec.source.crop_id, "sha256": spec.source.sha256,
                                      "version_id": version["id"]}
    return {"specs": list(entries.values())}


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
    if detail_id not in SPECS:
        if _database_version(detail_id):
            raise HTTPException(409, "AI-made details are corrected in Review on the detail page; "
                                     "each correction becomes a new version")
        raise HTTPException(404, "unknown detail")
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
    """A seed with the user's saved corrections, or an AI detail's current version."""
    override = var_root() / f"spec-{detail_id}.json"
    if detail_id in SPECS and override.exists():
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
    version_id: str | None = None


def _run_build(job_id: str, req: BuildRequest, doc_sha: str | None) -> None:
    progress: list[str] = []
    try:
        _set_build(job_id, "running", progress)
        progress.append("validate spec")
        _set_build(job_id, "running", progress)
        native = None
        if req.version_id:
            from .runtime import repository
            from . import database as tables
            native = repository().get(tables.versions, req.version_id)
        spec = DetailSpec.model_validate(native["spec"]) if native else _active_spec(req.detail_id)
        progress.append("generate meshes")
        _set_build(job_id, "running", progress)
        if native:
            from .runtime import repository
            from .verification_v3 import prepare_verification
            from .geometry import Mesh
            from .spec import CheckResult
            built = prepare_verification(repository(), native["id"])
            meshes = {solid["id"]: Mesh(solid["id"], positions=solid["tris"], color=solid["color"])
                      for solid in built["scene"]["solids"]}
            results = [CheckResult.model_validate(c) for c in built["checks"]]
            spec.verification = results
        else:
            meshes = build_assembly(spec, use_display=req.use_display_thickness)
        progress.append("verify")
        _set_build(job_id, "running", progress)
        if not native:
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
    native = _database_version(req.detail_id)
    if native:
        from .runtime import repository
        from . import database as tables
        if req.version_id:
            try:
                native = repository().get(tables.versions, req.version_id)
            except KeyError:
                raise HTTPException(404, "unknown version")
            if native["detail_id"] != req.detail_id:
                raise HTTPException(409, "version belongs to another detail")
        req = req.model_copy(update={"version_id": native["id"]})
    elif req.version_id:
        raise HTTPException(409, "version does not belong to this detail")
    doc_sha = None
    if req.doc_id:
        doc = get_document(req.doc_id)
        if not doc:
            raise HTTPException(404, "unknown document")
        doc_sha = doc["sha256"]
        if native and doc_sha != native["spec"]["source"]["sha256"]:
            raise HTTPException(409, "uploaded document differs from this version's source")
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
    indexed = {entry["detail_id"]: entry for entry in entries}
    from . import database as tables
    from .runtime import repository
    from .v3_api import drawing_regions
    with repository().engine.connect() as con:
        active = dict(con.execute(sa.select(tables.runs.c.detail_id, tables.jobs.c.id)
                      .join(tables.jobs, tables.jobs.c.id == tables.runs.c.job_id)
                      .where(tables.jobs.c.state.in_(("queued", "running")))).all())
    for detail in repository().list(tables.details):
        version = _database_version(detail["id"])
        if version:
            spec = DetailSpec.model_validate(version["spec"])
            entry = library.entry(spec, "ai" if spec.origin.startswith(("ai-", "user-")) else "human")
            entry["version_id"] = version["id"]
            entry["located"] = len(drawing_regions(repository(), version))
            entry["generating"] = active.get(detail["id"])
            if entry["made_by"] == "ai":
                # Versions are immutable: publishing moves the detail's pointer.
                entry["review"] = "reviewed" if detail["published_version_id"] == version["id"] else "draft"
            indexed[detail["id"]] = entry
    entries = list(indexed.values())
    return {"details": entries, "components": library.component_index(entries)}


@app.get("/api/specs/{detail_id}/scene")
async def get_scene(detail_id: str) -> dict:
    """Build + verify on demand (fast: pure-Python meshes) for the viewer page."""
    native = _database_version(detail_id)
    if native:
        from .v3_api import scene
        return scene(native["id"])
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
    native = _database_version(detail_id)
    if native and native["figure_id"]:
        from . import database as tables, sam
        from .runtime import repository
        repo = repository()
        figure = repo.get(tables.figures, native["figure_id"])
        # Archived v2 details may have a figure but no saved read: use the
        # rotation their spec recorded.
        rotation = (repo.get(tables.reads, native["drawing_read_id"])["response"].get("rotation_deg", 0)
                    if native["drawing_read_id"] else native["spec"].get("context", {}).get("drawing_rotation_deg") or 0)
        png = sam.upright(repo.artifacts.read(figure["artifact_id"]), int(rotation))
        return Response(png, media_type="image/png")
    spec = _load_spec(detail_id)
    png = ai.drawing_png(spec) if spec else None
    if png is None:
        raise HTTPException(404, "source drawing not uploaded on this server")
    return Response(png, media_type="image/png")


PREVIEW_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")
PREVIEW_MAX = 1_000_000


def _preview_path(detail_id: str) -> Path | None:
    if not PREVIEW_ID.fullmatch(detail_id):
        return None
    return ai.library_dir() / "previews" / f"{detail_id}.jpg"


def _spec_file(detail_id: str) -> Path | None:
    """The file whose change means the detail's geometry may have changed."""
    if detail_id in SPECS:
        override = var_root() / f"spec-{detail_id}.json"
        return override if override.is_file() else None
    f = ai.library_dir() / f"{detail_id}.json"
    return f if f.is_file() else None


@app.get("/api/specs/{detail_id}/preview")
async def get_preview(detail_id: str):
    """Library card image: a render of our own 3D, never the source drawing,
    so it is served in public mode too. One older than the detail counts as
    missing, so the next view of the detail takes a new one."""
    native = _database_version(detail_id)
    if native:
        from . import database as tables
        from .runtime import repository
        repo = repository()
        # The newest clean iso render of this version, else of the nearest
        # earlier version: a reviewer's edit makes a version with no render yet.
        version = native
        while version:
            renders = repo.list(tables.renders, tables.renders.c.version_id == version["id"],
                                tables.renders.c.view == "iso", tables.renders.c.tags == False,
                                order=tables.renders.c.created_at)
            if renders:
                return Response(repo.artifacts.read(renders[-1]["artifact_id"]), media_type="image/jpeg",
                                headers={"Cache-Control": "no-cache"})
            version = repo.get(tables.versions, version["parent_version_id"]) if version["parent_version_id"] else None
    p = _preview_path(detail_id)
    if p is None or not p.is_file():
        raise HTTPException(404, "no preview yet")
    src = _spec_file(detail_id)
    if src is not None and src.stat().st_mtime > p.stat().st_mtime:
        raise HTTPException(404, "preview out of date")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.post("/api/specs/{detail_id}/preview")
async def save_preview(detail_id: str, request: Request) -> dict:
    """The viewer posts one JPEG render of the detail as built."""
    native = _database_version(detail_id)
    if native and native["format_version"] == "3.0":
        raise HTTPException(409, "versioned card images are rendered by the worker")
    p = _preview_path(detail_id)
    if p is None or not (detail_id in SPECS or _spec_file(detail_id)):
        raise HTTPException(404, "unknown detail")
    if int(request.headers.get("content-length") or 0) > PREVIEW_MAX:
        raise HTTPException(413, "expected a JPEG under 1 MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > PREVIEW_MAX:
            raise HTTPException(413, "expected a JPEG under 1 MB")
    if not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "expected a JPEG under 1 MB")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)  # a reader never sees half a file
    return {"bytes": len(data)}


class ReviewRequest(BaseModel):
    edits: list[dict] = []  # [{name, value}]
    accept: list[str] = []
    labels: list[dict] = []  # [{part_id, label|null}]
    publish: bool = False
    reviewer: str = ""


@app.get("/api/specs/{detail_id}/review")
async def get_review(detail_id: str) -> dict:
    native = _database_version(detail_id)
    if not native:
        raise HTTPException(404, "only AI-generated details have a review")
    from .runtime import repository
    from . import review_v3
    return review_v3.get(repository(), native)


@app.post("/api/specs/{detail_id}/review")
async def post_review(detail_id: str, req: ReviewRequest) -> dict:
    native = _database_version(detail_id)
    if not native:
        raise HTTPException(404, "only AI-generated details have a review")
    from .repository import Conflict
    from .runtime import repository
    from . import review_v3
    try:
        return review_v3.apply(repository(), native, req.edits, req.accept, req.labels, req.publish, req.reviewer)
    except KeyError:
        raise HTTPException(404, "unknown version")
    except Conflict as e:
        raise HTTPException(409, str(e)[:300])
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

# Only the guide lives here: it is a short request (PLAN_V3). Every other AI
# action is a persistent job in the database (app/v3_api.py, app/worker.py).
GEN_JOBS: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    doc_id: str
    crop_id: str


@app.get("/api/ai/status")
async def ai_status() -> dict:
    return {"configured": meta_client.configured(),
            "model": meta_client.default_model(),
            "cad_sandbox": cad_sandbox.available(), "public": PUBLIC}


def ai_detail_id(sha256: str, crop_id: str) -> str:
    """One AI detail per drawing (the v2 rule, so imported v2 details match)."""
    return f"ai-{sha256[:8]}-{crop_id.lower()}"


@app.post("/api/generate")
async def start_generate(req: GenerateRequest) -> dict:
    """Generate a drawing's detail. A drawing that already has one gets a new
    version of it, built from its current version, not a second detail."""
    doc = get_document(req.doc_id)
    if not doc:
        raise HTTPException(404, "unknown document")
    if not figure_path(req.doc_id, req.crop_id):
        raise HTTPException(404, "unknown figure")
    from . import database as tables
    from .runtime import repository
    from .v3_api import submit
    from .versioning import register_figure
    repo = repository()
    detail_id = ai_detail_id(doc["sha256"], req.crop_id)
    payload = {"figure_id": register_figure(repo, req.doc_id, req.crop_id), "detail_id": detail_id}
    try:
        current = repo.get(tables.details, detail_id)["current_version_id"]
    except KeyError:
        current = None
    if current:
        payload["expected_current_version_id"] = current
    job = submit("generate_detail", payload)
    return _persistent_generation(job["id"])


def _ai_version(detail_id: str) -> dict:
    """The current version an AI action works on; seeds have none."""
    if detail_id in SPECS:
        raise HTTPException(409, "this is a hand-transcribed reference detail; AI actions apply to AI-made details")
    native = _database_version(detail_id)
    if not native:
        raise HTTPException(404, "unknown detail")
    return native


class RemodelRequest(BaseModel):
    part_id: str
    brief: str | None = None


@app.post("/api/specs/{detail_id}/cad")
async def remodel_part(detail_id: str, req: RemodelRequest) -> dict:
    if not cad_sandbox.available():
        raise HTTPException(503, "CAD sandbox not configured")
    from .v3_api import submit
    native = _ai_version(detail_id)
    job = submit("cad_component", {"base_version_id": native["id"], "part_key": req.part_id,
                 "brief": req.brief or "Rebuild this part faithfully from its named parameters"})
    return _persistent_generation(job["id"])


class CritiqueRequest(BaseModel):
    requested_changes: str | None = None


@app.post("/api/specs/{detail_id}/critique")
async def critique(detail_id: str, req: CritiqueRequest | None = None) -> dict:
    """AI self-check: the worker verifies, renders the version on the server,
    asks the independent critic and repairs concrete issues in a new version."""
    from .v3_api import submit
    native = _ai_version(detail_id)
    job = submit("revise_detail", {"detail_id": detail_id, "base_version_id": native["id"],
                 "requested_changes": (req and req.requested_changes) or
                 "Verify, render and independently critique this version. Fix concrete issues."})
    return _persistent_generation(job["id"])


@app.post("/api/specs/{detail_id}/locate")
async def locate_parts(detail_id: str) -> dict:
    """SAM 3.1: find each part on the (upright) source drawing."""
    from .v3_api import submit
    job = submit("locate", {"version_id": _ai_version(detail_id)["id"]})
    return _persistent_generation(job["id"])


@app.get("/api/generate/{job_id}")
async def get_generate(job_id: str) -> dict:
    try:
        return _persistent_generation(job_id)
    except KeyError:
        pass
    if job_id not in GEN_JOBS:
        raise HTTPException(404, "job not found: guide answers are kept in memory and "
                                 "are lost when the server restarts; ask again")
    return GEN_JOBS[job_id]


def _persistent_generation(job_id: str) -> dict:
    """A job in the shape the workbench polls. needs_review counts as done only
    when the run made a new version and its tool didn't fail: a draft to open.
    A recheck, CAD or locate job that changed nothing is reported as failed."""
    from . import database as tables
    from .runtime import repository
    from .v3_api import job_result
    repo = repository()
    repo.get(tables.jobs, job_id)
    job = job_result(job_id)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    problem = result.get("error") or (f"CAD failed: {result['placeholder']}" if result.get("placeholder") else None)
    made = bool(job["version_id"]) and job["version_id"] != repo.run(job_id)["base_version_id"]
    state = {"succeeded": "done", "needs_review": "done" if made and not problem else "failed",
             "cancelled": "failed"}.get(job["state"], job["state"])
    error = job.get("error") or problem
    if state == "failed" and not error:
        error = "; ".join(result.get("open_issues") or []) or job["state"].replace("_", " ")
    return {**job, "state": state, "native_state": job["state"], "gate_notes": [],
            "continued_by": result.get("continued_by"),
            "error": error.get("message", error.get("code")) if isinstance(error, dict) else error}


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
