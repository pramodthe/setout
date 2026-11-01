"""A section to a planned 3D assembly, kept and reused.

Apart from the verified versions, like the section drafts: it never writes a version, never reaches the verifier and can't be published. Muse reads the drawing, plans
the parts and chooses them from a catalogue shortlist. A person confirms or corrects that reading before anything is built. The engine then builds it and reports what it
could not do. A person's corrections become lessons, and details they call good guide the next reading. Sizes are assumptions, and implied parts are `inferred`.
"""
from __future__ import annotations

import json

import numpy as np
import sqlalchemy as sa

from . import database as d
from .repository import sha, uid
from .runtime import ensure_worker, repository
from .store import figure_path

STATES = ("planning", "awaiting_confirmation", "building", "done", "failed")
LIKE = 0.85  # how alike two plans must read before the earlier one is offered as an example. Measured: two cavity walls 0.88, different details 0.82 or less
EXAMPLES, LESSONS = 3, 12
LESSON_SCOPE = "assembly"
VIEWER_WORD = {"block": "concrete_block", "stone": "stone", "board": "fibre_cement", "wall tie": "steel_tie", "fixing": "steel_fixing", "reinforcement": "steel_rebar", "steel section": "steel_section",
               "metal trim": "aluminium_trim", "window": "aluminium_frame", "door": "timber_door", "glazing": "glass", "weep": "weep_plastic", "drainage": "drain_plastic", "flashing": "aluminium_flashing"}


class WrongState(RuntimeError):
    """The assembly isn't at the stage this asks for."""


def _set(repo, ident, **values):
    with d.transaction(repo.engine) as con:
        con.execute(d.assemblies.update().where(d.assemblies.c.id == ident).values(**values, updated_at=d.now(con)))


def _picture(row):
    path = figure_path(row["doc_id"], row["crop_id"])
    if path is None:
        raise KeyError(row["crop_id"])
    return str(path)


def _queue(repo, ident, kind, state, step):
    job = repo.enqueue(kind, {"assembly_id": ident})
    _set(repo, ident, state=state, step=step, job_id=job["id"], error=None)
    ensure_worker()
    return get(ident)


def start(doc_id, crop_id) -> dict:
    repo = repository()
    ident = uid()
    with d.transaction(repo.engine) as con:
        at = d.now(con)
        con.execute(d.assemblies.insert().values(id=ident, doc_id=doc_id, crop_id=crop_id, state="planning", step="queued", tokens=0, created_at=at, updated_at=at))
    return _queue(repo, ident, "plan_assembly", "planning", "reading the drawing")


def get(ident) -> dict:
    repo = repository()
    row = repo.get(d.assemblies, ident)
    if row["state"] in ("planning", "building") and row["job_id"]:
        job = repo.get(d.jobs, row["job_id"])
        if job["state"] in ("failed", "cancelled", "needs_review"):
            row = {**row, "state": "failed", "error": (job["error"] or {}).get("message") or "the job stopped"}
    return {k: v for k, v in row.items() if k not in ("embedding", "guidance")}


def scene(ident) -> bytes:
    repo = repository()
    row = repo.get(d.assemblies, ident)
    if not row["scene_artifact_id"]:
        raise KeyError(ident)
    return repo.artifacts.read(row["scene_artifact_id"])


def listing() -> list:
    rows = repository().list(d.assemblies, order=d.assemblies.c.created_at.desc())
    return [{"id": r["id"], "state": r["state"], "assembly": (r["plan"] or {}).get("assembly"), "verdict": r["verdict"], "created_at": r["created_at"]} for r in rows]


def thumb(ident):
    from goal.library import STORE
    path = STORE / "page" / "thumbs" / f"{ident}.jpg"
    return path if path.exists() else None


def _lesson(repo, text, tags):
    """A person's own correction, so it needs no reviewer to activate it. The verified agent's recall never reads this scope."""
    with d.transaction(repo.engine) as con:
        con.execute(d.memories.insert().values(id=uid(), scope=LESSON_SCOPE, scope_key="all", state="active", kind="lesson", text=text[:500], tags=tags, created_at=d.now(con)))


def confirm(ident, reviewer, assembly, edits) -> dict:
    """The person's reading of the drawing. Whatever they change is applied, kept as a lesson, and that part is chosen again."""
    from goal.plan import recategorise
    repo = repository()
    row = repo.get(d.assemblies, ident)
    if row["state"] != "awaiting_confirmation":
        raise WrongState("this reading isn't waiting for confirmation")
    if not reviewer.strip():
        raise ValueError("say who is confirming")
    plan, choices = json.loads(json.dumps(row["plan"])), dict(row["choices"] or {})
    known = {p["id"]: p for p in plan["parts"]}
    if assembly and assembly.strip() != plan["assembly"]:
        _lesson(repo, f"Muse read a drawing as \"{plan['assembly']}\". {reviewer} says it is \"{assembly.strip()}\".", ["reading"])
        plan["assembly"] = assembly.strip()
    for edit in edits:
        part = known.get(edit.get("id"))
        if part is None:
            raise ValueError(f"no part {edit.get('id')}")
        for field in ("category", "form"):
            value = edit.get(field)
            if value and value != part[field]:
                _lesson(repo, f"In \"{plan['assembly']}\", Muse called {part['name']} {field} {part[field]}. {reviewer} says {value}.", ["part", part["category"]])
                if field == "category":
                    recategorise(part, value)
                else:
                    part[field] = value
                choices.pop(part["id"], None)
    _set(repo, ident, plan=plan, choices=choices, confirmed_by=reviewer.strip())
    return _queue(repo, ident, "build_assembly", "building", "building")


