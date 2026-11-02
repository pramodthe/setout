"""What the checker found on a studio scene.

Frozen like 0001: never import app.database here.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_studio_checks"
down_revision = "0004_studio"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("studio_scenes", sa.Column("checks", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("studio_scenes", "checks")
