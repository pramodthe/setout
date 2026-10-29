"""Shared resource permits and deterministic review event order."""
import sqlalchemy as sa
from alembic import op
from app.database import resource_limits, resource_leases

revision = "0002_resources"
down_revision = "0001_v3"
branch_labels = None
depends_on = None


def upgrade():
    con = op.get_bind()
    resource_limits.create(con, checkfirst=True)
    resource_leases.create(con, checkfirst=True)
    for resource, capacity in (("meta", 4), ("cad", 2), ("renderer", 2)):
        if not con.scalar(sa.select(resource_limits.c.id).where(resource_limits.c.id == resource)):
            con.execute(resource_limits.insert().values(id=resource, capacity=capacity))
    if "seq" not in {c["name"] for c in sa.inspect(con).get_columns("reviews")}:
        op.add_column("reviews", sa.Column("seq", sa.Integer))
        con.execute(sa.text("UPDATE reviews SET seq=0 WHERE seq IS NULL"))


def downgrade():
    resource_leases.drop(op.get_bind(), checkfirst=True)
    resource_limits.drop(op.get_bind(), checkfirst=True)
