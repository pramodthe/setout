"""V3 database schema. SQLite locally; PostgreSQL in worker deployments."""
from __future__ import annotations

import os
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
documents = table("documents", C("sha256", sa.String(64)), C("filename", sa.Text),
                  C("pages", sa.Integer), ref("artifact_id", "artifacts.id", True))
figures = table("figures", ref("document_id", "documents.id"), C("crop_key", sa.Text),
                C("page", sa.Integer), C("width", sa.Integer), C("height", sa.Integer),
                C("rotation_deg", sa.Float), ref("artifact_id", "artifacts.id"),
                sa.UniqueConstraint("document_id", "crop_key"))
reads = table("drawing_reads", ref("figure_id", "figures.id"), C("model", sa.Text),
              C("response", sa.JSON), C("content_sha", sa.String(64)), C("created_at", sa.Float))
details = table("details", C("title", sa.Text), C("situation", sa.JSON), C("kind", sa.Text),
                C("current_version_id", sa.String(64)), C("published_version_id", sa.String(64)))
versions = table("detail_versions", ref("detail_id", "details.id"),
                 ref("parent_version_id", "detail_versions.id", True), C("n", sa.Integer),
                 C("format_version", sa.String(32)), C("content_sha", sa.String(64)),
                 ref("figure_id", "figures.id", True), ref("drawing_read_id", "drawing_reads.id", True),
                 C("program", sa.JSON), C("spec", sa.JSON), C("bindings", sa.JSON),
                 C("origin", sa.Text), C("created_by", sa.Text), C("created_at", sa.Float),
                 sa.UniqueConstraint("detail_id", "n"))
params = table("params", ref("version_id", "detail_versions.id"), C("name", sa.Text),
               C("data", sa.JSON), sa.UniqueConstraint("version_id", "name"))
parts = table("parts", ref("version_id", "detail_versions.id"), C("part_key", sa.Text),
              C("data", sa.JSON), sa.UniqueConstraint("version_id", "part_key"))
relations = table("relations", ref("version_id", "detail_versions.id"), C("data", sa.JSON))
components = table("components", C("family_key", sa.Text), C("code", sa.Text),
                   C("code_sha", sa.String(64)), C("param_schema", sa.JSON),
                   C("feature_schema", sa.JSON), C("brief", sa.Text), C("created_at", sa.Float))
component_builds = table("component_builds", ref("component_id", "components.id"),
                         C("params", sa.JSON), C("input_sha", sa.String(64)),
                         C("toolchain_sha", sa.String(64)), C("artifacts", sa.JSON),
                         C("measured", sa.JSON), C("checks", sa.JSON), C("created_at", sa.Float))
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
runs = table("agent_runs", ref("job_id", "jobs.id"), C("detail_id", sa.String(64)),
             C("base_version_id", sa.String(64)), C("working_version_id", sa.String(64)),
             C("model", sa.Text), C("budget", sa.JSON), C("consumed", sa.JSON),
             C("reserved", sa.JSON), C("deadline_at", sa.Float), C("outcome", sa.String(32)),
             sa.UniqueConstraint("job_id"))
steps = table("agent_steps", ref("run_id", "agent_runs.id"), C("n", sa.Integer),
              C("kind", sa.String(16)), C("state", sa.String(16)), C("request", sa.JSON),
              C("response", sa.JSON), C("call_id", sa.Text), C("tool_name", sa.Text),
              C("arguments", sa.JSON), C("output", sa.JSON), C("tokens", sa.JSON),
              C("ms", sa.Float), sa.UniqueConstraint("run_id", "n"))
operations = table("tool_operations", ref("run_id", "agent_runs.id"), C("call_id", sa.String(100)),
                   C("tool_name", sa.Text), C("args_sha", sa.String(64)), C("state", sa.String(16)),
                   C("result", sa.JSON), sa.UniqueConstraint("run_id", "call_id"))
verifications = table("verifications", ref("version_id", "detail_versions.id"),
                      C("manifest_sha", sa.String(64)), ref("manifest_artifact_id", "artifacts.id"),
                      C("verifier_version", sa.Text), C("checks", sa.JSON), C("quality", sa.JSON),
                      C("created_at", sa.Float))
renders = table("renders", ref("version_id", "detail_versions.id"), C("manifest_sha", sa.String(64)),
                C("profile_sha", sa.String(64)), C("viewer_sha", sa.String(64)), C("view", sa.Text),
                ref("artifact_id", "artifacts.id"), C("tags", sa.Boolean), C("created_at", sa.Float))
critics = table("critic_verdicts", ref("version_id", "detail_versions.id"),
                C("manifest_sha", sa.String(64)), ref("verification_id", "verifications.id"),
                C("render_ids", sa.JSON), C("model", sa.Text), C("verdict", sa.Text),
                C("issues", sa.JSON), C("created_at", sa.Float))
memories = table("memories", C("scope", sa.Text), C("scope_key", sa.Text), C("state", sa.Text),
                 C("kind", sa.Text), C("text", sa.Text), C("tags", sa.JSON),
                 C("source_event_id", sa.String(64)), C("source_version_id", sa.String(64)),
                 C("created_at", sa.Float))
reviews = table("reviews", ref("version_id", "detail_versions.id"), C("reviewer", sa.Text),
                C("action", sa.Text), C("data", sa.JSON), C("at", sa.Float), C("seq", sa.Integer))
component_reviews = table("component_reviews", ref("component_id", "components.id"),
                          ref("component_build_id", "component_builds.id"),
                          C("reviewer", sa.Text), C("action", sa.Text), C("data", sa.JSON), C("at", sa.Float))
sam_regions = table("sam_regions", ref("version_id", "detail_versions.id"),
                    C("part_key", sa.Text), C("data", sa.JSON))
resource_limits = table("resource_limits", C("capacity", sa.Integer, nullable=False))
resource_leases = table("resource_leases", ref("resource", "resource_limits.id"), ref("job_id", "jobs.id"),
                        C("owner", sa.String(64)), C("epoch", sa.Integer), C("expires_at", sa.Float))


def database_url():
    return os.environ.get("SETOUT_DATABASE_URL") or f"sqlite:///{store.var_root().resolve() / 'v3.db'}"


@lru_cache(maxsize=16)
def engine_for(url):
    engine = sa.create_engine(url, connect_args={"timeout": 30, "check_same_thread": False}
                              if url.startswith("sqlite") else {}, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
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
        if engine.dialect.name == "postgresql":
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(current_database()), hashtext(current_schema() || :suffix))"),
                               {"suffix": ":setout-migrations"})
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@contextmanager
def transaction(engine):
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def now(connection):
    if connection.dialect.name == "sqlite":
        return float(connection.scalar(sa.text("SELECT (julianday('now') - 2440587.5) * 86400.0")))
    return float(connection.scalar(sa.text("SELECT EXTRACT(EPOCH FROM clock_timestamp())")))
