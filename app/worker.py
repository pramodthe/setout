"""Persistent worker: python -m app.worker (safe to restart or kill)."""
from __future__ import annotations

import argparse
import threading
import urllib.error
import uuid

from .model_gateway import ModelGateway, ProviderRejected
from .repository import BudgetExceeded, LostLease, Repository
from . import database as d, studio


class Worker:
    def __init__(self, repo, responder=None):
        self.repo = repo
        self.owner = uuid.uuid4().hex
        self.responder = responder
        self.stop = threading.Event()

    def run_once(self, only_ids=None):
        fence = self.repo.claim(self.owner, only_ids)
        if fence is None:
            return False
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(15):
                try:
                    self.repo.heartbeat(fence)
                except LostLease:
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            job = self.repo.get(d.jobs, fence.job_id)
            if job["kind"] not in studio.JOBS:
                raise ValueError(f"unsupported job kind: {job['kind']}")
            studio.JOBS[job["kind"]](self.repo, fence, job, ModelGateway(self.repo, fence, self.responder))
        except LostLease:
            pass
        except (BudgetExceeded, ProviderRejected) as error:
            code = "budget_exhausted" if isinstance(error, BudgetExceeded) else "provider_rejected"
            try:
                self.repo.end(fence, "needs_review", {"stop_reason": code}, {"code": code, "message": str(error)[:500]})
            except LostLease:
                pass
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            try:
                self.repo.retry(fence, {"code": "provider_unavailable", "message": str(error)[:300]})
            except LostLease:
                pass
        except Exception as error:
            try:
                self.repo.end(fence, "failed", error={"code": type(error).__name__, "message": str(error)[:500]})
            except LostLease:
                pass
        finally:
            heartbeat_stop.set()
            thread.join(timeout=1)
        return True

    def serve(self):
        while not self.stop.is_set():
            try:
                busy = self.run_once()
            except Exception as error:  # noqa: BLE001 - e.g. "database is locked" while claiming
                # Never let the worker thread die: a job it held is retried once its lease expires.
                print(f"[setout] worker error, continuing: {type(error).__name__}: {error}")
                busy = False
            if not busy:
                self.stop.wait(0.25)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker = Worker(Repository())
    if args.once:
        worker.run_once()
    else:
        try:
            worker.serve()
        except KeyboardInterrupt:
            worker.stop.set()
