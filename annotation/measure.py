"""Score the proposals against synthetic truth.

    python annotation/cli.py measure --sheets 100 --seed 7

`app/synthetic.py` knows every part's true shape and class. This takes the DXFs it
drafts, runs the pipeline on each with nothing but the file path, and only then opens the
truth. Layer names are scrubbed first, because the generator's name each part.

Three variants of the same sheets, each harder than the last:
  clean  as drafted: closed polylines, leaders stopping at the middle of each part.
  lines  every part outline exploded into plain LINEs, as the owner's sheets are drawn.
  rough  lines, plus drafting damage: ends that stop short or overshoot, dashed outlines
         cut into real dashes, some leaders stopping at the outline, some real LEADERs.

All three are cleaner than real drawings, and the report says so.
"""
from __future__ import annotations

import copy
import inspect
import json
import logging
import math
import random
import shutil
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import ezdxf
import numpy as np

from app import synthetic

from . import names, pipeline, propagate, sheets

VARIANTS = ("clean", "lines", "rough")
IOUS = (0.5, 0.75)
PART_LAYER = "P_"  # the generator's layer prefix: read only here, never by the pipeline
ROUGH = {"end_miss": 0.12, "end_miss_p": 0.5, "overshoot_p": 0.15, "outline_tip_p": 0.5, "real_leader_p": 0.4}
HARDER_NOTE = ("Synthetic sheets, cleaner than real ones. Even `rough` has only simple leaders, one detail per sheet, "
               "no title block, no exploded hatches or dimensions, and classes printed in words the ontology mostly knows.")


# ---------------------------------------------------------------- sheets with truth

@dataclass
class Captured:
    doc: object
    arguments: dict


def _capture(index, seed, templates, styles, cfg):
    """make_drawing, keeping the document it drafted and the true geometry it drafted from.
    Saved after the render so the saved file is the sheet the generator rendered."""
    original = synthetic.draft
    signature = inspect.signature(original)
    seen = {}

    def spy(*args, **kwargs):
        doc = original(*args, **kwargs)
        seen.update(signature.bind(*args, **kwargs).arguments, doc=doc)
        return doc

    with mock.patch.object(synthetic, "draft", spy):
        drawing = synthetic.make_drawing(index, seed, templates, styles, cfg)
    return drawing, Captured(seen["doc"], seen)


def truth_of(drawing, captured, cfg) -> dict:
    a = captured.arguments
    d, n = a["d"], a["n"]
    ppu = d.dpi / 25.4 / n
    words = a["words"]
    parts = []
    for row, instance in zip(drawing.record["parts"], drawing.instances):
        pid = row["id"]
        g = a["geo"][pid]
        parts.append({"id": pid, "class_id": row["class_id"], "thin": row["thin"], "mode": row["mode"],
                      "linetype": row["linetype"], "min_width_px": row["min_width_px"], "widened": row["widened"],
                      "rings": [r.tolist() for r in g.rings], "centre": list(g.centre), "label": words.get(pid)})
    linetypes = {k: [v * n for v in values] for k, values in d.linetypes.items()}
    return {"drawing_id": drawing.drawing_id, "template": drawing.record["template"]["id"],
            "style": drawing.record["style"]["id"], "px_per_unit": ppu, "text_height": d.text_height * n,
            "paper_scale": n, "thin_px": cfg.thin_px, "parts": parts, "linetypes": linetypes}


def scrub_layers(doc) -> None:
    """Rename every layer: the generator's layer names carry part ids."""
    for k, name in enumerate(sorted(layer.dxf.name for layer in doc.layers)):
        if name not in ("0", "Defpoints"):
            doc.layers.get(name).rename(f"L{k:03d}")


def _attribs(entity) -> dict:
    return {k: entity.dxf.get(k) for k in ("layer", "linetype", "color", "lineweight") if entity.dxf.hasattr(k)}


def _explode(msp, entity, emit) -> None:
    points = [tuple(p) for p in entity.get_points("xy")]
    if entity.closed:
        points.append(points[0])
    attribs = _attribs(entity)
    msp.delete_entity(entity)
    for a, b in zip(points, points[1:]):
        emit(a, b, attribs)


