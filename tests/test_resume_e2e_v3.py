"""M1: kill -9 a worker mid-run and a fresh worker resumes from its last checkpoint.

A real worker process runs a scripted generate_detail job and is SIGKILLed while
a provider request is in flight. After its lease expires, a second worker
process must finish the job without re-requesting any completed model turn or
repeating any committed tool effect. No network: provider and renderer are fakes.
"""
import json
import multiprocessing
import os
import signal
import time
from collections import Counter

import pytest

from app import database as d
from app.repository import Repository, sha
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo  # noqa: F401 (fixture)

# Every provider request of the scripted run, in order: the agent turns plus the
# structured calls nested inside the read_drawing (call-0) and critic (call-4) tools.
ORDER = ["turn-0", "read", "turn-1", "turn-2", "turn-3", "turn-4", "critic", "turn-5"]
TOOLS = ["read_drawing", "write_program", "build_and_verify", "render", "critic", "finish"]
CALLS = [f"call-{i}" for i in range(len(TOOLS))]


def request_key(payload):
    if "text" in payload:
        return payload["text"]["format"]["name"]
    return "turn-%d" % sum(item.get("type") == "function_call" for item in payload["input"])


class ResumableScript(ScriptedModel):
    """ScriptedModel that takes its position from the request, not process memory,
    so another process can continue the run. Logs every request it receives and
    blocks (until SIGKILLed) on the request named `block`."""

    def __init__(self, log, block=None, reached=None):
        super().__init__()
        self.log, self.block, self.reached = log, block, reached

    def __call__(self, payload):
        key = request_key(payload)
        record = {"pid": os.getpid(), "key": key, "items": [sha(item) for item in payload["input"]],
                  "outputs": {item["call_id"]: item["output"] for item in payload["input"]
                              if item.get("type") == "function_call_output"}}
        with open(self.log, "a") as log:
            log.write(json.dumps(record) + "\n")
            log.flush()
            os.fsync(log.fileno())
        if key == self.block:
            self.reached.set()
            time.sleep(30)  # the parent SIGKILLs this process here
            raise AssertionError("worker was not killed")
        if key.startswith("turn-"):
            self.agent_calls = int(key.removeprefix("turn-"))
        return super().__call__(payload)


def _worker(url, root, log, block, reached, job_id):
    repo = Repository(url, root, upgrade=False)
    Worker(repo, responder=ResumableScript(log, block, reached),
           renderer=FakeRenderer(repo)).run_once([job_id])


@pytest.mark.parametrize("block,step_key,committed", [
    # Killed while an agent turn is with the provider (no tool running).
    ("turn-3", ("agent", "turn-3"), CALLS[:3]),
    # Killed inside the critic tool's nested model call: its operation is
    # pending, so the replacement must re-prepare it and commit exactly once.
    ("critic", ("critic", "call-4"), CALLS[:4]),
], ids=["model-turn-in-flight", "tool-effect-in-flight"])
def test_sigkilled_worker_resumes_from_its_last_checkpoint(source_repo, tmp_path, block, step_key, committed):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    url, root, log = str(repo.engine.url), str(repo.artifacts.root), tmp_path / "requests.jsonl"
    context = multiprocessing.get_context("spawn")
    reached = context.Event()
    first = context.Process(target=_worker, args=(url, root, str(log), block, reached, job["id"]))
    first.start()
    try:
        assert reached.wait(20), "worker never reached the kill point"
        os.kill(first.pid, signal.SIGKILL)
        first.join(10)
    finally:
        if first.is_alive():
            first.kill()
            first.join()
    assert first.exitcode == -signal.SIGKILL

    # At the crash: completed turns and effects are durable; the lease is still held.
    assert repo.get(d.jobs, job["id"])["state"] == "running"
    operations = repo.list(d.operations)
    before = {o["call_id"]: o["result"] for o in operations if o["state"] == "completed"}
    assert sorted(before) == committed
    assert len(repo.list(d.steps, d.steps.c.kind == "model", d.steps.c.state == "completed")) >= 2

    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(lease_until=0))
    assert repo.claim("sweeper") is None  # expiry requeues the job with a backoff
    expired = repo.get(d.jobs, job["id"])
    assert expired["state"] == "queued" and expired["error"]["code"] == "lease_expired"
    with d.transaction(repo.engine) as con:
        con.execute(d.jobs.update().where(d.jobs.c.id == job["id"]).values(available_at=0))

    second = context.Process(target=_worker, args=(url, root, str(log), None, None, job["id"]))
    second.start()
    second.join(30)
    if second.is_alive():
        second.kill()
        second.join()
    assert second.exitcode == 0

    final = repo.get(d.jobs, job["id"])
    assert final["state"] == "succeeded", final
    assert final["attempts"] == 2
    run = repo.run(job["id"])
    assert final["result"]["version_id"] == run["working_version_id"]

    # Provider requests across both processes: each completed one exactly once.
    # Only the request in flight at the kill repeats, and the new worker starts there.
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    at = ORDER.index(block)
    assert [r["key"] for r in requests] == ORDER[:at + 1] + ORDER[at:]
    assert {r["pid"] for r in requests[:at + 1]} == {first.pid}
    assert {r["pid"] for r in requests[at + 1:]} == {second.pid}
    assert requests[at + 1]["items"] == requests[at]["items"]  # the checkpointed request, resent

    # The resumed conversation carries the pre-kill conversation verbatim,
    # including the replayed (not recomputed) outputs of committed tool calls.
    last_before = [r for r in requests[:at + 1] if r["key"].startswith("turn-")][-1]
    first_after = next(r for r in requests[at + 1:] if r["key"].startswith("turn-") and r["key"] != block)
    assert first_after["items"][:len(last_before["items"])] == last_before["items"]
    assert set(committed) <= set(first_after["outputs"])

    # Tool effects: one operation and one tool step per call, none re-executed.
    operations = {o["call_id"]: o for o in repo.list(d.operations)}
    assert sorted(operations) == CALLS
    assert [operations[c]["tool_name"] for c in CALLS] == TOOLS
    assert all(o["state"] == "completed" for o in operations.values())
    assert all(operations[c]["result"] == result for c, result in before.items())
    assert len(repo.list(d.versions)) == 1  # one write_program, one version
    assert len(repo.list(d.reads)) == 1
    assert len(repo.list(d.verifications)) == 2  # write_program's own + build_and_verify
    assert len(repo.list(d.renders)) == 6
    assert len(repo.list(d.critics)) == 1

    # Checkpoints: contiguous step numbers; every request completed exactly once;
    # the dead worker's in-flight request is the only orphan, and its retry
    # resent the same checkpointed input.
    steps = repo.list(d.steps, d.steps.c.run_id == run["id"], order=d.steps.c.n)
    assert [s["n"] for s in steps] == list(range(1, len(steps) + 1))
    assert [s["call_id"] for s in steps if s["kind"] == "tool"] == CALLS
    model = [s for s in steps if s["kind"] == "model"]
    done = Counter((s["request"]["purpose"], s["request"]["operation_key"]) for s in model if s["state"] == "completed")
    assert len(done) == len(ORDER) and set(done.values()) == {1}
    orphans = [s for s in model if s["state"] != "completed"]
    assert [(s["state"], s["request"]["purpose"], s["request"]["operation_key"]) for s in orphans] == [("pending", *step_key)]
    retry = next(s for s in model if s["state"] == "completed" and
                 (s["request"]["purpose"], s["request"]["operation_key"]) == step_key)
    assert retry["n"] > orphans[0]["n"]
    assert retry["request"]["payload"]["input"] == orphans[0]["request"]["payload"]["input"]
