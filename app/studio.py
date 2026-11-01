"""A drawing to a 3D scene, built by Muse in a live Blender sandbox.

Apart from the verified versions: it never writes a version, never reaches the verifier and can't be published. Muse's code runs only in the no-network sandbox. The scene is the list of
code steps it ran, so a revision carries on from them and an undo drops one. Sizes are the model's assumptions, because the drawing isn't to scale.
"""
from __future__ import annotations

import io
import json

from PIL import Image

from . import database as d, meta_client
from .repository import BudgetExceeded, sha, uid
from .runtime import ensure_worker, repository
from .store import figure_path, store_pdf

FEED = 200
REVISE = ("A person looked at your model and says: {text}\nThe scene is as you left it, shown here with the drawing. Change what they ask for and anything it affects, keep everything that is right, "
          "and call finish when it's done.")


class Busy(RuntimeError):
    """The scene is being worked on."""


def _set(repo, ident, **values):
    with d.transaction(repo.engine) as con:
        con.execute(d.studio_scenes.update().where(d.studio_scenes.c.id == ident).values(**values, updated_at=d.now(con)))


def _keep(repo, data, media):
    reference = repo.artifacts.put(data, media)
    with d.transaction(repo.engine) as con:
        repo.artifact(con, reference)
    return reference["id"]


def _drawing(filename, data) -> bytes:
    """The drawing as a PNG, from a PDF's first figure or from a picture."""
    if data[:4] == b"%PDF":
        document = store_pdf(filename, data)
        if not document["figures"]:
            raise ValueError("no drawing found in this PDF")
        data = figure_path(document["id"], document["figures"][0]["crop_id"]).read_bytes()
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except OSError:
        raise ValueError("this file is not a PDF or a picture")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _queue(repo, ident, feedback=None):
    job = repo.enqueue("studio_build", {"scene_id": ident, "feedback": feedback})
    _set(repo, ident, state="working", step="Reading the drawing", job_id=job["id"], error=None)
    ensure_worker()


def start(filename, data) -> dict:
    repo = repository()
    drawing = _keep(repo, _drawing(filename, data), "image/png")
    ident = uid()
    with d.transaction(repo.engine) as con:
        at = d.now(con)
        con.execute(d.studio_scenes.insert().values(id=ident, title="New detail", state="working", step="Starting", drawing_artifact_id=drawing, steps=[], intents=[], feed=[], parts=[], unsure=[],
                                                    tokens=0, created_at=at, updated_at=at))
    _queue(repo, ident)
    return get(ident)


def get(ident) -> dict:
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    if row["state"] == "working" and row["job_id"]:
        job = repo.get(d.jobs, row["job_id"])
        if job["state"] in ("failed", "cancelled", "needs_review"):
            row = {**row, "state": "failed", "error": (job["error"] or {}).get("message") or "the job stopped"}
    return {k: v for k, v in row.items() if k != "steps"} | {"step_count": len(row["steps"] or [])}


def listing() -> list:
    rows = repository().list(d.studio_scenes, order=d.studio_scenes.c.created_at.desc())
    return [{"id": r["id"], "title": r["title"], "state": r["state"], "parts": len(r["parts"] or []), "has_preview": bool(r["preview_artifact_id"]), "created_at": r["created_at"]} for r in rows]


def file_of(ident, which) -> tuple[bytes, str]:
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    column, media = {"drawing": ("drawing_artifact_id", "image/png"), "model.glb": ("model_artifact_id", "model/gltf-binary"), "preview": ("preview_artifact_id", "image/jpeg")}.get(which, (None, None))
    if column is None or not row[column]:
        raise KeyError(which)
    return repo.artifacts.read(row[column]), media


def feedback(ident, text) -> dict:
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    if not text.strip():
        raise ValueError("say what to change")
    if get(ident)["state"] == "working":
        raise Busy("Muse is working on this scene")
    if not meta_client.configured():
        raise ValueError("AI not configured")
    _set(repo, ident, feed=[*(row["feed"] or []), {"kind": "person", "text": text.strip()}][-FEED:])
    _queue(repo, ident, text.strip())
    return get(ident)


def _publish(repo, ident, steps):
    from goal import blender_live
    found = blender_live.sandbox(steps, ("three_quarter",), export=True)
    scene = json.loads(found["scene.json"])
    if scene["error"]:
        raise ValueError(scene["error"])
    values = {"parts": scene["parts"]}
    if found.get("model.glb"):
        values["model_artifact_id"] = _keep(repo, found["model.glb"], "model/gltf-binary")
    if found.get("three_quarter.jpg"):
        values["preview_artifact_id"] = _keep(repo, found["three_quarter.jpg"], "image/jpeg")
    return values


