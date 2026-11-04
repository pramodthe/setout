"""A drawing to a 3D scene, built by Muse in a live Blender sandbox.

Nothing here is verified, and the app says so. Muse's code runs only in the no-network sandbox. The scene is the list of
code steps it ran, so a revision carries on from them and an undo drops one. Sizes are the model's assumptions, because the drawing isn't to scale.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error

import sqlalchemy as sa
from PIL import Image

from . import database as d, meta_client, presentation
from .model_gateway import ModelGateway, ProviderRejected
from .repository import BudgetExceeded, billed_tokens, sha, uid
from .runtime import ensure_worker, repository
from .store import read_pdf

FEED = 200
NO_MODEL = "Blender couldn't export this model. Tell Muse what to change to try again."
NEW_TITLE = "New detail"
LONGEST_TITLE = 60
PICTURE = re.compile(r"artifact:([0-9a-f]{64})")
REVISE = ("A person looked at your model and says: {text}\nThe scene is as you left it, shown here with the drawing. Change what they ask for and anything it affects, keep everything that is right, "
          "and call finish when it's done.")


class Busy(RuntimeError):
    """The scene is being worked on."""


def _clip(text, limit=400):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") or text[:limit]
    return cut + "…"


def _set(repo, ident, **values):
    with d.transaction(repo.engine) as con:
        con.execute(d.studio_scenes.update().where(d.studio_scenes.c.id == ident).values(**values, updated_at=d.now(con)))
        if values.get("state") == "ready":
            row = con.execute(sa.select(d.studio_scenes).where(d.studio_scenes.c.id == ident)).mappings().one()
            presentation.schedule_in(repo, con, row)


def _keep(repo, data, media):
    reference = repo.artifacts.put(data, media)
    with d.transaction(repo.engine) as con:
        repo.artifact(con, reference)
    return reference["id"]


def _drawing(data) -> bytes:
    """The drawing as a PNG, from a PDF's first figure or from a picture. Only the PNG is kept, so deleting a scene deletes its drawing."""
    if data[:4] == b"%PDF":
        document = read_pdf(data, first_only=True)
        if not document["figures"]:
            raise ValueError("no drawing found in this PDF")
        data = document["images"][document["figures"][0]["crop_id"]]
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except OSError:
        raise ValueError("this file is not a PDF or a picture")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _queue(repo, ident, feedback=None, method="serial"):
    job = repo.enqueue("studio_build", {"scene_id": ident, "feedback": feedback, "method": method, "from_step": len(repo.get(d.studio_scenes, ident)["steps"] or [])})
    _set(repo, ident, state="working", step="Reading the drawing", job_id=job["id"], error=None)
    ensure_worker()


def start(filename, data, method="serial") -> dict:
    if method not in ("serial", "hybrid"):
        raise ValueError("unknown build method")
    repo = repository()
    drawing = _keep(repo, _drawing(data), "image/png")
    ident = uid()
    with d.transaction(repo.engine) as con:
        at = d.now(con)
        con.execute(d.studio_scenes.insert().values(id=ident, title=NEW_TITLE, state="working", step="Starting", drawing_artifact_id=drawing, steps=[], intents=[], feed=[], parts=[], unsure=[],
                                                    tokens=0, created_at=at, updated_at=at))
    _queue(repo, ident, method=method)
    return get(ident)


def get(ident) -> dict:
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    live_model = False
    queued = False
    if row["state"] == "working" and row["job_id"]:
        job = repo.get(d.jobs, row["job_id"])
        queued = job["state"] == "queued"
        live_model = job["payload"].get("method") == "hybrid" and bool(row["model_artifact_id"])
        if job["state"] in ("failed", "needs_review"):
            row = {**row, "state": "failed", "error": (job["error"] or {}).get("message") or "the job stopped"}
    waiting = (row["checks"] or {}).get("measure_job")
    return {k: v for k, v in row.items() if k != "steps"} | {"step_count": len(row["steps"] or []), "queued": queued, "live_model": live_model and row["state"] == "working", "measuring": bool(waiting) and repo.get(d.jobs, waiting)["state"] in ("queued", "running"), "presentation": presentation.status(repo, row), "can_render": presentation.enabled()}


def present(ident) -> dict:
    """Start or retry finishing from the app, including scenes built before it was enabled."""
    repo = repository()
    with d.transaction(repo.engine) as con:
        row = con.execute(sa.select(d.studio_scenes).where(d.studio_scenes.c.id == ident)).mappings().first()
        if row is None:
            raise KeyError(ident)
        if row["state"] == "working":
            raise Busy("Wait for this model to finish building")
        if row["state"] != "ready" or not row["model_artifact_id"]:
            raise ValueError("This scene has no finished model to render")
        if not presentation.enabled():
            raise ValueError("Rendering is not enabled on this server")
        presentation.schedule_in(repo, con, row)
    ensure_worker()
    return get(ident)


