"""The sidebar installation guide: Muse narrates and drives the 3D viewer.

The model answers with a short tour: steps of narration plus viewer actions
(focus, isolate, explode, section, view, install step). Grounding is checked
in code: every number spoken must exist in the detail (a parameter, a drawing
value, a quoted label, or a step number). One retry with the offending
numbers; anything still ungrounded is flagged to the user, not hidden.
"""

from __future__ import annotations

import json
import re

from . import meta_client
from .spec import DetailSpec, Provenance


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": props, "required": list(props)}


STR = {"type": "string"}
ACTIONS = ["focus", "isolate", "show_all", "explode", "section", "view",
           "install_step", "select"]

GUIDE_SCHEMA = _obj({
    "steps": {"type": "array", "items": _obj({
        "say": STR,
        "actions": {"type": "array", "items": _obj({
            "type": {"type": "string", "enum": ACTIONS},
            "ids": {"type": "array", "items": STR},
            "value": {"type": "number"},
            "view": {"type": "string", "enum": ["", "iso", "front", "top", "side"]},
        })},
    })},
    "follow_up": STR,
})

PROMPT = """\
You are the installation guide inside a 3D viewer of a construction detail. You
explain AND control the viewer. Answer the user's request as a short guided tour
of 2 to 8 steps. Each step: "say" (1-3 short sentences in plain trade language)
and "actions" that show what you are talking about:
- focus ids: fly the camera to these parts and select the first
- isolate ids: show only these parts (others ghosted)
- show_all: reset (no isolation, no explode, no section, full assembly)
- explode value: lift layers apart, 0 (closed) to 300
- section value: cut the model at z = value mm (0 turns the cut off)
- view view: iso | front (the section face) | top | side
- install_step value: show the install sequence up to step N (1-based)
- select ids: highlight one part and show its evidence
Use part ids exactly as given. Unused fields: ids [], value 0, view "".
GROUNDING: use only numbers that appear in the DETAIL below (parameter values,
drawing values) or install step numbers. Say where a number comes from: "from the
drawing" for drawing values, otherwise call it an assumption. Never invent
dimensions, products, standards, approvals or compliance claims. If the detail
does not answer the question, say so plainly.
"follow_up": one short suggestion for what to ask next.
"""


_NUM = r"(?<![\w.])\d+(?:\.\d+)?"
_RATIO = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?::|\bin\b)\s*(\d+(?:\.\d+)?)(?!\w|\.\d)")


def _extract(s: str) -> tuple[list[float], list[tuple[float, str]]]:
    """Plain numbers and ratios in text. "1:80" and "1 in 80" are one ratio
    (0.0125), never the numbers 1 and 80."""
    s = s or ""
    ratios = [(float(a) / float(b), m.group(0))
              for m in _RATIO.finditer(s) for a, b in [m.groups()] if float(b)]
    plain = [float(x) for x in re.findall(_NUM, _RATIO.sub(" ", s))]
    return plain, ratios


def _numbers(s: str) -> list[float]:
    return _extract(s)[0]


def context(spec: DetailSpec) -> dict:
    """Compact, model-facing summary of one detail."""
    order = spec.install_order()
    parts = []
    for c in spec.components:
        if c.representation == "annotation":
            continue
        params = [{"name": p.name, "value": p.value, "unit": p.unit,
                   "from": "drawing" if p.provenance == Provenance.DRAWING else p.provenance.value,
                   **({"quote": p.source_quote} if p.source_quote else {})}
                  for p in c.params if p.role.value == "physical"][:10]
        parts.append({"id": c.id, "type": c.type, "label": c.source_label,
                      "install_step": order.index(c.id) + 1 if c.id in order else None,
                      "note": (c.note or "")[:160], "params": params})
    return {"title": spec.title, "kind": spec.context.get("kind", "section"),
            "situation": spec.context.get("situation") or [],
            "install_order": order, "parts": parts,
            "drawing_values": [{k: q.get(k) for k in ("name", "value", "unit", "source_quote", "used_by")}
                               for q in spec.context.get("drawing_params", [])],
            "relations": [r.model_dump() for r in spec.relations][:40],
            "unresolved": [u.description for u in spec.unresolved][:10]}


