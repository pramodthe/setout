"""Crash boundaries, operation replay and fencing; no network/provider calls."""
import multiprocessing
import os
import uuid
from urllib.parse import quote

import pytest
import sqlalchemy as sa

from app import database as d
from app.repository import BudgetExceeded, Conflict, LostLease, Repository


@pytest.fixture(params=["sqlite", "postgres"] if os.environ.get("SETOUT_TEST_DATABASE_URL") else ["sqlite"])
def repo(tmp_path, request):
    if request.param == "sqlite":
        yield Repository("sqlite:///" + str(tmp_path / "v3.db"), tmp_path / "artifacts")
        return
    url = os.environ["SETOUT_TEST_DATABASE_URL"]
    engine = sa.create_engine(url)
    schema = "test_" + uuid.uuid4().hex
    with engine.begin() as con:
        con.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    scoped = url + ("&" if "?" in url else "?") + "options=" + quote("-csearch_path=" + schema)
    repository = Repository(scoped, tmp_path / "artifacts")
    try:
        yield repository
    finally:
        repository.engine.dispose()
        with engine.begin() as con:
            con.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_job_and_tool_replay_survive_new_repository(repo):
    job = repo.enqueue("test", {"value": "v"}, "request")
    assert repo.enqueue("test", {"value": "v"}, "request")["id"] == job["id"]
    with pytest.raises(Conflict):
        repo.enqueue("test", {"value": "other"}, "request")
    fence = repo.claim("worker")
    def commit(con, _):
        repo._event(con, job["id"], "domain-effect")
        return {"id": "the-saved-effect"}
    result = repo.operation(fence, "call-1", "tool", {"x": 1}, lambda: None, commit)
    reopened = Repository(repo.engine.url.render_as_string(hide_password=False), repo.artifacts.root)
    assert reopened.operation(fence, "call-1", "tool", {"x": 1},
                              lambda: pytest.fail("completed tool re-executed"), commit) == result
    with pytest.raises(Conflict):
        reopened.operation(fence, "call-1", "tool", {"x": 2}, lambda: None, commit)
    assert len(repo.list(d.events, d.events.c.kind == "domain-effect")) == 1


def test_failed_effect_transaction_rolls_back_and_replays(repo):
    job = repo.enqueue("render", {})
    fence = repo.claim("one")
    def crash(con, _):
        repo._event(con, job["id"], "must-rollback")
        raise RuntimeError("simulated crash before commit")
    with pytest.raises(RuntimeError):
        repo.operation(fence, "call", "tool", {}, lambda: None, crash)
    assert not repo.list(d.events, d.events.c.kind == "must-rollback")
    assert repo.operation(fence, "call", "tool", {}, lambda: None, lambda con, _: {"ok": True}) == {"ok": True}


def test_expired_worker_cannot_commit_after_reassignment(repo):
    job = repo.enqueue("render", {})
    stale = repo.claim("old")
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(lease_until=0))
    assert repo.claim("replacement") is None  # backoff applies
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(available_at=0))
    current = repo.claim("replacement")
    assert current.epoch > stale.epoch
    for action in (lambda: repo.heartbeat(stale), lambda: repo.end(stale, "succeeded"),
                   lambda: repo.operation(stale, "late", "tool", {}, lambda: None, lambda c, p: {})):
        with pytest.raises(LostLease):
            action()
    repo.end(current, "succeeded", {"ok": True})


def test_cancellation_and_budget_survive_restart(repo):
    job = repo.enqueue("render", {}, budget={"cad_calls": 1})
    fence = repo.claim("worker")
    repo.reserve(fence, {"cad_calls": 1})
    reopened = Repository(repo.engine.url.render_as_string(hide_password=False), repo.artifacts.root)
    with pytest.raises(BudgetExceeded):
        reopened.reserve(fence, {"cad_calls": 1})
    assert reopened.run(job["id"])["deadline_at"] == repo.run(job["id"])["deadline_at"]
    assert reopened.cancel(job["id"])["state"] == "cancelled"
    assert reopened.cancel(job["id"])["state"] == "cancelled"
    with pytest.raises(LostLease):
        repo.end(fence, "succeeded")