def correct(ident, reviewer, text) -> dict:
    """Read the drawing again with the person's words in front of Muse."""
    repo = repository()
    row = repo.get(d.assemblies, ident)
    if row["state"] != "awaiting_confirmation":
        raise WrongState("this reading isn't waiting for confirmation")
    if not text.strip() or not reviewer.strip():
        raise ValueError("say who you are and what is wrong")
    _lesson(repo, f"Muse read a drawing as \"{row['plan']['assembly']}\". {reviewer} corrected it: {text.strip()}", ["reading"])
    _set(repo, ident, guidance=text.strip())
    return _queue(repo, ident, "plan_assembly", "planning", "reading the drawing again")


def judge(ident, reviewer, verdict, note) -> dict:
    """Good details guide later readings. A wrong one is kept, says why, and is never offered as an example."""
    repo = repository()
    row = repo.get(d.assemblies, ident)
    if row["state"] != "done":
        raise WrongState("there's nothing built to judge yet")
    if verdict not in ("good", "wrong") or not reviewer.strip():
        raise ValueError("a verdict is good or wrong, from a named person")
    if note.strip():
        _lesson(repo, f"{reviewer} on \"{row['plan']['assembly']}\", built from it: {note.strip()}", ["verdict", verdict])
    _set(repo, ident, verdict=verdict, verdict_note=note.strip() or None)
    return get(ident)


def examples(repo, vector, own) -> list:
    """Details a person called good that mean much the same as this one."""
    out = []
    for row in repo.list(d.assemblies, d.assemblies.c.verdict == "good", d.assemblies.c.id != own):
        if row["embedding"]:
            alike = float(np.dot(row["embedding"], vector))
            if alike >= LIKE:
                out.append((alike, row))
    return [row for _, row in sorted(out, key=lambda item: -item[0])[:EXAMPLES]]


def guidance(repo, row) -> str:
    """What people have said, for the reading. It is marked as being about other drawings, except a correction of this one."""
    lessons = [m["text"] for m in repo.list(d.memories, d.memories.c.scope == LESSON_SCOPE, order=d.memories.c.created_at.desc())][:LESSONS]
    reading = ""
    if lessons:
        reading += "Lessons from people correcting earlier readings. They are about other drawings: use them only where this drawing shows the same thing.\n" + "\n".join(f"- {t}" for t in lessons) + "\n\n"
    if row["guidance"]:
        reading += f"A person looked at this drawing and corrected an earlier reading of it. Believe them: {row['guidance']}\n\n"
    return reading


def shown(found) -> list:
    return [{"assembly": r["plan"]["assembly"], "parts": [{k: p[k] for k in ("name", "category", "form", "reason")} for p in r["plan"]["parts"] if p["form"] != "none"]} for r in found]


def _model(repo, gateway):
    """The planner's model call, through the job's budget and checkpoints. Pictures become artifacts, because a checkpoint holds references and not images."""
    def call(prompt, pictures, schema, name, _effort):
        references = [repo.artifacts.put(p, "image/png") for p in pictures]
        with d.transaction(repo.engine) as con:
            for reference in references:
                repo.artifact(con, reference)
        spent = repo.run(gateway.fence.job_id)["consumed"].get("tokens", 0)
        answer = gateway.json(prompt, [r["id"] for r in references], schema, name, f"{name}:{sha(prompt)[:16]}")  # a job can make the same kind of call twice
        return answer, repo.run(gateway.fence.job_id)["consumed"].get("tokens", 0) - spent
    return call


def plan_job(repo, job, gateway):
    from goal import choose, engine, part_search, plan as planner
    ident = job["payload"]["assembly_id"]
    row = repo.get(d.assemblies, ident)
    picture, model = _picture(row), _model(repo, gateway)
    _set(repo, ident, step="reading the drawing")
    plan, regions, size = planner.read(model, picture, guidance(repo, row))
    _set(repo, ident, step="comparing with confirmed details")
    vector = part_search.embed(texts=[planner.summary(plan)])["texts"][0].round(5).tolist()
    found = examples(repo, vector, ident)
    learned, tokens = planner.learn(model, picture, regions, plan, shown(found)) if found else ([], 0)
    choosing = ("Choices a person confirmed on similar details, as a guide only:\n" + json.dumps([{"assembly": r["plan"]["assembly"], "choices": list((r["choices"] or {}).values())} for r in found]) + "\n\n") if found else ""
    _set(repo, ident, step="choosing parts", embedding=vector)
    choices, shortlists, more = choose.in_order(model, plan, lambda held: engine.Scene(picture, plan, regions, size, held), choosing)
    faults = planner.faults(plan, regions)
    _set(repo, ident, state="awaiting_confirmation", step="waiting for a person", plan={**plan, "faults": faults, "learned": learned, "compared_with": [r["id"] for r in found]},
         shortlists=shortlists, choices=choices, tokens=(row["tokens"] or 0) + plan["tokens"] + tokens + more)


