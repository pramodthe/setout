#!/usr/bin/env python
"""Synthetic drawing-segmentation dataset: section drawings with exact masks.

    python tools/make_seg_dataset.py --out data/datasets/seg-synth-v1 --drawings 1000 --seed 1 [--workers 8]

Writes the Roboflow / RF-DETR COCO layout: train/, valid/ and test/, each with
`_annotations.coco.json` and 1008 x 1008 PNG tiles, plus `manifest.json`,
`drawings.jsonl` (one record per drawing) and `run.json` (timings). Splits
are by drawing, never by tile. The same seed writes byte-identical files
whatever the worker count; only run.json changes from run to run. Everything is synthetic: our own templates and generic styles
(app/synthetic.py); nothing is derived from anyone's drawings.

The output must be under data/ (gitignored) or outside the repository: a
dataset never lands in tracked files.
"""
import argparse
import hashlib
import json
import logging
import multiprocessing as mp
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import ontology, synthetic  # noqa: E402

SPLITS = ("train", "valid", "test")
SHARES = (0.8, 0.1, 0.1)
COCO_FILE = "_annotations.coco.json"
OUTPUTS = SPLITS + ("manifest.json", "drawings.jsonl", "run.json")


def allowed_output(out) -> bool:
    """A directory under data/, or outside the repo tree: never a tracked path."""
    out = Path(out).resolve()
    data = (ROOT / "data").resolve()
    return ROOT not in out.parents and out != ROOT or data in out.parents


def split_of(n: int, seed: int) -> dict[int, str]:
    """Drawing index -> split, about 80/10/10, fixed by the seed alone."""
    order = list(range(n))
    random.Random(f"split:{seed}").shuffle(order)
    cut1, cut2 = round(n * SHARES[0]), round(n * (SHARES[0] + SHARES[1]))
    return {i: SPLITS[0] if k < cut1 else SPLITS[1] if k < cut2 else SPLITS[2] for k, i in enumerate(order)}


# ---------------------------------------------------------------- workers

_STATE = {}


def _init(out: str, seed: int, n: int, cfg: dict) -> None:
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    _STATE.update(out=Path(out), seed=seed, cfg=synthetic.Config(**cfg),
                  templates=synthetic.load_templates(), styles=synthetic.load_styles(),
                  splits=split_of(n, seed))


def _one(index: int) -> dict:
    """Make one drawing, write its tiles, return their annotations and its record."""
    st = _STATE
    started = time.perf_counter()
    drawing = synthetic.make_drawing(index, st["seed"], st["templates"], st["styles"], st["cfg"])
    split = st["splits"][index]
    tiles = synthetic.tiles(drawing, st["cfg"], random.Random(f"tiles:{st['seed']}:{index}"))
    images = []
    for tile in tiles:
        name = f"{drawing.drawing_id}_x{tile.x0:05d}_y{tile.y0:05d}.png"
        tile.image.save(st["out"] / split / name, format="PNG")
        images.append({"file_name": name, "tile_xy": [tile.x0, tile.y0], "annotations": tile.annotations})
    record = drawing.record | {"split": split, "tiles": len(images)}
    seconds = record.pop("seconds") | {"with_tiles": round(time.perf_counter() - started, 4)}
    return {"index": index, "split": split, "images": images, "record": record, "seconds": seconds}


# ---------------------------------------------------------------- assembly

def categories(templates) -> list[dict]:
    names = sorted({c for t in templates for c in t.class_ids()})
    return [{"id": i, "name": name, "supercategory": "component"} for i, name in enumerate(names, 1)]


def coco(split: str, results: list[dict], cats: list[dict], name: str, size: int) -> dict:
    """COCO for one split, ids assigned in drawing order (deterministic)."""
    ids = {c["name"]: c["id"] for c in cats}
    images, annotations = [], []
    for res in sorted(results, key=lambda r: r["index"]):
        rec = res["record"]
        for img in res["images"]:
            image_id = len(images) + 1
            images.append({"id": image_id, "file_name": img["file_name"], "width": size, "height": size, "drawing_id": rec["drawing_id"],
                           "tile_xy": img["tile_xy"], "template": rec["template"]["id"],
                           "style": rec["style"]["id"]})
            for ann in img["annotations"]:
                annotations.append({"id": len(annotations) + 1, "image_id": image_id,
                                    "category_id": ids[ann["class_id"]], **{k: v for k, v in ann.items()
                                                                           if k != "class_id"}})
    return {"info": {"description": f"{name} {split}: synthetic section drawings",
                     "version": synthetic.VERSION, "contributor": "setout synthetic data engine"},
            "licenses": [], "images": images, "annotations": annotations, "categories": cats}


