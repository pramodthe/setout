"""Version-pinned v3 jobs, events, evidence, review and artifact APIs."""
from __future__ import annotations

import asyncio
import copy
import json
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import completion, database as d, meta_client, ontology
from .lessons import correction_lessons
from .native_program import kept_bindings, prepare
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
    continuation: int = Field(default=0, ge=0, le=10)


class Revise(Strict):
    detail_id: str
    base_version_id: str
    requested_changes: str
    manufacturer: str | None = None
    continuation: int = Field(default=0, ge=0, le=10)  # n-th automatic continuation


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
    return submit(body.kind, body.payload, idempotency_key, body.budget)


def submit(kind, raw_payload, idempotency_key=None, budget=None):
    """Validate and enqueue one job. Every AI action in the app goes through here."""
    repo = repository()
    schemas = {"generate_detail": Generate, "revise_detail": Revise, "cad_component": CAD,
               "render": Render, "locate": Locate, "batch_eval": Batch}
    try:
        payload = schemas[kind].model_validate(raw_payload).model_dump()
        if kind == "generate_detail":
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
        elif kind == "batch_eval":
            dataset = json.loads(repo.artifacts.read(payload["dataset_artifact_id"]))
            for figure_id in dataset["figure_ids"]:
                repo.get(d.figures, figure_id)
        else:
            version = repo.get(d.versions, payload.get("base_version_id") or payload.get("version_id"))
            if kind == "revise_detail" and version["detail_id"] != payload["detail_id"]:
                raise ValueError("version belongs to another detail")
            if kind in ("revise_detail", "cad_component", "locate") and not version["figure_id"]:
                raise ValueError("source drawing is unavailable for this archived detail; upload it before starting a native revision")
            if kind == "cad_component" and bool(payload["brief"]) == bool(payload["component_id"]):
                raise ValueError("choose brief or component_id")
        if kind != "render" and not meta_client.configured():
            raise HTTPException(503, "AI not configured")
        result = repo.enqueue(kind, payload, idempotency_key, budget)
    except KeyError:
        raise HTTPException(404, "unknown source or version")
    except Conflict as error:
        raise HTTPException(409, str(error))
    except (ValueError, ValidationError) as error:
        raise HTTPException(422, str(error)[:500])
    ensure_worker()
    return job_result(result["id"])


@router.get("/jobs")
def list_jobs(detail_id: str, active: bool = False, limit: int = 10):
    """Recent jobs on one detail, newest first; active: queued or running only.
    Lets a reloaded page pick a running job back up."""
    query = (sa.select(d.jobs.c.id, d.jobs.c.kind, d.jobs.c.state, d.jobs.c.created_at)
             .join(d.runs, d.runs.c.job_id == d.jobs.c.id).where(d.runs.c.detail_id == detail_id))
    if active:
        query = query.where(d.jobs.c.state.in_(("queued", "running")))
    query = query.order_by(d.jobs.c.created_at.desc()).limit(max(1, min(limit, 50)))
    with repository().engine.connect() as con:
        return {"jobs": [dict(r) for r in con.execute(query).mappings()]}


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


def _short(value, size=400):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= size else text[:size] + "…"


def step_summary(step):
    """What a person needs to follow a run: model turns as the calls they made,
    tool steps as their outcome. Full rows stay available with ?full=1."""
    base = {k: step[k] for k in ("id", "n", "kind", "state", "ms")}
    if step["kind"] == "model":
        response = step["response"] or {}
        output = response.get("output") or []
        usage = response.get("usage") or {}
        return {**base, "purpose": (step["request"] or {}).get("purpose"),
                "tokens": {"input": usage.get("input_tokens"), "output": usage.get("output_tokens"),
                           "cached": (usage.get("input_tokens_details") or {}).get("cached_tokens")},
                "calls": [{"name": o.get("name"), "arguments": _short(o.get("arguments") or "")}
                          for o in output if o.get("type") == "function_call"],
                "text": _short(" ".join(c.get("text", "") for o in output if o.get("type") == "message"
                                        for c in o.get("content") or []), 600)}
    output = step["output"] or {}
    problem = output.get("error") or output.get("tool_error")
    checks = output.get("checks") or []
    critic = output.get("critic") if isinstance(output.get("critic"), dict) else output
    return {**base, "tool_name": step["tool_name"], "arguments": _short(step["arguments"] or {}),
            "error": _short(problem.get("message") or problem.get("code") or problem) if isinstance(problem, dict)
                     else _short(problem) if problem else None,
            "version_id": output.get("version_id"), "verification_id": output.get("verification_id"),
            "checks": {status: sum(c.get("status") == status for c in checks) for status in ("PASS", "FAIL", "UNVERIFIED")}
                      if checks else None,
            "verdict": critic.get("verdict"), "issues": [_short(i.get("reason") or i.get("id") or i, 240)
                                                        for i in (critic.get("issues") or [])][:12],
            "images": [i for i in output.get("images") or [] if isinstance(i, str)]}