def diagnostics(ident) -> dict:
    """Read the saved checker report and current job logs without running any checks."""
    repo = repository()
    scene = get(ident)
    job_ids = dict.fromkeys(filter(None, (scene["job_id"], (scene["checks"] or {}).get("measure_job"), (scene["presentation"] or {}).get("job_id"))))
    jobs = []
    for job_id in job_ids:
        job = repo.get(d.jobs, job_id)
        events = repo.list(d.events, d.events.c.job_id == job_id, order=d.events.c.seq)
        jobs.append({k: job[k] for k in ("id", "kind", "state", "error", "created_at", "updated_at")} | {
            "events": [{k: event[k] for k in ("seq", "at", "kind", "message", "data")} for event in events],
        })
    return {k: scene[k] for k in ("id", "state", "error", "updated_at", "model_artifact_id", "checks", "measuring", "tokens", "feed", "unsure", "presentation")} | {"jobs": jobs}


def listing() -> list:
    rows = repository().list(d.studio_scenes, order=d.studio_scenes.c.created_at.desc())
    return [{"id": r["id"], "title": r["title"], "state": r["state"], "parts": len(r["parts"] or []), "has_preview": bool(r["preview_artifact_id"]), "created_at": r["created_at"]} for r in rows]


def file_of(ident, which) -> tuple[bytes, str]:
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    if which in ("surface.glb", "render"):
        finished = presentation.status(repo, row) or {}
        field, media = ("surface_artifact_id", "model/gltf-binary") if which == "surface.glb" else ("render_artifact_id", "image/jpeg")
        if finished.get("state") != "succeeded" or not finished.get(field):
            raise KeyError(which)
        return repo.artifacts.read(finished[field]), media
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


def rename(ident, title) -> dict:
    repo = repository()
    repo.get(d.studio_scenes, ident)
    if not title.strip():
        raise ValueError("a scene needs a name")
    _set(repo, ident, title=title.strip()[:LONGEST_TITLE])
    return get(ident)


def strike(ident, index, struck) -> dict:
    """A person says Muse misread a relation, or takes that back. The next check leaves it out."""
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    if get(ident)["state"] == "working":
        raise Busy("Muse is working on this scene")
    checks = row["checks"] or {}
    relations = ((checks.get("features") or {}).get("contract") or {}).get("relations") or []
    if not 0 <= index < len(relations):
        raise KeyError(index)
    relations[index]["struck"] = struck
    for measured in (checks.get("measured") or {}).get("rows") or []:
        if measured["index"] == index:
            measured["struck"] = struck
    _set(repo, ident, checks=checks)
    return get(ident)


def delete(ident) -> None:
    """A scene goes with its jobs, its model calls and its files. A file another scene also uses stays: files are stored once, by content."""
    repo = repository()
    row = repo.get(d.studio_scenes, ident)
    if get(ident)["state"] == "working":
        raise Busy("Muse is working on this scene")
    scene_jobs = [j for j in repo.list(d.jobs) if (j["payload"] or {}).get("scene_id") == ident]
    jobs = [j["id"] for j in scene_jobs]
    runs = [r["id"] for r in repo.list(d.runs, d.runs.c.job_id.in_(jobs))]
    mine = {row[k] for k in ("drawing_artifact_id", "model_artifact_id", "preview_artifact_id") if row[k]}
    mine |= presentation.artifacts(row["presentation"])
    mine |= {a for job in scene_jobs for a in presentation.artifacts(job["result"])}
    mine |= {a for step in repo.list(d.steps, d.steps.c.run_id.in_(runs)) for a in PICTURE.findall(json.dumps(step["request"]))}
    with d.transaction(repo.engine) as con:
        con.execute(d.studio_scenes.delete().where(d.studio_scenes.c.id == ident))
        con.execute(d.steps.delete().where(d.steps.c.run_id.in_(runs)))
        con.execute(d.runs.delete().where(d.runs.c.id.in_(runs)))
        con.execute(d.events.delete().where(d.events.c.job_id.in_(jobs)))
        con.execute(d.resource_leases.delete().where(d.resource_leases.c.job_id.in_(jobs)))
        con.execute(d.jobs.delete().where(d.jobs.c.id.in_(jobs)))
        kept = {r[k] for r in con.execute(sa.select(d.studio_scenes)).mappings() for k in ("drawing_artifact_id", "model_artifact_id", "preview_artifact_id") if r[k]}
        kept |= {a for r in con.execute(sa.select(d.studio_scenes.c.presentation)) for a in presentation.artifacts(r[0])}
        kept |= {a for r in con.execute(sa.select(d.jobs.c.result)) for a in presentation.artifacts(r[0])}
        kept |= {a for r in con.execute(sa.select(d.steps.c.request)) for a in PICTURE.findall(json.dumps(r[0]))}
        unused = mine - kept
        con.execute(d.artifacts.delete().where(d.artifacts.c.id.in_(unused)))
    for artifact in unused:
        repo.artifacts.delete(artifact)


