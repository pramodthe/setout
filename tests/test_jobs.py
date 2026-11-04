"""Crash boundaries, leases and budgets; no network/provider calls."""
import multiprocessing

import pytest

from app import database as d
from app.repository import BudgetExceeded, Conflict, LostLease, Repository


@pytest.fixture
def repo(tmp_path):
    return Repository("sqlite:///" + str(tmp_path / "v3.db"), tmp_path / "artifacts")


def test_a_job_survives_a_new_repository(repo):
    job = repo.enqueue("studio_build", {"scene_id": "a"})
    fence = repo.claim("worker")
    assert fence.job_id == job["id"]
    reopened = Repository(str(repo.engine.url), repo.artifacts.root)
    assert reopened.get(d.jobs, job["id"])["state"] == "running"


def test_an_expired_worker_cannot_finish_a_job(repo):
    repo.enqueue("studio_build", {"scene_id": "a"})
    stale = repo.claim("old")
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == stale.job_id).values(lease_until=0, available_at=0))
    current = repo.claim("new")
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == stale.job_id).values(available_at=0))
    current = current or repo.claim("new")
    assert current.owner == "new" and current.epoch > stale.epoch
    with pytest.raises(LostLease):
        repo.end(stale, "succeeded")
    repo.end(current, "succeeded")


def test_a_budget_stops_spending_and_survives_a_restart(repo):
    job = repo.enqueue("studio_build", {}, budget={"model_calls": 1})
    fence = repo.claim("worker")
    repo.reserve(fence, {"model_calls": 1})
    reopened = Repository(str(repo.engine.url), repo.artifacts.root)
    with pytest.raises(BudgetExceeded):
        reopened.reserve(fence, {"model_calls": 1})
    assert reopened.run(job["id"])["reserved"]["model_calls"] == 1


def test_a_budget_can_only_lower_the_limits(repo):
    with pytest.raises(ValueError):
        repo.enqueue("studio_build", {}, budget={"tokens": 10 ** 9})


def test_one_key_gives_one_job(repo):
    first = repo.enqueue("studio_build", {"scene_id": "a"}, "key")
    assert repo.enqueue("studio_build", {"scene_id": "a"}, "key")["id"] == first["id"]
    with pytest.raises(Conflict):
        repo.enqueue("studio_build", {"scene_id": "b"}, "key")


def test_artifacts_are_stored_once_by_content(repo):
    first, second = repo.artifacts.put(b"same", "text/plain"), repo.artifacts.put(b"same", "text/plain")
    assert first["id"] == second["id"]
    with d.transaction(repo.engine) as con:
        repo.artifact(con, first)
        repo.artifact(con, second)
    assert repo.artifacts.read(first["id"]) == b"same"


def claim_one(url, root, ready, results):
    repository = Repository(url, root, upgrade=False)
    ready.wait()
    fence = repository.claim("process")
    results.put(fence.job_id if fence else None)


def test_separate_processes_claim_different_jobs(repo):
    jobs = {repo.enqueue("studio_build", {"scene_id": str(k)})["id"] for k in range(4)}
    ready, results = multiprocessing.Event(), multiprocessing.Queue()
    workers = [multiprocessing.Process(target=claim_one, args=(str(repo.engine.url), repo.artifacts.root, ready, results)) for _ in range(4)]
    for worker in workers:
        worker.start()
    ready.set()
    claimed = {results.get(timeout=30) for _ in workers}
    for worker in workers:
        worker.join(timeout=30)
    assert claimed == jobs


def spend_one(url, root, fence, ready, results):
    repository = Repository(url, root, upgrade=False)
    ready.wait()
    try:
        repository.reserve(fence, {"model_calls": 1})
        results.put(True)
    except BudgetExceeded:
        results.put(False)


def test_separate_processes_cannot_spend_the_last_call_twice(repo):
    job = repo.enqueue("studio_build", {}, budget={"model_calls": 1})
    fence = repo.claim("worker")
    ready, results = multiprocessing.Event(), multiprocessing.Queue()
    workers = [multiprocessing.Process(target=spend_one, args=(str(repo.engine.url), repo.artifacts.root, fence, ready, results)) for _ in range(4)]
    for worker in workers:
        worker.start()
    ready.set()
    spent = [results.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=30)
    assert sum(spent) == 1 and repo.run(job["id"])["reserved"]["model_calls"] == 1


def test_builder_leaves_tokens_available_for_an_independent_final_check(repo):
    from app.model_gateway import ModelGateway
    from app.repository import tokens_left
    from builder.loop import CHECK_RESERVE

    repo.enqueue("studio_build", {}, budget={"tokens": CHECK_RESERVE + 13_000})
    fence = repo.claim("worker")
    sent = []

    def responder(payload):
        sent.append(payload)
        return {"output": [], "usage": {"input_tokens": 4096, "output_tokens": payload["max_output_tokens"]}}

    gateway = ModelGateway(repo, fence, responder)
    payload = {"input": [], "max_output_tokens": 32768}
    gateway.call(payload, "studio", "turn-1", reserve_tokens=CHECK_RESERVE)
    assert tokens_left(repo.run(fence.job_id)) == CHECK_RESERVE
    with pytest.raises(BudgetExceeded):
        gateway.call(payload, "studio", "turn-2", reserve_tokens=CHECK_RESERVE)
    gateway.call({"input": [], "max_output_tokens": 24576}, "studio_check", "final-check")
    assert len(sent) == 2 and sent[-1]["max_output_tokens"] == 24576
