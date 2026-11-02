"""Database schema, in SQLite under SETOUT_VAR."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Column as C

from . import store

metadata = sa.MetaData()


def table(name, *columns, **kw):
    return sa.Table(name, metadata, C("id", sa.String(64), primary_key=True), *columns, **kw)


def ref(name, target, nullable=False):
    return C(name, sa.String(64), sa.ForeignKey(target), nullable=nullable)


artifacts = table("artifacts", C("sha256", sa.String(64), unique=True, nullable=False),
                  C("storage_key", sa.Text, nullable=False), C("media_type", sa.Text),
                  C("size_bytes", sa.BigInteger), C("created_at", sa.Float))
jobs = table("jobs", C("kind", sa.String(32), nullable=False),
             C("state", sa.String(32), nullable=False, index=True), C("payload", sa.JSON),
             C("payload_sha", sa.String(64)), C("idempotency_key", sa.String(200), unique=True),
             C("result", sa.JSON), C("error", sa.JSON), C("attempts", sa.Integer),
             C("max_attempts", sa.Integer), C("available_at", sa.Float, index=True),
             C("lease_owner", sa.String(64)), C("lease_epoch", sa.Integer),
             C("lease_until", sa.Float), C("heartbeat_at", sa.Float),
             C("created_at", sa.Float), C("updated_at", sa.Float))
events = table("job_events", ref("job_id", "jobs.id"), C("seq", sa.Integer),
               C("at", sa.Float), C("kind", sa.Text), C("message", sa.Text), C("data", sa.JSON),
               sa.UniqueConstraint("job_id", "seq"))
runs = table("agent_runs", ref("job_id", "jobs.id"), C("budget", sa.JSON), C("consumed", sa.JSON), C("reserved", sa.JSON),
             C("deadline_at", sa.Float), C("outcome", sa.String(32)), sa.UniqueConstraint("job_id"))
steps = table("agent_steps", ref("run_id", "agent_runs.id"), C("n", sa.Integer), C("kind", sa.String(16)), C("state", sa.String(16)), C("request", sa.JSON),
              C("response", sa.JSON), C("tokens", sa.JSON), C("ms", sa.Float), sa.UniqueConstraint("run_id", "n"))
studio_scenes = table("studio_scenes", C("title", sa.Text), C("state", sa.String(32), index=True), C("step", sa.Text), ref("job_id", "jobs.id", True),
                      ref("drawing_artifact_id", "artifacts.id", True), ref("model_artifact_id", "artifacts.id", True), ref("preview_artifact_id", "artifacts.id", True),
                      C("steps", sa.JSON), C("intents", sa.JSON), C("feed", sa.JSON), C("parts", sa.JSON), C("unsure", sa.JSON), C("tokens", sa.Integer), C("error", sa.Text), C("checks", sa.JSON),
                      C("created_at", sa.Float), C("updated_at", sa.Float))
resource_limits = table("resource_limits", C("capacity", sa.Integer, nullable=False))
resource_leases = table("resource_leases", ref("resource", "resource_limits.id"), ref("job_id", "jobs.id"),
                        C("owner", sa.String(64)), C("epoch", sa.Integer), C("expires_at", sa.Float))


def database_url():
    return f"sqlite:///{store.var_root().resolve() / 'v3.db'}"


@lru_cache(maxsize=16)
def engine_for(url):
    engine = sa.create_engine(url, connect_args={"timeout": 30, "check_same_thread": False}, pool_pre_ping=True)

    @sa.event.listens_for(engine, "connect")
    def pragmas(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
    return engine


def migrate(engine):
    from alembic import command
    from alembic.config import Config
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    with transaction(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@contextmanager
def transaction(engine):
    """One writer at a time: SQLite takes its lock here, so what a transaction reads stays true until it commits."""
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def now(connection):
    return float(connection.scalar(sa.text("SELECT (julianday('now') - 2440587.5) * 86400.0")))