def _pct(values, qs=(0, 5, 25, 50, 75, 95, 100)) -> dict:
    if not len(values):
        return {}
    return {f"p{q}": round(float(np.percentile(values, q)), 3) for q in qs}


def class_counts(dataset: dict) -> dict:
    names = {c["id"]: c["name"] for c in dataset["categories"]}
    out = {name: {"instances": 0, "thin": 0} for name in names.values()}
    for ann in dataset["annotations"]:
        row = out[names[ann["category_id"]]]
        row["instances"] += 1
        row["thin"] += int(ann["attributes"]["thin"])
    for row in out.values():
        row["thin_share"] = round(row["thin"] / row["instances"], 4) if row["instances"] else None
    return out


def alignment_summary(records: list[dict]) -> dict:
    """The share of mask-boundary pixels with ink within 1 and 2 px, by how
    the part is drawn. Continuous outlines test the transform; dashed and
    dotted lines lose some boundary to their gaps by design."""
    by_mode, by_class, rows = defaultdict(list), defaultdict(list), []
    for rec in records:
        classes = {p["id"]: p["class_id"] for p in rec["parts"]}
        for pid, a in rec["alignment"].items():
            mode = a["mode"] if a["linetype"] == "continuous" else f"{a['mode']}/{a['linetype']}"
            by_mode[mode].append((a["r1"], a["r2"]))
            by_class[classes[pid]].append((a["r1"], a["r2"]))
            rows.append((a["r1"], a["r2"], mode, rec["drawing_id"], pid))

    def summary(pairs):
        r1 = np.array([p[0] for p in pairs])
        r2 = np.array([p[1] for p in pairs])
        return {"parts": len(pairs), "r1_mean": round(float(r1.mean()), 4), "r1_p5": round(float(np.percentile(r1, 5)), 4),
                "r1_min": round(float(r1.min()), 4), "r2_mean": round(float(r2.mean()), 4),
                "r2_p5": round(float(np.percentile(r2, 5)), 4), "r2_min": round(float(r2.min()), 4)}

    continuous = [(r1, r2) for r1, r2, mode, _, _ in rows if "/" not in mode]
    rows.sort()
    return {"definition": "share of mask-boundary pixels with ink (< 160 on the sheet before photometric damage) "
                          "within 1 px (r1) and 2 px (r2), Chebyshev",
            "all_parts": summary([(r[0], r[1]) for r in rows]),
            "continuous_lines": summary(continuous),
            "by_mode": {k: summary(v) for k, v in sorted(by_mode.items())},
            "by_class": {k: summary(v) for k, v in sorted(by_class.items())},
            "lowest": [{"r1": r[0], "r2": r[1], "mode": r[2], "drawing": r[3], "part": r[4]} for r in rows[:10]]}


