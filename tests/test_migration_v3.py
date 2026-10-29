import json

from app import database as d, geometry, store
from app.program import DetailProgram, FIXTURES, compile_program
from app.repository import Repository
from app.versioning import archive, archive_scene
from test_agent_v3 import program


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
