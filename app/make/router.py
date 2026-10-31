"""HTTP API for 3D Make. Settings beyond the model list are Day 7.

Jobs are queued and run on the make worker thread. Event streams replay the
job row until it is done or failed. Responses never include an API key.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app import store
from app.make import db
from app.make.agent import merge_overrides
from app.make.attachments import AttachmentError, store_upload
from app.make.llm import list_models
from app.make.queue import enqueue
from app.make.redact import redact

router = APIRouter(prefix="/api/make")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectIn(Strict):
    title: str | None = None


class MessageIn(Strict):
    text: str = ""
    attachments: list[str] = Field(default_factory=list)
    mode: Literal["create", "feedback"] = "create"
    use_blender: bool = False
    profile: str | None = None


class OverrideIn(Strict):
    name: str
    location: list[float] | None = None
    rotation_deg: list[float] | None = None
    dims: dict[str, float] | None = None


class OverridesIn(Strict):
    overrides: list[OverrideIn]


class RenderIn(Strict):
    quality: Literal["auto", "draft", "high"] = "draft"


@router.post("/projects")
def create_project(body: ProjectIn | None = None):
    title = (body.title if body and body.title else "Untitled").strip() or "Untitled"
    project_id = uuid.uuid4().hex[:12]
    db.create_project(title[:80], project_id)
    return {"id": project_id, "title": title[:80]}


@router.get("/projects")
def get_projects():
    return [_project_brief(row) for row in db.list_projects()]


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(404, "unknown project")
    return {
        "project": _project_brief(project),
        "messages": [_message(row) for row in db.list_messages(project_id)],
        "versions": [_version(row) for row in db.list_versions(project_id)],
    }


@router.post("/projects/{project_id}/attachments")
async def add_attachment(project_id: str, file: UploadFile = File(...), page: int = Query(default=1, ge=1)):
    _require_project(project_id)
    data = await file.read()
    try:
        saved = store_upload(project_id, uuid.uuid4().hex, data, page=page)
    except AttachmentError as exc:
        status = 413 if "10 MB" in str(exc) else 400
        raise HTTPException(status, redact(str(exc))) from exc
    return saved


@router.post("/projects/{project_id}/messages")
def add_message(project_id: str, body: MessageIn):
    _require_project(project_id)
    mode = "feedback" if body.mode == "feedback" else "prompt"
    if mode == "feedback" and db.latest_version(project_id) is None:
        raise HTTPException(409, "feedback needs a version")
    if not body.text.strip() and not body.attachments:
        raise HTTPException(400, "a message needs text or an attachment")
    for ident in body.attachments:
        folder = store.var_root() / "make" / project_id / "attachments" / f"{ident}.png"
        if not folder.is_file():
            raise HTTPException(400, "unknown attachment")
    text = redact(body.text)
    job_id = enqueue(project_id, "generate", {
        "mode": mode,
        "text": text,
        "attachments": list(body.attachments),
        "profile": body.profile,
        "use_blender": bool(body.use_blender),
    })
    db.add_message(uuid.uuid4().hex, project_id, "user", text, job_id=job_id, attachments=list(body.attachments))
    return {"job_id": job_id}


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str):
    if db.get_job(job_id) is None:
        raise HTTPException(404, "unknown job")
    return StreamingResponse(_stream(job_id), media_type="text/event-stream")


@router.get("/projects/{project_id}/versions/{number}/scene.glb")
def scene_glb(project_id: str, number: int):
    return _artifact(project_id, number, "scene.glb")


@router.get("/projects/{project_id}/versions/{number}/tree.json")
def scene_tree(project_id: str, number: int):
    version = _require_version(project_id, number)
    if not version.get("tree_json"):
        raise HTTPException(404, "tree.json is not available")
    return json.loads(version["tree_json"])


@router.get("/projects/{project_id}/versions/{number}/preview.png")
def scene_preview(project_id: str, number: int):
    return _artifact(project_id, number, "preview.png")


@router.patch("/projects/{project_id}/versions/{number}/overrides")
def patch_overrides(project_id: str, number: int, body: OverridesIn):
    version = _require_version(project_id, number)
    if version.get("status") != "done" or not version.get("script"):
        raise HTTPException(409, "that version has no script to rebuild")
    overrides = {}
    for item in body.overrides:
        spec = {}
        if item.location is not None:
            if len(item.location) != 3:
                raise HTTPException(400, "location needs 3 values")
            spec["location"] = item.location
        if item.rotation_deg is not None:
            if len(item.rotation_deg) != 3:
                raise HTTPException(400, "rotation_deg needs 3 values")
            spec["rotation_deg"] = item.rotation_deg
        if item.dims:
            spec["dims"] = item.dims
        if spec:
            overrides[item.name] = spec
    if not overrides:
        raise HTTPException(400, "overrides are empty")
    stored = version.get("overrides")
    if isinstance(stored, str):
        try:
            stored = json.loads(stored or "{}")
        except json.JSONDecodeError:
            stored = {}
    overrides = merge_overrides(stored if isinstance(stored, dict) else {}, overrides)
    job_id = enqueue(project_id, "rebuild", {"version_n": number, "overrides": overrides})
    return {"job_id": job_id}


@router.post("/projects/{project_id}/versions/{number}/render")
def render_version(project_id: str, number: int, body: RenderIn | None = None):
    version = _require_version(project_id, number)
    if version.get("status") != "done" or not version.get("script"):
        raise HTTPException(409, "that version has no script to render")
    quality = "draft" if body is None or body.quality == "auto" else body.quality
    job_id = enqueue(project_id, "render", {"version_n": number, "quality": quality})
    return {"job_id": job_id}


@router.get("/settings/models")
def settings_models():
    """Model chip data. Booleans and names only; Day 7 adds the rest of settings."""
    models = []
    for item in list_models():
        models.append({
            "id": item["id"],
            "label": item["label"],
            "model": item["model"],
            "ready": bool(item["ready"]),
            "caps": {
                "vision": bool(item["caps"].get("vision")),
                "tools": item["caps"].get("tools") is not False,
            },
        })
    return models


def _require_project(project_id: str) -> dict:
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(404, "unknown project")
    return project


def _require_version(project_id: str, number: int) -> dict:
    _require_project(project_id)
    version = db.get_version(project_id, number)
    if version is None:
        raise HTTPException(404, "unknown version")
    return version


def _artifact(project_id: str, number: int, name: str):
    version = _require_version(project_id, number)
    column = {"scene.glb": "glb_path", "preview.png": "preview_path"}[name]
    rel = version.get(column)
    if not rel:
        raise HTTPException(404, f"{name} is not available")
    root = (store.var_root() / "make").resolve()
    path = (store.var_root() / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, f"{name} is not available")
    media = {"scene.glb": "model/gltf-binary", "preview.png": "image/png"}[name]
    return FileResponse(path, media_type=media, filename=name)


def _stream(job_id: str):
    seen = 0
    last_state = None
    last_attempt = None
    while True:
        job = db.get_job(job_id)
        if job is None:
            yield _sse("error", {"message": "unknown job"})
            return
        state = job["state"]
        attempt = job["attempt"] or 0
        if state != last_state:
            last_state = state
            yield _sse("status", {"state": state, "attempt": attempt})
        if attempt != last_attempt:
            last_attempt = attempt
            yield _sse("attempt", {"attempt": attempt})
        events = _load_events(job.get("events"))
        while seen < len(events):
            event = events[seen]
            seen += 1
            if event.get("kind") == "request":
                continue
            yield _sse("log", _log(event))
        if state in ("done", "failed"):
            result = next((event for event in reversed(events) if event.get("kind") == "result"), {})
            if state == "done":
                yield _sse("done", {"version": result.get("version")})
            else:
                yield _sse("error", {"message": redact(str(result.get("error") or "failed"))[:800]})
            return
        time.sleep(0.25)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _log(event: dict) -> dict:
    message = event.get("error_tail") or event.get("error") or event.get("via") or event.get("kind") or ""
    return {
        "attempt": event.get("attempt"),
        "via": event.get("via") or "",
        "kind": event.get("kind") or "",
        "message": redact(str(message))[:400],
    }


def _load_events(raw) -> list:
    try:
        events = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return events if isinstance(events, list) else []


def _project_brief(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "active_version": row["active_version"],
        "created": row["created"],
        "updated": row["updated"],
    }


def _message(row: dict) -> dict:
    try:
        attachments = json.loads(row.get("attachments") or "[]")
    except json.JSONDecodeError:
        attachments = []
    return {
        "id": row["id"],
        "role": row["role"],
        "text": row["text"],
        "attachments": attachments,
        "job_id": row["job_id"],
        "created": row["created"],
    }


def _version(row: dict) -> dict:
    return {
        "n": row["n"],
        "parent_n": row["parent_n"],
        "source": row["source"],
        "status": row["status"],
        "profile": row["profile"],
        "model": row["model"],
        "created": row["created"],
        "error": row["error"],
    }
