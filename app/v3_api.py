"""Version-pinned v3 jobs, events, evidence, review and artifact APIs."""
from __future__ import annotations

import asyncio
import json
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import database as d, meta_client
from .native_program import prepare
from .repository import Conflict, TERMINAL, canonical, row, sha, uid
from .runtime import ensure_worker, repository
from .verification_v3 import prepare_verification

router = APIRouter(prefix="/api")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Generate(Strict):
    figure_id: str
    manufacturer: str | None = None
    detail_id: str | None = None
    expected_current_version_id: str | None = None


class Revise(Strict):
    detail_id: str
    base_version_id: str
    requested_changes: str
    manufacturer: str | None = None


class Render(Strict):
    version_id: str
    views: list[Literal["iso", "front", "top"]] = ["iso", "front", "top"]
    profile: Literal["physical"] = "physical"


class Locate(Strict):
    version_id: str


class CAD(Strict):
    base_version_id: str
    part_key: str
    brief: str | None = None
    component_id: str | None = None


class Batch(Strict):
    dataset_artifact_id: str
    memory_mode: Literal["on", "off"] = "on"


class JobRequest(Strict):
    kind: Literal["generate_detail", "revise_detail", "cad_component", "render", "locate", "batch_eval"]
    payload: dict
    budget: dict[str, int] | None = None


class Dataset(Strict):
    figure_ids: list[str] = Field(min_length=1, max_length=100)
    annotations: dict = {}
    baseline: dict = {}


@router.post("/eval/datasets", status_code=201)
def create_dataset(body: Dataset):
    repo = repository()
    if len(set(body.figure_ids)) != len(body.figure_ids):
        raise HTTPException(422, "duplicate figures")
    try:
        evidence = []
        for ident in body.figure_ids:
            figure = repo.get(d.figures, ident)
            document = repo.get(d.documents, figure["document_id"])
            evidence.append({"figure_id": ident, "source_sha": document["sha256"], "image_sha": figure["artifact_id"]})
        manifest = {**body.model_dump(), "source_evidence": evidence}
        reference = repo.artifacts.put(canonical(manifest).encode(), "application/json")
        with d.transaction(repo.engine) as con:
            repo.artifact(con, reference)
        return {"dataset_artifact_id": reference["id"], "manifest_sha": sha(manifest), "figures": len(evidence)}
    except KeyError:
        raise HTTPException(404, "unknown source figure")


@router.post("/jobs", status_code=202)
def create_job(body: JobRequest, idempotency_key: str | None = Header(default=None, max_length=200)):
    repo = repository()
    schemas = {"generate_detail": Generate, "revise_detail": Revise, "cad_component": CAD,
               "render": Render, "locate": Locate, "batch_eval": Batch}
    try:
        payload = schemas[body.kind].model_validate(body.payload).model_dump()
        if body.kind == "generate_detail":
            repo.get(d.figures, payload["figure_id"])
            if payload.get("expected_current_version_id"):
                version = repo.get(d.versions, payload["expected_current_version_id"])
                source_matches = version["figure_id"] == payload["figure_id"]
                if version["format_version"] == "legacy-v2" and not version["figure_id"]:
                    figure = repo.get(d.figures,payload["figure_id"])
                    document = repo.get(d.documents,figure["document_id"])
                    source = version["spec"]["source"]
                    source_matches = source["sha256"] == document["sha256"] and source["crop_id"] == figure["crop_key"]
                if version["detail_id"] != payload["detail_id"] or not source_matches:
                    raise ValueError("source/detail does not match base version")
        elif body.kind == "batch_eval":
            dataset = json.loads(repo.artifacts.read(payload["dataset_artifact_id"]))
            for figure_id in dataset["figure_ids"]:
                repo.get(d.figures, figure_id)
        else:
            version = repo.get(d.versions, payload.get("base_version_id") or payload.get("version_id"))
            if body.kind == "revise_detail" and version["detail_id"] != payload["detail_id"]:
                raise ValueError("version belongs to another detail")
            if body.kind in ("revise_detail", "cad_component", "locate") and not version["figure_id"]:
                raise ValueError("source drawing is unavailable for this archived detail; upload it before starting a native revision")
            if body.kind == "cad_component" and bool(payload["brief"]) == bool(payload["component_id"]):
                raise ValueError("choose brief or component_id")
        if body.kind != "render" and not meta_client.configured():
            raise HTTPException(503, "AI not configured")
        result = repo.enqueue(body.kind, payload, idempotency_key, body.budget)
    except KeyError:
        raise HTTPException(404, "unknown source or version")
    except Conflict as error:
        raise HTTPException(409, str(error))
    except (ValueError, ValidationError) as error:
        raise HTTPException(422, str(error)[:500])
    ensure_worker()
    return job_result(result["id"])