def _dash_pieces(a, b, pattern, rng):
    """The drawn dashes of one line. `pattern` is dash, -gap, dash, -gap ..."""
    a, b = np.array(a), np.array(b)
    length = float(np.hypot(*(b - a)))
    unit = (b - a) / max(length, 1e-12)
    at = -rng.uniform(0, sum(abs(x) for x in pattern))
    k = 0
    pieces = []
    while at < length:
        step = abs(pattern[k % len(pattern)])
        if pattern[k % len(pattern)] > 0:
            lo, hi = max(at, 0.0), min(at + step, length)
            if hi > lo:
                pieces.append((tuple(a + unit * lo), tuple(b - unit * (length - hi))))
        at += step
        k += 1
    return pieces


def apply_variant(doc, truth, variant, seed) -> None:
    if variant == "clean":
        return
    rng = random.Random(f"damage:{seed}:{truth['drawing_id']}:{variant}")
    msp = doc.modelspace()
    th = truth["text_height"]
    miss = ROUGH["end_miss"] * th
    looks = {p["id"].upper(): p for p in truth["parts"]}

    def emit(a, b, attribs):
        a, b = np.array(a, dtype=float), np.array(b, dtype=float)
        length = float(np.hypot(*(b - a)))
        if length <= 0:
            return
        if variant == "rough":
            unit = (b - a) / length
            for sign, end in ((-1.0, 0), (1.0, 1)):
                roll = rng.random()
                move = 0.0
                if roll < ROUGH["end_miss_p"]:
                    move = -rng.uniform(0.0, miss)
                elif roll < ROUGH["end_miss_p"] + ROUGH["overshoot_p"]:
                    move = rng.uniform(0.0, miss)
                if end == 0:
                    a = a + unit * sign * move
                else:
                    b = b + unit * sign * move
            part = looks.get(attribs.get("layer", "")[len(PART_LAYER):].upper())
            if part and part["linetype"] != "continuous":
                clean = {k: v for k, v in attribs.items() if k != "linetype"}
                for p, q in _dash_pieces(a, b, truth["linetypes"][part["linetype"]], rng):
                    msp.add_line(p, q, dxfattribs=clean)
                return
        msp.add_line(tuple(a), tuple(b), dxfattribs=attribs)

    for entity in list(msp.query("LWPOLYLINE")):
        if entity.dxf.layer.startswith(PART_LAYER):
            _explode(msp, entity, emit)
    if variant == "rough":
        _rough_leaders(doc, msp, truth, rng)


def _rough_leaders(doc, msp, truth, rng) -> None:
    """Some leaders stop at the part's outline, and some become real LEADER entities."""
    centres = {tuple(np.round(p["centre"], 3)): p for p in truth["parts"] if p["label"]}
    texts = list(msp.query("MTEXT"))
    tips = []
    for entity in list(msp.query("LWPOLYLINE")):
        if entity.closed or len(entity) != 3:
            continue
        points = [tuple(p) for p in entity.get_points("xy")]
        part = centres.get(tuple(np.round(points[0], 3)))
        if part is None:
            continue
        old_tip = points[0]
        if rng.random() < ROUGH["outline_tip_p"]:
            hit = _outline_hit(points[0], points[1], part["rings"])
            if hit is not None and math.dist(hit, points[0]) > 0:
                points[0] = hit
                tips.append(old_tip)
        if rng.random() < ROUGH["real_leader_p"]:
            note = min(texts, key=lambda t: math.dist((t.dxf.insert.x, t.dxf.insert.y), points[-1]))
            attribs = {"layer": entity.dxf.layer, "annotation_handle": note.dxf.handle}
            msp.delete_entity(entity)
            msp.add_leader(points, dimstyle="EZDXF", dxfattribs=attribs)
        else:
            entity.set_points(points, format="xy")
    for old in tips:  # the end marker went with the tip
        for entity in list(msp):
            kind = entity.dxftype()
            if kind == "SOLID" and math.dist((entity.dxf.vtx0.x, entity.dxf.vtx0.y), old) < 1e-6:
                msp.delete_entity(entity)
            elif kind == "LINE" and math.dist(((entity.dxf.start.x + entity.dxf.end.x) / 2,
                                               (entity.dxf.start.y + entity.dxf.end.y) / 2), old) < 1e-6:
                msp.delete_entity(entity)
            elif kind == "HATCH" and entity.dxf.solid_fill and len(entity.paths) == 1:
                ring = np.array([v[:2] for v in entity.paths[0].vertices]) if hasattr(entity.paths[0], "vertices") else None
                if ring is not None and len(ring) == 12 and math.dist(ring.mean(axis=0), old) < 1e-6:
                    msp.delete_entity(entity)


