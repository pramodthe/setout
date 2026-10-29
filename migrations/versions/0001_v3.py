"""Initial persistent v3 schema."""
from alembic import op
from app.database import metadata

revision = "0001_v3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    metadata.drop_all(op.get_bind())