@router.get("/jobs/{ident}")
def job_result(ident: str):
    repo = repository()
    try:
        job = repo.get(d.jobs, ident)
        run = repo.run(ident)
    except KeyError:
        raise HTTPException(404, "unknown job")
    events = repo.list(d.events, d.events.c.job_id == ident, order=d.events.c.seq)
    return {**job, "run_id": run["id"], "version_id": run["working_version_id"],
            "detail_id": run["detail_id"], "progress": [e["message"] or e["kind"] for e in events],
            "budget": {"limits": run["budget"], "consumed": run["consumed"], "reserved": run["reserved"]}}


@router.post("/jobs/{ident}/cancel")
def cancel_job(ident: str):
    try:
        repository().cancel(ident)
    except KeyError:
        raise HTTPException(404, "unknown job")
    return job_result(ident)


@router.get("/jobs/{ident}/events")
async def job_events(ident: str, request: Request, last_event_id: int = Header(default=0, ge=0)):
    repo = repository()
    try:
        repo.get(d.jobs, ident)
    except KeyError:
        raise HTTPException(404, "unknown job")
    async def stream():
        last = last_event_id
        while not await request.is_disconnected():
            found = repo.list(d.events, d.events.c.job_id == ident, d.events.c.seq > last, order=d.events.c.seq)
            for event in found:
                last = event["seq"]
                yield f"id: {last}\nevent: progress\ndata: {json.dumps(event)}\n\n"
            if repo.get(d.jobs, ident)["state"] in TERMINAL:
                # A terminal commit can race the preceding read: drain once more.
                remaining = repo.list(d.events, d.events.c.job_id == ident, d.events.c.seq > last, order=d.events.c.seq)
                for event in remaining:
                    yield f"id: {event['seq']}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                yield f"event: terminal\ndata: {json.dumps(job_result(ident))}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/runs/{ident}/steps")
def run_steps(ident: str, after: int = 0, limit: int = 50):
    repo = repository()
    try:
        repo.get(d.runs, ident)
    except KeyError:
        raise HTTPException(404, "unknown run")
    rows = repo.list(d.steps, d.steps.c.run_id == ident, d.steps.c.n > after, order=d.steps.c.n)
    selected = rows[:max(1, min(limit, 100))]
    return {"steps": selected, "next_after": selected[-1]["n"] if selected else after}


@router.get("/details/{ident}/versions")
def detail_versions(ident: str):
    repo = repository()
    try:
        detail = repo.get(d.details, ident)
    except KeyError:
        raise HTTPException(404, "unknown detail")
    return {"detail": detail, "versions": repo.list(d.versions, d.versions.c.detail_id == ident, order=d.versions.c.n)}


@router.get("/versions/{ident}")
def version(ident: str):
    try:
        return repository().get(d.versions, ident)
    except KeyError:
        raise HTTPException(404, "unknown version")


