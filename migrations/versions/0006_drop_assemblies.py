"""Planned assemblies are gone: the studio replaced them.

Frozen like 0001: never import app.database here.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_drop_assemblies"
down_revision = "0005_studio_checks"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index(op.f("ix_assemblies_state"), table_name="assemblies")
    op.drop_table("assemblies")


def downgrade():
    op.create_table(
        "assemblies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=True),
        sa.Column("crop_id", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True),
        sa.Column("step", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("shortlists", sa.JSON(), nullable=True),
        sa.Column("choices", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("scene_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("verdict_note", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["scene_artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_assemblies_state"), "assemblies", ["state"])
