"""Lessons a reviewer's correction teaches, written from the version diff."""
from __future__ import annotations


def _number(value):
    return f"{value:g}"


def correction_lessons(old, new, detail_id):
    """Proposed memories for one correction, as (scope, scope_key, text, tags).

    A value the drawing printed stays with this detail: on another drawing the
    printed value must be read afresh. A value the drawing left out becomes a
    convention for drawings of the same kind, and is only ever reused as an
    explicit assumption. Every lesson stays proposed until a reviewer
    activates it."""
    kind = new.get("kind") or "assembly"
    before = {p["name"]: p for p in old.get("params", [])}
    on_detail, conventions, tags = [], [], []
    for param in new.get("params", []):
        prior = before.get(param["name"])
        if not prior or prior["value"] == param["value"]:
            continue
        tags.append(param["name"])
        unit = param.get("unit") or "mm"
        if prior["provenance"] == "drawing":
            quote = f" (printed as {prior['source_quote']!r})" if prior.get("source_quote") else ""
            on_detail.append(f"The reviewer corrected the printed value {param['name']} from "
                             f"{_number(prior['value'])} to {_number(param['value'])} {unit}{quote}; "
                             "re-read that printed dimension.")
        else:
            why = f" ({prior['note'].strip()})" if (prior.get("note") or "").strip() else ""
            conventions.append(f"Where a {kind} drawing prints no {param['name']}{why}, the reviewer chose "
                               f"{_number(param['value'])} {unit} instead of {_number(prior['value'])} {unit}. "
                               "Reuse it only as an explicit assumption, never as a drawing value.")
    parts_before = {p["id"]: p for p in old.get("parts", [])}
    parts_after = {p["id"]: p for p in new.get("parts", [])}
    for ident, part in parts_after.items():
        prior = parts_before.get(ident)
        if prior and prior.get("label") != part.get("label"):
            tags.append(ident)
            on_detail.append(f"The reviewer attributed the drawing label {part.get('label')!r} to the "
                             f"{part['type']} part {ident} (was {prior.get('label')!r}).")
    added = [f"{ident} ({part['type']})" for ident, part in parts_after.items() if ident not in parts_before]
    removed = [f"{ident} ({part['type']})" for ident, part in parts_before.items() if ident not in parts_after]
    if added:
        on_detail.append("The reviewer added " + ", ".join(added) + ".")
    if removed:
        on_detail.append("The reviewer removed " + ", ".join(removed) + ".")
    lessons = []
    if conventions:
        lessons.append(("type", kind, " ".join(conventions), tags))
    if on_detail:
        lessons.append(("detail", detail_id, " ".join(on_detail), tags))
    return lessons
