"""Initial persistent v3 schema, frozen.

This revision describes the schema exactly as it stood when it shipped. It must
never import app.database: a later edit to the live metadata would otherwise
silently change what "revision 1" creates. Schema changes go in a new revision;
tests/test_migration_v3.py fails when app.database and the migrations disagree.
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_v3"
down_revision = None
branch_labels = None
depends_on = None

# Creation order is FK-safe (every referenced table precedes its referrers);
# downgrade drops in the reverse order.
TABLES = ("artifacts", "components", "details", "jobs", "memories", "agent_runs",
          "component_builds", "documents", "job_events", "agent_steps", "component_reviews",
          "figures", "tool_operations", "drawing_reads", "detail_versions", "params", "parts",
          "relations", "renders", "reviews", "sam_regions", "verifications", "critic_verdicts")


def key(name="id", nullable=False):
    """Every primary and foreign key in this schema is a 64-character string."""
    return sa.Column(name, sa.String(length=64), nullable=nullable)


def fk(column, target):
    return sa.ForeignKeyConstraint([column], [target])


def upgrade():
    op.create_table(
        "artifacts", key(),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"))
    op.create_table(
        "components", key(),
        sa.Column("family_key", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("code_sha", sa.String(length=64), nullable=True),
        sa.Column("param_schema", sa.JSON(), nullable=True),
        sa.Column("feature_schema", sa.JSON(), nullable=True),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "details", key(),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("situation", sa.JSON(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        key("current_version_id", nullable=True),
        key("published_version_id", nullable=True),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "jobs", key(),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("payload_sha", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=True),
        sa.Column("available_at", sa.Float(), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.Column("heartbeat_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"))
    op.create_index("ix_jobs_available_at", "jobs", ["available_at"], unique=False)
    op.create_index("ix_jobs_state", "jobs", ["state"], unique=False)
    op.create_table(
        "memories", key(),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("scope_key", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        key("source_event_id", nullable=True),
        key("source_version_id", nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "agent_runs", key(), key("job_id"),
        key("detail_id", nullable=True),
        key("base_version_id", nullable=True),
        key("working_version_id", nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("budget", sa.JSON(), nullable=True),
        sa.Column("consumed", sa.JSON(), nullable=True),
        sa.Column("reserved", sa.JSON(), nullable=True),
        sa.Column("deadline_at", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        fk("job_id", "jobs.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"))
    op.create_table(
        "component_builds", key(), key("component_id"),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("input_sha", sa.String(length=64), nullable=True),
        sa.Column("toolchain_sha", sa.String(length=64), nullable=True),
        sa.Column("artifacts", sa.JSON(), nullable=True),
        sa.Column("measured", sa.JSON(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        fk("component_id", "components.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "documents", key(),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        key("artifact_id", nullable=True),
        fk("artifact_id", "artifacts.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "job_events", key(), key("job_id"),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("at", sa.Float(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        fk("job_id", "jobs.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "seq"))
    op.create_table(
        "agent_steps", key(), key("run_id"),
        sa.Column("n", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=True),
        sa.Column("request", sa.JSON(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("call_id", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("arguments", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("tokens", sa.JSON(), nullable=True),
        sa.Column("ms", sa.Float(), nullable=True),
        fk("run_id", "agent_runs.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "n"))
    op.create_table(
        "component_reviews", key(), key("component_id"), key("component_build_id"),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("at", sa.Float(), nullable=True),
        fk("component_build_id", "component_builds.id"),
        fk("component_id", "components.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "figures", key(), key("document_id"),
        sa.Column("crop_key", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("rotation_deg", sa.Float(), nullable=True),
        key("artifact_id"),
        fk("artifact_id", "artifacts.id"),
        fk("document_id", "documents.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "crop_key"))
    op.create_table(
        "tool_operations", key(), key("run_id"),
        sa.Column("call_id", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("args_sha", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        fk("run_id", "agent_runs.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "call_id"))
    op.create_table(
        "drawing_reads", key(), key("figure_id"),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("content_sha", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        fk("figure_id", "figures.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "detail_versions", key(), key("detail_id"),
        key("parent_version_id", nullable=True),
        sa.Column("n", sa.Integer(), nullable=True),
        sa.Column("format_version", sa.String(length=32), nullable=True),
        sa.Column("content_sha", sa.String(length=64), nullable=True),
        key("figure_id", nullable=True),
        key("drawing_read_id", nullable=True),
        sa.Column("program", sa.JSON(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("bindings", sa.JSON(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        fk("detail_id", "details.id"),
        fk("drawing_read_id", "drawing_reads.id"),
        fk("figure_id", "figures.id"),
        fk("parent_version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("detail_id", "n"))
    op.create_table(
        "params", key(), key("version_id"),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "name"))
    op.create_table(
        "parts", key(), key("version_id"),
        sa.Column("part_key", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "part_key"))
    op.create_table(
        "relations", key(), key("version_id"),
        sa.Column("data", sa.JSON(), nullable=True),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "renders", key(), key("version_id"),
        sa.Column("manifest_sha", sa.String(length=64), nullable=True),
        sa.Column("profile_sha", sa.String(length=64), nullable=True),
        sa.Column("viewer_sha", sa.String(length=64), nullable=True),
        sa.Column("view", sa.Text(), nullable=True),
        key("artifact_id"),
        sa.Column("tags", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        fk("artifact_id", "artifacts.id"),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"))
    # reviews.seq arrives in 0002_resources.
    op.create_table(
        "reviews", key(), key("version_id"),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("at", sa.Float(), nullable=True),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "sam_regions", key(), key("version_id"),
        sa.Column("part_key", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "verifications", key(), key("version_id"),
        sa.Column("manifest_sha", sa.String(length=64), nullable=True),
        key("manifest_artifact_id"),
        sa.Column("verifier_version", sa.Text(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        fk("manifest_artifact_id", "artifacts.id"),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"))
    op.create_table(
        "critic_verdicts", key(), key("version_id"),
        sa.Column("manifest_sha", sa.String(length=64), nullable=True),
        key("verification_id"),
        sa.Column("render_ids", sa.JSON(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        fk("verification_id", "verifications.id"),
        fk("version_id", "detail_versions.id"),
        sa.PrimaryKeyConstraint("id"))


def downgrade():
    # Dropping a table drops its indexes (ix_jobs_*) with it.
    for name in reversed(TABLES):
        op.drop_table(name)
