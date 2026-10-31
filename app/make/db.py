"""SQLite for 3D Make, in the existing ``setout.db``.

These tables are the make feature's own projects, messages, scene versions,
jobs and settings. They do not replace the detail-program store, and the
in-memory ``GEN_JOBS`` dict stays in place for the other routes. The first
connection turns WAL on for this file.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT,
  created TEXT,
  updated TEXT,
  active_version INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  role TEXT,
  text TEXT,
  attachments TEXT,
  job_id TEXT,
  created TEXT
);
CREATE TABLE IF NOT EXISTS scene_versions (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  n INTEGER,
  parent_n INTEGER,
  source TEXT,
  script TEXT,
  params TEXT,
  overrides TEXT,
  glb_path TEXT,
  tree_json TEXT,
  preview_path TEXT,
  render_path TEXT,
  profile TEXT,
  model TEXT,
  status TEXT,
  error TEXT,
  created TEXT,
  UNIQUE(project_id, n)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  kind TEXT CHECK(kind IN ('generate', 'rebuild', 'render')),
  state TEXT CHECK(state IN ('queued', 'running', 'done', 'failed')),
  attempt INTEGER,
  events TEXT,
  created TEXT,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path():
    return store.var_root() / "setout.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(SCHEMA)
    return con


def create_project(title: str, project_id: str) -> None:
    stamp = now()
    with connect() as con:
        con.execute(
            "INSERT INTO projects (id, title, created, updated, active_version) VALUES (?, ?, ?, ?, NULL)",
            (project_id, title, stamp, stamp),
        )
        con.commit()


def touch_project(project_id: str, *, title: str | None = None, active_version: int | None = None) -> None:
    with connect() as con:
        row = con.execute("SELECT title, active_version FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        con.execute(
            "UPDATE projects SET title = ?, active_version = ?, updated = ? WHERE id = ?",
            (title if title is not None else row["title"],
             active_version if active_version is not None else row["active_version"],
             now(), project_id),
        )
        con.commit()


def get_project(project_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def add_message(message_id: str, project_id: str, role: str, text: str, *,
                job_id: str | None = None, attachments: list | None = None) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO messages (id, project_id, role, text, attachments, job_id, created)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (message_id, project_id, role, text, json.dumps(attachments or []), job_id, now()),
        )
        con.commit()


def latest_version(project_id: str) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY n DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def insert_version(row: dict) -> None:
    columns = (
        "id", "project_id", "n", "parent_n", "source", "script", "params", "overrides",
        "glb_path", "tree_json", "preview_path", "render_path", "profile", "model",
        "status", "error", "created",
    )
    payload = dict(row)
    payload.setdefault("created", now())
    with connect() as con:
        con.execute(
            f"INSERT INTO scene_versions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(payload.get(name) for name in columns),
        )
        con.commit()


def create_job(job_id: str, project_id: str, kind: str) -> None:
    stamp = now()
    with connect() as con:
        con.execute(
            """INSERT INTO jobs (id, project_id, kind, state, attempt, events, created, updated)
               VALUES (?, ?, ?, 'queued', 0, '[]', ?, ?)""",
            (job_id, project_id, kind, stamp, stamp),
        )
        con.commit()


def set_job_state(job_id: str, state: str, *, attempt: int | None = None) -> None:
    with connect() as con:
        if attempt is None:
            con.execute("UPDATE jobs SET state = ?, updated = ? WHERE id = ?", (state, now(), job_id))
        else:
            con.execute(
                "UPDATE jobs SET state = ?, attempt = ?, updated = ? WHERE id = ?",
                (state, attempt, now(), job_id),
            )
        con.commit()


def append_event(job_id: str, event: dict, *, attempt: int | None = None) -> None:
    with connect() as con:
        row = con.execute("SELECT events, attempt FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        events = json.loads(row["events"] or "[]")
        events.append(event)
        con.execute(
            "UPDATE jobs SET events = ?, attempt = ?, updated = ? WHERE id = ?",
            (json.dumps(events), attempt if attempt is not None else row["attempt"], now(), job_id),
        )
        con.commit()


def get_job(job_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def put_setting(key: str, value: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        con.commit()


def get_setting(key: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]
