"""AI generation: drawing crop -> detail program -> canonical spec.

Two independent model passes plus deterministic gates:
  1. READ    — Muse reads the image: drawing type, rotation, every label
               verbatim, every printed dimension with its exact words.
  2. COMPOSE — Muse writes a detail program (closed grammar, app/program.py)
               from the image + the READ. It never writes geometry.
  3. GATES   — code, not model: a "drawing" value survives only if its quote
               is in the READ and the number is in the quote; labels must be
               verbatim READ labels; everything must compile and build. One
               retry with the error text (verifier-retry), else fail loudly.
Anything unconfirmed is demoted to an assumption, with a note saying why.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import cad_sandbox, meta_client, sam
from .geometry import build_assembly
from .verify import verify_all
from .program import (FIXTURES, MIN_PATH, SHAPE_DIMS, DetailProgram, ProgPart,
                      compile_program)
from .spec import DetailSpec, Provenance, SourceDoc
from .store import doc_dir, figure_path, find_document_by_sha, get_document, var_root


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
        "label_verbatim": STR, "kind": STR, "where": STR})},
    "dimensions": {"type": "array", "items": _obj({
        "value_mm": {"type": "number"}, "quote_verbatim": STR, "applies_to": STR})},
})

READ_PROMPT = (
    "You are reading a construction installation drawing. Report ONLY what is "
    "printed on it. rotation_deg: degrees clockwise the image must be rotated so "
    "its text reads upright. elements: every labelled part, label copied "
    "verbatim. dimensions: every printed dimension, value in mm (a ratio like "
    "1:80 as 0.0125), the exact printed words, and what it applies to. Never "
    "invent a value.")

DIM_NAMES = sorted({d for dims in SHAPE_DIMS.values() for d in dims} | {"z0", "z1"})

PROGRAM_SCHEMA = _obj({
    "title": STR,
    "kind": {"type": "string", "enum": ["section", "room", "assembly"]},
    "situation": {"type": "array", "items": STR},
    "params": {"type": "array", "items": _obj({
        "name": STR, "value": {"type": "number"},
        "unit": {"type": "string", "enum": ["mm", "ratio"]},
        "provenance": {"type": "string", "enum": ["drawing", "assumption"]},
        "source_quote": STR,
        "kind": {"type": "string", "enum": ["exact", "min", "max", "datum"]},
        "note": STR})},
    "parts": {"type": "array", "items": _obj({
        "id": STR, "type": STR, "label": STR,
        "shape": {"type": "string", "enum": [
            "box", "sheet", "post", "rod", "wedge", "fillet",
            "profile", "revolve", "sweep", "fixture", "cad"]},
        "fixture": {"type": "string", "enum": ["", *FIXTURES]},
        "cad_brief": STR,
        "dims": {"type": "array", "items": _obj({
            "name": {"type": "string", "enum": DIM_NAMES}, "expr": STR})},
        "path": {"type": "array", "items": _obj({"x": STR, "y": STR, "z": STR})},
        "install_after": {"type": "array", "items": STR},
        "note": STR})},
    "unresolved": {"type": "array", "items": _obj({"id": STR, "description": STR})},
})

GRAMMAR = """\
Write a DETAIL PROGRAM that builds the drawing in 3D: named parameters plus parts.
Units: millimetres. Axes: +x right, +y up, +z toward the viewer.

ROOM drawings (isometric or plan of a room): origin at the inside corner where the
back wall meets the left wall, y=0 at top of the floor substrate. Back wall spans x,
left wall spans z. Give every room part explicit z0/z1.
SECTION drawings (a cut through a junction): x across, y up; parts are extruded
along z by default, so omit z0/z1.

Shapes (dims are EXPRESSIONS over parameter names: + - * / min max numbers):
- box: x0 x1 y0 y1 [z0 z1]  axis-aligned block: wall, slab, tile layer, membrane
  zone (thin box on a surface), hob, bath, angle leg, threshold.
- sheet: path [{x,y}...] + thickness [z0 z1]  bent sheet along a centreline in a
  section: membrane, flashing, tape.
- post: cx cz r y0 y1  vertical cylinder: waste, pipe, tap body, fixing.
- rod: cx cy r [z0 z1]  horizontal cylinder along z: backing rod, pipe.
- wedge: x0 x1 y_bottom y_top_x0 fall_ratio [z0 z1]  screed falling toward +x.
- fillet: x y leg_x leg_y [z0 z1]  triangular cove / bond breaker.
- profile: path [{x,y}...] closed outline in the x-y plane (>= 3 points, do not
  repeat the first) [z0 z1]  ANY shaped cross-section: angles, channels, kerbs and
  hobs, sills, shower trays, step profiles, waterstop angles. Prefer ONE profile to
  several boxes when the drawing shows a shaped section. Extruded along z.
