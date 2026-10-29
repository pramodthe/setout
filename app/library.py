"""The browsable library: human-transcribed seeds plus AI-generated details.

Indexed two ways, like a manufacturer's system selector:
  - by situation: sector > area > element (from each detail's situation);
  - by component: every detail that contains a given kind of part, which is
    where a manufacturer's product would sit. Product references are still
    null everywhere (no system selected), so the index groups by part type.
"""

from __future__ import annotations

from .spec import DetailSpec, Provenance

LEVELS = ("sector", "area", "element")
SKIP_TYPES = {"annotation", "termination_datum", "datum"}


def _situation(spec: DetailSpec) -> list[str]:
    raw = [str(s).strip() for s in (spec.context.get("situation") or []) if str(s).strip()]
    sector = next((s for s in raw if s.lower() in ("residential", "commercial")), "General")
    rest = [s for s in raw if s != sector]
    area = " · ".join(rest[:-1][:2]) if len(rest) > 1 else (rest[0] if rest else "General")
    element = rest[-1] if len(rest) > 1 else spec.context.get("kind", "detail")
    return [sector, area or "General", str(element).capitalize()]


def entry(spec: DetailSpec, kind: str) -> dict:
    comps = [c for c in spec.components if c.type not in SKIP_TYPES]
    # printed values confirmed against the drawing: AI details list them in
    # context (their part dims also depend on assumptions, so they're never
    # tagged "drawing" themselves); seeds carry them on the parts
    printed = spec.context.get("drawing_params")
    drawing = len(printed) if printed is not None else sum(
        1 for c in spec.components for p in c.params if p.provenance == Provenance.DRAWING)
    return {
        "detail_id": spec.detail_id, "title": spec.title, "origin": spec.origin,
        "made_by": kind, "situation": _situation(spec),
        "kind": spec.context.get("kind", "section"),
        "parts": len(comps), "drawing_values": drawing,
        "labels": sum(1 for c in comps if c.source_label),
        "sha256": spec.source.sha256, "crop_id": spec.source.crop_id,
        "review": spec.context.get("review_status", "draft") if kind == "ai" else None,
        "located": len(spec.context.get("drawing_regions") or {}),
        "components": [{"id": c.id, "type": c.type, "label": c.source_label} for c in comps],
    }


def component_index(entries: list[dict]) -> list[dict]:
    """[{type, details: [{detail_id, title, part_ids}]}], most widespread first."""
    by: dict[str, dict[str, dict]] = {}
    for e in entries:
        for c in e["components"]:
            t = c["type"].lower().strip()
            d = by.setdefault(t, {}).setdefault(
                e["detail_id"], {"detail_id": e["detail_id"], "title": e["title"], "part_ids": []})
            d["part_ids"].append(c["id"])
    out = [{"type": t, "details": list(ds.values())} for t, ds in by.items()]
    return sorted(out, key=lambda x: (-len(x["details"]), x["type"]))
