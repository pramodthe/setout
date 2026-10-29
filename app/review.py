"""Human review of an AI-generated detail, before it is published.

A reviewer accepts or fixes each value, fixes labels (only to labels that are
printed on the drawing), and publishes the detail as reviewed. Edits change
the program's parameters, so every dimension computed from an edited value
moves with it. An edited value becomes user_input; a reviewer can never
promote anything to "drawing" (only the gates can, against the printed words).
"""

from __future__ import annotations

import json
import re
import time

from .generate import carry_context, library_dir
from .geometry import build_assembly
from .program import DetailProgram, compile_program, expr_names
from .spec import DetailSpec, Provenance
from .store import var_root


def _load(detail_id: str) -> tuple[dict, DetailProgram, DetailSpec]:
    f = library_dir() / f"{detail_id}.json"
    if not f.exists():
        raise KeyError(detail_id)
    rec = json.loads(f.read_text())
    return rec, DetailProgram.model_validate(rec["program"]), DetailSpec.model_validate(rec["spec"])


def _state(rec: dict) -> dict:
    return rec.get("review") or {"status": "draft", "accepted": [], "edited": {},
                                 "by": None, "at": None}


def get(detail_id: str) -> dict:
    rec, prog, spec = _load(detail_id)
    st = _state(rec)
    used: dict[str, list[str]] = {q.name: [] for q in prog.params}
    for part in prog.parts:
        exprs = [d.expr for d in part.dims] + [c for pt in part.path for c in (pt.x, pt.y, pt.z) if c]
        for n in set().union(*(expr_names(e) for e in exprs)) if exprs else set():
            if n in used and part.id not in used[n]:
                used[n].append(part.id)
    params = [{**q.model_dump(mode="json"), "used_by": used.get(q.name, []),
               "accepted": q.name in st["accepted"], "edited": st["edited"].get(q.name)}
              for q in prog.params]
    params.sort(key=lambda p: (p["provenance"] != "drawing", not p["used_by"], p["name"]))
    return {
        "detail_id": detail_id, "status": st["status"], "by": st["by"], "at": st["at"],
        "params": params,
        "parts": [{"id": p.id, "type": p.type, "label": p.label, "shape": p.shape}
                  for p in prog.parts],
        "read_labels": [e["label_verbatim"] for e in (rec.get("read") or {}).get("elements", [])],
        "gate_notes": rec.get("gate_notes", []),
        "revisions": [{"accepted": r.get("accepted"), "reason": r.get("reason"),
                       "issues": r.get("issues", [])} for r in rec.get("revisions", [])],
    }


def apply(detail_id: str, edits: list[dict], accept: list[str], labels: list[dict],
          publish: bool = False, reviewer: str = "") -> dict:
    rec, prog, old_spec = _load(detail_id)
    st = _state(rec)
    by = {q.name: q for q in prog.params}
    for e in edits:
        q = by.get(e["name"])
        if q is None:
            raise ValueError(f"unknown parameter {e['name']!r}")
        old, new = q.value, float(e["value"])
        if old == new:
            continue
        q.value = new
        q.provenance = Provenance.USER_INPUT
        q.note = ((q.note or "") + f" [review: was {old:g}]").strip()
        st["edited"][q.name] = {"from": old, "to": new}
    for name in accept:
        if name in by and name not in st["accepted"]:
            st["accepted"].append(name)
    printed = {e["label_verbatim"] for e in (rec.get("read") or {}).get("elements", [])}
    parts = {p.id: p for p in prog.parts}
    for lab in labels:
        part = parts.get(lab["part_id"])
        if part is None:
            raise ValueError(f"unknown part {lab['part_id']!r}")
        text = lab.get("label") or None
        if text is not None and text not in printed:
            raise ValueError("a label must be one printed on the drawing (verbatim)")
        part.label = text
    if publish:
        if not reviewer.strip():
            raise ValueError("publishing needs the reviewer's name")
        st.update(status="reviewed", by=reviewer.strip()[:80],
                  at=time.strftime("%Y-%m-%d %H:%M"))
        prog.origin = re.sub(r"\(unreviewed", f"(reviewed by {st['by']} {st['at']}",
                             prog.origin, count=1)
    spec = carry_context(old_spec, compile_program(prog))
    spec.context["review_status"] = st["status"]
    build_assembly(spec)  # an edit that breaks geometry is rejected here
    rec.update(program=prog.model_dump(mode="json"), spec=spec.model_dump(mode="json"), review=st)
    (library_dir() / f"{detail_id}.json").write_text(json.dumps(rec, indent=1))
    override = var_root() / f"spec-{detail_id}.json"
    if override.exists():
        override.unlink()  # the reviewed program is now the source of truth
    return get(detail_id)