@router.get("/versions/{ident}/scene")
def scene(ident: str):
    repo = repository()
    try:
        reports = repo.list(d.verifications, d.verifications.c.version_id == ident, order=d.verifications.c.created_at)
        if reports and reports[-1]["quality"].get("scene_artifact_id"):
            result = json.loads(repo.artifacts.read(reports[-1]["quality"]["scene_artifact_id"]))
            result["version_id"], result["manifest_sha"] = ident, reports[-1]["manifest_sha"]
            return result
        result = prepare_verification(repo, ident)
        return {**result["scene"], "version_id": ident}
    except KeyError:
        raise HTTPException(404, "unknown version")


@router.get("/artifacts/{ident}")
def artifact(ident: str):
    repo = repository()
    try:
        ref = repo.get(d.artifacts, ident)
        return Response(repo.artifacts.read(ident), media_type=ref["media_type"],
                        headers={"ETag": f'"{ident}"', "Cache-Control": "private, immutable"})
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, "unknown artifact")
    except ValueError as error:
        raise HTTPException(409, str(error))


class ReviewRequest(Strict):
    reviewer: str = Field(min_length=1, max_length=80)
    action: Literal["accept", "reject", "resolve", "correct", "activate_memory"]
    data: dict = {}


@router.get("/versions/{ident}/reviews")
def get_reviews(ident: str):
    version(ident)
    return {"reviews": repository().list(d.reviews, d.reviews.c.version_id == ident, order=d.reviews.c.seq)}


@router.post("/versions/{ident}/reviews")
def write_review(ident: str, body: ReviewRequest):
    repo = repository()
    if not body.reviewer.strip():
        raise HTTPException(422, "reviewer's name is required")
    try:
        original = repo.get(d.versions, ident)
        revised = None
        if body.action == "correct":
            if original["format_version"] != "3.0":
                raise ValueError("convert archived detail to a native v3 program before editing")
            read = repo.get(d.reads, original["drawing_read_id"])
            program, spec, _ = prepare(body.data["program"], original["program"]["source"], original["detail_id"],
                                      read["response"], original["program"], human=True)
            revised = program.model_dump(mode="json"), spec.model_dump(mode="json")
        with d.transaction(repo.engine) as con:
            detail = row(con, d.details, original["detail_id"], True)
            if revised and detail["current_version_id"] != ident:
                raise Conflict("detail changed; reload before correcting this version")
            target = ident
            if revised:
                result = repo.insert_version(con, detail_id=original["detail_id"], expected_parent=ident,
                            program=revised[0], spec=revised[1], bindings={}, figure_id=original["figure_id"],
                            read_id=original["drawing_read_id"], created_by=body.reviewer, origin="user-revised:v3")
                target = result["version_id"]
            review_id = repo.review_event(con, target, body.reviewer.strip(), body.action, body.data)
            if body.action == "activate_memory":
                memory = row(con, d.memories, body.data["memory_id"])
                if memory["source_version_id"] != ident:
                    raise ValueError("memory belongs to another source version")
                con.execute(d.memories.update().where(d.memories.c.id == memory["id"]).values(
                            state="active", source_event_id=memory["source_event_id"] or review_id))
            if revised:
                # A correction produces a proposed scoped lesson, never a new drawing value.
                con.execute(d.memories.insert().values(id=uid(), scope="type", scope_key=original["program"].get("kind", "assembly"),
                            state="proposed", kind="correction", text=body.data.get("lesson", "Reviewer corrected this detail; inspect the linked version diff."),
                            tags=[], source_event_id=review_id, source_version_id=target, created_at=d.now(con)))
        return {"review_id": review_id, "version_id": target}
    except KeyError:
        raise HTTPException(404, "unknown version or memory")
    except Conflict as error:
        raise HTTPException(409,str(error))
    except ValueError as error:
        raise HTTPException(422, str(error)[:500])


class Publish(Strict):
    version_id: str
    expected_published_version_id: str | None