def _publish(repo, ident, steps, found=None):
    from builder import loop
    if found is None:
        found = loop.sandbox(steps, ("three_quarter",), export=True)
    scene = json.loads(found["scene.json"])
    if scene["error"]:
        raise ValueError(scene["error"])
    values = {"parts": scene["parts"], "state": "ready", "error": None}
    if found.get("model.glb"):
        values["model_artifact_id"] = _keep(repo, found["model.glb"], "model/gltf-binary")
    else:  # the last model no longer matches these parts
        values |= {"model_artifact_id": None, "state": "failed", "error": NO_MODEL}
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
    values = _publish(repo, ident, steps) if steps else {"parts": [], "model_artifact_id": None, "preview_artifact_id": None, "state": "ready", "error": None}
    checks = {"features": (row["checks"] or {}).get("features"), "rounds": [], "last": None}
    _set(repo, ident, steps=steps, intents=intents, checks=checks, feed=[*(row["feed"] or []), {"kind": "system", "text": "Undid the last step"}][-FEED:], **values)
    _measure(repo, ident)
    ensure_worker()
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


def _measure(repo, ident):
    """How the parts meet is worked out once a scene is ready, in a job of its own. It takes minutes, nobody should wait for it, and if it fails the scene just has no measurements."""
    row = repo.get(d.studio_scenes, ident)
    checks = {k: v for k, v in (row["checks"] or {}).items() if k != "measured"}
    if not row["model_artifact_id"] or not checks.get("features"):
        return
    job = repo.enqueue("studio_measure", {"scene_id": ident, "model": row["model_artifact_id"]})
    _set(repo, ident, checks={**checks, "measure_job": job["id"]})


def measure(repo, fence, job, gateway):
    from builder import checker, loop
    ident, model = job["payload"]["scene_id"], job["payload"]["model"]
    try:
        row = repo.get(d.studio_scenes, ident)
    except KeyError:  # deleted while it waited
        row = None
    if row and row["model_artifact_id"] == model:  # a newer model has its own job
        spent, ask = 0, _asker(repo, gateway)
        drawing = repo.artifacts.read(row["drawing_artifact_id"])
        contract = row["checks"]["features"].get("contract")
        if not contract:
            contract, tokens = checker.read_contract(ask, drawing)
            spent += tokens
        standard = loop.named_materials()
        matched, tokens = checker.match(ask, drawing, contract, [{**part, "material": part["material"] if part["material"] in standard else None} for part in row["parts"]])
        spent += tokens
        found = json.loads(loop.sandbox(row["steps"], (), relations=checker.relations(contract, matched), measure=True)["scene.json"])
        now = repo.get(d.studio_scenes, ident)
        if now["model_artifact_id"] == model:
            checks = now["checks"]
            _set(repo, ident, tokens=(now["tokens"] or 0) + spent, checks={**checks, "features": {**checks["features"], "contract": contract}, "measured": checker.reckon(contract, matched, found.get("measured"))})
    repo.end(fence, "succeeded", {"scene_id": ident})


def _asker(repo, gateway, parallel=False):
    def ask(prompt, images, schema, name):
        # usage/replayed describe one call. Concurrent requests need their own
        # gateway; reservations and checkpoints still share the fenced run.
        caller = ModelGateway(repo, gateway.fence, gateway.responder) if parallel else gateway
        references = [repo.artifacts.put(data, "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png") for data in images]
        with d.transaction(repo.engine) as con:
            for reference in references:
                repo.artifact(con, reference)
        answer = caller.json(prompt, [r["id"] for r in references], schema, name, f"{name}:{sha(prompt + ''.join(r['id'] for r in references))[:16]}")
        return answer, billed_tokens(caller.usage)
    return ask