def _outline_hit(tip, knee, rings):
    """Where the leader from the knee first meets the part's outline."""
    tip, knee = np.array(tip), np.array(knee)
    d = tip - knee
    best = None
    for ring in rings:
        p = np.array(ring)
        q = np.roll(p, -1, axis=0)
        e = q - p
        den = d[0] * e[:, 1] - d[1] * e[:, 0]
        safe = np.where(np.abs(den) > 1e-12, den, 1.0)
        wx, wy = p[:, 0] - knee[0], p[:, 1] - knee[1]
        t = (wx * e[:, 1] - wy * e[:, 0]) / safe
        u = (wx * d[1] - wy * d[0]) / safe
        hits = t[(np.abs(den) > 1e-12) & (t > 0) & (t <= 1) & (u >= 0) & (u <= 1)]
        if len(hits):
            best = min(best, float(hits.min())) if best is not None else float(hits.min())
    return None if best is None else tuple(knee + d * best)


def make_sheets(folder, count, seed, log=print) -> list[dict]:
    """`count` sheets spread evenly over every style, each variant saved beside its truth."""
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    folder = Path(folder)
    cfg = synthetic.Config()
    templates, styles = synthetic.load_templates(), synthetic.load_styles()
    rows = []
    for index in range(count):
        style = styles[index % len(styles)]
        drawing, captured = _capture(index, seed, templates, [style], cfg)
        truth = truth_of(drawing, captured, cfg)
        base = folder / "truth" / f"{drawing.drawing_id}.json"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(json.dumps(truth))
        master = folder / "master" / f"{drawing.drawing_id}.dxf"
        master.parent.mkdir(parents=True, exist_ok=True)
        captured.doc.saveas(master)
        rows.append({"drawing_id": drawing.drawing_id, "truth": str(base), "master": str(master)})
        if (index + 1) % 25 == 0:
            log(f"  {index + 1}/{count} sheets drafted")
    return rows


# ---------------------------------------------------------------- overlap

def _pixels(rings, ppu):
    return [np.asarray(r, dtype=float) * (ppu, -ppu) + (0.31830988, 0.27182818) for r in rings]


def _area_px(rings) -> float:
    return abs(sum(synthetic.ring_area(r) for r in rings))


