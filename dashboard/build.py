#!/usr/bin/env python
"""Build the training dashboard: one local HTML page under data/dashboard/.

    python dashboard/build.py                  # pull from the Modal Volume and build once
    python dashboard/build.py --watch          # pull and rebuild until stopped (--every 60)
    python dashboard/build.py --no-pull        # local files only

The page shows what a segmentation run is doing now, how it went, and test scores before and after
training. Reading is all it does: it never starts or stops a Modal run. Its output will one day show
the owner's private drawings, so it goes to data/dashboard/ (gitignored) or outside the repo; any
other path inside a repo is refused.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python dashboard/build.py` puts only dashboard/ on the path
    sys.path.insert(0, str(ROOT))

from dashboard import page, runs, tiles  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "dashboard"
DEFAULT_MODELS = ROOT / "data" / "models"
DEFAULT_DATASETS = ROOT / "data" / "datasets"


def allowed_output(out):
    """Under the repo's data/dashboard, or in no repo at all. Never any other path inside a repo."""
    out = Path(out).resolve()
    for folder in (out, *out.parents):
        if (folder / ".git").exists() or folder == ROOT:
            home = folder / "data" / "dashboard"
            return out == home or home in out.parents
    return True


def read_manifests(world, datasets_dir):
    """{dataset name: manifest} for every dataset a run used."""
    names = {run.dataset for run in world.runs if run.dataset}
    found = {name: runs.read_json(Path(datasets_dir) / name / "manifest.json") for name in sorted(names)}
    return {name: manifest for name, manifest in found.items() if manifest}


def gallery_columns(models, dataset):
    """Correct masks, then before and after per model that has any result on this dataset."""
    columns = [tiles.Column("gt", "Correct masks")]
    for name, group in models.items():
        if not any(run and run.dataset == dataset for run in (group["before"], group["after"])):
            continue
        for word in ("before", "after"):
            run = group[word]
            if run and run.dataset == dataset and run.predictions:
                columns.append(tiles.Column(run.run_id, f"{group['model']} {word}", run.predictions))
            else:
                columns.append(tiles.Column(f"{name}-{word}", f"{group['model']} {word}"))
    return columns


def main_dataset(models):
    """The dataset of the newest real scored run: the side-by-side images use its test split."""
    candidates = [run for group in models.values() for run in (group["after"], group["before"]) if run and run.dataset]
    return max(candidates, key=lambda r: r.last_seen).dataset if candidates else None


def build(out, models_dir=DEFAULT_MODELS, datasets_dir=DEFAULT_DATASETS, modal=None, every=60, now=None):
    """Read everything, paint what is stale, write index.html in one step. Returns the World."""
    out, cache = Path(out), Path(out) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    world = runs.collect(models_dir, cache, modal, now)
    models = runs.group_models(world.runs)
    manifests = read_manifests(world, datasets_dir)
    dataset = main_dataset(models)
    gallery, styles = None, {}
    if dataset:
        coco = runs.read_json(Path(datasets_dir) / dataset / "test" / tiles.COCO_NAME)
        if coco:
            styles = tiles.class_styles(tiles.thin_shares(manifests.get(dataset), coco))
            gallery = tiles.build_gallery(Path(datasets_dir) / dataset, gallery_columns(models, dataset), styles, out)
    html = page.render(world, models, manifests, styles, gallery, every)
    part = out / "index.html.part"
    part.write_text(html)
    part.replace(out / "index.html")
    return world


def describe(world):
    focus = runs.focus_runs(world.runs)
    words = ", ".join(f"{run.run_id}: {run.stage.kind}" for run in focus) or "no runs"
    problem = f" (Volume not read: {world.volume.error})" if world.volume.error else ""
    return f"{world.now.astimezone():%H:%M} {words}{problem}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--watch", action="store_true", help="pull and rebuild until stopped")
    ap.add_argument("--every", type=int, default=60, help="seconds between builds, and the page's refresh")
    ap.add_argument("--no-pull", action="store_true", help="local files only: no Modal calls")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output folder (data/dashboard or outside the repo)")
    ap.add_argument("--models", default=str(DEFAULT_MODELS), help="folder holding <model>/<run_id>/ and gpu_ledger.json")
    ap.add_argument("--datasets", default=str(DEFAULT_DATASETS), help="folder holding <dataset>/")
    args = ap.parse_args(argv)
    if not allowed_output(args.out):
        print(f"refusing to write {args.out}: use data/dashboard or a path outside the repo", file=sys.stderr)
        return 2
    every = max(args.every, 5)
    modal = None if args.no_pull else runs.ModalCli()
    try:
        while True:
            try:
                world = build(args.out, args.models, args.datasets, modal, every)
                print(f"{describe(world)} -> {Path(args.out) / 'index.html'}", flush=True)
            except Exception as exc:  # a half-written file must not end a watch; the last page stays
                if not args.watch:
                    raise
                print(f"build failed, keeping the last page: {exc!r}", file=sys.stderr, flush=True)
            if not args.watch:
                return 0
            time.sleep(every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