- revolve: path [{x: radius, y: height}...] + cx cz  round part on a vertical axis:
  floor waste and puddle flange, tap body, pipe collar.
- sweep: path [{x,y,z}...] + r  pipe of radius r along a 3D path.
- fixture: x0 x1 y0 y1 z0 z1 + fixture (bath, shower_tray, wc, basin, vanity, door)
  a recognisable fixture filling that box. WC cistern sits at min z.
- cad: x0 x1 y0 y1 z0 z1 + cad_brief  a distinctive object NO other shape can show
  (shaped floor grate, mixer or tap, clamping ring, door handle, bracket). It is
  modelled separately in CadQuery and is illustrative only, so never use it for a
  part that carries a printed dimension. At most 3 per program.
Path "z" is only for sweep (else ""); "fixture" is "" unless shape is fixture;
"cad_brief" is "" unless shape is cad (then: what the object looks like).

Parameters:
- provenance "drawing" ONLY for values printed on the drawing. source_quote = the
  exact printed words, copied from a READ dimension or label below. value must be
  the printed number.
- Everything else (room size, wall and slab thickness, layer thickness, fixture
  size, positions) is "assumption" with a typical value and note "typical; not on
  drawing". kind: "min" for minimum/at least, "max" for maximum, "datum" for
  reference levels and positions, else "exact".
Parts:
- id: lower_snake_case. type: what it is (wall, floor_slab, membrane, waterstop,
  tile, screed, bath, shower_screen, waste, hob, bond_breaker...).
- label: copy one READ label verbatim if it names this part, else "".
- install_after: parts installed before this one (substrate, then membrane, then
  finishes).
- Show what the drawing shows: substrates, every waterproofing zone at its printed
  height, junction parts (waterstops, angles, hobs, bond breakers), fixtures as
  simple boxes/posts, finishes where shown. 8 to 30 parts.
