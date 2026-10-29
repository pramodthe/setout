"""Import source evidence and archive the v2 library without rerunning AI."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

import sqlalchemy as sa

from . import database as d, store
from .geometry import build_assembly, export_glb, export_scene_json
from .repository import canonical, row, sha, uid
from .spec import DetailSpec


def register_figure(repo, doc_id, crop_id):
    document = store.get_document(doc_id)
    path = store.figure_path(doc_id, crop_id)
    if not document or path is None:
        raise KeyError("unknown source figure")
    figure = next((f for f in document["figures"] if f["crop_id"] == crop_id), None)
    if figure is None:
        raise KeyError("unknown source figure")
    crop = repo.artifacts.put(path.read_bytes(), "image/jpeg" if path.suffix == ".jpg" else "image/png")
    pdf_path = store.var_root() / "docs" / doc_id / "original.pdf"
    pdf = repo.artifacts.put(pdf_path.read_bytes(), "application/pdf") if pdf_path.is_file() else None
    ident = sha({"document": doc_id, "crop": crop_id})[:32]
    with d.transaction(repo.engine) as con:
        if not con.scalar(sa.select(d.documents.c.id).where(d.documents.c.id == doc_id)):
            if pdf:
                repo.artifact(con, pdf)
            con.execute(d.documents.insert().values(id=doc_id, sha256=document["sha256"],
                        filename=document["filename"], pages=document["pages"], artifact_id=pdf["id"] if pdf else None))
        if not con.scalar(sa.select(d.figures.c.id).where(d.figures.c.id == ident)):
            repo.artifact(con, crop)
            con.execute(d.figures.insert().values(id=ident, document_id=doc_id, crop_key=crop_id,
                        page=figure["page"], width=figure["width"], height=figure["height"],
                        rotation_deg=0, artifact_id=crop["id"]))
    return ident


def archive(repo, dry_run=False, skip_existing=False, skip_bad=False):
    """Import the v2 AI library (var/library/*.json) as archived versions.

    The hand-transcribed seeds are not imported: they are reference specs in
    app/seed.py, served and corrected from code. Everything AI-made lives in
    the database. With skip_existing, details already imported are left alone
    (used at startup, so an old library is never served from files). With
    skip_bad, a record that can't be loaded or built is reported and left out
    instead of stopping the import of every other record."""
    from .generate import library_dir
    from .seed import SEED_IDS
    records = {}
    skipped = {}
    def bad(name, error):
        if not skip_bad:
            raise error
        skipped[name] = f"{type(error).__name__}: {str(error)[:200]}"
    for path in library_dir().glob("*.json"):
        try:
            record = json.loads(path.read_text())
            if "spec" in record and record["spec"]["detail_id"] not in SEED_IDS:
                records[record["spec"]["detail_id"]] = record
        except (ValueError, KeyError, TypeError) as error:
            bad(path.name, error)
    if skip_existing:
        with repo.engine.connect() as con:
            present = set(con.execute(sa.select(d.details.c.id)).scalars())
        records = {k: v for k, v in records.items() if k not in present}
    prepared = []
    for detail_id, record in records.items():
        try:
            override = store.var_root() / f"spec-{detail_id}.json"
            spec = DetailSpec.model_validate_json(override.read_text()) if override.is_file() else DetailSpec.model_validate(record["spec"])
            physical = build_assembly(spec)
            display = build_assembly(spec, use_display=True)
        except Exception as error:  # noqa: BLE001 - reported per record
            bad(detail_id, error)
            continue
        prepared.append((detail_id, record, spec, physical, display))
    if dry_run:
        return {"dry_run": True, "details": [p[0] for p in prepared]}
    imported = []
    # Validate/export all inputs before advancing any library pointer.
    staged = []
    for detail_id, record, spec, physical, display in prepared:
        source = spec.source
        try:
            document = store.find_document_by_sha(source.sha256)
            if not document:
                raise KeyError("source PDF is unavailable")
            figure_id = register_figure(repo, document["id"], source.crop_id)
        except (StopIteration, KeyError, FileNotFoundError):
            figure_id = None
        payloads = {"scene": (canonical(export_scene_json(spec, physical)).encode(), "application/json"),
                    "display_scene": (canonical(export_scene_json(spec, display)).encode(), "application/json"),
                    "glb": (export_glb(physical, [c.id for c in spec.components]), "model/gltf-binary"),
                    "record": (canonical(record).encode(), "application/json")}
        refs = {name: repo.artifacts.put(data, media) for name, (data, media) in payloads.items()}
        staged.append((detail_id, record, spec, refs, figure_id))
    with d.transaction(repo.engine) as con:
        for detail_id, record, spec, refs, figure_id in staged:
            bindings = {"archive": {name: ref["id"] for name, ref in refs.items()}}
            for ref in refs.values():
                repo.artifact(con, ref)
            existing = con.execute(sa.select(d.versions).where(d.versions.c.detail_id == detail_id,
                                   d.versions.c.format_version == "legacy-v2")).mappings().first()
            if existing:
                if existing["bindings"] != bindings:
                    raise ValueError(f"legacy source changed since import: {detail_id}")
                imported.append(existing["id"])
                continue
            read_id = None
            if figure_id and record.get("read"):
                response = json.loads(json.dumps(record["read"]))
                for key,prefix in (("dimensions","d"),("elements","l")):
                    for i,item in enumerate(response.get(key, [])):
                        item["id"] = f"{prefix}{i}"
                read_id = uid()
                con.execute(d.reads.insert().values(id=read_id, figure_id=figure_id, model="imported-v2",
                            response=response, content_sha=sha(response), created_at=d.now(con)))
            result = repo.insert_version(con, detail_id=detail_id, expected_parent=None,
                        program=record.get("program") or {}, spec=spec.model_dump(mode="json"),
                        bindings=bindings, origin=spec.origin, created_by="migration", format_version="legacy-v2",
                        figure_id=figure_id, read_id=read_id)
            state = record.get("review") or {}
            if state.get("status") == "reviewed":
                repo.review_event(con, result["version_id"], state.get("by") or "legacy reviewer", "imported-v2", state)
                con.execute(d.details.update().where(d.details.c.id == detail_id).values(
                            published_version_id=result["version_id"]))
            imported.append(result["version_id"])
    return {"dry_run": False, "versions": imported, "details": [p[0] for p in prepared], "skipped": skipped}


def import_library(repo):
    """Startup: bring any v2 library records not yet in the database across."""
    try:
        result = archive(repo, skip_existing=True, skip_bad=True)
    except Exception as error:  # noqa: BLE001 - the server starts regardless
        print(f"[setout] v2 library import failed: {error}")
        return None
    for name, reason in result["skipped"].items():
        print(f"[setout] v2 library record not imported: {name}: {reason}")
    return result


def backup(destination):
    """Copy original mutable records; preserve a consistent legacy SQLite DB."""
    from .generate import library_dir
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=False)
    files = list(library_dir().glob("*.json")) + list(store.var_root().glob("spec-*.json"))
    manifest = []
    for index, path in enumerate(files):
        copy = target / f"{index}-{path.name}"
        shutil.copy2(path, copy)
        manifest.append({"original":str(path.resolve()), "copy":copy.name, "sha256":__import__("hashlib").sha256(copy.read_bytes()).hexdigest()})
    database = store.var_root() / "setout.db"
    if database.is_file():
        with sqlite3.connect(database) as source, sqlite3.connect(target / "setout.db") as dest:
            source.backup(dest)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return str(target.resolve())


def archive_scene(repo, version, display=False):
    name = "display_scene" if display else "scene"
    scene = json.loads(repo.artifacts.read(version["bindings"]["archive"][name]))
    if not scene.get("verification"):
        scene["verification"] = [{"id":"legacy-format","stage":"C","status":"UNVERIFIED",
            "description":"Archived v2 geometry; no native v3 verification has been performed", "required":True}]
    return scene


if __name__ == "__main__":
    import argparse
    from .repository import Repository
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", help="New directory for a consistent backup of the original records")
    args = parser.parse_args()
    if args.backup:
        backup(args.backup)
    print(json.dumps(archive(Repository(), args.dry_run), indent=2))
