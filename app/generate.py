"""The READ pass and the honesty gates, shared by the v3 agent.

  READ  — Muse reads the image: drawing type, rotation, every label verbatim
          with a layer/object/other category, every printed dimension with
          its exact words (app/agent_tools.py read_drawing).
  GATES — code, not model: a "drawing" value survives only if its quote is in
          the READ and the number is in the quote; labels must be verbatim
          READ labels. Anything unconfirmed is demoted to an assumption, with a
          note saying why (app/native_program.py prepare).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import sam
from .program import MIN_PATH, SHAPE_DIMS
from .spec import DetailSpec
from .store import figure_path, find_document_by_sha, var_root


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": props, "required": list(props)}


STR = {"type": "string"}

READ_SCHEMA = _obj({
    "drawing_type": {"type": "string", "enum": [
        "section", "isometric_room", "plan", "elevation", "photo", "table", "other"]},
    "rotation_deg": {"type": "integer", "enum": [0, 90, 180, 270]},
    "title": STR,
    "elements": {"type": "array", "items": _obj({
        "label_verbatim": STR, "kind": STR, "where": STR,
        "category": {"type": "string", "enum": ["layer", "object", "other"]}})},
    "dimensions": {"type": "array", "items": _obj({
        "value_mm": {"type": "number"}, "quote_verbatim": STR, "applies_to": STR})},
})

READ_PROMPT = (
    "You are reading a construction installation drawing. Report ONLY what is "
    "printed on it. rotation_deg: degrees clockwise the image must be rotated so "
    "its text reads upright. elements: every labelled part, label copied "
    "verbatim. category: layer for a material built up in place or laid as a "
    "sheet or board (substrate, slab, screed, bed, membrane, coating, "
    "insulation, cladding board, finish); object for a discrete manufactured "
    "item (fixing, bracket, anchor, flashing piece, trim, grate, fixture, "
    "appliance); other for notes and annotations. dimensions: every printed dimension, value in mm (a ratio like "
    "1:80 as 0.0125), the exact printed words, and what it applies to. Never "
    "invent a value.")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _numbers(s: str) -> list[float]:
    out = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s or "")]
    for a, b in re.findall(r"(\d+)\s*:\s*(\d+)", s or ""):
        if float(b):
            out.append(float(a) / float(b))
    return out


def gate(raw: dict, read: dict) -> tuple[dict, list[str]]:
    """Deterministic gates on a composed program (mutates a copy)."""
    prog = json.loads(json.dumps(raw))
    notes: list[str] = []
    printed = [_norm(d["quote_verbatim"]) for d in read.get("dimensions", [])] + \
              [_norm(e["label_verbatim"]) for e in read.get("elements", [])]
    labels = {_norm(e["label_verbatim"]): e["label_verbatim"]
              for e in read.get("elements", []) if e.get("label_verbatim")}
    for q in prog["params"]:
        q["name"] = re.sub(r"[^a-z0-9_]", "_", q["name"].lower())[:48] or "p"
        if q["provenance"] != "drawing":
            continue
        quote = _norm(q.get("source_quote", ""))
        on_drawing = bool(quote) and any(quote in p for p in printed if p)
        value_ok = any(abs(n - q["value"]) <= 1e-6 * max(1, abs(n))
                       for n in _numbers(q.get("source_quote") or ""))
        if not (on_drawing and value_ok):
            q["provenance"] = "assumption"
            q["note"] = (q.get("note") or "") + " [gate: not confirmed on drawing]"
            notes.append(f"param {q['name']}={q['value']:g}: quote "
                         f"{q.get('source_quote')!r} not confirmed -> assumption")
    ids = set()
    for part in prog["parts"]:
        part["id"] = re.sub(r"[^a-z0-9_]", "_", part["id"].lower())[:48] or "part"
        if not part["id"][0].isalpha():
            part["id"] = "p_" + part["id"]
        ids.add(part["id"])
    for part in prog["parts"]:
        lab = _norm(part.get("label", ""))
        if lab and lab in labels:
            part["label"] = labels[lab]
        else:
            if lab:
                notes.append(f"part {part['id']}: label {part['label']!r} "
                             "not on drawing -> dropped")
            part["label"] = None
        if part["shape"] != "cad":
            part["cad_brief"] = ""
        allowed = set(SHAPE_DIMS[part["shape"]]) | {"z0", "z1"}
        part["dims"] = [d for d in part["dims"] if d["name"] in allowed]
        part["install_after"] = [a for a in part["install_after"] if a in ids and a != part["id"]]
        if part["shape"] not in MIN_PATH:
            part["path"] = []
        for pt in part["path"]:
            if part["shape"] != "sweep":
                pt["z"] = ""
    return prog, notes


def library_dir() -> Path:
    d = var_root() / "library"
    d.mkdir(parents=True, exist_ok=True)
    return d


def drawing_png(spec: DetailSpec) -> bytes | None:
    """The source drawing, turned upright (as SAM and the viewer see it)."""
    doc = find_document_by_sha(spec.source.sha256)
    fig = figure_path(doc["id"], spec.source.crop_id) if doc else None
    if not fig:
        return None
    cache = library_dir() / "drawings" / f"{spec.detail_id}.png"
    rot = int(spec.context.get("drawing_rotation_deg") or 0)
    if cache.exists() and cache.stat().st_mtime >= fig.stat().st_mtime:
        return cache.read_bytes()
    png = sam.upright(fig.read_bytes(), rot)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(png)
    return png
