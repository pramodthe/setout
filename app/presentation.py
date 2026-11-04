"""Durable Blender finishing jobs on Modal. The base model is available first."""
from __future__ import annotations

import json
import os
import struct

import sqlalchemy as sa

from . import database as d

VERSION = "surface-studio-3-modal"
OUTPUTS = {"model.glb": "model/gltf-binary", "render.jpg": "image/jpeg"}


def enabled():
    return os.environ.get("SETOUT_RENDERER", "modal") == "modal"


def validate_glb(data):
    """A render must never read arbitrary file/network URIs from an imported model."""
    if len(data) < 20 or len(data) > 100 * 1024 * 1024:
        raise ValueError("Invalid GLB size")
    magic, version, size, length, kind = struct.unpack_from("<4sIIII", data)
    if magic != b"glTF" or version != 2 or size != len(data) or kind != 0x4E4F534A or 20 + length > len(data):
        raise ValueError("Invalid GLB header")
    doc = json.loads(data[20:20 + length])
    for item in doc.get("images", []) + doc.get("buffers", []):
        if "uri" in item:  # accept only bufferView images and the GLB's own binary chunk
            raise ValueError("Presentation requires an embedded GLB without external URIs")
    return doc


def render_assets(data):
    validate_glb(data)
    if not enabled():
        raise ValueError("Modal presentation rendering is disabled")
    import modal
    found = {}
    for name, chunk in modal.Function.from_name("setout-presentation", "finish_model").remote_gen(data):
        if name not in (*OUTPUTS, "render.json"):
            raise ValueError("Unexpected rendering output")
        found[name] = found.get(name, b"") + chunk
    if any(name not in found for name in (*OUTPUTS, "render.json")):
        raise ValueError("Modal returned an incomplete render")
    return found


def schedule_in(repo, con, row):
    """Called in the same transaction that publishes a ready model."""
    if not enabled() or row["state"] != "ready" or not row["model_artifact_id"]:
        return
    payload = {"scene_id": row["id"], "model": row["model_artifact_id"], "version": VERSION}
    key = f"render:{row['id']}:{row['model_artifact_id']}:{VERSION}"
    previous = con.execute(sa.select(d.jobs).where(d.jobs.c.idempotency_key == key)).mappings().first()
    if previous and (previous["state"] in ("failed", "needs_review") or (previous["result"] or {}).get("discarded")):
        # A later publication may retry a failed or superseded render, even when geometry is identical.
        con.execute(d.jobs.update().where(d.jobs.c.id == previous["id"]).values(idempotency_key=None))
    job = repo.enqueue_in(con, "studio_render", payload, key)
    value = {"job_id": job["id"], "source_model_id": row["model_artifact_id"], **(job["result"] or {})}
    con.execute(d.studio_scenes.update().where(d.studio_scenes.c.id == row["id"]).values(presentation=value))


def status(repo, scene):
    value = scene.get("presentation") or {}
    if scene["state"] != "ready" or value.get("source_model_id") != scene["model_artifact_id"]:
        return None
    job = repo.get(d.jobs, value["job_id"])
    return {**value, "state": job["state"]}


def artifacts(value):
    return {value[k] for k in ("surface_artifact_id", "render_artifact_id") if value and value.get(k)}


def run_job(repo, fence, job, gateway):
    payload = job["payload"]

    def current(con):
        row = con.execute(sa.select(d.studio_scenes).where(d.studio_scenes.c.id == payload["scene_id"])).mappings().first()
        return row and row["state"] == "ready" and row["model_artifact_id"] == payload["model"] and (row["presentation"] or {}).get("job_id") == job["id"]

    with repo.engine.connect() as con:
        wanted = current(con)
    if not wanted:
        repo.end(fence, "succeeded", {"discarded": True})
        return
    found = render_assets(repo.artifacts.read(payload["model"]))
    validate_glb(found["model.glb"])
    stats = json.loads(found["render.json"])
    with d.transaction(repo.engine) as con:
        if not current(con):  # a revision/deletion won while Blender was rendering
            if not con.scalar(sa.select(d.jobs.c.id).where(d.jobs.c.id == job["id"])):
                return
            repo._check(con, fence)
            repo._end(con, job["id"], "succeeded", {"discarded": True})
            return
        repo._check(con, fence)
        saved = {}
        for name, field in (("model.glb", "surface_artifact_id"), ("render.jpg", "render_artifact_id")):
            reference = repo.artifacts.put(found[name], OUTPUTS[name])
            repo.artifact(con, reference)
            saved[field] = reference["id"]
        result = {**saved, "stats": stats}
        con.execute(d.studio_scenes.update().where(d.studio_scenes.c.id == payload["scene_id"]).values(
            presentation={"job_id": job["id"], "source_model_id": payload["model"], **result}, updated_at=d.now(con)))
        repo._end(con, job["id"], "succeeded", result)
