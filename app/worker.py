"""Persistent worker: python -m app.worker (safe to restart or kill)."""
from __future__ import annotations

import argparse
import os
import threading
import urllib.error
import uuid

from . import database as d
from .agent import Agent
from .agent_tools import Tools
from .model_gateway import ProviderRejected
from .repository import CUT_SHORT, BudgetExceeded, LostLease, Repository


# A run that spends its budget with work left (CAD placeholders, FAILs)
# continues in a fresh run, at most this many times per generation.
AUTO_CONTINUE = int(os.environ.get("SETOUT_AUTO_CONTINUE", "2"))
CONTINUE_REQUEST = ("Continue where the previous run stopped on its budget. Any empty required slot (a slot-missing "
                    "check) comes first: complete_assembly fills it. Then model the remaining placeholder objects with "
                    "CAD, then fix the remaining FAILs. Keep everything that already passes.")


def continue_if_cut_short(repo, job_id, limit=None):
    """Continue a generation or revision that was cut short mid-work.

    The run says why it stopped (stop_reason, recorded when it stopped); this
    never works that out again from what was spent afterwards. Only when the
    run made a new version, that version is still the detail's current one, its
    checks still block completion (a CAD placeholder, a failed measurement) and
    the chain hasn't hit its limit. Returns the new job id."""
    limit = AUTO_CONTINUE if limit is None else limit
    job, run = repo.get(d.jobs, job_id), repo.run(job_id)
    step = int((job["payload"] or {}).get("continuation") or 0)
    version_id = run["working_version_id"]
    if (job["kind"] not in ("generate_detail", "revise_detail") or job["state"] != "needs_review"
            or step >= limit or not version_id or version_id == run["base_version_id"]):
        return None
    if repo.get(d.details, run["detail_id"])["current_version_id"] != version_id:
        return None
    reports = repo.list(d.verifications, d.verifications.c.version_id == version_id, order=d.verifications.c.created_at)
    if (job["result"] or {}).get("stop_reason") not in CUT_SHORT or not reports or not any(
            c["status"] == "FAIL" or (c.get("required") and c["status"] != "PASS") for c in reports[-1]["checks"]):
        return None
    payload = {"detail_id": run["detail_id"], "base_version_id": version_id,
               "requested_changes": CONTINUE_REQUEST, "continuation": step + 1}
    if (job["payload"] or {}).get("manufacturer"):
        payload["manufacturer"] = job["payload"]["manufacturer"]
    return repo.continue_job(job_id, "revise_detail", payload)


class Worker:
    def __init__(self, repo, responder=None, renderer=None, cad_builder=None):
        self.repo = repo
        self.owner = uuid.uuid4().hex
        self.responder, self.renderer, self.cad_builder = responder, renderer, cad_builder
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
            if job["kind"] == "batch_eval":
                from .evaluation import run_batch
                run_batch(self, fence, job["payload"])
            elif job["kind"] in ("generate_detail", "revise_detail"):
                Agent(self.repo, fence, self.responder, self.renderer, self.cad_builder).run()
                continue_if_cut_short(self.repo, fence.job_id)
            else:
                from .model_gateway import ModelGateway
                tools = Tools(self.repo, fence, ModelGateway(self.repo, fence, self.responder),
                              self.renderer, self.cad_builder)
                payload = job["payload"]
                if job["kind"] == "cad_component":
                    name = "reuse_component" if payload.get("component_id") else "model_component"
                    args = {"version_id": payload["base_version_id"], "part_key": payload["part_key"]}
                    args.update({"component_id": payload["component_id"]} if payload.get("component_id") else
                                {"brief": payload["brief"]})
                elif job["kind"] in ("render", "locate"):
                    name = "render" if job["kind"] == "render" else "locate_on_drawing"
                    args = {"version_id": payload["version_id"]}
                    if name == "render":
                        tools.run("verify", "build_and_verify", args)
                else:
                    raise ValueError(f"unsupported job kind: {job['kind']}")
                result = tools.run("main", name, args)
                self.repo.end(fence, "needs_review" if result.get("error") or result.get("placeholder") else "succeeded", result)
        except LostLease:
            pass
        except (BudgetExceeded, ProviderRejected) as error:
            # Keep the draft for review: the budget ran out, or the provider
            # refused a request that would only fail again.
            code = "budget_exhausted" if isinstance(error, BudgetExceeded) else "provider_rejected"
            try:
                run = self.repo.run(fence.job_id)
                self.repo.end(fence, "needs_review", {"version_id": run["working_version_id"], "detail_id": run["detail_id"],
                              "open_issues": [str(error)], "stop_reason": code}, {"code": code, "message": str(error)[:500]})
            except LostLease:
                pass
            else:
                continue_if_cut_short(self.repo, fence.job_id)
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
                # Never let the worker thread die: a job it held is retried
                # once its lease expires.
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