def overlap_area(a_px, b_px) -> float:
    """Shared area in pixels, on a grid fine enough for thin parts."""
    box_a, box_b = synthetic.ring_box(a_px, pad=1), synthetic.ring_box(b_px, pad=1)
    x0, y0 = max(box_a[0], box_b[0]), max(box_a[1], box_b[1])
    x1, y1 = min(box_a[0] + box_a[2], box_b[0] + box_b[2]), min(box_a[1] + box_a[3], box_b[1] + box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    w, h = x1 - x0, y1 - y0
    fine = 4 if w * h * 16 <= 6e6 else 2 if w * h * 4 <= 6e6 else 1
    box = (0, 0, w * fine, h * fine)
    def grid(rings):
        return [(r - (x0, y0)) * fine for r in rings]
    mask = synthetic.rasterize(grid(a_px), box) & synthetic.rasterize(grid(b_px), box)
    return float(np.count_nonzero(mask)) / (fine * fine)


def _widened(rings, width_px, narrow_floor=1.0):
    """The dataset's min-width rule: a part under a pixel wide is grown to one."""
    return rings if width_px >= narrow_floor else [
        np.asarray(synthetic.offset_ring(r, (narrow_floor - width_px) / 2.0 + 0.01)) for r in rings]


@dataclass
class Scored:
    iou: np.ndarray  # parts x regions
    share_of_region: np.ndarray  # the share of each region that lies in each part
    share_of_part: np.ndarray


def overlaps(parts_px, regions_px) -> Scored:
    n, m = len(parts_px), len(regions_px)
    iou = np.zeros((n, m))
    of_region = np.zeros((n, m))
    of_part = np.zeros((n, m))
    part_area = [_area_px(p) for p in parts_px]
    region_area = [_area_px(r) for r in regions_px]
    for i, part in enumerate(parts_px):
        for j, region in enumerate(regions_px):
            shared = overlap_area(part, region)
            if shared <= 0:
                continue
            union = part_area[i] + region_area[j] - shared
            iou[i, j] = min(shared / max(union, 1e-9), 1.0)
            of_region[i, j] = min(shared / max(region_area[j], 1e-9), 1.0)
            of_part[i, j] = min(shared / max(part_area[i], 1e-9), 1.0)
    return Scored(iou, of_region, of_part)


def match(iou, threshold) -> dict:
    """One-to-one, best overlap first: {part index: region index}."""
    pairs = sorted(((iou[i, j], i, j) for i, j in zip(*np.nonzero(iou >= threshold))), reverse=True)
    taken_part, taken_region, out = set(), set(), {}
    for _, i, j in pairs:
        if i not in taken_part and j not in taken_region:
            taken_part.add(i)
            taken_region.add(j)
            out[i] = j
    return out


# ---------------------------------------------------------------- one sheet

def score_sheet(truth, record) -> dict:
    ppu = truth["px_per_unit"]
    parts = truth["parts"]
    parts_px = [_widened(_pixels(p["rings"], ppu), p["min_width_px"]) for p in parts]
    live = [r for r in record["regions"] if not r["dropped"]]
    aside = [r for r in record["regions"] if r["dropped"]]
    regions_px = [_pixels(r["rings"], ppu) for r in live]
    scored = overlaps(parts_px, regions_px)
    thin_region = [r["width"] * ppu < truth["thin_px"] for r in live]
    out = {"drawing_id": truth["drawing_id"], "style": truth["style"], "template": truth["template"],
           "parts": len(parts), "thin_parts": sum(p["thin"] for p in parts),
           "regions": len(live), "thin_regions": sum(thin_region), "set_aside": len(aside),
           "by_iou": {}}
    for threshold in IOUS:
        found = match(scored.iou, threshold)
        hit_regions = set(found.values())
        out["by_iou"][str(threshold)] = {
            "hit_parts": len(found), "hit_thin_parts": sum(parts[i]["thin"] for i in found),
            "hit_regions": len(hit_regions), "hit_thin_regions": sum(thin_region[j] for j in hit_regions)}
    found = match(scored.iou, 0.5)
    names_row = Counter()
    wrong = []
    unnamed_labels = []
    for i, part in enumerate(parts):
        if i not in found:
            names_row["no_region"] += 1
            continue
        proposal = live[found[i]]["proposal"]
        if proposal["class_id"] == names.UNKNOWN:
            names_row["neither"] += 1
            if proposal["verbatim"]:
                unnamed_labels.extend(proposal["verbatim"])
        elif proposal["class_id"] == part["class_id"]:
            names_row[f"correct_{proposal['source']}"] += 1
        else:
            names_row[f"wrong_{proposal['source']}"] += 1
            wrong.append({"part": part["class_id"], "proposed": proposal["class_id"], "source": proposal["source"],
                          "verbatim": proposal["verbatim"], "reason": proposal["reason"]})
    out["names"] = dict(names_row)
    out["wrong"] = wrong
    out["unnamed_labels"] = unnamed_labels
    matched_regions = set(found.values())
    out["named_false_regions"] = sum(1 for j, r in enumerate(live) if j not in matched_regions
                                     and r["proposal"]["class_id"] != names.UNKNOWN)
    out["cost"] = reviewer_cost(parts, parts_px, live, regions_px, aside, scored, found, ppu)
    return out


def reviewer_cost(parts, parts_px, live, regions_px, aside, scored, found, ppu) -> dict:
    """What a person has to do beyond pressing accept on each good region."""
    regions_used = set(found.values())
    restore, merge, split, trace = 0, 0, 0, 0
    aside_px = [_pixels(r["rings"], ppu) for r in aside]
    aside_scored = overlaps(parts_px, aside_px) if aside_px else None
    merged_regions = set()
    for i, _ in enumerate(parts):
        if i in found:
            continue
        if aside_scored is not None and len(aside) and aside_scored.iou[i].max() >= 0.5:
            restore += 1
            continue
        pieces = [j for j in np.flatnonzero(scored.share_of_region[i] >= 0.7).tolist() if j not in regions_used]
        if len(pieces) >= 2 and sum(scored.share_of_part[i, j] for j in pieces) >= 0.5:
            merge += 1
            merged_regions.update(pieces)
            continue
        covering = np.flatnonzero(scored.share_of_part[i] >= 0.7).tolist()
        if covering:
            split += 1
            continue
        trace += 1
    false_regions = sum(1 for j in range(len(live)) if j not in regions_used and j not in merged_regions)
    row = Counter()
    for i, _ in enumerate(parts):
        if i in found:
            proposal = live[found[i]]["proposal"]
            if proposal["class_id"] == names.UNKNOWN:
                row["name_unknown"] += 1
            elif proposal["class_id"] != parts[i]["class_id"]:
                row["fix_wrong_name"] += 1
    fixes = false_regions + row["name_unknown"] + row["fix_wrong_name"] + 2 * (restore + merge + split)
    return {"reject_false_region": false_regions, "name_unknown": row["name_unknown"],
            "fix_wrong_name": row["fix_wrong_name"], "restore_region": restore, "merge": merge, "split": split,
            "corrections": fixes, "cannot_fix_without_tracing": trace,
            "accepts": len(found) - row["name_unknown"] - row["fix_wrong_name"]}


# ---------------------------------------------------------------- report

def _share(top, bottom):
    return None if not bottom else round(top / bottom, 4)


def _sum(rows, *path):
    total = 0
    for row in rows:
        value = row
        for key in path:
            value = value.get(key, 0) if isinstance(value, dict) else 0
        total += value
    return total


def summarise(rows, seconds) -> dict:
    parts, thin = _sum(rows, "parts"), _sum(rows, "thin_parts")
    regions, thin_regions = _sum(rows, "regions"), _sum(rows, "thin_regions")
    compact, compact_regions = parts - thin, regions - thin_regions
    out = {"sheets": len(rows), "parts": parts, "thin_parts": thin, "compact_parts": compact, "regions": regions,
           "thin_regions": thin_regions, "compact_regions": compact_regions, "set_aside": _sum(rows, "set_aside"),
           "recall": {}, "precision": {}}
    for threshold in IOUS:
        key = str(threshold)
        hit, hit_thin = _sum(rows, "by_iou", key, "hit_parts"), _sum(rows, "by_iou", key, "hit_thin_parts")
        region, region_thin = _sum(rows, "by_iou", key, "hit_regions"), _sum(rows, "by_iou", key, "hit_thin_regions")
        out["recall"][key] = {"all": _share(hit, parts), "thin": _share(hit_thin, thin),
                              "compact": _share(hit - hit_thin, compact)}
        out["precision"][key] = {"all": _share(region, regions), "thin": _share(region_thin, thin_regions),
                                 "compact": _share(region - region_thin, compact_regions)}
    counts = Counter()
    for row in rows:
        counts.update(row["names"])
    correct = counts["correct_callout"] + counts["correct_hatch"]
    wrong = counts["wrong_callout"] + counts["wrong_hatch"]
    out["names"] = {
        "correct_from_callout": _share(counts["correct_callout"], parts),
        "correct_from_hatch": _share(counts["correct_hatch"], parts),
        "wrong": _share(wrong, parts), "neither": _share(counts["neither"], parts),
        "no_region": _share(counts["no_region"], parts),
        "wrong_name_rate": _share(wrong, correct + wrong),
        "wrong_from_callout": counts["wrong_callout"], "wrong_from_hatch": counts["wrong_hatch"],
        "named_false_regions": _sum(rows, "named_false_regions")}
    cost = Counter()
    for row in rows:
        cost.update(row["cost"])
    sheets_count = len(rows) or 1
    out["per_sheet"] = {key: round(value / sheets_count, 3) for key, value in sorted(cost.items())}
    out["sheets_with_no_correction"] = sum(1 for r in rows if r["cost"]["corrections"] == 0
                                           and r["cost"]["cannot_fix_without_tracing"] == 0)
    out["seconds_per_sheet"] = {"mean": round(statistics.fmean(seconds), 3),
                                "p95": round(float(np.percentile(seconds, 95)), 3), "max": round(max(seconds), 3)}
    return out


def _by(rows, seconds, key) -> dict:
    groups = {}
    for row, second in zip(rows, seconds):
        groups.setdefault(row[key], ([], []))
        groups[row[key]][0].append(row)
        groups[row[key]][1].append(second)
    return {name: summarise(*group) for name, group in sorted(groups.items())}


def run_variant(folder, rows, variant, seed, log=print) -> dict:
    folder = Path(folder)
    dxf_dir, store = folder / variant / "dxf", folder / variant / "sheets"
    dxf_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        truth = json.loads(Path(row["truth"]).read_text())
        doc = ezdxf.readfile(row["master"])
        apply_variant(doc, truth, variant, seed)
        scrub_layers(doc)
        doc.saveas(dxf_dir / f"{row['drawing_id']}.dxf")
    scored, seconds, failed = [], [], []
    for row in rows:
        path = dxf_dir / f"{row['drawing_id']}.dxf"
        started = time.perf_counter()
        try:
            records = pipeline.propose_file(path, store, root=dxf_dir)  # blind: the path and nothing else
        except Exception as error:
            failed.append({"drawing_id": row["drawing_id"], "error": f"{type(error).__name__}: {error}"})
            continue
        seconds.append(time.perf_counter() - started)
        truth = json.loads(Path(row["truth"]).read_text())
        scored.append({**score_sheet(truth, {"regions": [r for rec in records for r in rec["regions"]]}),
                       "details": len(records)})
        if len(scored) % 25 == 0:
            log(f"  {variant}: {len(scored)}/{len(rows)} sheets scored")
    report = summarise(scored, seconds) if scored else {}
    report["details_per_sheet"] = dict(sorted(Counter(r["details"] for r in scored).items()))
    report["failed"] = failed
    report["by_style"] = _by(scored, seconds, "style") if scored else {}
    report["wrong_examples"] = Counter(
        (w["part"], w["proposed"], w["source"], (w["verbatim"] or [""])[0]) for r in scored for w in r["wrong"]).most_common(12)
    report["labels_with_no_class"] = Counter(t for r in scored for t in r["unnamed_labels"]).most_common(12)
    (folder / variant / "scores.json").write_text(json.dumps(scored))
    return report


def truth_of_regions(store, truths) -> dict:
    """{(sheet id, region id): class} for regions that match a part. The rest are not parts."""
    out = {}
    for sheet_id in sheets.sheet_ids(store):
        record = sheets.read_sheet(store, sheet_id)
        truth = truths[record["source"]["file"][:-4]]
        ppu = truth["px_per_unit"]
        live = [r for r in record["regions"] if not r["dropped"]]
        parts_px = [_widened(_pixels(p["rings"], ppu), p["min_width_px"]) for p in truth["parts"]]
        found = match(overlaps(parts_px, [_pixels(r["rings"], ppu) for r in live]).iou, 0.5)
        for part, region in found.items():
            out[(sheet_id, live[region]["id"])] = truth["parts"][part]["class_id"]
    return out


def _copy_of(record, rng) -> dict:
    """A near-copy, as a drafter makes one: moved along the sheet, one part stretched a
    little and one deleted."""
    twin = copy.deepcopy(record)
    twin["sheet_id"] = f"{record['sheet_id']}-copy"
    shift = np.array([rng.uniform(2000, 5000), rng.uniform(-2000, 2000)])
    for region in twin["regions"]:
        region["rings"] = [(np.array(r) + shift).tolist() for r in region["rings"]]
        region["proposal"].update(class_id=names.UNKNOWN, source=None, candidates=[], state=names.PROPOSED)
        region["rejected"] = False
    live = [r for r in twin["regions"] if not r["dropped"]]
    if len(live) > 3:
        victim, stretched = rng.sample(live, 2)
        twin["regions"].remove(victim)
        pivot = np.concatenate([np.array(r) for r in stretched["rings"]]).mean(axis=0)
        stretched["rings"] = [((np.array(r) - pivot) * (1.04, 1.0) + pivot).tolist() for r in stretched["rings"]]
    return twin


def propagation(store, truths, seed) -> dict:
    """Carried names scored against truth, the one thing a real file cannot give: reviewing
    these sheets in order, and carrying each onto a near-copy of itself."""
    labels = truth_of_regions(store, truths)
    records = [sheets.read_sheet(store, s) for s in sheets.sheet_ids(store)]

    def answer(record, region):
        return labels.get((record["sheet_id"].removesuffix("-copy"), region["id"]))

    across = propagate.simulate(records, answer)
    rng = random.Random(f"copies:{seed}")
    right = wrong = missed = 0
    for record in records:
        for region in record["regions"]:
            truth = answer(record, region)
            region["rejected"] = truth is None and not region["dropped"]
            if truth:
                region["proposal"].update(class_id=truth, state=names.CONFIRMED)
        twin = _copy_of(record, rng)
        propagate.between(record, twin, by_hatch=False)
        for region in twin["regions"]:
            truth = answer(twin, region)
            if truth is None:
                continue
            carried = region["proposal"]["class_id"]
            if carried == names.UNKNOWN:
                missed += 1
            elif carried == truth:
                right += 1
            else:
                wrong += 1
    return {"across_sheets": across, "near_copies": {"right": right, "wrong": wrong, "not_carried": missed,
                                                     "wrong_rate": _share(wrong, right + wrong),
                                                     "carried_share": _share(right + wrong, right + wrong + missed)}}


def table(reports) -> str:
    lines = []
    head = f"{'':34}" + "".join(f"{v:>10}" for v in reports)

    def row(label, get):
        cells = []
        for report in reports.values():
            value = get(report)
            cells.append(f"{'n/a':>10}" if value is None else f"{value:>10.3f}" if isinstance(value, float)
                         else f"{value:>10}")
        lines.append(f"{label:34}" + "".join(cells))

    lines.append(head)
    row("sheets", lambda r: r.get("sheets"))
    row("sheets split into >1 detail", lambda r: sum(v for k, v in r["details_per_sheet"].items() if k > 1))
    row("parts (thin / compact)", lambda r: f"{r['thin_parts']}/{r['compact_parts']}")
    row("regions kept / set aside", lambda r: f"{r['regions']}/{r['set_aside']}")
    for t in map(str, IOUS):
        for group in ("all", "thin", "compact"):
            row(f"recall    IoU {t} {group}", lambda r, g=group, k=t: r["recall"][k][g])
        for group in ("all", "thin", "compact"):
            row(f"precision IoU {t} {group}", lambda r, g=group, k=t: r["precision"][k][g])
    row("parts named right by callout", lambda r: r["names"]["correct_from_callout"])
    row("parts named right by hatch", lambda r: r["names"]["correct_from_hatch"])
    row("parts named wrong", lambda r: r["names"]["wrong"])
    row("parts with a region, no name", lambda r: r["names"]["neither"])
    row("parts with no region", lambda r: r["names"]["no_region"])
    row("wrong-name rate", lambda r: r["names"]["wrong_name_rate"])
    row("false regions given a name", lambda r: r["names"]["named_false_regions"])
    row("seconds per sheet (mean)", lambda r: r["seconds_per_sheet"]["mean"])
    row("seconds per sheet (p95)", lambda r: r["seconds_per_sheet"]["p95"])
    row("corrections per sheet", lambda r: r["per_sheet"]["corrections"])
    row("  reject a false region", lambda r: r["per_sheet"]["reject_false_region"])
    row("  name an unknown", lambda r: r["per_sheet"]["name_unknown"])
    row("  fix a wrong name", lambda r: r["per_sheet"]["fix_wrong_name"])
    row("  restore a set-aside region", lambda r: r["per_sheet"]["restore_region"])
    row("  merge regions", lambda r: r["per_sheet"]["merge"])
    row("  split a region", lambda r: r["per_sheet"]["split"])
    row("accepts per sheet", lambda r: r["per_sheet"]["accepts"])
    row("parts needing tracing per sheet", lambda r: r["per_sheet"]["cannot_fix_without_tracing"])
    row("sheets with nothing to fix", lambda r: r["sheets_with_no_correction"])
    return "\n".join(lines)


def run(out, count, seed, variants=VARIANTS, keep=False, log=print) -> dict:
    out = sheets.require_output(out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="measure-", dir=out))
    try:
        log(f"drafting {count} synthetic sheets, seed {seed}")
        rows = make_sheets(work, count, seed, log)
        reports = {}
        for variant in variants:
            log(f"variant {variant}")
            reports[variant] = run_variant(work, rows, variant, seed, log)
        report = {"seed": seed, "sheets": count, "statement": HARDER_NOTE, "rough": ROUGH, "variants": reports}
        if "clean" in reports:
            truths = {Path(r["truth"]).stem: json.loads(Path(r["truth"]).read_text()) for r in rows}
            report["propagation"] = propagation(work / "clean" / "sheets", truths, seed)
        (out / f"report-{count}-seed{seed}.json").write_text(json.dumps(report, indent=2))
        return report
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
