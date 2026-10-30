"""Durable jobs, fenced transactions and immutable version/evidence records."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from . import database as d
from .artifacts import ArtifactStore

TERMINAL = {"succeeded", "failed", "needs_review", "cancelled"}
DEFAULT_BUDGET = {"model_calls": 32, "tool_calls": 48, "tokens": 320_000,
                  "cad_calls": 8, "render_calls": 6, "seconds": 900}


def _verifier_sources():
    """The code and the library data a verification depends on. Any change
    invalidates earlier evidence, which is then recomputed."""
    root = Path(__file__).parent
    paths = [root/name for name in ("verification_v3.py","verification_probes.py","native_program.py",
                                    "interfaces.py","cad_components.py","verify.py","geometry.py")]
    library = root.parent/"ontology"
    if library.is_dir():
        paths += sorted(p for p in library.rglob("*") if p.is_file() and (p.suffix in (".yaml",".yml") or p.name == "VERSION"))
    return b"".join(p.read_bytes() for p in paths)


VERIFIER_VERSION = "setout-v3-" + hashlib.sha256(_verifier_sources()).hexdigest()[:16]
RESOURCE_TTL = 360  # exceeds the longest bounded external call (300 seconds)
OUTCOMES = ("incomplete", "complete_with_inferred", "verified")  # quality["outcome"]; None when none applies


def uid():
    return uuid.uuid4().hex


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def inferred_part_ids(parts):
    """Ids of the parts a model or archetype filled in (part-level provenance
    `inferred`), from program JSON or NativePart objects."""
    def provenance(part):
        return part.get("provenance") if isinstance(part, dict) else getattr(part, "provenance", None)
    def ident(part):
        return part["id"] if isinstance(part, dict) else part.id
    return [ident(part) for part in parts if provenance(part) == "inferred"]


def outcome_tier(checks, inferred):
    """How complete a verified version is. incomplete: a required slot is
    empty, so it can never finish as done. complete_with_inferred: every
    required slot is filled, some by inferred parts. verified: no FAIL and no
    required check short of PASS. None when no tier applies (complete, but a
    check failed): such a version is never promoted to verified."""
    if any(c["status"] == "FAIL" and c["id"].startswith("slot-missing:") for c in checks):
        return "incomplete"
    if inferred:
        return "complete_with_inferred"
    if any(c["status"] == "FAIL" or (c.get("required") and c["status"] != "PASS") for c in checks):
        return None
    return "verified"


def version_content(program, spec, bindings, figure_id, read_id, format_version="3.0"):
    return dict(program=program, spec=spec, bindings=bindings or {}, figure_id=figure_id,
                drawing_read_id=read_id, format_version=format_version)


def row(connection, table, ident, lock=False):
    query = sa.select(table).where(table.c.id == ident)
    if lock:
        query = query.with_for_update()
    value = connection.execute(query).mappings().first()
    if value is None:
        raise KeyError(ident)
    return dict(value)


class Conflict(ValueError):
    pass


class LostLease(Conflict):
    pass


class BudgetExceeded(Conflict):
    pass


@dataclass(frozen=True)
class Fence:
    job_id: str
    owner: str
    epoch: int


class Repository:
    def __init__(self, url=None, artifact_root=None, upgrade=True):
        self.engine = d.engine_for(url or d.database_url())
        self.artifacts = ArtifactStore(artifact_root)
        if upgrade:
            d.migrate(self.engine)

    def get(self, table, ident):
        with self.engine.connect() as con:
            return row(con, table, ident)

    def list(self, table, *conditions, order=None):
        query = sa.select(table).where(*conditions)
        if order is not None:
            query = query.order_by(order)
        with self.engine.connect() as con:
            return [dict(r) for r in con.execute(query).mappings()]

    def _check(self, con, fence):
        job = row(con, d.jobs, fence.job_id, True)
        if (job["state"] != "running" or job["lease_owner"] != fence.owner or
                job["lease_epoch"] != fence.epoch or job["lease_until"] <= d.now(con)):
            raise LostLease("worker no longer owns this job")
        return job

    def _run(self, con, job_id):
        return dict(con.execute(sa.select(d.runs).where(d.runs.c.job_id == job_id)).mappings().one())

    def _event(self, con, job_id, kind, message="", data=None):
        seq = con.scalar(sa.select(sa.func.max(d.events.c.seq)).where(d.events.c.job_id == job_id)) or 0
        con.execute(d.events.insert().values(id=uid(), job_id=job_id, seq=seq + 1,
                                             at=d.now(con), kind=kind, message=message, data=data))

    def enqueue(self, kind, payload, key=None, budget=None):
        digest = sha({"kind": kind, "payload": payload, "budget": budget})
        limits = {**DEFAULT_BUDGET, **(budget or {})}
        if any(k not in DEFAULT_BUDGET or not isinstance(v, int) or v <= 0 or
               v > DEFAULT_BUDGET[k] for k, v in limits.items()):
            raise ValueError("budget must be positive and within server limits")
        try:
            with d.transaction(self.engine) as con:
                return self.enqueue_in(con, kind, payload, key, limits, digest)
        except IntegrityError:
            # Concurrent identical POSTs race on the unique key, not on an in-memory lock.
            if not key:
                raise
            with self.engine.connect() as con:
                existing = con.execute(sa.select(d.jobs).where(d.jobs.c.idempotency_key == key)).mappings().one()
                if existing["payload_sha"] != digest:
                    raise Conflict("idempotency key already used with different arguments")
                return dict(existing)

    def enqueue_in(self, con, kind, payload, key, limits=None, digest=None):
        """Schedule children atomically with the coordinator's saved operation."""
        limits = limits or DEFAULT_BUDGET
        digest = digest or sha({"kind": kind, "payload": payload, "budget": None})
        if key:
            existing = con.execute(sa.select(d.jobs).where(d.jobs.c.idempotency_key == key)).mappings().first()
            if existing:
                if existing["payload_sha"] != digest:
                    raise Conflict("idempotency key already used with different arguments")
                return dict(existing)
        ident, at = uid(), d.now(con)
        con.execute(d.jobs.insert().values(id=ident, kind=kind, state="queued", payload=payload,
                    payload_sha=digest, idempotency_key=key, attempts=0, max_attempts=3,
                    available_at=at, lease_epoch=0, created_at=at, updated_at=at))
        base = payload.get("base_version_id") or payload.get("version_id") or payload.get("expected_current_version_id")
        detail_id = payload.get("detail_id")
        if base:
            detail_id = row(con, d.versions, base)["detail_id"]
        con.execute(d.runs.insert().values(id=uid(), job_id=ident, detail_id=detail_id,
                    base_version_id=base, working_version_id=base, budget=limits,
                    consumed={}, reserved={}, outcome=None))
        self._event(con, ident, "queued")
        return row(con, d.jobs, ident)

    def claim(self, owner, only_ids=None):
        with d.transaction(self.engine) as con:
            at = d.now(con)
            expired = con.execute(sa.select(d.jobs).where(d.jobs.c.state == "running",
                        d.jobs.c.lease_until <= at).with_for_update(skip_locked=True)).mappings().all()
            for job in expired:
                state = "failed" if job["attempts"] >= job["max_attempts"] else "queued"
                con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(state=state,
                            lease_owner=None, lease_until=None, lease_epoch=job["lease_epoch"] + 1,
                            available_at=at + min(300, 5 * 2 ** (job["attempts"] - 1)), updated_at=at,
                            error={"code": "lease_expired", "message": "worker lease expired"}))
                if state == "failed":
                    con.execute(d.runs.update().where(d.runs.c.job_id == job["id"]).values(outcome=state))
                self._event(con, job["id"], state, "worker lease expired")
            job = con.execute(sa.select(d.jobs).where(d.jobs.c.state == "queued",
                         d.jobs.c.available_at <= at, d.jobs.c.id.in_(only_ids) if only_ids else sa.true())
                         .order_by(d.jobs.c.created_at, d.jobs.c.id)
                         .with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not job:
                return None
            epoch = job["lease_epoch"] + 1
            con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(state="running",
                        attempts=job["attempts"] + 1, lease_owner=owner, lease_epoch=epoch,
                        lease_until=at + 60, heartbeat_at=at, updated_at=at))
            run = self._run(con, job["id"])
            if run["deadline_at"] is None and job["kind"] != "batch_eval":
                con.execute(d.runs.update().where(d.runs.c.id == run["id"]).values(
                            deadline_at=at + run["budget"]["seconds"]))
            self._event(con, job["id"], "running")
            return Fence(job["id"], owner, epoch)

    def heartbeat(self, fence):
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            at = d.now(con)
            con.execute(d.jobs.update().where(d.jobs.c.id == fence.job_id).values(
                        heartbeat_at=at, lease_until=at + 60, updated_at=at))
            con.execute(d.resource_leases.update().where(d.resource_leases.c.job_id == fence.job_id,
                        d.resource_leases.c.owner == fence.owner, d.resource_leases.c.epoch == fence.epoch).values(expires_at=at + RESOURCE_TTL))

    @contextmanager
    def resource(self, fence, name):
        ident = uid()
        while True:
            acquired = False
            with d.transaction(self.engine) as con:
                self._check(con, fence)
                run = self._run(con, fence.job_id)
                at = d.now(con)
                if run["deadline_at"] and run["deadline_at"] <= at:
                    raise BudgetExceeded("deadline reached while waiting for resource")
                limit = row(con, d.resource_limits, name, True)
                con.execute(d.resource_leases.delete().where(d.resource_leases.c.resource == name,
                                                             d.resource_leases.c.expires_at <= at))
                count = con.scalar(sa.select(sa.func.count()).select_from(d.resource_leases).where(d.resource_leases.c.resource == name))
                if count < limit["capacity"]:
                    con.execute(d.resource_leases.insert().values(id=ident, resource=name, job_id=fence.job_id,
                                owner=fence.owner, epoch=fence.epoch, expires_at=at + RESOURCE_TTL))
                    acquired = True
            if acquired:
                break
            time.sleep(0.1)
        try:
            yield
        finally:
            # Releasing this unique permit cannot mutate a replacement worker's
            # effects. Unreturned external calls retain a bounded cooldown.
            with d.transaction(self.engine) as con:
                con.execute(d.resource_leases.delete().where(d.resource_leases.c.id == ident))

    def review_event(self, con, version_id, reviewer, action, data):
        version = row(con, d.versions, version_id)
        row(con, d.details, version["detail_id"], True)
        seq = con.scalar(sa.select(sa.func.max(d.reviews.c.seq)).where(d.reviews.c.version_id == version_id)) or 0
        ident = uid()
        con.execute(d.reviews.insert().values(id=ident, version_id=version_id, reviewer=reviewer,
                    action=action, data=data, at=d.now(con), seq=seq + 1))
        if action == "accept":
            current = con.execute(sa.select(d.critics).where(d.critics.c.version_id == version_id)
                                  .order_by(d.critics.c.created_at.desc())).mappings().first()
            parent_id = version["parent_version_id"]
            while current and current["verdict"] == "pass" and parent_id:
                previous = con.execute(sa.select(d.critics).where(d.critics.c.version_id == parent_id)
                                       .order_by(d.critics.c.created_at.desc())).mappings().first()
                if previous and previous["verdict"] == "fail":
                    issues = [issue.get("reason",issue.get("id","")) for issue in previous["issues"]]
                    self.propose_memory(con,version,"lesson", "Human-accepted repair of prior critic issues: " +
                        "; ".join(issues)[:2500] + ". Consult the linked version diff; sizes and drawing evidence must come from the current source.",
                        ["accepted-critic-repair"], ident)
                    break
                parent_id = row(con,d.versions,parent_id)["parent_version_id"]
        return ident

    def propose_memory(self, con, version, kind, text, tags, event_id=None):
        # Proposed memories are deliberately inert until a reviewer activates
        # them. Rechecking/reaccepting the same pattern does not multiply it.
        existing = con.execute(sa.select(d.memories).where(d.memories.c.scope == "detail",
            d.memories.c.scope_key == version["detail_id"],d.memories.c.kind == kind)).mappings().all()
        if any(memory["tags"] == tags for memory in existing):
            return
        con.execute(d.memories.insert().values(id=uid(),scope="detail",scope_key=version["detail_id"],
            state="proposed",kind=kind,text=text,tags=tags,source_event_id=event_id,
            source_version_id=version["id"],created_at=d.now(con)))

    def note_repeated_failures(self, con, version_id, checks):
        version = row(con,d.versions,version_id)
        prior = con.execute(sa.select(d.verifications.c.checks).join(d.versions,
            d.verifications.c.version_id == d.versions.c.id).where(d.versions.c.detail_id == version["detail_id"],
            d.verifications.c.version_id != version_id)).scalars().all()
        previous_failures = {check["id"] for report in prior for check in report if check["status"] == "FAIL"}
        for check in checks:
            if check["status"] == "FAIL" and check["id"] in previous_failures:
                self.propose_memory(con,version,"failure", "Repeated verifier failure " + check["id"] + ": " +
                    str(check.get("reason") or check.get("description") or "")[:1500] +
                    ". Inspect measured geometry and named bindings; do not weaken requirements to clear the check.",
                    [check["id"]])

    def cancel(self, ident):
        with d.transaction(self.engine) as con:
            job = row(con, d.jobs, ident, True)
            if job["state"] not in TERMINAL:
                con.execute(d.jobs.update().where(d.jobs.c.id == ident).values(state="cancelled",
                            lease_epoch=job["lease_epoch"] + 1, lease_owner=None, lease_until=None,
                            updated_at=d.now(con)))
                con.execute(d.runs.update().where(d.runs.c.job_id == ident).values(outcome="cancelled"))
                self._event(con, ident, "cancelled")
            return row(con, d.jobs, ident)

    def end(self, fence, state, result=None, error=None):
        if state not in TERMINAL:
            raise ValueError("invalid terminal state")
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            self._end(con, fence.job_id, state, result, error)

    def _end(self, con, job_id, state, result=None, error=None):
        con.execute(d.jobs.update().where(d.jobs.c.id == job_id).values(state=state, result=result,
                    error=error, lease_owner=None, lease_until=None, updated_at=d.now(con)))
        con.execute(d.runs.update().where(d.runs.c.job_id == job_id).values(outcome=state))
        self._event(con, job_id, state, data=result or error)

    def continue_job(self, job_id, kind, payload):
        """Schedule the run that continues `job_id`, once (idempotent), and
        record the link on the finished job so the UI can follow it."""
        with d.transaction(self.engine) as con:
            follow = self.enqueue_in(con, kind, payload, f"continue:{job_id}")
            job = row(con, d.jobs, job_id, True)
            con.execute(d.jobs.update().where(d.jobs.c.id == job_id).values(
                        result={**(job["result"] or {}), "continued_by": follow["id"]}))
            self._event(con, job_id, "continued", f"continues as job {follow['id']}", {"job_id": follow["id"]})
            return follow["id"]

    def retry(self, fence, error):
        with d.transaction(self.engine) as con:
            job = self._check(con, fence)
            if job["attempts"] >= job["max_attempts"]:
                self._end(con, fence.job_id, "failed", error=error)
            else:
                con.execute(d.jobs.update().where(d.jobs.c.id == fence.job_id).values(state="queued",
                            error=error, lease_owner=None, lease_until=None,
                            available_at=d.now(con) + min(300, 5 * 2 ** (job["attempts"] - 1))))
                self._event(con, fence.job_id, "queued", "retry scheduled", error)

    def run(self, job_id):
        with self.engine.connect() as con:
            return self._run(con, job_id)

    def reserve(self, fence, amounts):
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            run = self._run(con, fence.job_id)
            used, reserved = dict(run["consumed"]), dict(run["reserved"])
            if run["deadline_at"] <= d.now(con):
                raise BudgetExceeded("run deadline reached")
            if any(used.get(key, 0) + reserved.get(key, 0) > limit for key, limit in run["budget"].items() if key != "seconds"):
                raise BudgetExceeded("observed usage exceeded the reserved run budget")
            for key, amount in amounts.items():
                if amount < 0 or key not in DEFAULT_BUDGET:
                    raise ValueError("invalid reservation")
                if used.get(key, 0) + reserved.get(key, 0) + amount > run["budget"][key]:
                    raise BudgetExceeded(f"{key} budget exhausted")
                reserved[key] = reserved.get(key, 0) + amount
            con.execute(d.runs.update().where(d.runs.c.id == run["id"]).values(reserved=reserved))

    def settle(self, fence, reserved_amounts, actual):
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            run = self._run(con, fence.job_id)
            used, reserved = dict(run["consumed"]), dict(run["reserved"])
            for key, amount in reserved_amounts.items():
                reserved[key] = max(0, reserved.get(key, 0) - amount)
            for key, amount in actual.items():
                used[key] = used.get(key, 0) + amount
            con.execute(d.runs.update().where(d.runs.c.id == run["id"]).values(
                        consumed=used, reserved=reserved))

    def operation(self, fence, call_id, name, args, prepare, commit):
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            run = self._run(con, fence.job_id)
            existing = con.execute(sa.select(d.operations).where(d.operations.c.run_id == run["id"],
                       d.operations.c.call_id == call_id)).mappings().first()
            if existing:
                if existing["args_sha"] != sha(args) or existing["tool_name"] != name:
                    raise Conflict("tool call ID reused with different arguments")
                if existing["state"] == "completed":
                    return existing["result"]
                operation_id = existing["id"]
            else:
                operation_id = uid()
                con.execute(d.operations.insert().values(id=operation_id, run_id=run["id"], call_id=call_id,
                            tool_name=name, args_sha=sha(args), state="pending"))
        prepared = prepare()  # External calls/staged bytes: never a database transaction.
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            operation = row(con, d.operations, operation_id, True)
            if operation["state"] == "completed":
                return operation["result"]
            current_run = self._run(con, fence.job_id)
            if (current_run["deadline_at"] and current_run["deadline_at"] <= d.now(con) and
                    not (name == "finish" and args.get("status") == "needs_review")):
                raise BudgetExceeded("run deadline reached before tool commit")
            result = commit(con, prepared)
            con.execute(d.operations.update().where(d.operations.c.id == operation_id).values(
                        state="completed", result=result))
            self._step(con, run["id"], kind="tool", state="completed", call_id=call_id,
                       tool_name=name, arguments=args, output=result)
            self._event(con, fence.job_id, "tool", name, result)
            return result

    def _step(self, con, run_id, **data):
        n = con.scalar(sa.select(sa.func.max(d.steps.c.n)).where(d.steps.c.run_id == run_id)) or 0
        ident = uid()
        con.execute(d.steps.insert().values(id=ident, run_id=run_id, n=n + 1, **data))
        return ident

    def model_request(self, fence, request):
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            return self._step(con, self._run(con, fence.job_id)["id"], kind="model",
                              state="pending", request=request)

    def model_response(self, fence, step_id, response, reservation=None):
        with d.transaction(self.engine) as con:
            self._check(con, fence)
            step = row(con, d.steps, step_id)
            if step["run_id"] != self._run(con, fence.job_id)["id"]:
                raise Conflict("step belongs to another run")
            con.execute(d.steps.update().where(d.steps.c.id == step_id).values(
                        state="completed", response=response, tokens=response.get("usage")))
            if reservation:
                run = self._run(con, fence.job_id)
                consumed, reserved = dict(run["consumed"]), dict(run["reserved"])
                usage = response.get("usage") or {}
                actual_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                for key, amount in reservation.items():
                    reserved[key] = max(0, reserved.get(key, 0) - amount)
                    consumed[key] = consumed.get(key, 0) + (actual_tokens or amount if key == "tokens" else amount)
                con.execute(d.runs.update().where(d.runs.c.id == run["id"]).values(
                            consumed=consumed, reserved=reserved))
            self._event(con, fence.job_id, "model", data={"step_id": step_id})

    def artifact(self, con, reference):
        exists = con.scalar(sa.select(d.artifacts.c.id).where(d.artifacts.c.id == reference["id"]))
        if not exists:
            # PostgreSQL can concurrently publish the same content from different jobs.
            nested = con.begin_nested()
            try:
                con.execute(d.artifacts.insert().values(**reference, created_at=d.now(con)))
                nested.commit()
            except IntegrityError:
                nested.rollback()
        return reference["id"]

    def insert_version(self, con, *, detail_id, expected_parent, program, spec, bindings=None,
                       figure_id=None, read_id=None, origin="agent-v3", created_by=None,
                       format_version="3.0", version_id=None):
        detail = con.execute(sa.select(d.details).where(d.details.c.id == detail_id).with_for_update()).mappings().first()
        if detail is None:
            if expected_parent is not None:
                raise Conflict("unknown parent detail")
            con.execute(d.details.insert().values(id=detail_id, title=spec["title"],
                        situation=spec.get("context", {}).get("situation", []), kind=program.get("kind", "assembly")))
            head = None
        else:
            head = detail["current_version_id"]
        n = (con.scalar(sa.select(sa.func.max(d.versions.c.n)).where(d.versions.c.detail_id == detail_id)) or 0) + 1
        data = version_content(program, spec, bindings, figure_id, read_id, format_version)
        ident = version_id or uid()
        con.execute(d.versions.insert().values(id=ident, detail_id=detail_id, n=n,
                    parent_version_id=expected_parent, content_sha=sha(data), **data,
                    origin=origin, created_by=created_by, created_at=d.now(con)))
        for param in program.get("params", []):
            con.execute(d.params.insert().values(id=uid(), version_id=ident, name=param["name"], data=param))
        for part in program.get("parts", []):
            con.execute(d.parts.insert().values(id=uid(), version_id=ident, part_key=part["id"], data=part))
        for relation in program.get("relations", []):
            con.execute(d.relations.insert().values(id=uid(), version_id=ident, data=relation))
        advanced = head == expected_parent
        if advanced:
            con.execute(d.details.update().where(d.details.c.id == detail_id).values(current_version_id=ident,
                        title=spec["title"], situation=spec.get("context", {}).get("situation", []), kind=program.get("kind", "assembly")))
        return {"version_id": ident, "advanced": advanced, "current_head_id": ident if advanced else head,
                "detail_id": detail_id}

    def completion_errors(self, con, version_id, verification_id, critic_id, open_issues=None):
        version = row(con, d.versions, version_id)
        detail = row(con, d.details, version["detail_id"], True)
        verification = row(con, d.verifications, verification_id)
        critic = row(con, d.critics, critic_id)
        errors = []
        if detail["current_version_id"] != version_id:
            errors.append("version_conflict")
        if (verification["version_id"] != version_id or verification["verifier_version"] != VERIFIER_VERSION or
                not verification["checks"]):
            errors.append("verification_mismatch")
        errors.extend(check["id"] for check in verification["checks"]
                      if check["status"] == "FAIL" or (check.get("required") and check["status"] != "PASS"))
        # Completeness: an empty required slot never finishes as done. Inferred
        # parts may complete it, and a finishing summary (open_issues) must
        # name every one of them. Older records carry no outcome; derive it.
        quality = verification["quality"] or {}
        inferred = quality.get("inferred_parts")
        if inferred is None:
            inferred = inferred_part_ids((version["program"] or {}).get("parts", []))
        outcome = quality["outcome"] if "outcome" in quality else outcome_tier(verification["checks"], inferred)
        if outcome == "incomplete":
            errors.append("incomplete")
        if open_issues is not None:
            unlisted = [part for part in inferred if not any(part in issue for issue in open_issues)]
            if unlisted:
                errors.append("inferred_parts_not_in_open_issues:" + ",".join(unlisted))
        if (critic["version_id"] != version_id or critic["verification_id"] != verification_id or
                critic["manifest_sha"] != verification["manifest_sha"] or critic["verdict"] != "pass"):
            errors.append("critic_mismatch_or_not_pass")
        render_rows = [row(con, d.renders, ident) for ident in critic["render_ids"]]
        if (not {"iso", "front", "top"} <= {r["view"] for r in render_rows if not r["tags"]} or
                any(r["version_id"] != version_id or r["manifest_sha"] != verification["manifest_sha"] for r in render_rows)):
            errors.append("render_mismatch_or_incomplete")
        resolutions = con.execute(sa.select(d.reviews).where(d.reviews.c.version_id == version_id,
                                  d.reviews.c.action == "resolve")).mappings().all()
        try:
            from .render import validate_image
            manifest = json.loads(self.artifacts.read(verification["manifest_artifact_id"]))
            if sha(manifest) != verification["manifest_sha"] or manifest["version_sha"] != version["content_sha"]:
                errors.append("manifest_mismatch")
            self.artifacts.read(verification["quality"]["scene_artifact_id"])
            if version["figure_id"]:
                figure = row(con,d.figures,version["figure_id"])
                document = row(con,d.documents,figure["document_id"])
                self.artifacts.read(figure["artifact_id"])
                self.artifacts.read(document["artifact_id"])
            for binding in version["bindings"].values():
                if binding.get("build_id"):
                    built = row(con,d.component_builds,binding["build_id"])
                    for ident in built["artifacts"].values():
                        self.artifacts.read(ident)
            for rendered in render_rows:
                validate_image(self.artifacts.read(rendered["artifact_id"]))
        except (ValueError,KeyError,FileNotFoundError,TypeError,OSError):
            errors.append("missing_corrupt_or_blank_evidence")
        resolved = {issue for r in resolutions for issue in (r["data"] or {}).get("issue_ids", [])}
        errors.extend(issue["id"] for issue in critic["issues"]
                      if issue.get("needs_human") and issue["id"] not in resolved)
        return errors