def undo(ident) -> dict:
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    if get(ident)["state"] == "working":
        raise Busy("Muse is working on this scene")
    if not row["steps"]:
        raise ValueError("nothing to undo")
    steps, intents = row["steps"][:-1], (row["intents"] or [])[:-1]
    values = _publish(repo, ident, steps) if steps else {"parts": [], "model_artifact_id": None, "preview_artifact_id": None}
    _set(repo, ident, steps=steps, intents=intents, feed=[*(row["feed"] or []), {"kind": "system", "text": "Undid the last step"}][-FEED:], **values)
    return get(ident)


def _shown(repo):
    """Pictures go to the model by reference, so a checkpoint never holds an image."""
    def show(data, longest=1000):
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((longest, longest))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=86)
        return {"type": "input_image", "image_url": "artifact:" + _keep(repo, out.getvalue(), "image/jpeg")}
    return show


def build(repo, job, gateway):
    from goal import blender_live
    ident = job["payload"]["scene_id"]
    row = repo.get(d.studio_scenes, ident)
    feed = list(row["feed"] or [])
    turns = {"n": 0}

    def respond(payload):
        turns["n"] += 1
        return gateway.call({"model": meta_client.default_model(), **payload}, "studio", f"turn-{turns['n']}")

    def say(text):
        kind = "muse"
        if "] check " in text:
            text, kind = text.split("] check ", 1)[1], "check"
        elif "] run_python " in text:
            text, kind = text.split("] run_python ", 1)[1].split(" -> ")[0], "step"
        elif "] look" in text or "] undo" in text or "] finish" in text:
            kind, text = "tool", ("Checking other views" if "] look" in text else "Undoing a step" if "] undo" in text else "Finishing")
        else:
            text = text.split("] ", 1)[-1]
        feed.append({"kind": kind, "text": text[:400]})
        _set(repo, ident, feed=feed[-FEED:], step=text[:120])

    def on_step(live, scene, found):
        values = {"steps": live.steps, "intents": live.intents, "parts": scene["parts"]}
        if found.get("three_quarter.jpg"):
            values["preview_artifact_id"] = _keep(repo, found["three_quarter.jpg"], "image/jpeg")
        _set(repo, ident, **values)

    brief = REVISE.format(text=job["payload"]["feedback"]) if job["payload"].get("feedback") else None
    def ask(prompt, images, schema, name):
        references = [repo.artifacts.put(data, "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png") for data in images]
        with d.transaction(repo.engine) as con:
            for reference in references:
                repo.artifact(con, reference)
        spent = repo.run(gateway.fence.job_id)["consumed"].get("tokens", 0)
        answer = gateway.json(prompt, [r["id"] for r in references], schema, name, f"{name}:{sha(prompt + ''.join(r['id'] for r in references))[:16]}")
        return answer, repo.run(gateway.fence.job_id)["consumed"].get("tokens", 0) - spent

    held = row["checks"] or {}
    live = blender_live.Live(repo.artifacts.read(row["drawing_artifact_id"]), respond, steps=row["steps"], intents=row["intents"], say=say, show=_shown(repo), on_step=on_step, ask=ask, features=held.get("features"))
    try:
        live.run(brief)
    except BudgetExceeded:  # what it built so far still stands
        say("[0] The budget ran out, so this stops here.")
    if not live.steps:
        raise ValueError("Muse built nothing from this drawing")
    finished = live.result or {}
    _set(repo, ident, step="Preparing the model")
    values = _publish(repo, ident, live.steps)
    feed.append({"kind": "muse", "text": finished.get("what") or "Stopped before finishing: the budget ran out."})
    checks = {"features": live.features, "rounds": [{k: c[k] for k in ("right", "total", "passed")} for c in live.checks], "last": live.checks[-1] if live.checks else held.get("last")}
    _set(repo, ident, state="ready", step=None, title=(finished.get("what") or row["title"])[:90], unsure=finished.get("unsure") or row["unsure"] or [], feed=feed[-FEED:],
         tokens=(row["tokens"] or 0) + live.spent, checks=checks, **values)


def run_job(repo, fence, job, gateway):
    ident = job["payload"]["scene_id"]
    try:
        build(repo, job, gateway)
    except (ValueError, KeyError, OSError) as error:
        if not isinstance(error, (ConnectionError, TimeoutError)):
            _set(repo, ident, state="failed", error=str(error)[:300])
        raise
    repo.end(fence, "succeeded", {"scene_id": ident})