- Thin layers as assumptions: membrane 1.5, tile 10, tile bed 5.
"""

EXAMPLE: dict = {
    "title": "Shower corner (example)", "kind": "room",
    "situation": ["Residential", "Internal", "Bathroom / Shower", "Floor + walls"],
    "params": [
        {"name": "floor", "value": 0, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "datum", "note": "top of floor substrate"},
        {"name": "room_w", "value": 1800, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "room_d", "value": 1500, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "wall_h", "value": 2400, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "wall_t", "value": 90, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "slab_t", "value": 120, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "memb_t", "value": 1.5, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "shower_w", "value": 900, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "upturn_h", "value": 150, "unit": "mm", "provenance": "drawing",
         "source_quote": "150mm", "kind": "min", "note": "membrane upturn outside shower"},
        {"name": "shower_d", "value": 900, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "hob_w", "value": 100, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
        {"name": "hob_h", "value": 50, "unit": "mm", "provenance": "assumption",
         "source_quote": "", "kind": "exact", "note": "typical; not on drawing"},
    ],
    "parts": [
        {"id": "floor_slab", "type": "floor_slab", "label": "", "shape": "box",
         "dims": [{"name": "x0", "expr": "0 - wall_t"}, {"name": "x1", "expr": "room_w"},
                  {"name": "y0", "expr": "floor - slab_t"}, {"name": "y1", "expr": "floor"},
                  {"name": "z0", "expr": "0 - wall_t"}, {"name": "z1", "expr": "room_d"}],
         "path": [], "install_after": [], "note": ""},
        {"id": "back_wall", "type": "wall", "label": "", "shape": "box",
         "dims": [{"name": "x0", "expr": "0 - wall_t"}, {"name": "x1", "expr": "room_w"},
                  {"name": "y0", "expr": "floor"}, {"name": "y1", "expr": "wall_h"},
                  {"name": "z0", "expr": "0 - wall_t"}, {"name": "z1", "expr": "0"}],
         "path": [], "install_after": ["floor_slab"], "note": ""},
        {"id": "membrane_floor", "type": "membrane", "label": "", "shape": "box",
         "dims": [{"name": "x0", "expr": "0"}, {"name": "x1", "expr": "room_w"},
                  {"name": "y0", "expr": "floor"}, {"name": "y1", "expr": "floor + memb_t"},
                  {"name": "z0", "expr": "0"}, {"name": "z1", "expr": "room_d"}],
         "path": [], "install_after": ["floor_slab"], "note": ""},
        {"id": "membrane_upturn_back", "type": "membrane", "label": "", "shape": "box",
         "dims": [{"name": "x0", "expr": "shower_w"}, {"name": "x1", "expr": "room_w"},
                  {"name": "y0", "expr": "floor"}, {"name": "y1", "expr": "floor + upturn_h"},
                  {"name": "z0", "expr": "0"}, {"name": "z1", "expr": "memb_t"}],
         "path": [], "install_after": ["back_wall"], "note": "upturn outside shower"},
        {"id": "shower_hob", "type": "hob", "label": "", "shape": "profile",
         "dims": [{"name": "z0", "expr": "0"}, {"name": "z1", "expr": "shower_d"}],
         "path": [{"x": "shower_w", "y": "floor", "z": ""},
                  {"x": "shower_w + hob_w", "y": "floor", "z": ""},
                  {"x": "shower_w + hob_w", "y": "floor + hob_h - 10", "z": ""},
                  {"x": "shower_w + hob_w - 10", "y": "floor + hob_h", "z": ""},
                  {"x": "shower_w", "y": "floor + hob_h", "z": ""}],
         "install_after": ["membrane_floor"], "note": "chamfered kerb at shower edge"},
        {"id": "bath", "type": "bath", "label": "", "shape": "fixture", "fixture": "bath",
         "dims": [{"name": "x0", "expr": "room_w - 1500"}, {"name": "x1", "expr": "room_w"},
                  {"name": "y0", "expr": "floor"}, {"name": "y1", "expr": "floor + 550"},
                  {"name": "z0", "expr": "0"}, {"name": "z1", "expr": "760"}],
         "path": [], "install_after": ["membrane_floor"], "note": "size typical"},
    ],
    "unresolved": [],
}


for _part in EXAMPLE["parts"]:
    _part.setdefault("fixture", "")
    _part.setdefault("cad_brief", "")
    for _pt in _part["path"]:
        _pt.setdefault("z", "")


MAX_CAD = 3

CAD_SCHEMA = _obj({"code": STR, "summary": STR})


def _cad_prompt(part, env: tuple[float, ...]) -> str:
    W, H, D = env[1] - env[0], env[3] - env[2], env[5] - env[4]
    return (
        "Write CadQuery (Python) for ONE part of the construction drawing in the image.\n"
        f"Part: {part.id} ({part.type}). What to model: {part.cad_brief}\n"
        f"Envelope in mm: W={W:g} along CadQuery X, D={D:g} along CadQuery Y (the "
        f"part's front is at Y=0), H={H:g} along CadQuery Z (up). Stay inside "
        "0..W, 0..D, 0..H.\n"
        "Define exactly: def build(W, H, D): returning a cq.Workplane or cq.Shape.\n"
        "Only `import cadquery as cq` and `import math`. No files, OS, network, exec "
        "or eval.\n"
        "Make it recognisable with solids: box, cylinder, polyline + extrude, revolve, "
        "sweep, loft, fillet, chamfer, shell, cut, union. Under 80 lines.\n"
        "It is illustrative: proportion it from the drawing; do not invent "
        "manufacturer dimensions.")


def _cad_part(img: bytes, mime: str, part, env: tuple[float, ...], model: str,
              runner: cad_sandbox.Runner | None) -> tuple[cad_sandbox.Fitted, str, list]:
    """Model writes CadQuery; sandbox runs it; one retry with the error."""
    usage: list = []
    feedback, last = "", ""
    for _ in range(2):
        out, u = meta_client.respond_json(_content(img, mime, _cad_prompt(part, env) + feedback),
                                          CAD_SCHEMA, "cad_part", model)
        usage.append(u)
        try:
            return cad_sandbox.run_cad(out["code"], env, runner), out["code"], usage
        except cad_sandbox.CadCodeError as err:
            last = str(err)[:800]
            feedback = f"\n\nYOUR CODE WAS REJECTED:\n{last}\nFix it."
    raise cad_sandbox.CadCodeError(last)


def _boxed(program: DetailProgram, ids: set[str] | None = None,
           why: str = "") -> DetailProgram:
    """Copy with cad parts (all, or `ids`) turned into plain envelope boxes."""
    parts = []
    for part in program.parts:
        if part.shape == "cad" and (ids is None or part.id in ids):
            note = "; ".join(x for x in (part.note, why and f"CAD fallback: {why}") if x)
            part = part.model_copy(update={"shape": "box", "cad_brief": None,
                                           "note": note or None})
        parts.append(part)
    return program.model_copy(update={"parts": parts})


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
                       for n in _numbers(q.get("source_quote", "")))
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
        if part["shape"] == "fixture" and part.get("fixture") not in FIXTURES:
            notes.append(f"part {part['id']}: unknown fixture {part.get('fixture')!r} -> box")
            part["shape"] = "box"
        if part["shape"] != "fixture":
            part["fixture"] = ""
        if part["shape"] == "cad":
            n_cad = sum(1 for q in prog["parts"][:prog["parts"].index(part)] if q["shape"] == "cad")
            if not (part.get("cad_brief") or "").strip() or n_cad >= MAX_CAD:
                notes.append(f"part {part['id']}: cad not allowed here -> box")
                part["shape"] = "box"
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


def _content(img: bytes, mime: str, text: str) -> list[dict]:
    b64 = base64.b64encode(img).decode()
    return [{"type": "input_text", "text": text},
            {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}]


def _source(doc: dict, crop_id: str) -> SourceDoc:
    meta = json.loads((doc_dir(doc["id"]) / "meta.json").read_text())
    page = int(crop_id[1:].split("_", 1)[0]) if crop_id.startswith("p") else 0
    pg = next((p for p in meta.get("pages", []) if p["index"] == page), {})
    return SourceDoc(filename=doc["filename"], sha256=doc["sha256"], page_index=page,
                     crop_id=crop_id, page_width_pt=pg.get("width_pt", 595),
                     page_height_pt=pg.get("height_pt", 842),
                     coord_note="Program axes: +x right, +y up, +z toward viewer (mm).")


def library_dir() -> Path:
    d = var_root() / "library"
    d.mkdir(parents=True, exist_ok=True)
    return d


def detail_id_for(sha: str, crop_id: str) -> str:
    return f"ai-{sha[:8]}-{crop_id.lower()}"


def generate(doc_id: str, crop_id: str,
             progress: Callable[[str], None] = lambda s: None,
             model: str | None = None,
             cad_runner: cad_sandbox.Runner | None = None) -> dict:
    doc = get_document(doc_id)
    if not doc:
        raise ValueError("unknown document")
    fig = figure_path(doc_id, crop_id)
    if not fig:
        raise ValueError("unknown figure")
    img = fig.read_bytes()
    mime = "image/jpeg" if fig.suffix == ".jpg" else "image/png"
    model = model or meta_client.default_model()
    usage: list[dict | None] = []
    t0 = time.time()

    progress(f"reading drawing ({model})")
    read, u = meta_client.respond_json(_content(img, mime, READ_PROMPT),
                                       READ_SCHEMA, "drawing_read", model)
    usage.append(u)
    progress(f"read: {read['drawing_type']}, {len(read['elements'])} labels, "
             f"{len(read['dimensions'])} dimensions")

    base = (GRAMMAR + "\nEXAMPLE (structure only; its values are not this drawing):\n"
            + json.dumps(EXAMPLE) + "\n\nREAD of this drawing (labels and dimensions "
            "printed on it):\n" + json.dumps(read) + "\n\nWrite the program for the "
            "drawing in the image.")
    feedback = ""
    last_err = ""
    for attempt in range(2):
        progress("composing detail program" + (" (retry with verifier feedback)" if attempt else ""))
        raw, u = meta_client.respond_json(_content(img, mime, base + feedback),
                                          PROGRAM_SCHEMA, "detail_program", model)
        usage.append(u)
        prog_raw, notes = gate(raw, read)
        try:
            program = DetailProgram(
                detail_id=detail_id_for(doc["sha256"], crop_id),
                title=prog_raw["title"] or f"Detail {crop_id}",
                origin=f"ai-generated:{model} (unreviewed; two-pass read+compose, gated)",
                kind=prog_raw["kind"], situation=prog_raw["situation"],
                source=_source(doc, crop_id),
                params=[{**q, "source_quote": q["source_quote"] or None,
                         "note": q["note"] or None} for q in prog_raw["params"]],
                parts=[{**p, "note": p["note"] or None, "fixture": p.get("fixture") or None,
                        "cad_brief": p.get("cad_brief") or None,
                        "path": [{**pt, "z": pt.get("z") or None} for pt in p["path"]]}
                       for p in prog_raw["parts"]],
                unresolved=[{"id": u_["id"], "description": u_["description"]}
                            for u_ in prog_raw["unresolved"]])
            meshes = build_assembly(compile_program(_boxed(program)))
            if not any(m.tri_count for m in meshes.values()):
                raise ValueError("program produced no geometry")
            break
        except Exception as err:  # noqa: BLE001 - fed back to the model once
            last_err = str(err)[:600]
            progress(f"verifier rejected program: {last_err[:120]}")
            feedback = (f"\n\nYOUR PREVIOUS PROGRAM WAS REJECTED: {last_err}\n"
                        "Fix it: every expression may only use declared parameter "
                        "names; every shape needs its dims; sizes must be positive.")
    else:
        raise RuntimeError(f"program rejected twice: {last_err}")

    # CAD stage: model-written CadQuery for parts the grammar can't express
    cad_parts = [p for p in program.parts if p.shape == "cad"]
    cad_info: dict[str, dict] = {}
    cad_code: dict[str, str] = {}
    if cad_parts and not (cad_runner or cad_sandbox.available()):
        program = _boxed(program, why="CAD sandbox not configured")
        notes.append("cad parts drawn as boxes: CAD sandbox not configured")
    elif cad_parts:
        progress(f"modelling {len(cad_parts)} part(s) in CadQuery (Modal sandbox)")
        envs = {c.id: tuple({q.name: q.value for q in c.params}[k]
                            for k in ("x0", "x1", "y0", "y1", "z0", "z1"))
                for c in compile_program(_boxed(program)).components
                if c.id in {p.id for p in cad_parts}}
        detail = detail_id_for(doc["sha256"], crop_id)

        def one(part):
            try:
                return part.id, _cad_part(img, mime, part, envs[part.id], model, cad_runner), None
            except Exception as err:  # noqa: BLE001 - becomes a box + note
                return part.id, None, str(err)[:300]

        failed: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=MAX_CAD) as ex:
            for pid, res, err in ex.map(one, cad_parts):
                if res is None:
                    failed[pid] = err or "unknown error"
                    progress(f"cad {pid}: failed -> box ({failed[pid][:80]})")
                    continue
                fitted, code, u = res
                usage.extend(u)
                rel = f"cad/{detail}/{pid}.json"
                (library_dir() / "cad" / detail).mkdir(parents=True, exist_ok=True)
                (library_dir() / rel).write_text(json.dumps(
                    {"positions": [round(v, 3) for v in fitted.positions]}))
                cad_code[pid] = code
                cad_info[pid] = {"file": rel, "scale": round(fitted.scale, 4),
                                 "native_size_mm": [round(v, 1) for v in fitted.native_size],
                                 "code_sha": hashlib.sha256(code.encode()).hexdigest()}
                progress(f"cad {pid}: ok ({len(fitted.positions) // 9} triangles"
                         + (f", scaled x{fitted.scale:.2f} to fit" if fitted.scale < 1 else "") + ")")
        if failed:
            for pid, why in failed.items():
                notes.append(f"part {pid}: CAD failed -> box ({why[:120]})")
            program = _boxed(program, set(failed), "sandbox run failed")
    spec = compile_program(program)
    spec.context["cad_parts"] = cad_info
    build_assembly(spec)  # final check, now with the AI-modelled meshes

    record = {"detail_id": spec.detail_id, "model": model, "read": read,
              "program": program.model_dump(mode="json"),
              "spec": spec.model_dump(mode="json"), "gate_notes": notes,
              "cad_code": cad_code,
              "usage": usage, "seconds": round(time.time() - t0, 1)}
    (library_dir() / f"{spec.detail_id}.json").write_text(json.dumps(record, indent=1))
    if meta_client.configured():
        try:
            locate_on_drawing(spec.detail_id, progress)
            record = json.loads((library_dir() / f"{spec.detail_id}.json").read_text())
            spec = DetailSpec.model_validate(record["spec"])
        except Exception as err:  # noqa: BLE001 - linking is a bonus, not a gate
            progress(f"SAM linking skipped: {str(err)[:100]}")
    progress(f"done: {len(spec.components)} parts, "
             f"{len(spec.context['drawing_params'])} drawing values, "
             f"{len(notes)} gate notes")
    return record


def remodel_part(detail_id: str, part_id: str, brief: str | None = None,
                 progress: Callable[[str], None] = lambda s: None,
                 model: str | None = None,
                 cad_runner: cad_sandbox.Runner | None = None) -> dict:
    """Replace one part of an AI-generated detail with AI-modelled CadQuery
    geometry, fitted into the space the part occupies now. The part keeps its
    id, label and install order; its dims become illustrative."""
    f = library_dir() / f"{detail_id}.json"
    if not f.exists():
        raise ValueError("only AI-generated library details can be re-modelled")
    rec = json.loads(f.read_text())
    program = DetailProgram.model_validate(rec["program"])
    part = next((p for p in program.parts if p.id == part_id), None)
    if part is None:
        raise ValueError(f"unknown part {part_id!r}")
    spec = DetailSpec.model_validate(rec["spec"])
    b = build_assembly(spec)[part_id].bounds()
    if b is None:
        raise ValueError(f"{part_id} has no geometry to re-model")
    env = (b[0], b[3], b[1], b[4], b[2], b[5])
    env = tuple(v if i % 2 == 0 else max(v, env[i - 1] + 1.0) for i, v in enumerate(env))
    doc = find_document_by_sha(spec.source.sha256)
    fig = figure_path(doc["id"], spec.source.crop_id) if doc else None
    if not fig:
        raise ValueError("source drawing not uploaded on this server")
    model = model or meta_client.default_model()
    brief = brief or " — ".join(x for x in (part.label, part.type.replace("_", " "), part.note) if x)
    cad_part = part.model_copy(update={"shape": "cad", "cad_brief": brief})
    progress(f"writing CadQuery for {part_id} ({model})")
    mime = "image/jpeg" if fig.suffix == ".jpg" else "image/png"
    fitted, code, usage = _cad_part(fig.read_bytes(), mime, cad_part, env, model, cad_runner)
    progress(f"sandbox ok: {len(fitted.positions) // 9} triangles"
             + (f", scaled x{fitted.scale:.2f} to fit" if fitted.scale < 1 else ""))
    rel = f"cad/{detail_id}/{part_id}.json"
    (library_dir() / "cad" / detail_id).mkdir(parents=True, exist_ok=True)
    (library_dir() / rel).write_text(json.dumps({"positions": [round(v, 3) for v in fitted.positions]}))
    names = ("x0", "x1", "y0", "y1", "z0", "z1")
    new_part = ProgPart.model_validate({  # validate: model_copy would skip it
        **cad_part.model_dump(),
        "dims": [{"name": k, "expr": f"{v:.3f}"} for k, v in zip(names, env)],
        "path": [], "fixture": None,
        "note": "re-modelled on request in CadQuery (was " + part.shape + ")"})
    program = program.model_copy(update={"parts": [new_part if p.id == part_id else p
                                                    for p in program.parts]})
    new_spec = carry_context(spec, compile_program(program))
    cad_parts = dict(spec.context.get("cad_parts") or {})
    cad_parts[part_id] = {"file": rel, "scale": round(fitted.scale, 4),
                          "native_size_mm": [round(v, 1) for v in fitted.native_size],
                          "code_sha": hashlib.sha256(code.encode()).hexdigest()}
    new_spec.context["cad_parts"] = cad_parts
    build_assembly(new_spec)
    rec.update(program=program.model_dump(mode="json"), spec=new_spec.model_dump(mode="json"))
    rec.setdefault("cad_code", {})[part_id] = code
    rec.setdefault("gate_notes", []).append(f"part {part_id}: re-modelled in CadQuery on request")
    rec.setdefault("usage", []).extend(usage)
    f.write_text(json.dumps(rec, indent=1))
    override = var_root() / f"spec-{detail_id}.json"
    if override.exists():
        override.unlink()  # a stale user override would hide the new geometry
    return {"detail_id": detail_id, "part_id": part_id, "triangles": len(fitted.positions) // 9}


CRITIQUE_SCHEMA = _obj({
    "issues": {"type": "array", "items": _obj({"part_id": STR, "problem": STR, "fix": STR})},
    "program": PROGRAM_SCHEMA,
})

CRITIQUE_PROMPT = """\
You are checking a 3D model generated from a construction drawing.
Image 1 is the ORIGINAL DRAWING. The next images are OUR 3D RENDER of it (an
iso view and a section/front view); each part is tagged with its part id.
Compare them carefully and list concrete problems: missing or extra parts, wrong
position, size or proportion, a printed dimension attached to the wrong part, a
label on the wrong part, wrong layer order. Then return the corrected FULL program
in the same grammar (below).
Rules: keep part ids for parts that stay. Do not change parts whose shape is "cad".
Never invent printed values: provenance "drawing" only with the exact printed
words as source_quote. If nothing is wrong, return the program unchanged and no
issues.
"""


def _to_program(prog_raw: dict, base: DetailProgram) -> DetailProgram:
    return DetailProgram(
        detail_id=base.detail_id, title=prog_raw["title"] or base.title,
        origin=base.origin, kind=prog_raw["kind"], situation=prog_raw["situation"],
        source=base.source,
        params=[{**q, "source_quote": q["source_quote"] or None, "note": q["note"] or None}
                for q in prog_raw["params"]],
        parts=[{**p, "note": p["note"] or None, "fixture": p.get("fixture") or None,
                "cad_brief": p.get("cad_brief") or None,
                "path": [{**pt, "z": pt.get("z") or None} for pt in p["path"]]}
               for p in prog_raw["parts"]],
        unresolved=[{"id": u["id"], "description": u["description"]}
                    for u in prog_raw["unresolved"]])


def _llm_view(program: DetailProgram) -> dict:
    """Program in the exact shape the model writes (schema keys, '' for None)."""
    d = program.model_dump(mode="json")
    return {
        "title": d["title"], "kind": d["kind"], "situation": d["situation"],
        "params": [{"name": q["name"], "value": q["value"], "unit": q["unit"],
                    "provenance": "drawing" if q["provenance"] == "drawing" else "assumption",
                    "source_quote": q["source_quote"] or "", "kind": q["kind"],
                    "note": q["note"] or ""} for q in d["params"]],
        "parts": [{"id": p["id"], "type": p["type"], "label": p["label"] or "",
                   "shape": p["shape"], "fixture": p["fixture"] or "",
                   "cad_brief": p["cad_brief"] or "", "dims": p["dims"],
                   "path": [{"x": pt["x"], "y": pt["y"], "z": pt["z"] or ""} for pt in p["path"]],
                   "install_after": p["install_after"], "note": p["note"] or ""}
                  for p in d["parts"]],
        "unresolved": [{"id": u["id"], "description": u["description"]} for u in d["unresolved"]],
    }


def quality(spec: DetailSpec, meshes: dict) -> dict:
    """What a correction must not lose: failures, drawing values driving
    geometry, and parts carrying a verbatim drawing label."""
    res = verify_all(spec, meshes, None)
    return {"fail": sum(1 for r in res if r.status == "FAIL" and r.id != "C-labels"),
            "traced": sum(1 for r in res if r.id.startswith("C-trace-") and r.status == "PASS"),
            "labels": sum(1 for c in spec.components if c.source_label)}


def _diff(old: DetailProgram, new: DetailProgram) -> dict:
    op = {q.name: q for q in old.params}
    np_ = {q.name: q for q in new.params}
    oparts = {p.id: p for p in old.parts}
    nparts = {p.id: p for p in new.parts}
    changed_params = [n for n in op.keys() & np_.keys()
                      if (op[n].value, op[n].provenance) != (np_[n].value, np_[n].provenance)]
    changed_parts = [i for i in oparts.keys() & nparts.keys()
                     if oparts[i].model_dump() != nparts[i].model_dump()]
    return {"params_added": sorted(np_.keys() - op.keys()),
            "params_removed": sorted(op.keys() - np_.keys()),
            "params_changed": sorted(changed_params),
            "parts_added": sorted(nparts.keys() - oparts.keys()),
            "parts_removed": sorted(oparts.keys() - nparts.keys()),
            "parts_changed": sorted(changed_parts)}


def critique(detail_id: str, renders: list[str],
             progress: Callable[[str], None] = lambda s: None,
             model: str | None = None) -> dict:
    """One render -> compare -> correct round, accepted only if it doesn't
    make the detail worse (no new failures; no lost traced values or labels)."""
    f = library_dir() / f"{detail_id}.json"
    if not f.exists():
        raise ValueError("only AI-generated library details can be checked")
    if not renders or not all(r.startswith("data:image/") for r in renders):
        raise ValueError("renders must be data:image URLs")
    rec = json.loads(f.read_text())
    old = DetailProgram.model_validate(rec["program"])
    old_spec = DetailSpec.model_validate(rec["spec"])
    doc = find_document_by_sha(old.source.sha256)
    fig = figure_path(doc["id"], old.source.crop_id) if doc else None
    if not fig:
        raise ValueError("source drawing not uploaded on this server")
    model = model or meta_client.default_model()
    mime = "image/jpeg" if fig.suffix == ".jpg" else "image/png"
    content = _content(fig.read_bytes(), mime, CRITIQUE_PROMPT + "\n" + GRAMMAR
                       + "\nCURRENT PROGRAM:\n" + json.dumps(_llm_view(old))
                       + "\n\nREAD of the drawing:\n" + json.dumps(rec.get("read", {})))
    content += [{"type": "input_image", "image_url": r} for r in renders[:3]]
    progress(f"comparing render with drawing ({model})")
    out, usage = meta_client.respond_json(content, CRITIQUE_SCHEMA, "critique", model)
    issues = out["issues"]
    progress(f"{len(issues)} issue(s) found")

    # cad parts are frozen: put the old ones back exactly
    prog_raw, notes = gate(out["program"], rec.get("read", {}))
    old_cad = {p.id: p for p in old.parts if p.shape == "cad"}
    verdict = {"issues": issues, "accepted": False, "reason": "", "diff": {}}
    try:
        new = _to_program(prog_raw, old)
        parts = [old_cad.get(p.id, p) if p.id in old_cad else p
                 for p in new.parts if not (p.shape == "cad" and p.id not in old_cad)]
        parts += [p for i, p in old_cad.items() if i not in {q.id for q in parts}]
        new = DetailProgram.model_validate({**new.model_dump(), "parts": [p.model_dump() for p in parts]})
        new_spec = carry_context(old_spec, compile_program(new))
        q_old = quality(old_spec, build_assembly(old_spec))
        q_new = quality(new_spec, build_assembly(new_spec))
        verdict["diff"] = _diff(old, new)
        verdict["quality"] = {"before": q_old, "after": q_new}
        if not any(verdict["diff"].values()):
            verdict["reason"] = "no changes proposed"
        elif q_new["fail"] > q_old["fail"]:
            verdict["reason"] = f"rejected: {q_new['fail']} failing checks (was {q_old['fail']})"
        elif q_new["traced"] < q_old["traced"] or q_new["labels"] < q_old["labels"]:
            verdict["reason"] = ("rejected: would lose drawing evidence "
                                 f"(traced {q_old['traced']}->{q_new['traced']}, "
                                 f"labels {q_old['labels']}->{q_new['labels']})")
        else:
            verdict["accepted"] = True
            verdict["reason"] = "accepted"
    except Exception as err:  # noqa: BLE001 - a broken proposal is just rejected
        verdict["reason"] = f"rejected: proposal does not build ({str(err)[:200]})"
    progress(verdict["reason"])
    rec.setdefault("revisions", []).append({**verdict, "gate_notes": notes, "model": model,
                                            "usage": usage})
    if verdict["accepted"]:
        rec.update(program=new.model_dump(mode="json"), spec=new_spec.model_dump(mode="json"))
        override = var_root() / f"spec-{detail_id}.json"
        if override.exists():
            override.unlink()
    f.write_text(json.dumps(rec, indent=1))
    return verdict


def carry_context(old: DetailSpec, new: DetailSpec) -> DetailSpec:
    """Recompiling a program rebuilds context; keep what later stages added
    (CAD meshes, SAM drawing regions and the drawing's rotation)."""
    for k in ("drawing_regions", "drawing_rotation_deg"):
        if k in old.context:
            new.context[k] = old.context[k]
    new.context["cad_parts"] = old.context.get("cad_parts") or {}
    return new


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


def locate_on_drawing(detail_id: str, progress: Callable[[str], None] = lambda s: None,
                      call=None) -> dict:
    """SAM 3.1 pass: a region on the upright drawing for each part it can find."""
    f = library_dir() / f"{detail_id}.json"
    if not f.exists():
        raise ValueError("only AI-generated library details can be located")
    rec = json.loads(f.read_text())
    spec = DetailSpec.model_validate(rec["spec"])
    spec.context["drawing_rotation_deg"] = int((rec.get("read") or {}).get("rotation_deg") or 0)
    (library_dir() / "drawings" / f"{detail_id}.png").unlink(missing_ok=True)
    png = drawing_png(spec)
    if png is None:
        raise ValueError("source drawing not uploaded on this server")
    parts = [(c.id, c.type, c.source_label) for c in spec.components]
    progress(f"locating {len(parts)} parts on the drawing (SAM 3.1)")
    found = sam.locate(png, parts, call)
    spec.context["drawing_regions"] = found
    rec["spec"] = spec.model_dump(mode="json")
    f.write_text(json.dumps(rec, indent=1))
    progress(f"SAM located {len(found)} of {len(parts)} parts")
    return {"located": len(found), "parts": len(parts)}


def load_library_spec(detail_id: str) -> DetailSpec | None:
    f = library_dir() / f"{detail_id}.json"
    if not f.exists():
        return None
    return DetailSpec.model_validate(json.loads(f.read_text())["spec"])


def list_library() -> list[dict]:
    out = []
    for f in sorted(library_dir().glob("*.json")):
        rec = json.loads(f.read_text())
        s = rec["spec"]
        out.append({"detail_id": s["detail_id"], "title": s["title"],
                    "crop_id": s["source"]["crop_id"], "sha256": s["source"]["sha256"],
                    "origin": s["origin"]})
    return out