def test_artifact_store_is_immutable_and_checks_corruption(repo):
    reference = repo.artifacts.put(b"source", "text/plain")
    assert repo.artifacts.put(b"source")["id"] == reference["id"]
    assert repo.artifacts.read(reference["id"]) == b"source"
    repo.artifacts.resolve(reference["id"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        repo.artifacts.read(reference["id"])
    with pytest.raises(ValueError):
        repo.artifacts.read("../source")


def test_version_conflict_preserves_candidate_without_changing_head(repo):
    spec = {"title": "Test", "context": {}}
    program = {"kind": "assembly", "params": [], "parts": []}
    with d.transaction(repo.engine) as con:
        first = repo.insert_version(con, detail_id="test", expected_parent=None, program=program, spec=spec)
    with d.transaction(repo.engine) as con:
        conflict = repo.insert_version(con, detail_id="test", expected_parent=None, program=program, spec=spec)
    assert not conflict["advanced"]
    assert repo.get(d.details, "test")["current_version_id"] == first["version_id"]
    assert repo.get(d.versions, conflict["version_id"])["n"] == 2


def _crash_effect(url, root, after_commit):
    repository = Repository(url, root, upgrade=False)
    fence = repository.claim("crashing-worker")
    def commit(con, _):
        repository._event(con, fence.job_id, "crash-effect")
        if not after_commit:
            os._exit(19)
        return {"effect_id": "original"}
    repository.operation(fence, "stable-call", "crash-test", {}, lambda: None, commit)
    os._exit(20)


@pytest.mark.parametrize("after_commit", [False, True])
def test_real_process_crash_replays_one_effect(repo, after_commit):
    job = repo.enqueue("test", {})
    process = multiprocessing.get_context("spawn").Process(target=_crash_effect,
              args=(repo.engine.url.render_as_string(hide_password=False), str(repo.artifacts.root), after_commit))
    process.start()
    process.join(15)
    assert not process.is_alive()
    assert process.exitcode == (20 if after_commit else 19)
    committed = repo.list(d.events, d.events.c.kind == "crash-effect")
    assert len(committed) == int(after_commit)
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(lease_until=0))
    assert repo.claim("replacement") is None
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(available_at=0))
    fence = repo.claim("replacement")
    def commit(con, _):
        repo._event(con, job["id"], "crash-effect")
        return {"effect_id": "original"}
    result = repo.operation(fence, "stable-call", "crash-test", {}, lambda: None, commit)
    assert result == {"effect_id": "original"}
    assert len(repo.list(d.events, d.events.c.kind == "crash-effect")) == 1


def _claim_concurrently(url, root, start, results):
    repository = Repository(url, root, upgrade=False)
    start.wait(10)
    fence = repository.claim(str(os.getpid()))
    results.put(fence.job_id if fence else None)


def test_independent_processes_claim_distinct_jobs(repo):
    jobs = {repo.enqueue("test", {})["id"] for _ in range(2)}
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    processes = [context.Process(target=_claim_concurrently,
                 args=(repo.engine.url.render_as_string(hide_password=False), str(repo.artifacts.root), start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    claimed = {results.get(timeout=15) for _ in range(2)}
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert claimed == jobs


def _reserve_concurrently(url,root,fence,start,results):
    repository = Repository(url,root,upgrade=False)
    start.wait(10)
    try:
        repository.reserve(fence,{"cad_calls":1})
        results.put("reserved")
    except BudgetExceeded:
        results.put("refused")


def test_independent_processes_cannot_double_spend_the_last_budget_slot(repo):
    job = repo.enqueue("test",{},budget={"cad_calls":1})
    fence = repo.claim("owner")
    context = multiprocessing.get_context("spawn")
    start,results = context.Event(),context.Queue()
    processes = [context.Process(target=_reserve_concurrently,
        args=(repo.engine.url.render_as_string(hide_password=False),str(repo.artifacts.root),fence,start,results)) for _ in range(2)]
    for process in processes:process.start()
    start.set()
    assert sorted(results.get(timeout=15) for _ in range(2)) == ["refused","reserved"]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert repo.run(job["id"])["reserved"]["cad_calls"] == 1