def manifest(name, args, cfg, templates, styles, cats, datasets, records) -> dict:
    by_class = defaultdict(lambda: {"parts": 0, "thin_parts": 0, "widths": []})
    for rec in records:
        for p in rec["parts"]:
            row = by_class[p["class_id"]]
            row["parts"] += 1
            row["thin_parts"] += int(p["thin"])
            row["widths"].append(p["min_width_px"])
    instances = {s: class_counts(datasets[s]) for s in SPLITS}
    overall = {}
    for cat in cats:
        n = sum(instances[s][cat["name"]]["instances"] for s in SPLITS)
        thin = sum(instances[s][cat["name"]]["thin"] for s in SPLITS)
        row = by_class.get(cat["name"], {"parts": 0, "thin_parts": 0, "widths": []})
        overall[cat["name"]] = {"instances": n, "thin": thin, "thin_share": round(thin / n, 4) if n else None,
                                "drawing_parts": row["parts"], "drawing_parts_thin": row["thin_parts"],
                                "min_width_px": _pct(row["widths"], (5, 50, 95))}
    used_t = Counter(r["template"]["id"] for r in records)
    used_s = Counter(r["style"]["id"] for r in records)
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
    return {
        "name": name,
        "source": "synthetic",  # training/roboflow_upload.py packages only datasets that say so
        "synthetic": True,
        "statement": ("Synthetic section drawings generated from our own templates and generic drafting styles. "
                      "They are not drawings of real buildings and nothing here is derived from anyone's drawings. "
                      "Every dimension is a draw from a synthetic sampling range, not a standard; every sheet prints "
                      "SYNTHETIC SAMPLE."),
        "generator": {"name": synthetic.GENERATOR, "version": synthetic.VERSION,
                      "code_sha256": {p: sha(ROOT / p) for p in ("app/synthetic.py", "app/section_cut.py",
                                                                 "app/dxf_ingest.py", "app/program.py",
                                                                 "app/geometry.py", "tools/make_seg_dataset.py")}},
        "command": {"drawings": args.drawings, "seed": args.seed},
        "ontology_version": ontology.load().version,
        "templates": [{"id": t.id, "version": t.version, "sha256": t.sha256, "assembly_class": t.assembly_class,
                       "weight": t.weight, "drawings": used_t[t.id]} for t in templates],
        "styles": [{"id": s.id, "version": s.version, "sha256": s.sha256, "weight": s.weight,
                    "drawings": used_s[s.id]} for s in styles],
        "categories": cats,
        "thin_rule": {
            "threshold_px": cfg.thin_px,
            "rule": "thin when min_width_px < threshold_px",
            "min_width_px": ("the part's width in the rendered drawing: the length-weighted median of its "
                             "cross-sections, each measured from a point on its outline along the inward normal "
                             "to the opposite side (a strip's thickness, a rectangle's short side, a disc's "
                             "diameter; a fold does not inflate it). Measured on the whole part before tiling, "
                             "so a clipped piece keeps its part's value."),
            "min_mask_px": cfg.min_mask_px,
            "min_width_rule": ("a part whose narrow sections (10th percentile) are under min_mask_px is grown "
                               "outward until they reach it, so its mask is at least one pixel wide; "
                               "`widened` in drawings.jsonl marks those parts"),
        },
        "pixel_convention": "corner: pixel (i, j) covers [i, i+1) x [j, j+1); a mask holds pixels whose centres "
                            "lie inside the polygon (pycocotools convention)",
        "holes": {"parts_with_holes": sum(1 for r in records for p in r["parts"] if p["holes"]),
                  "handling": "a hole is joined to its outer ring by a zero-width cut (one COCO polygon)"},
        "tiles": {"size": cfg.tile, "stride": cfg.stride, "overlap": cfg.tile - cfg.stride,
                  "min_remnant_px2": cfg.min_remnant_px2, "keep_empty_p": cfg.keep_empty_p,
                  "padding": "sheets smaller than a tile are padded with paper before damage"},
        "config": asdict(cfg),
        "splits": {s: {"drawings": sum(1 for r in records if r["split"] == s),
                       "images": len(datasets[s]["images"]),
                       "images_without_instances": len({i["id"] for i in datasets[s]["images"]}
                                                       - {a["image_id"] for a in datasets[s]["annotations"]}),
                       "instances": len(datasets[s]["annotations"]),
                       "thin_instances": sum(int(a["attributes"]["thin"]) for a in datasets[s]["annotations"]),
                       "per_class": instances[s]} for s in SPLITS},
        "per_class": overall,
        "thinnest_part_px": _pct([r["drafting"]["thinnest_px"] for r in records]),
        "thin_instance_width_px": _pct([a["attributes"]["min_width_px"] for s in SPLITS
                                        for a in datasets[s]["annotations"] if a["attributes"]["thin"]]),
        "sheet_px": {"width": _pct([r["sheet"]["size"][0] for r in records]),
                     "height": _pct([r["sheet"]["size"][1] for r in records])},
        "alignment": alignment_summary(records),
        "rejections": {"constraint_redraws": sum(r["attempts"]["sample"] - 1 for r in records),
                       "overlap_redraws": sum(len(r["attempts"]["overlap_rejects"]) for r in records),
                       "widened_parts": sum(1 for r in records for p in r["parts"] if p["widened"])},
    }


