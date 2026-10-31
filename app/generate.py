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
from difflib import SequenceMatcher, get_close_matches
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
        "category": {"type": "string", "enum": ["layer", "object", "zone", "other"]}})},
    "dimensions": {"type": "array", "items": _obj({
        "value_mm": {"type": "number"}, "quote_verbatim": STR, "applies_to": STR})},
    "unlabelled": {"type": "array", "items": _obj({
        "what": STR, "where": STR, "category": {"type": "string", "enum": ["layer", "object"]}})},
})

READ_PROMPT = (
    "You are reading a construction installation drawing. Report ONLY what is "
    "printed on it. rotation_deg: degrees clockwise the image must be rotated so "
    "its text reads upright. elements: every labelled part, label copied "
    "verbatim. category: layer for a material built up in place or laid as a "
    "sheet or board (substrate, slab, screed, bed, membrane, coating, "
    "insulation, cladding board, finish); object for a discrete manufactured "
    "item (fixing, bracket, anchor, flashing piece, trim, grate, fixture, "
    "appliance); zone for something drawn that is not built: a clearance or "
    "circulation space, a set-out line, a datum, a dimension zone; other for "
    "notes and annotations. dimensions: every printed dimension, value in mm (a ratio like "
    "1:80 as 0.0125), the exact printed words, and what it applies to. Never "
    "invent a value. Copy every label letter for letter as printed: keep spelling "
    "mistakes, odd abbreviations, capitals and punctuation, even when a word looks "
    "wrong. Never correct, expand or tidy the words. unlabelled: every distinct "
    "part that is drawn but has no label of its own: a plate, strip, fixing, layer or "
    "block that no leader points at. Go over the drawing from one end to the other and "
    "list each with what it is, where it is and its category, saying how it bends or "
    "turns. A printed note that names a part is its name. Skip dimension lines, "
    "leaders, hatching and the drawing frame.")

PROOF_SCHEMA = _obj({"labels": {"type": "array", "items": _obj({"read": STR, "printed": STR})}})
PROOF_PROMPT = (
    "Proofread these labels against the drawing like a typesetter comparing a proof with "
    "its original. For each label find the printed words on the drawing and copy them "
    "letter by letter in `printed`, keeping any misspelling exactly: a printed word may "
    "contain a typing error, and we need to know. Do not assume the listed spelling is "
    "right. Copy the listed label into `read`. Labels: ")
# How alike two spellings must be to be the same words. A proofreader's answer that
# differs by more than a slip is a different reading, not a correction of this one.
SAME_WORDS = 0.8
# Shorter words are never matched loosely: one letter makes another word of them,
# and a label left unconfirmed is better than a label changed to something else.
FUZZY_WORD = 5


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _numbers(s: str) -> list[float]:
    out = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s or "")]
    for a, b in re.findall(r"(\d+)\s*:\s*(\d+)", s or ""):
        if float(b):
            out.append(float(a) / float(b))
    return out


def labels_in_text(labels: list[str], text: str) -> dict[str, str | None]:
    """{label: the words as the text layer spells them, or None if one is not there}.
    Word by word: a CAD export breaks a label across lines and text runs, in no order."""
    printed = text.split()
    words, folded = set(printed), {word.lower(): word for word in printed}
    found = {}
    for label in labels:
        spelled = []
        for word in label.split():
            match = word if word in words else folded.get(word.lower())
            if match is None and len(word) >= FUZZY_WORD:
                match = next(iter(get_close_matches(word, printed, 1, SAME_WORDS)), None)
            spelled.append(match)
        found[label] = " ".join(spelled) if spelled and all(spelled) else None
    return found


def proofread(labels: list[str], answers: list[dict]) -> dict[str, str | None]:
    """{label: the proofreader's spelling, or None when there is no usable answer}."""
    given = {answer["read"]: answer["printed"].strip() for answer in answers}
    found = {}
    for label in labels:
        printed = given.get(label)
        close = printed and SequenceMatcher(None, label, printed).ratio() >= SAME_WORDS
        found[label] = printed if close else None
    return found


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
