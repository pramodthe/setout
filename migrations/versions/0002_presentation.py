"""Optional presentation artifacts, separate from checked geometry."""
import sqlalchemy as sa
from alembic import op

revision = "0002_presentation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("studio_scenes", sa.Column("presentation", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("studio_scenes", "presentation")
