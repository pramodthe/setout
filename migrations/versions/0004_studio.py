"""Studio scenes: a drawing built by Muse in a live Blender sandbox.

Frozen like 0001: never import app.database here.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_studio"
down_revision = "0003_assemblies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "studio_scenes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True),
        sa.Column("step", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("drawing_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("model_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("preview_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("intents", sa.JSON(), nullable=True),
        sa.Column("feed", sa.JSON(), nullable=True),
        sa.Column("parts", sa.JSON(), nullable=True),
        sa.Column("unsure", sa.JSON(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["drawing_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["model_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["preview_artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_studio_scenes_state"), "studio_scenes", ["state"])


def downgrade():
    op.drop_index(op.f("ix_studio_scenes_state"), table_name="studio_scenes")
    op.drop_table("studio_scenes")