def allowed_numbers(spec: DetailSpec) -> set[float]:
    nums: set[float] = set(range(0, len(spec.components) + 2))
    for c in spec.components:
        for p in c.params:
            nums.add(round(p.value, 3))
            nums.update(round(n, 3) for n in _numbers(p.source_quote or ""))
        nums.update(round(n, 3) for n in _numbers(c.source_label or ""))
    for q in spec.context.get("drawing_params", []):
        nums.add(round(float(q["value"]), 3))
    return nums


def allowed_ratios(spec: DetailSpec) -> list[float]:
    out = [p.value for c in spec.components for p in c.params if p.unit == "ratio"]
    for c in spec.components:
        for text in [c.source_label or ""] + [p.source_quote or "" for p in c.params]:
            out += [r for r, _ in _extract(text)[1]]
    return out


def ungrounded(steps: list[dict], spec: DetailSpec) -> dict[int, list[float | str]]:
    ok = allowed_numbers(spec)
    ratios = allowed_ratios(spec)
    bad: dict[int, list[float | str]] = {}
    for i, st in enumerate(steps):
        plain, said = _extract(st["say"])
        miss: list[float | str] = [n for n in plain if round(n, 3) not in ok]
        miss += [txt for r, txt in said
                 if not any(abs(r - a) <= 1e-3 * max(abs(a), 1e-9) for a in ratios)]
        if miss:
            bad[i] = miss
    return bad


def clean_actions(steps: list[dict], spec: DetailSpec) -> list[dict]:
    ids = {c.id for c in spec.components}
    n = len(spec.install_order())
    for st in steps:
        acts = []
        for a in st["actions"]:
            a["ids"] = [i for i in a["ids"] if i in ids]
            if a["type"] in ("focus", "isolate", "select") and not a["ids"]:
                continue
            if a["type"] == "install_step":
                a["value"] = max(1, min(n, int(a["value"] or 1)))
            if a["type"] == "explode":
                a["value"] = max(0, min(300, a["value"]))
            if a["type"] == "view" and not a["view"]:
                continue
            acts.append(a)
        st["actions"] = acts
    return steps


def ask(spec: DetailSpec, question: str, history: list[dict] | None = None,
        model: str | None = None) -> dict:
    """One guide answer: {steps, follow_up, ungrounded, retried, usage}."""
    ctx = json.dumps(context(spec))
    convo = "".join(f"\n{h['role'].upper()}: {h['text'][:600]}" for h in (history or [])[-6:])
    base = f"{PROMPT}\nDETAIL:\n{ctx}\n\nCONVERSATION SO FAR:{convo or ' (none)'}\n\nUSER: {question[:800]}"
    usage = []
    out, u = meta_client.respond_json([{"type": "input_text", "text": base}],
                                      GUIDE_SCHEMA, "guide_tour", model)
    usage.append(u)
    bad = ungrounded(out["steps"], spec)
    retried = False
    if bad:
        retried = True
        fb = "; ".join(f"step {i + 1}: " + ", ".join(n if isinstance(n, str) else f"{n:g}" for n in v)
                       for i, v in bad.items())
        out, u = meta_client.respond_json(
            [{"type": "input_text", "text": base + "\n\nYOUR PREVIOUS ANSWER USED NUMBERS "
              f"THAT ARE NOT IN THE DETAIL ({fb}). Remove them or use the detail's own values."}],
            GUIDE_SCHEMA, "guide_tour", model)
        usage.append(u)
        bad = ungrounded(out["steps"], spec)
    steps = clean_actions(out["steps"], spec)
    for i, st in enumerate(steps):
        st["ungrounded"] = bad.get(i, [])
    return {"steps": steps, "follow_up": out.get("follow_up", ""),
            "ungrounded": sum(len(v) for v in bad.values()), "retried": retried,
            "usage": usage}
