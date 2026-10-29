"""The existing review screen backed by immutable v3 versions and review events."""
from __future__ import annotations

import copy
import math

import sqlalchemy as sa

from . import database as d
from .lessons import correction_lessons
from .native_program import prepare
from .program import expr_names
from .repository import Conflict, row, uid


def get(repo, version):
    program = version["program"]
    events = repo.list(d.reviews, d.reviews.c.version_id == version["id"], order=d.reviews.c.seq)
    accepted = {name for event in events for name in (event["data"] or {}).get("accepted", [])}
    used = {q["name"]: [] for q in program.get("params", [])}
    for part in program.get("parts", []):
        expressions = [q["expr"] for q in part.get("dims", [])] + list(part.get("param_bindings", {}).values())
        expressions += [value for point in part.get("path", []) for value in point.values() if value]
        for expression in expressions:
            for name in expr_names(expression):
                if name in used and part["id"] not in used[name]:
                    used[name].append(part["id"])
    params = [{**q, "used_by": used[q["name"]], "accepted": q["name"] in accepted,
               "edited": None} for q in program.get("params", [])]
    params.sort(key=lambda q: (q["provenance"] != "drawing", q["name"]))
    detail = repo.get(d.details, version["detail_id"])
    read = repo.get(d.reads, version["drawing_read_id"])["response"] if version["drawing_read_id"] else {}
    return {"detail_id": version["detail_id"], "version_id": version["id"],
            "status": "reviewed" if detail["published_version_id"] == version["id"] else "draft",
            "by": events[-1]["reviewer"] if events else None, "at": events[-1]["at"] if events else None,
            "params": params, "parts": [{k: p.get(k) for k in ("id", "type", "label", "shape")} for p in program.get("parts", [])],
            "read_labels": [e["label_verbatim"] for e in read.get("elements", [])], "gate_notes": [], "revisions": []}


def apply(repo, version, edits, accept, labels, publish=False, reviewer=""):
    if version["format_version"] != "3.0":
        raise ValueError("archive is read-only; create a native v3 successor to revise it")
    program = copy.deepcopy(version["program"])
    by_name = {p["name"]: p for p in program["params"]}
    for edit in edits:
        if edit["name"] not in by_name or not math.isfinite(float(edit["value"])):
            raise ValueError("unknown parameter or non-finite value")
        by_name[edit["name"]]["value"] = float(edit["value"])
    if not set(accept) <= by_name.keys():
        raise ValueError("unknown accepted parameter")
    read = repo.get(d.reads, version["drawing_read_id"])
    printed = {e["label_verbatim"] for e in read["response"].get("elements", [])}
    parts = {p["id"]: p for p in program["parts"]}
    for label in labels:
        if label["part_id"] not in parts or (label.get("label") and label["label"] not in printed):
            raise ValueError("unknown part or non-verbatim drawing label")
        parts[label["part_id"]]["label"] = label.get("label")
    if publish and not reviewer.strip():
        raise ValueError("publishing needs the reviewer's name")
    revised = None
    if edits or labels:
        native, spec, _ = prepare(program, program["source"], version["detail_id"], read["response"], version["program"], human=True)
        revised = native.model_dump(mode="json"), spec.model_dump(mode="json")
    with d.transaction(repo.engine) as con:
        detail = row(con, d.details, version["detail_id"], True)
        if detail["current_version_id"] != version["id"]:
            raise Conflict("detail changed; reload before reviewing")
        target = version["id"]
        if revised:
            result = repo.insert_version(con, detail_id=version["detail_id"], expected_parent=target,
                        program=revised[0], spec=revised[1], bindings={} if edits else version["bindings"],
                        figure_id=version["figure_id"], read_id=version["drawing_read_id"],
                        created_by=reviewer.strip() or "local user", origin="user-revised:v3")
            target = result["version_id"]
        if publish:
            critic = con.execute(sa.select(d.critics).where(d.critics.c.version_id == target)
                                 .order_by(d.critics.c.created_at.desc())).mappings().first()
            if not critic or repo.completion_errors(con, target, critic["verification_id"], critic["id"]):
                raise Conflict("verify and critique this version before publishing")
        review_id = repo.review_event(con, target, reviewer.strip() or "local user",
                    "accept" if publish else "correct" if revised else "accept_params",
                    {"accepted": accept, "edits": edits, "labels": labels})
        if revised:
            for scope, key, text, tags in correction_lessons(version["program"], revised[0], version["detail_id"]):
                con.execute(d.memories.insert().values(id=uid(), scope=scope, scope_key=key, state="proposed",
                            kind="correction", text=text, tags=tags, source_event_id=review_id,
                            source_version_id=target, created_at=d.now(con)))
        if publish:
            con.execute(d.details.update().where(d.details.c.id == version["detail_id"]).values(published_version_id=target))
    return get(repo, repo.get(d.versions, target))
