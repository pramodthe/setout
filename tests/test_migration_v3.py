import json
import os
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app import database as d, geometry, store
from app.program import DetailProgram, FIXTURES, compile_program
from app.repository import Repository
from app.versioning import archive, archive_scene
from test_agent_v3 import program

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(params=["sqlite", "postgres"])
def empty_engine(tmp_path, request):
    """A fresh, unmigrated database. PostgreSQL gets a throwaway schema."""
    if request.param == "sqlite":
        yield d.engine_for("sqlite:///" + str(tmp_path / "empty.db"))
        return
    url = os.environ.get("SETOUT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SETOUT_TEST_DATABASE_URL is not set")
    admin = sa.create_engine(url)
    schema = "test_" + uuid.uuid4().hex
    with admin.begin() as con:
        con.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    engine = d.engine_for(url + ("&" if "?" in url else "?") + "options=" + quote("-csearch_path=" + schema))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as con:
            con.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def alembic(engine, action, revision):
    """Run one alembic command the way app.database.migrate does."""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with d.transaction(engine) as connection:
        config.attributes["connection"] = connection
        getattr(command, action)(config, revision)


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
    No type-affinity diffs need filtering on SQLite or PostgreSQL today; filter
    them here, with a reason, if a backend ever reports one.
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
        assert con.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0002_resources"
        assert dict(con.execute(sa.select(d.resource_limits.c.id, d.resource_limits.c.capacity)).all()) == {
            "meta": 4, "cad": 2, "renderer": 2}


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
    m.tables["details"].append_constraint(sa.UniqueConstraint("title"))


def drop_unique(m):
    figures = m.tables["figures"]
    figures.constraints.discard(next(c for c in figures.constraints if isinstance(c, sa.UniqueConstraint)))


def change_type(m):
    m.tables["memories"].c.kind.type = sa.Integer()


@pytest.mark.parametrize("change", [add_table, add_column, flip_nullable, add_unique, drop_unique, change_type])
def test_schema_drift_check_is_not_vacuous(empty_engine, change):
    d.migrate(empty_engine)
    edited = copy_metadata()
    change(edited)
    assert drift(empty_engine, edited)


def test_upgrade_over_a_create_all_era_database(empty_engine):
    # The original 0001 ran metadata.create_all, so a database stamped 0001 may
    # already hold everything 0002 adds. Reproduce that from frozen revisions
    # (not the live metadata, which will keep moving) and upgrade over it.
    alembic(empty_engine, "upgrade", "0002_resources")
    alembic(empty_engine, "stamp", "0001_v3")
    with d.transaction(empty_engine) as con:
        con.execute(d.details.insert().values(id="detail"))
        con.execute(d.versions.insert().values(id="version", detail_id="detail", n=1))
        con.execute(d.reviews.insert().values(id="review", version_id="version", action="accept"))
    d.migrate(empty_engine)
    assert drift(empty_engine, d.metadata) == []
    with empty_engine.connect() as con:
        assert con.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0002_resources"
        assert con.scalar(sa.select(sa.func.count()).select_from(d.resource_limits)) == 3
        assert con.scalar(sa.select(d.reviews.c.seq).where(d.reviews.c.id == "review")) == 0


def test_downgrade_drops_everything_in_fk_safe_order_and_upgrades_again(empty_engine):
    d.migrate(empty_engine)
    alembic(empty_engine, "downgrade", "0001_v3")
    after_0001 = snapshot(empty_engine)
    assert not {"resource_limits", "resource_leases"} & after_0001.keys()
    assert "seq" not in after_0001["reviews"][0]
    d.migrate(empty_engine)  # SQLite rebuilt reviews to drop seq; its keys must survive
    assert drift(empty_engine, d.metadata) == []
    alembic(empty_engine, "downgrade", "base")
    assert snapshot(empty_engine) == {}
    d.migrate(empty_engine)
    assert drift(empty_engine, d.metadata) == []


def test_fixture_history_survives_archive_and_registry_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VAR", tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    for fixture in FIXTURES:
        raw = program()
        raw.update(version="2.0", detail_id="old-" + fixture, origin="legacy test", source={"filename": "own.pdf", "sha256": "a" * 64,
                   "page_index": 0, "crop_id": "p0_x", "page_width_pt": 300, "page_height_pt": 300})
        raw["parts"][0].update(shape="fixture", fixture=fixture)
        parsed = DetailProgram.model_validate(raw)
        record = {"program": parsed.model_dump(mode="json"), "spec": compile_program(parsed).model_dump(mode="json")}
        (library / f"old-{fixture}.json").write_text(json.dumps(record))
    repo = Repository("sqlite:///" + str(tmp_path / "v3.db"), tmp_path / "artifacts")
    assert archive(repo, dry_run=True)["dry_run"]
    first = archive(repo)
    assert archive(repo)["versions"] == first["versions"]
    for fixture in FIXTURES:
        monkeypatch.delitem(geometry.REGISTRY, "fixture_" + fixture, raising=False)
    monkeypatch.setattr(geometry,"LEGACY_FIXTURE_REGISTRY",{})
    # A new repository process handle reads bytes; it never invokes a fixture.
    reopened = Repository(str(repo.engine.url), repo.artifacts.root)
    for fixture in FIXTURES:
        detail = reopened.get(d.details, "old-" + fixture)
        version = reopened.get(d.versions, detail["current_version_id"])
        assert version["format_version"] == "legacy-v2"
        scene = archive_scene(reopened, version)
        assert scene["solids"][0]["tri_count"] > 0
        assert reopened.artifacts.read(version["bindings"]["archive"]["glb"])[:4] == b"glTF"
