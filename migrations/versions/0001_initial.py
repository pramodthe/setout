"""Jobs, model calls, stored files and studio scenes."""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def text_id(name, **kw):
    return sa.Column(name, sa.String(length=64), **kw)


def upgrade():
    op.create_table(
        "artifacts", text_id("id", nullable=False), sa.Column("sha256", sa.String(length=64), nullable=False), sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text()), sa.Column("size_bytes", sa.BigInteger()), sa.Column("created_at", sa.Float()),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("sha256"))
    op.create_table(
        "jobs", text_id("id", nullable=False), sa.Column("kind", sa.String(length=32), nullable=False), sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON()), text_id("payload_sha"), sa.Column("idempotency_key", sa.String(length=200)), sa.Column("result", sa.JSON()), sa.Column("error", sa.JSON()),
        sa.Column("attempts", sa.Integer()), sa.Column("max_attempts", sa.Integer()), sa.Column("available_at", sa.Float()), text_id("lease_owner"), sa.Column("lease_epoch", sa.Integer()),
        sa.Column("lease_until", sa.Float()), sa.Column("heartbeat_at", sa.Float()), sa.Column("created_at", sa.Float()), sa.Column("updated_at", sa.Float()),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("idempotency_key"))
    op.create_index(op.f("ix_jobs_state"), "jobs", ["state"])
    op.create_index(op.f("ix_jobs_available_at"), "jobs", ["available_at"])
    op.create_table(
        "job_events", text_id("id", nullable=False), text_id("job_id", nullable=False), sa.Column("seq", sa.Integer()), sa.Column("at", sa.Float()), sa.Column("kind", sa.Text()),
        sa.Column("message", sa.Text()), sa.Column("data", sa.JSON()), sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("job_id", "seq"))
    op.create_table(
        "agent_runs", text_id("id", nullable=False), text_id("job_id", nullable=False), sa.Column("budget", sa.JSON()), sa.Column("consumed", sa.JSON()), sa.Column("reserved", sa.JSON()),
        sa.Column("deadline_at", sa.Float()), sa.Column("outcome", sa.String(length=32)), sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("job_id"))
    op.create_table(
        "agent_steps", text_id("id", nullable=False), text_id("run_id", nullable=False), sa.Column("n", sa.Integer()), sa.Column("kind", sa.String(length=16)), sa.Column("state", sa.String(length=16)),
        sa.Column("request", sa.JSON()), sa.Column("response", sa.JSON()), sa.Column("tokens", sa.JSON()), sa.Column("ms", sa.Float()),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("run_id", "n"))
    op.create_table(
        "studio_scenes", text_id("id", nullable=False), sa.Column("title", sa.Text()), sa.Column("state", sa.String(length=32)), sa.Column("step", sa.Text()), text_id("job_id"),
        text_id("drawing_artifact_id"), text_id("model_artifact_id"), text_id("preview_artifact_id"), sa.Column("steps", sa.JSON()), sa.Column("intents", sa.JSON()), sa.Column("feed", sa.JSON()),
        sa.Column("parts", sa.JSON()), sa.Column("unsure", sa.JSON()), sa.Column("tokens", sa.Integer()), sa.Column("error", sa.Text()), sa.Column("checks", sa.JSON()),
        sa.Column("created_at", sa.Float()), sa.Column("updated_at", sa.Float()), sa.ForeignKeyConstraint(["drawing_artifact_id"], ["artifacts.id"]), sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["model_artifact_id"], ["artifacts.id"]), sa.ForeignKeyConstraint(["preview_artifact_id"], ["artifacts.id"]), sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_studio_scenes_state"), "studio_scenes", ["state"])
    limits = op.create_table("resource_limits", text_id("id", nullable=False), sa.Column("capacity", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "resource_leases", text_id("id", nullable=False), text_id("resource", nullable=False), text_id("job_id", nullable=False), text_id("owner"), sa.Column("epoch", sa.Integer()),
        sa.Column("expires_at", sa.Float()), sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]), sa.ForeignKeyConstraint(["resource"], ["resource_limits.id"]), sa.PrimaryKeyConstraint("id"))
    op.bulk_insert(limits, [{"id": "meta", "capacity": 4}])


def downgrade():
    for name in ("resource_leases", "resource_limits", "studio_scenes", "agent_steps", "agent_runs", "job_events", "jobs", "artifacts"):
        op.drop_table(name)