@router.get("/runs/{ident}/steps")
def run_steps(ident: str, after: int = 0, limit: int = 50, full: bool = False):
    repo = repository()
    try:
        repo.get(d.runs, ident)
    except KeyError:
        raise HTTPException(404, "unknown run")
    rows = repo.list(d.steps, d.steps.c.run_id == ident, d.steps.c.n > after, order=d.steps.c.n)
    selected = rows[:max(1, min(limit, 100))]
    return {"steps": selected if full else [step_summary(step) for step in selected],
            "next_after": selected[-1]["n"] if selected else after, "more": len(rows) > len(selected)}


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
            # The outcome tier and the inferred part ids live in the quality dict.
            result["quality"] = reports[-1]["quality"]
        else:
            staged = prepare_verification(repo, ident)
            result = {**staged["scene"], "version_id": ident}
            if staged.get("quality") is not None:
                result["quality"] = staged["quality"]
        located = drawing_regions(repo, repo.get(d.versions, ident))
        if located:
            result.setdefault("context", {})["drawing_regions"] = located
        return result
    except KeyError:
        raise HTTPException(404, "unknown version")


def drawing_regions(repo, version):
    """SAM's boxes on the source drawing for this version's parts. A locate
    run belongs to one version; later versions of the same drawing inherit its
    boxes for the parts they still have, as the v2 library did."""
    parts = {p["id"] for p in (version["program"] or {}).get("parts", [])}
    while version:
        rows = repo.list(d.sam_regions, d.sam_regions.c.version_id == version["id"])
        if rows:
            return {r["part_key"]: r["data"] for r in rows if r["part_key"] in parts}
        parent = version["parent_version_id"]
        version = repo.get(d.versions, parent) if parent else None
    return {}


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


def confirm_inferred(program, part_ids):
    """The program with the named inferred parts confirmed by the reviewer:
    provenance user_input, confidence cleared, inferred_by kept as the record
    of where the part came from. Sizes stay the assumptions they were. A box
    standing in for an object becomes a CAD part with no model yet, so it is
    labelled a placeholder and FAILs until modelled: confirming that a part
    exists says nothing about its shape."""
    if not isinstance(part_ids, list) or not part_ids or any(not isinstance(p, str) for p in part_ids):
        raise ValueError("confirm_inferred lists the ids of inferred parts")
    revised = copy.deepcopy(program)
    parts = {p["id"]: p for p in revised.get("parts", [])}
    for ident in part_ids:
        if parts.get(ident, {}).get("provenance") != "inferred":
            raise ValueError(f"{ident} is not an inferred part of this version")
        parts[ident].update(provenance="user_input", confidence=None)
        if parts[ident].get("role") == "object" and parts[ident].get("shape") != "cad":
            parts[ident].update(completion.cad_placeholder(parts[ident], ontology.load()))
    return revised


def confirmation_lesson(program, part_ids):
    """A proposed lesson: the archetype's slot was right to call for these parts."""
    parts = [p for p in program.get("parts", []) if p["id"] in part_ids]
    named = ", ".join(f"{p['id']} ({p.get('class_id') or p['type']}, {p.get('inferred_by') or 'no slot recorded'})" for p in parts)
    kind = program.get("kind") or "assembly"
    return ("type", kind, f"On a {kind} drawing of assembly {program.get('assembly_class') or 'unknown'}, the reviewer "
            f"confirmed the inferred part(s) {named}. Reuse them only as inferred placeholders whose sizes stay "
            "assumptions, never as drawing values.", [p["id"] for p in parts])


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
            proposed, confirmed = body.data.get("program"), body.data.get("confirm_inferred")
            if proposed is None and confirmed is None:
                raise ValueError("correct needs a program or confirm_inferred")
            if proposed is None:
                # Confirming inferred parts: the same immutable-version path as
                # an edit, with only the parts' provenance changed.
                proposed = confirm_inferred(original["program"], confirmed)
            program, spec, _ = prepare(proposed, original["program"]["source"], original["detail_id"],
                                      read["response"], original["program"], human=True)
            revised = (program.model_dump(mode="json"), spec.model_dump(mode="json"),
                       kept_bindings(original["program"], original["bindings"], program))
        with d.transaction(repo.engine) as con:
            detail = row(con, d.details, original["detail_id"], True)
            if revised and detail["current_version_id"] != ident:
                raise Conflict("detail changed; reload before correcting this version")
            target = ident
            if revised:
                result = repo.insert_version(con, detail_id=original["detail_id"], expected_parent=ident,
                            program=revised[0], spec=revised[1], bindings=revised[2], figure_id=original["figure_id"],
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
                # A correction proposes lessons, never a new drawing value: the
                # reviewer's own words if given, plus what the diff shows.
                lessons = correction_lessons(original["program"], revised[0], original["detail_id"])
                if body.data.get("confirm_inferred"):
                    lessons.append(confirmation_lesson(revised[0], body.data["confirm_inferred"]))
                if body.data.get("lesson"):
                    lessons.insert(0, ("type", original["program"].get("kind", "assembly"), body.data["lesson"], []))
                for scope, key, text, tags in lessons:
                    con.execute(d.memories.insert().values(id=uid(), scope=scope, scope_key=key, state="proposed",
                                kind="correction", text=text, tags=tags, source_event_id=review_id,
                                source_version_id=target, created_at=d.now(con)))
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
def memories(version_id: str | None = None, detail_id: str | None = None):
    repo = repository()
    conditions = [d.memories.c.source_version_id == version_id] if version_id else []
    if detail_id:
        # Lessons learnt on any version of this detail, wherever they apply.
        conditions.append(d.memories.c.source_version_id.in_(
            sa.select(d.versions.c.id).where(d.versions.c.detail_id == detail_id)))
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
