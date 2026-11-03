"""Durable jobs, fenced transactions and checkpointed model calls."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
import time

import sqlalchemy as sa

from . import database as d
from .artifacts import ArtifactStore

TERMINAL = {"succeeded", "failed", "needs_review"}
# `tokens` counts what is paid for: uncached input plus output (billed_tokens). Cached input is
# recorded beside it as consumed["cached_tokens"]. model_calls and seconds bound a run that is
# nearly all cache hits.
DEFAULT_BUDGET = {"model_calls": 60, "tokens": 320_000, "seconds": 1800}
RESOURCE_TTL = 360  # exceeds the longest bounded external call (300 seconds)


def uid():
    return uuid.uuid4().hex


def cached_tokens(usage):
    return (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0


def billed_tokens(usage):
    """What a response costs the budget: uncached input plus output. The
    provider counts cached input inside input_tokens, and each turn re-sends
    the whole conversation, so charging it would spend the budget on repeats."""
    return max(usage.get("input_tokens", 0) - cached_tokens(usage), 0) + usage.get("output_tokens", 0)


def tokens_left(run):
    """The run's unspent tokens: the one reading every budget check uses.
    Tokens still reserved (say by a request a crash cut off) count as spent."""
    return run["budget"]["tokens"] - run["consumed"].get("tokens", 0) - run["reserved"].get("tokens", 0)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def row(connection, table, ident):
    value = connection.execute(sa.select(table).where(table.c.id == ident)).mappings().first()
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
        job = row(con, d.jobs, fence.job_id)
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
        with d.transaction(self.engine) as con:
            return self.enqueue_in(con, kind, payload, key, limits, digest)

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
        con.execute(d.runs.insert().values(id=uid(), job_id=ident, budget=limits, consumed={}, reserved={}, outcome=None))
        self._event(con, ident, "queued")
        return row(con, d.jobs, ident)

    def claim(self, owner, only_ids=None):
        with d.transaction(self.engine) as con:
            at = d.now(con)
            expired = con.execute(sa.select(d.jobs).where(d.jobs.c.state == "running",
                        d.jobs.c.lease_until <= at)).mappings().all()
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
                         .limit(1)).mappings().first()
            if not job:
                return None
            epoch = job["lease_epoch"] + 1
            con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(state="running",
                        attempts=job["attempts"] + 1, lease_owner=owner, lease_epoch=epoch,
                        lease_until=at + 60, heartbeat_at=at, updated_at=at))
            run = self._run(con, job["id"])
            if run["deadline_at"] is None:
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
                limit = row(con, d.resource_limits, name)
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

    def reserve(self, fence, amounts, token_floor=0):
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
                floor = token_floor if key == "tokens" else 0
                if used.get(key, 0) + reserved.get(key, 0) + amount + floor > run["budget"][key]:
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
                # No usage reported: the reservation stands, as for a lost response.
                reported = usage.get("input_tokens") or usage.get("output_tokens")
                for key, amount in reservation.items():
                    reserved[key] = max(0, reserved.get(key, 0) - amount)
                    consumed[key] = consumed.get(key, 0) + (billed_tokens(usage) if key == "tokens" and reported else amount)
                if cached_tokens(usage):
                    consumed["cached_tokens"] = consumed.get("cached_tokens", 0) + cached_tokens(usage)
                con.execute(d.runs.update().where(d.runs.c.id == run["id"]).values(
                            consumed=consumed, reserved=reserved))
            self._event(con, fence.job_id, "model", data={"step_id": step_id})

    def artifact(self, con, reference):
        if not con.scalar(sa.select(d.artifacts.c.id).where(d.artifacts.c.id == reference["id"])):
            con.execute(d.artifacts.insert().values(**reference, created_at=d.now(con)))