def build(repo, job, gateway):
    from builder import loop
    ident = job["payload"]["scene_id"]
    row = repo.get(d.studio_scenes, ident)
    feed = list(row["feed"] or [])
    turns = {"n": 0}
    if job["attempts"] > 1:
        _set(repo, ident, step="Picking up where it stopped")

    def respond(payload):
        turns["n"] += 1
        try:
            return gateway.call({"model": meta_client.default_model(), **payload}, "studio", f"turn-{turns['n']}", reserve_tokens=loop.CHECK_RESERVE)
        except ProviderRejected as error:
            if "reasoning item" not in str(error):
                raise
            # Some models forget their earlier reasoning between turns, and then refuse a conversation that points at it. The turns themselves are kept; only the reasoning goes.
            payload["input"][:] = [item for item in payload["input"] if item.get("type") != "reasoning"]
            return gateway.call({"model": meta_client.default_model(), **payload}, "studio", f"turn-{turns['n']}-again", reserve_tokens=loop.CHECK_RESERVE)

    def say(text):
        if gateway.replayed:  # the feed already has this: a retry goes back over the earlier attempt's turns
            return
        kind = "muse"
        if "] check " in text:
            text, kind = text.split("] check ", 1)[1], "check"
        elif "] run_python " in text:
            text, kind = text.split("] run_python ", 1)[1].split(" -> ")[0], "step"
        elif any(f"] {tool}" in text for tool in ("look", "undo", "finish", "list_parts", "inspect_source")):
            kind, text = "tool", ("Inspecting the drawing" if "] inspect_source" in text else "Checking other views" if "] look" in text else "Undoing a step" if "] undo" in text else "Measuring the parts" if "] list_parts" in text else "Finishing")
        else:
            text = text.split("] ", 1)[-1]
        text = _clip(text)
        feed.append({"kind": kind, "text": text})
        _set(repo, ident, feed=feed[-FEED:], step=text)

    def on_step(live, scene, found):
        values = {"steps": live.steps, "intents": live.intents, "parts": scene["parts"]}
        if found.get("three_quarter.jpg"):
            values["preview_artifact_id"] = _keep(repo, found["three_quarter.jpg"], "image/jpeg")
        if found.get("model.glb"):
            values["model_artifact_id"] = _keep(repo, found["model.glb"], "model/gltf-binary")
        _set(repo, ident, **values)

    brief = REVISE.format(text=job["payload"]["feedback"]) if job["payload"].get("feedback") else None
    held = row["checks"] or {}
    begun = job["payload"].get("from_step", len(row["steps"] or []))  # a retry replays from here: checkpoints answer for the model, and the tools run again
    is_hybrid = job["payload"].get("method") == "hybrid" and not begun and not brief
    if is_hybrid:
        from builder.hybrid import Build
        live = Build(repo.artifacts.read(row["drawing_artifact_id"]), _asker(repo, gateway, parallel=True), say=say, on_step=on_step)
        live.run()
    else:
        live = loop.Live(repo.artifacts.read(row["drawing_artifact_id"]), respond, steps=(row["steps"] or [])[:begun], intents=(row["intents"] or [])[:begun], say=say, show=_shown(repo), on_step=on_step, ask=_asker(repo, gateway), features=held.get("features"))
        try:
            live.run(brief)
        except BudgetExceeded:
            try:
                live.conclude("The build budget ran out")
            except BudgetExceeded:
                live.stop("The budget ran out before the final check; any unchecked repair was discarded when an earlier checked model was available")
    if not live.steps:
        raise ValueError("Muse built nothing from this drawing")
    finished = live.result or {}
    _set(repo, ident, step="Preparing the model")
    values = _publish(repo, ident, live.steps, found=live.found if is_hybrid else None)
    feed.append({"kind": "muse", "text": finished.get("what") or "Stopped before finishing: the budget ran out."})
    checks = {"features": live.features, "rounds": [{k: c[k] for k in ("right", "total", "passed")} for c in live.checks], "last": live.last_check}
    if is_hybrid:
        checks["build"] = live.metadata()
    named = repo.get(d.studio_scenes, ident)["title"]  # read now: a person may have renamed it while Muse worked
    title = named if named != NEW_TITLE else (finished.get("title") or (live.features or {}).get("title") or finished.get("what") or named)[:LONGEST_TITLE]
    _set(repo, ident, steps=live.steps, intents=live.intents, step=None, title=title, unsure=finished.get("unsure", []), feed=feed[-FEED:],
         tokens=(row["tokens"] or 0) + (repo.run(job["id"])["consumed"].get("tokens", 0) if is_hybrid else live.spent), checks=checks, **values)
    _measure(repo, ident)


def run_job(repo, fence, job, gateway):
    ident = job["payload"]["scene_id"]
    try:
        build(repo, job, gateway)
    except (ValueError, KeyError, OSError) as error:
        if not isinstance(error, (urllib.error.URLError, ConnectionError, TimeoutError)):  # those are retried
            _set(repo, ident, state="failed", error=str(error)[:300])
        raise
    repo.end(fence, "succeeded", {"scene_id": ident})


JOBS = {"studio_build": run_job, "studio_measure": measure, "studio_render": presentation.run_job}