@router.post("/details/{ident}/publish")
def publish(ident: str, body: Publish):
    repo = repository()
    try:
        with d.transaction(repo.engine) as con:
            detail = row(con, d.details, ident, True)
            candidate = row(con, d.versions, body.version_id)
            if candidate["detail_id"] != ident or detail["published_version_id"] != body.expected_published_version_id:
                raise Conflict("publication pointer changed or version belongs to another detail")
            reviews = con.execute(sa.select(d.reviews).where(d.reviews.c.version_id == body.version_id,
                                  d.reviews.c.action.in_(["accept", "reject"])).order_by(d.reviews.c.seq)).mappings().all()
            if not reviews or reviews[-1]["action"] != "accept":
                raise Conflict("this version needs human acceptance")
            critic = con.execute(sa.select(d.critics).where(d.critics.c.version_id == body.version_id)
                                 .order_by(d.critics.c.created_at.desc())).mappings().first()
            if not critic or repo.completion_errors(con, body.version_id, critic["verification_id"], critic["id"]):
                raise Conflict("this version has incomplete verification or critique")
            con.execute(d.details.update().where(d.details.c.id == ident).values(published_version_id=body.version_id))
        return {"published_version_id": body.version_id}
    except KeyError:
        raise HTTPException(404, "unknown detail or version")
    except Conflict as error:
        raise HTTPException(409, str(error))


@router.get("/components")
def components():
    repo = repository()
    return {"components": repo.list(d.components)}


class ComponentReview(Strict):
    reviewer: str = Field(min_length=1, max_length=80)
    component_build_id: str
    action: Literal["accept", "reject"]
    data: dict = {}


@router.post("/components/{ident}/reviews")
def component_review(ident: str, body: ComponentReview):
    repo = repository()
    if not body.reviewer.strip():
        raise HTTPException(422, "reviewer's name is required")
    try:
        with d.transaction(repo.engine) as con:
            row(con, d.components, ident, True)
            build = row(con, d.component_builds, body.component_build_id)
            if build["component_id"] != ident:
                raise ValueError("build belongs to another component revision")
            review_id = uid()
            con.execute(d.component_reviews.insert().values(id=review_id, component_id=ident,
                        component_build_id=build["id"], reviewer=body.reviewer.strip(), action=body.action,
                        data=body.data, at=d.now(con)))
        return {"review_id": review_id, "component_id": ident, "component_build_id": build["id"]}
    except KeyError:
        raise HTTPException(404, "unknown component/build")
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.get("/memories")
def memories(version_id: str | None = None):
    repo = repository()
    conditions = [d.memories.c.source_version_id == version_id] if version_id else []
    return {"memories": repo.list(d.memories, *conditions, order=d.memories.c.created_at)}


class Convention(Strict):
    version_id: str
    reviewer: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=5000)
    scope: Literal["global", "manufacturer", "type", "detail"]
    scope_key: str
    tags: list[str] = []


@router.post("/memories", status_code=201)
def add_convention(body: Convention):
    repo = repository()
    if not body.reviewer.strip():
        raise HTTPException(422, "reviewer's name is required")
    try:
        with d.transaction(repo.engine) as con:
            source = row(con, d.versions, body.version_id)
            if body.scope == "detail" and body.scope_key != source["detail_id"]:
                raise ValueError("detail scope does not match source version")
            event_id = repo.review_event(con, body.version_id, body.reviewer.strip(), "convention", {"text": body.text})
            ident = uid()
            con.execute(d.memories.insert().values(id=ident, scope=body.scope, scope_key=body.scope_key,
                        state="active", kind="convention", text=body.text, tags=body.tags, source_event_id=event_id,
                        source_version_id=body.version_id, created_at=d.now(con)))
        return {"memory_id": ident, "state": "active"}
    except KeyError:
        raise HTTPException(404, "unknown source version")
    except ValueError as error:
        raise HTTPException(422, str(error))