def rows_of(scene_) -> list:
    gathered: dict = {}
    for item in scene_.items:
        gathered.setdefault(item["part"], []).append(item)
    return [{"id": ident, "items": items} for ident, items in gathered.items()]


def scene_of(ident, plan, built, run_mm) -> dict:
    """The viewer's scene. The engine's along, across, up in metres become millimetres, with across and up as the viewer's x and y and the run behind the cut face."""
    solids, parts = [], {p["id"]: p for p in plan["parts"]}
    for row in built:
        part = parts.get(row["id"], {"name": "head joints", "category": "mortar", "seen": "drawn"})
        tris = np.vstack([i["triangles"] for i in row["items"]]).astype(float) * 1000
        moved = np.stack([tris[..., 1], tris[..., 2], run_mm - tris[..., 0]], -1)[:, [0, 2, 1]]  # the third axis turns round, so two corners swap to face out
        implied = part["seen"] == "implied"
        source = row["items"][0]["source"]
        word = "masonry_joint" if row["id"] == "head_joints" else VIEWER_WORD.get(part["category"], part["category"])
        solids.append({"id": row["id"], "name": part["name"], "layer": word, "source_label": None, "representation": "solid", "color": "#9aa3ad", "class_id": None,
                       "provenance": "inferred" if implied else "assumption", "confidence": None, "inferred_by": "assembly-plan:muse" if implied else None,
                       "note": ("Not in the drawing: Muse says it is needed. " if implied else "") + f"{source}. {len(row['items'])} piece{'s' if len(row['items']) != 1 else ''}. Size assumed, not verified.",
                       "tris": moved.reshape(-1).round(2).tolist(), "tri_count": len(moved), "bounds": [*moved.reshape(-1, 3).min(0).round(2).tolist(), *moved.reshape(-1, 3).max(0).round(2).tolist()],
                       "params": [], "product": None, "install_after": [], "source_regions": []})
    return {"detail_id": ident, "title": plan["assembly"], "units": "mm", "viewer_scale": 0.01, "origin": "planned-assembly-v0", "source": {}, "unresolved": [],
            "context": {"kind": "section", "illustrative_extent_z_mm": run_mm}, "install_order": [s["id"] for s in solids], "relations": [], "verification": [], "solids": solids}


def build_job(repo, job, gateway):
    from goal import choose, engine, plan as planner
    ident = job["payload"]["assembly_id"]
    row = repo.get(d.assemblies, ident)
    picture, plan = _picture(row), row["plan"]
    regions, size = planner.regions_of(picture)
    built = engine.Scene(picture, plan, regions, size, row["choices"])
    needed = [s for s in built.shortlists() if s["part"] not in built.choices]
    tokens = 0
    if needed:  # a part the person changed is chosen again, with their reading to go on
        _set(repo, ident, step="choosing the corrected parts")
        more, tokens = choose.choose(_model(repo, gateway), plan, needed)
        built = engine.Scene(picture, plan, regions, size, {**built.choices, **more})
    _set(repo, ident, step="placing parts")
    built.build()
    state, clashes = built.contract(), built.clashes()
    made = scene_of(ident, plan, rows_of(built), engine.RUN * 1000)
    reference = repo.artifacts.put(json.dumps(made).encode(), "application/json")
    with d.transaction(repo.engine) as con:
        repo.artifact(con, reference)
    binding = [r for r in state if r["after"] != "not binding"]
    result = {"contract": state, "clashes": [list(c) for c in clashes], "notes": built.notes, "misfits": built.misfits, "scale_mm_per_px": round(built.scale * 1000, 3),
              "broken_before": sum(r["before"] == "broken" for r in binding), "broken_after": sum(r["after"] == "broken" for r in binding), "binding": len(binding)}
    _set(repo, ident, state="done", step="built", choices=built.choices, result=result, scene_artifact_id=reference["id"], tokens=(row["tokens"] or 0) + tokens)


def run_job(repo, fence, job, gateway):
    """A job that fails leaves the assembly saying so. Anything the worker retries or budgets for is left for it to handle."""
    ident = job["payload"]["assembly_id"]
    try:
        (plan_job if job["kind"] == "plan_assembly" else build_job)(repo, job, gateway)
    except (ValueError, KeyError, OSError, IndexError) as error:
        if not isinstance(error, (ConnectionError, TimeoutError)):
            _set(repo, ident, state="failed", error=str(error)[:300])
        raise
    repo.end(fence, "succeeded", {"assembly_id": ident})