def run_info(results: list[dict], wall: float, workers: int) -> dict:
    """Timings: the one output that changes from run to run."""
    per = {k: [r["seconds"][k] for r in results] for k in ("sample", "draft", "render", "total", "with_tiles")}
    return {"wall_s": round(wall, 1), "workers": workers,
            "mean_per_drawing_s": {k: round(float(np.mean(v)), 4) for k, v in per.items()},
            "per_drawing_with_tiles_s": _pct(per["with_tiles"], (50, 95, 100)),
            "drawings": {r["record"]["drawing_id"]: r["seconds"] for r in sorted(results, key=lambda r: r["index"])}}


# ---------------------------------------------------------------- main

def build(out: Path, drawings: int, seed: int, workers: int = 1, cfg: synthetic.Config | None = None,
          args=None, log=print) -> dict:
    """Generate the dataset into `out` (which must not hold a dataset yet)."""
    cfg = cfg or synthetic.Config()
    for split in SPLITS:
        (out / split).mkdir(parents=True, exist_ok=True)
    templates, styles = synthetic.load_templates(), synthetic.load_styles()
    started = time.perf_counter()
    results = []
    init = (str(out), seed, drawings, asdict(cfg))
    if workers > 1:
        with mp.get_context("spawn").Pool(workers, initializer=_init, initargs=init) as pool:
            for k, res in enumerate(pool.imap_unordered(_one, range(drawings), chunksize=2), 1):
                results.append(res)
                if k % 50 == 0 or k == drawings:
                    log(f"  {k}/{drawings} drawings, {time.perf_counter() - started:.0f}s")
    else:
        _init(*init)
        for i in range(drawings):
            results.append(_one(i))
    seconds = time.perf_counter() - started
    cats = categories(templates)
    name = out.name
    datasets = {s: coco(s, [r for r in results if r["split"] == s], cats, name, cfg.tile) for s in SPLITS}
    for s in SPLITS:
        (out / s / COCO_FILE).write_text(json.dumps(datasets[s], separators=(",", ":")))
    records = [r["record"] for r in sorted(results, key=lambda r: r["index"])]
    with open(out / "drawings.jsonl", "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    args = args or argparse.Namespace(drawings=drawings, seed=seed, workers=workers)
    info = manifest(name, args, cfg, templates, styles, cats, datasets, records)
    (out / "manifest.json").write_text(json.dumps(info, indent=2, sort_keys=False))
    run = run_info(results, seconds, workers)
    (out / "run.json").write_text(json.dumps(run, indent=2))
    return info | {"run": run}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="dataset directory, under data/ (e.g. data/datasets/seg-synth-v1)")
    ap.add_argument("--drawings", type=int, required=True, help="number of drawings to generate")
    ap.add_argument("--seed", type=int, required=True, help="seed: the same seed writes the same dataset")
    ap.add_argument("--workers", type=int, default=1, help="processes (default 1)")
    ap.add_argument("--overwrite", action="store_true", help="replace a dataset already in --out")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if not allowed_output(out):
        sys.exit(f"Refusing --out inside the repo tree outside data/: {out}\n"
                 "Datasets are data: they must never land in tracked files.")
    if args.drawings < 1 or args.workers < 1:
        sys.exit("--drawings and --workers must be at least 1")
    existing = [p for p in OUTPUTS if (out / p).exists()]
    if existing and not args.overwrite:
        sys.exit(f"{out} already holds {existing}; pass --overwrite to replace them")
    for p in existing:  # only what this tool writes
        target = out / p
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    print(f"seg-synth: {args.drawings} drawings, seed {args.seed}, {args.workers} worker(s) -> {out}")
    info = build(out, args.drawings, args.seed, args.workers, args=args)
    for s in SPLITS:
        sp = info["splits"][s]
        print(f"  {s}: {sp['drawings']} drawings, {sp['images']} tiles, {sp['instances']} instances "
              f"({sp['thin_instances']} thin)")
    a = info["alignment"]["continuous_lines"]
    print(f"  alignment (continuous lines): r1 mean {a['r1_mean']} p5 {a['r1_p5']}; r2 mean {a['r2_mean']}")
    print(f"  {info['run']['wall_s']}s wall, {info['run']['mean_per_drawing_s']['with_tiles']}s per drawing "
          f"per worker")
    print(f"manifest: {out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
