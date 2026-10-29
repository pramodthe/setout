"""Process-local service handles; job state always resides in the database."""
from __future__ import annotations

import os
import threading

from .database import database_url
from .repository import Repository

_lock = threading.RLock()
_repositories = {}
_workers = {}


def repository():
    key = (database_url(), os.environ.get("SETOUT_ARTIFACT_ROOT"))
    with _lock:
        if key not in _repositories:
            _repositories[key] = Repository(key[0], key[1])
        return _repositories[key]


def ensure_worker():
    if os.environ.get("SETOUT_WORKER", "embedded") != "embedded":
        return
    repo = repository()
    key = str(repo.engine.url)
    with _lock:
        if key not in _workers or not _workers[key][1].is_alive():
            from .worker import Worker
            worker = Worker(repo)
            thread = threading.Thread(target=worker.serve, daemon=True, name="setout-worker")
            _workers[key] = worker, thread
            thread.start()
