import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import database as d

@pytest.fixture
def empty_engine(tmp_path):
    return d.engine_for("sqlite:///" + str(tmp_path / "empty.db"))


def unique_sets(constraints):
    return {tuple(sorted(columns)) for columns in constraints}


def snapshot(engine):
    """Tables, column nullability and unique column sets as the database has them."""
    with engine.connect() as con:
        inspector = sa.inspect(con)
        names = set(inspector.get_table_names()) - {"alembic_version"}
        return {name: ({c["name"]: c["nullable"] for c in inspector.get_columns(name)},
                       unique_sets([u["column_names"] for u in inspector.get_unique_constraints(name)] +
                                   [i["column_names"] for i in inspector.get_indexes(name) if i["unique"]]))
                for name in names}


def declared(metadata):
    """The same snapshot, read from SQLAlchemy metadata."""
    return {table.name: ({c.name: c.nullable for c in table.columns},
                         unique_sets([[c.name for c in u.columns] for u in table.constraints
                                      if isinstance(u, sa.UniqueConstraint)] +
                                     [[c.name for c in i.columns] for i in table.indexes if i.unique]))
            for table in metadata.tables.values()}


def drift(engine, metadata):
    """Every difference between the migrated database and the metadata.

    compare_metadata covers tables, columns, types, nullability, FKs and indexes.
    It cannot see a unique constraint that metadata dropped but the database
    still has (SQLite reflects them unnamed), so the explicit snapshot adds that.
    """
    with engine.connect() as con:
        context = MigrationContext.configure(con, opts={"compare_type": True})
        diffs = [repr(diff) for diff in compare_metadata(context, metadata)]
    actual, expected = snapshot(engine), declared(metadata)
    for name in sorted(actual.keys() | expected.keys()):
        if actual.get(name) != expected.get(name):
            diffs.append(f"{name}: database {actual.get(name)} != metadata {expected.get(name)}")
    return diffs


def test_migrations_create_exactly_the_declared_schema(empty_engine):
    d.migrate(empty_engine)
    assert drift(empty_engine, d.metadata) == [], "app/database.py changed without a migration"
    with empty_engine.connect() as con:
        assert con.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0001_initial"
        assert dict(con.execute(sa.select(d.resource_limits.c.id, d.resource_limits.c.capacity)).all()) == {
            "meta": 4}


def copy_metadata():
    copy = sa.MetaData()
    for table in d.metadata.sorted_tables:
        table.to_metadata(copy)
    return copy


def add_table(m):
    sa.Table("unmigrated", m, sa.Column("id", sa.String(64), primary_key=True))


def add_column(m):
    m.tables["jobs"].append_column(sa.Column("unmigrated", sa.Integer))


def flip_nullable(m):
    m.tables["jobs"].c.payload.nullable = False


def add_unique(m):
    m.tables["studio_scenes"].append_constraint(sa.UniqueConstraint("title"))


def drop_unique(m):
    steps = m.tables["agent_steps"]
    steps.constraints.discard(next(c for c in steps.constraints if isinstance(c, sa.UniqueConstraint)))


def change_type(m):
    m.tables["studio_scenes"].c.title.type = sa.Integer()


@pytest.mark.parametrize("change", [add_table, add_column, flip_nullable, add_unique, drop_unique, change_type])
def test_schema_drift_check_is_not_vacuous(empty_engine, change):
    d.migrate(empty_engine)
    edited = copy_metadata()
    change(edited)
    assert drift(empty_engine, edited)
