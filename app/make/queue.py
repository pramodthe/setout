"""Background runner for 3D Make jobs.

The HTTP handler inserts a queued row and returns. This thread claims it, so
closing the request does not cancel the work. A job left ``running`` by a dead
process is requeued once on startup, and failed if that already happened.
"""
from __future__ import annotations

import json
import os
import threading

from app.make import db
from app.make.agent import generate, rebuild, render_scene
from app.make.attachments import AttachmentError, load_image
from app.make.redact import redact

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def ensure_worker() -> None:
    """Start the daemon thread and recover jobs interrupted by a previous process."""
    if os.environ.get("SETOUT_MAKE_WORKER", os.environ.get("SETOUT_WORKER", "embedded")) == "external":
        recover_stale()
        return
    recover_stale()
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="make-jobs", daemon=True)
        _thread.start()


def stop_worker() -> None:
    _stop.set()


def recover_stale() -> int:
    """Requeue an interrupted running job once. A second interruption fails it."""
    changed = 0
    for job in db.jobs_in_state("running"):
        events = _events(job)
        if any(event.get("kind") == "result" for event in events):
            status = next(event.get("status") for event in reversed(events) if event.get("kind") == "result")
            db.set_job_state(job["id"], "done" if status == "done" else "failed", attempt=job["attempt"])
        elif any(event.get("kind") == "recovered" for event in events):
            db.append_event(job["id"], {"kind": "result", "status": "failed", "error": "interrupted"})
            db.set_job_state(job["id"], "failed", attempt=job["attempt"])
        else:
            db.append_event(job["id"], {"kind": "recovered"})
            db.set_job_state(job["id"], "queued", attempt=job["attempt"])
        changed += 1
    return changed


def process_available(complete_fn=None, runner=None, render_fn=None, limit: int = 20) -> int:
    """Claim and run queued jobs on this thread. The background loop and tests both use this."""
    ran = 0
    while ran < limit:
        job = db.claim_queued()
        if job is None:
            return ran
        _execute(job, complete_fn=complete_fn, runner=runner, render_fn=render_fn)
        ran += 1
    return ran


def _loop() -> None:
    while not _stop.is_set():
        try:
            if process_available() == 0:
                _stop.wait(0.4)
        except Exception as exc:
            # Keep the thread alive. The message is redacted; it is not a key.
            import sys
            sys.stderr.write(redact(f"make worker: {type(exc).__name__}: {exc}") + "\n")
            _stop.wait(0.4)


def _execute(job: dict, *, complete_fn, runner, render_fn) -> None:
    request = _request(job)
    try:
        if job["kind"] == "generate":
            mode = "feedback" if request.get("mode") == "feedback" else "prompt"
            images = _images(job["project_id"], request.get("attachments") or [])
            result = generate(
                request.get("text") or "",
                project_id=job["project_id"],
                mode=mode,
                profile=request.get("profile"),
                job_id=job["id"],
                record_user=False,
                images=images,
                complete_fn=complete_fn,
                runner=runner,
            )
            if request.get("use_blender") and result.get("status") == "done":
                enqueue(job["project_id"], "render", {
                    "version_n": result["version"],
                    "quality": "draft",
                })
        elif job["kind"] == "rebuild":
            rebuild(
                job["id"],
                job["project_id"],
                int(request["version_n"]),
                request.get("overrides") or {},
                runner=runner,
            )
        elif job["kind"] == "render":
            render_scene(
                job["id"],
                job["project_id"],
                int(request["version_n"]),
                request.get("quality") or "draft",
                render_fn=render_fn,
            )
        else:
            db.append_event(job["id"], {"kind": "result", "status": "failed", "error": "unknown job kind"})
            db.set_job_state(job["id"], "failed")
    except Exception as exc:
        message = redact(f"{type(exc).__name__}: {exc}")
        db.append_event(job["id"], {"kind": "result", "status": "failed", "error": message[:800]})
        db.set_job_state(job["id"], "failed")


def enqueue(project_id: str, kind: str, request: dict) -> str:
    import uuid
    job_id = uuid.uuid4().hex
    db.create_job(job_id, project_id, kind)
    db.append_event(job_id, {"kind": "request", **request}, attempt=0)
    return job_id


def _request(job: dict) -> dict:
    for event in _events(job):
        if event.get("kind") == "request":
            return event
    return {}


def _events(job: dict) -> list:
    try:
        events = json.loads(job.get("events") or "[]")
    except json.JSONDecodeError:
        return []
    return events if isinstance(events, list) else []


def _images(project_id: str, attachment_ids: list) -> list[dict]:
    images = []
    for ident in attachment_ids:
        try:
            images.append(load_image(project_id, ident))
        except AttachmentError as exc:
            raise ValueError(str(exc)) from exc
    return images
