"""Shared resource permits and deterministic review event order.

Frozen like 0001: never import app.database here. Databases built by the
original create_all-style 0001 may already hold these objects (that revision
created whatever the live metadata contained at the time), so each step checks
the database before acting and the upgrade is safe to run over them.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_resources"
down_revision = "0001_v3"
branch_labels = None
depends_on = None

CAPACITIES = (("meta", 4), ("cad", 2), ("renderer", 2))


def upgrade():
    con = op.get_bind()
    inspector = sa.inspect(con)
    if not inspector.has_table("resource_limits"):
        op.create_table(
            "resource_limits",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("capacity", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"))
    if not inspector.has_table("resource_leases"):
        op.create_table(
            "resource_leases",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("resource", sa.String(length=64), nullable=False),
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column("owner", sa.String(length=64), nullable=True),
            sa.Column("epoch", sa.Integer(), nullable=True),
            sa.Column("expires_at", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.ForeignKeyConstraint(["resource"], ["resource_limits.id"]),
            sa.PrimaryKeyConstraint("id"))
    limits = sa.table("resource_limits", sa.column("id", sa.String), sa.column("capacity", sa.Integer))
    for resource, capacity in CAPACITIES:
        if not con.scalar(sa.select(limits.c.id).where(limits.c.id == resource)):
            con.execute(limits.insert().values(id=resource, capacity=capacity))
    if "seq" not in {c["name"] for c in inspector.get_columns("reviews")}:
        op.add_column("reviews", sa.Column("seq", sa.Integer(), nullable=True))
    con.execute(sa.text("UPDATE reviews SET seq=0 WHERE seq IS NULL"))


def downgrade():
    # Leaves exactly the 0001 schema behind.
    op.drop_table("resource_leases")
    op.drop_table("resource_limits")
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("seq")
