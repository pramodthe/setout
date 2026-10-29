"""A durable batch coordinator; failed and incomplete drawings remain results."""
from __future__ import annotations

from collections import Counter
import json
import threading

from . import database as d
from .repository import LostLease, TERMINAL


def run_batch(worker, fence, payload):
    repo = worker.repo
    def prepare():
        dataset = json.loads(repo.artifacts.read(payload["dataset_artifact_id"]))
        figures = dataset["figure_ids"]
        if not 1 <= len(figures) <= 100 or len(set(figures)) != len(figures):
            raise ValueError("dataset needs 1–100 unique figure IDs")
        for figure in figures:
            repo.get(d.figures, figure)
        return figures
    def commit(con, figures):
        return {"child_job_ids": [repo.enqueue_in(con, "generate_detail",
                    {"figure_id": figure, "memory_mode": payload.get("memory_mode", "on")},
                    f"batch-{fence.job_id}-{figure}")["id"] for figure in figures]}
    scheduled = repo.operation(fence, "schedule", "batch_schedule", payload, prepare, commit)
    children = scheduled["child_job_ids"]
    stop = threading.Event()
    def cancellation():
        while not stop.wait(.5):
            if repo.get(d.jobs, fence.job_id)["state"] == "cancelled":
                for child in children:
                    repo.cancel(child)
                return
    monitor = threading.Thread(target=cancellation, daemon=True)
    monitor.start()
    try:
        from .worker import Worker
        child_worker = Worker(repo, worker.responder, worker.renderer, worker.cad_builder)
        while any(repo.get(d.jobs, child)["state"] not in TERMINAL for child in children):
            with d.transaction(repo.engine) as con:
                repo._check(con, fence)
            if not child_worker.run_once(only_ids=children):
                stop.wait(.25)
        rows = []
        for child in children:
            job = repo.get(d.jobs, child)
            run = repo.run(child)
            reports = repo.list(d.verifications, d.verifications.c.version_id == run["working_version_id"], order=d.verifications.c.created_at)
            checks = reports[-1]["checks"] if reports else []
            rows.append({"job_id": child, "version_id": run["working_version_id"], "state": job["state"],
                         "tokens": run["consumed"].get("tokens", 0), "error": job["error"],
                         "required_checks": dict(Counter(c["status"] for c in checks if c.get("required")))})
        result = {"dataset_artifact_id": payload["dataset_artifact_id"], "rows": rows,
                  "states": dict(Counter(row["state"] for row in rows)),
                  "tokens": sum(row["tokens"] for row in rows)}
        repo.end(fence, "succeeded", result)
    finally:
        stop.set()
        monitor.join(timeout=1)
