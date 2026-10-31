"""Reviewed sheets become a dataset in the layout of tools/make_seg_dataset.py.

    train/, valid/, test/   1008 x 1008 PNG tiles and _annotations.coco.json
    manifest.json           "source": "owner", so training/roboflow_upload.py refuses it
    drawings.jsonl          one record per sheet
    run.json                timings

Tiling, clipping, keyholes, the thin rule and the COCO writer are imported from the
synthetic dataset code, so the two datasets cannot drift apart.

Only what a person decided goes in. A sheet with undecided regions is left out whole: a
part left unlabelled would teach a model that it is background.

Splits are by project, never by sheet or tile, or a model is tested on drawings it has
effectively seen. Every sheet needs a project, from a CSV or a folder, or nothing is
exported. Which split a project is in is frozen in `<store>/splits.json` the first time it
is seen and never moves, so the test set stays the test set as drawings are added.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import random
import shutil
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from app import ontology, synthetic
from tools import make_seg_dataset as seg

from . import names, planar, sheets

GENERATOR = "setout-annotation"
VERSION = "1"
FROZEN = "splits.json"
SHARES = dict(zip(seg.SPLITS, seg.SHARES))
OUTPUTS = seg.OUTPUTS


class Refused(sheets.Refused):
    """The export cannot be honest: nothing is written."""


# ---------------------------------------------------------------- projects

def projects_from_csv(path) -> dict:
    """{key: project} from a CSV with columns `sheet` and `project`. `sheet` is a file name
    (with or without .dxf), a path below the drawings root, or a sheet id."""
    out = {}
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"sheet", "project"} - set(reader.fieldnames or [])
        if missing:
            raise Refused(f"{path}: needs the columns sheet and project (missing {sorted(missing)})")
        for row in reader:
            sheet, project = (row["sheet"] or "").strip(), (row["project"] or "").strip()
            if sheet and project:
                out[sheet] = project
    return out


def _project_of(record, table, folders) -> str | None:
    source = record["source"]
    relative = source.get("relative_path")
    if table is not None:
        name = source.get("file") or ""
        for key in (relative, name, Path(name).stem, record["sheet_id"]):
            if key and key in table:
                return table[key]
        return None
    if folders and relative:
        parts = Path(relative).parts
        return parts[0] if len(parts) > 1 else None  # a file loose in the root names no project
    return None


def assign_splits(projects, frozen, seed) -> dict:
    """{project: split}: what is frozen stays, and each new project goes to the split
    furthest under its share, in an order fixed by the seed."""
    out = {p: s for p, s in frozen.items()}
    fresh = sorted(set(projects) - set(out), key=lambda p: hashlib.sha256(f"{seed}:{p}".encode()).hexdigest())
    counts = Counter(out.values())
    for project in fresh:
        missing = [s for s in ("test", "valid", "train") if counts[s] == 0]
        if missing:
            split = missing[0]  # every split needs a project before shares mean anything
        else:
            total = sum(counts.values()) + 1
            split = max(seg.SPLITS, key=lambda s: SHARES[s] - counts[s] / total)
        out[project] = split
        counts[split] += 1
    return out


# ---------------------------------------------------------------- one sheet

def to_pixels(rings, render) -> list[np.ndarray]:
    """Drawing units to sheet pixels: the transform the render used."""
    x0, _, _, y1 = render["extents"]
    k, margin = render["px_per_unit"], render["margin_px"]
    return [np.stack([margin + (r[:, 0] - x0) * k, margin + (y1 - r[:, 1]) * k], axis=1)
            for r in (np.asarray(ring, dtype=float) for ring in rings)]


def is_confirmed(region) -> bool:
    return (not region["dropped"] and not region["rejected"]
            and region["proposal"]["state"] in (names.CONFIRMED, names.CORRECTED)
            and region["proposal"]["class_id"] != names.UNKNOWN)


def _is_outer(ring, rings) -> bool:
    """A ring inside an odd number of others is a hole."""
    return sum(1 for other in rings if other is not ring and planar.contains(other, ring[0])) % 2 == 0


def _as_instance(region, rings, cfg) -> synthetic.Instance:
    """The same measurements the synthetic dataset makes: the thin rule, and the min-width
    rule that grows a sub-pixel part to one pixel."""
    width, narrow = synthetic.region_width(rings)
    widened = bool(narrow < cfg.min_mask_px)
    if widened:
        delta = (cfg.min_mask_px - narrow) / 2.0 + 0.01
        rings = [np.asarray(synthetic.offset_ring(r, delta if _is_outer(r, rings) else -delta)) for r in rings]
    stored = round(float(max(width, cfg.min_mask_px) if widened else width), 3)
    joined = [np.asarray(r) for r in synthetic.keyhole([list(map(tuple, r)) for r in rings])]
    linetypes = region.get("linetypes") or ["continuous"]
    return synthetic.Instance(part_id=region["id"], class_id=region["proposal"]["class_id"], rings=joined,
                              min_width_px=stored, thin=bool(stored < cfg.thin_px), widened=widened,
                              mode=region["kind"], linetype=linetypes[0].lower())


def sheet_drawing(record, image, cfg, rng) -> synthetic.Drawing:
    """A reviewed sheet in the form the tiler takes. Padded with paper to at least one
    tile and never rotated or damaged: this is the sheet as drawn."""
    render = record["render"]
    confirmed = [r for r in record["regions"] if is_confirmed(r)]
    rings = {r["id"]: to_pixels(r["rings"], render) for r in confirmed}
    still = dataclasses.replace(cfg, rotate_p=0.0)
    sheet, moved, placement = synthetic.place(image.convert("L"), rings, still, rng)
    instances = [_as_instance(r, moved[r["id"]], cfg) for r in confirmed]
    return synthetic.Drawing(record["sheet_id"], sheet, sheet, instances, {"placement": placement})


# ---------------------------------------------------------------- the whole export

def _load(store) -> list[dict]:
    return [sheets.read_sheet(store, sheet_id) for sheet_id in sheets.sheet_ids(store)]


def _sort(records) -> tuple[list, dict]:
    """(sheets ready to export, {reason: sheet ids} for the rest)."""
    ready, skipped = [], {}
    for record in records:
        if record.get("excluded_from_export"):
            skipped.setdefault("left out by a reviewer", []).append(record["sheet_id"])
        elif not sheets.summary(record)["done"]:
            skipped.setdefault("not fully reviewed", []).append(record["sheet_id"])
        elif not any(is_confirmed(r) for r in record["regions"]):
            skipped.setdefault("no confirmed regions", []).append(record["sheet_id"])
        else:
            ready.append(record)
    return ready, skipped


def _manifest(name, cfg, seed, cats, datasets, rows, projects, skipped) -> dict:
    counts = {s: seg.class_counts(datasets[s]) for s in seg.SPLITS}
    per_class = {}
    for cat in cats:
        n = sum(counts[s][cat["name"]]["instances"] for s in seg.SPLITS)
        thin = sum(counts[s][cat["name"]]["thin"] for s in seg.SPLITS)
        per_class[cat["name"]] = {"instances": n, "thin": thin, "thin_share": round(thin / n, 4) if n else None}
    seconds = sum(r["review"]["seconds"] for r in rows)
    decided = sum(r["review"]["parts"] for r in rows)
    return {
        "name": name,
        "source": "owner",  # anything but "synthetic" keeps it away from training/roboflow_upload.py
        "synthetic": False,
        "statement": ("Drawn by the owner. Private: it stays on this machine and on the owner's private Modal "
                      "Volume, and never goes to Roboflow or any public place."),
        "generator": {"name": GENERATOR, "version": VERSION},
        "ontology_version": ontology.load().version,
        "seed": seed,
        "categories": cats,
        "thin_rule": {"threshold_px": cfg.thin_px, "rule": "thin when min_width_px < threshold_px",
                      "min_width_px": "as in tools/make_seg_dataset.py: the part's length-weighted median "
                                      "cross-section on the sheet, measured before tiling"},
        "pixel_convention": "corner: pixel (i, j) covers [i, i+1) x [j, j+1)",
        "holes": "a hole is joined to its outer ring by a zero-width cut (one COCO polygon)",
        "tiles": {"size": cfg.tile, "stride": cfg.stride, "overlap": cfg.tile - cfg.stride,
                  "min_remnant_px2": cfg.min_remnant_px2, "keep_empty_p": cfg.keep_empty_p},
        "config": asdict(cfg),
        "split_rule": "by project; frozen in the annotation store's splits.json",
        "projects": {s: sorted({r["project"] for r in rows if r["split"] == s}) for s in seg.SPLITS},
        "splits": {s: {"drawings": sum(1 for r in rows if r["split"] == s),
                       "images": len(datasets[s]["images"]), "instances": len(datasets[s]["annotations"]),
                       "thin_instances": sum(int(a["attributes"]["thin"]) for a in datasets[s]["annotations"]),
                       "per_class": counts[s]} for s in seg.SPLITS},
        "per_class": per_class,
        "skipped_sheets": skipped,
        "review": {"parts": decided, "seconds": round(seconds, 1),
                   "seconds_per_part": round(seconds / decided, 2) if decided else None},
    }


def export(store, out, *, csv_path=None, folders=False, seed=1, overwrite=False, cfg=None, log=print) -> dict:
    """Write the dataset to `out`. Raises Refused, with nothing written, when it cannot be
    honest: no projects, too few of them, or one drawing in two projects."""
    out = sheets.require_output(out)
    store = Path(store)
    cfg = cfg or synthetic.Config()
    if csv_path is None and not folders:
        raise Refused("every sheet needs a project: pass --projects <csv> or --project-folders. "
                      "Splitting by sheet would test a model on drawings it has effectively seen.")
    table = projects_from_csv(csv_path) if csv_path is not None else None
    ready, skipped = _sort(_load(store))
    if not ready:
        raise Refused(f"nothing to export from {store}: no fully reviewed sheet ({ {k: len(v) for k, v in skipped.items()} })")
    project = {r["sheet_id"]: _project_of(r, table, folders) for r in ready}
    missing = sorted(k for k, v in project.items() if not v)
    if missing:
        raise Refused(f"no project for {len(missing)} sheet(s), so no safe split: {missing[:8]}")
    seen = {}
    for record in ready:
        seen.setdefault(record["source"]["sha256"], set()).add(project[record["sheet_id"]])
    shared = {k[:12]: sorted(v) for k, v in seen.items() if len(v) > 1}
    if shared:
        raise Refused(f"the same drawing is in more than one project, which would leak it across splits: {shared}")
    if len(set(project.values())) < len(seg.SPLITS):
        raise Refused(f"{len(set(project.values()))} project(s): train, valid and test each need their own")
    frozen_path = store / FROZEN
    frozen = json.loads(frozen_path.read_text()) if frozen_path.is_file() else {}
    split_of = assign_splits(project.values(), frozen, seed)
    existing = [p for p in OUTPUTS if (out / p).exists()]
    if existing and not overwrite:
        raise Refused(f"{out} already holds {existing}; pass --overwrite to replace them")
    for name in existing:  # only what this tool writes
        target = out / name
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    for split in seg.SPLITS:
        (out / split).mkdir(parents=True, exist_ok=True)
    frozen_path.write_text(json.dumps(split_of, indent=2, sort_keys=True))
    started = time.perf_counter()
    names_used = sorted({r["proposal"]["class_id"] for record in ready for r in record["regions"] if is_confirmed(r)})
    cats = [{"id": i, "name": n, "supercategory": "component"} for i, n in enumerate(names_used, 1)]
    results, rows = [], []
    for index, record in enumerate(ready):
        sheet_id = record["sheet_id"]
        image = Image.open(sheets.sheet_dir(store, sheet_id) / record["render"]["file"])
        drawing = sheet_drawing(record, image, cfg, random.Random(f"place:{seed}:{sheet_id}"))
        tiles = synthetic.tiles(drawing, cfg, random.Random(f"tiles:{seed}:{sheet_id}"))
        split = split_of[project[sheet_id]]
        images = []
        for tile in tiles:
            name = f"{sheet_id}_x{tile.x0:05d}_y{tile.y0:05d}.png"
            tile.image.save(out / split / name, format="PNG")
            images.append({"file_name": name, "tile_xy": [tile.x0, tile.y0], "annotations": tile.annotations})
        decided = [r for r in record["regions"] if sheets.is_decided(r) and not r["dropped"]]
        row = {"drawing_id": sheet_id, "project": project[sheet_id], "split": split, "file": record["source"]["file"],
               "detail": record["source"].get("detail"),
               "sha256": record["source"]["sha256"], "template": {"id": project[sheet_id]}, "style": {"id": "owner"},
               "tiles": len(images), "render": record["render"], "placement": drawing.record["placement"],
               "parts": [{"id": i.part_id, "class_id": i.class_id, "min_width_px": i.min_width_px, "thin": i.thin,
                          "widened": i.widened, "mode": i.mode} for i in drawing.instances],
               "review": {"parts": len(decided), "seconds": round(sum(r.get("seconds", 0.0) for r in record["regions"]), 2)}}
        rows.append(row)
        results.append({"index": index, "split": split, "images": images, "record": row})
    datasets = {}
    for split in seg.SPLITS:
        dataset = seg.coco(split, [r for r in results if r["split"] == split], cats, out.name, cfg.tile)
        dataset["info"] = {"description": f"{out.name} {split}: owner drawings, private", "version": VERSION,
                           "contributor": GENERATOR}
        datasets[split] = dataset
        (out / split / seg.COCO_FILE).write_text(json.dumps(dataset, separators=(",", ":")))
    with open(out / "drawings.jsonl", "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    info = _manifest(out.name, cfg, seed, cats, datasets, rows, {}, skipped)
    (out / "manifest.json").write_text(json.dumps(info, indent=2))
    (out / "run.json").write_text(json.dumps({"wall_s": round(time.perf_counter() - started, 1), "sheets": len(rows)}, indent=2))
    for split in seg.SPLITS:
        row = info["splits"][split]
        log(f"  {split}: {len(info['projects'][split])} project(s), {row['drawings']} sheets, {row['images']} tiles, "
            f"{row['instances']} instances ({row['thin_instances']} thin)")
    for reason, ids in skipped.items():
        log(f"  left out, {reason}: {len(ids)} sheet(s)")
    return info
