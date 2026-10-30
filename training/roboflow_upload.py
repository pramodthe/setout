#!/usr/bin/env python
"""Package a synthetic segmentation dataset for Roboflow (SAM 3 fine-tuning) and bring predictions back.

    python training/roboflow_upload.py zip seg-synth-v1          # builds data/roboflow/seg-synth-v1.zip (dry run)
    python training/roboflow_upload.py upload seg-synth-v1 --project seg-synth-v1 [--workspace <slug>]
    python training/roboflow_upload.py predictions seg-synth-v1 <responses_dir> \\
        --out data/models/sam3/<run_id>/predictions_test.json [--split test]

`zip` writes train/, valid/ and test/, each with its images and an _annotations.coco.json cut down
to standard COCO keys (our extras such as attributes.thin stay in the local copy, which remains
the scoring reference). On import the folders set the splits: keep them, do not rebalance.

`upload` sends that zip to a private instance-segmentation project with the roboflow SDK (1.7.3;
`pip install roboflow==1.7.3`, deliberately not in requirements.txt). The key is read from the
ROBOFLOW_API_KEY environment variable; it is never printed, and SDK output and errors are
redacted because the SDK puts it in request URLs.

`predictions` turns Roboflow instance-segmentation responses (one JSON per image, named after the
image file, with `predictions[*].points`, `class` and `confidence`) into COCO results for
training/eval_seg.py: image ids by file name, category ids by class name.

Only a directory under data/datasets/ whose manifest.json says "source": "synthetic" is packaged
or uploaded, so nothing derived from the owner's drawings leaves the machine through this tool.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training.eval_seg import allowed_output  # noqa: E402

DATASETS = ROOT / "data" / "datasets"
ZIP_DIR = ROOT / "data" / "roboflow"
SPLITS = ("train", "valid", "test")
COCO_KEYS = {
    "images": ("id", "file_name", "width", "height"),
    "categories": ("id", "name", "supercategory"),
    "annotations": ("id", "image_id", "category_id", "segmentation", "bbox", "area", "iscrowd"),
}
PROJECT_TYPE = "instance-segmentation"
PROJECT_LICENSE = "Private"  # the roboflow CLI's own default for private projects


class Refused(Exception):
    """The dataset or request may not leave this machine through this tool."""


def synthetic_dataset(name_or_path):
    """The dataset root, only if it lies under data/datasets/ and its manifest says synthetic."""
    given = Path(name_or_path)
    root = (given if given.exists() else DATASETS / name_or_path).resolve()
    if DATASETS.resolve() not in root.parents:
        raise Refused(f"{name_or_path}: only datasets under data/datasets/ can be packaged")
    manifest = root / "manifest.json"
    if not manifest.is_file():
        raise Refused(f"{root.name}: no manifest.json")
    if json.loads(manifest.read_text()).get("source") != "synthetic":
        raise Refused(f"{root.name}: manifest.json does not say \"source\": \"synthetic\"")
    return root


def roboflow_coco(coco):
    """Standard COCO keys only; Roboflow's importer reads these."""
    out = {key: [{k: item[k] for k in keys if k in item} for item in coco[key]] for key, keys in COCO_KEYS.items()}
    out["info"] = {"description": "synthetic drawing-segmentation tiles, training/roboflow_upload.py"}
    return out


def build_zip(root, out_dir=ZIP_DIR):
    """data/roboflow/<name>.zip in Roboflow's COCO layout. Returns (path, per-split counts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{root.name}.zip"
    partial = zip_path.with_suffix(".zip.partial")
    counts = {}
    with zipfile.ZipFile(partial, "w") as zf:
        for split in SPLITS:
            coco = json.loads((root / split / "_annotations.coco.json").read_text())
            for img in coco["images"]:
                path = (root / split / img["file_name"]).resolve()
                if root not in path.parents:
                    raise Refused(f"{img['file_name']} resolves outside {root.name}")
                zf.write(path, f"{split}/{img['file_name']}", compress_type=zipfile.ZIP_STORED)
            zf.writestr(f"{split}/_annotations.coco.json", json.dumps(roboflow_coco(coco)),
                        compress_type=zipfile.ZIP_DEFLATED)
            counts[split] = {"images": len(coco["images"]), "instances": len(coco["annotations"])}
    partial.replace(zip_path)
    return zip_path, counts


def _redact(text, secret):
    return text.replace(secret, "***") if secret else text


def upload(zip_path, project, workspace=None):
    """Upload the zip to a private instance-segmentation project (created if missing)."""
    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        raise Refused("ROBOFLOW_API_KEY is not set")
    import roboflow  # roboflow==1.7.3, installed only where uploads happen

    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            ws = roboflow.Roboflow(api_key=key).workspace(workspace)
            status = ws.upload_dataset(str(zip_path), project, project_license=PROJECT_LICENSE,
                                       project_type=PROJECT_TYPE)
    except Exception as exc:  # the SDK's HTTP errors carry the key in their URLs
        raise RuntimeError(_redact(f"{type(exc).__name__}: {exc}", key)) from None
    finally:
        print(_redact(captured.getvalue(), key), end="")
    return json.loads(_redact(json.dumps(status, default=str), key)) if status else None


def roboflow_predictions(responses_dir, gt):
    """COCO results from per-image Roboflow responses. Returns (rows, counts of what was skipped)."""
    by_stem = {Path(img["file_name"]).stem: img for img in gt["images"]}
    category = {c["name"]: c["id"] for c in gt["categories"]}
    rows, skipped, seen = [], Counter(), set()
    for path in sorted(Path(responses_dir).glob("*.json")):
        img = by_stem.get(path.stem)
        if img is None:
            skipped["unknown_image"] += 1
            continue
        seen.add(img["id"])
        response = json.loads(path.read_text())
        size = response.get("image") or {}
        sx = img["width"] / float(size.get("width") or img["width"])
        sy = img["height"] / float(size.get("height") or img["height"])
        for p in response.get("predictions", []):
            if p.get("class") not in category:
                skipped["unknown_class"] += 1
                continue
            points = p.get("points") or []
            if len(points) < 3:
                skipped["no_polygon"] += 1
                continue
            w, h = float(p["width"]) * sx, float(p["height"]) * sy
            rows.append({
                "image_id": img["id"],
                "category_id": category[p["class"]],
                "segmentation": [[round(float(v), 2) for pt in points for v in (pt["x"] * sx, pt["y"] * sy)]],
                "bbox": [float(p["x"]) * sx - w / 2, float(p["y"]) * sy - h / 2, w, h],
                "score": float(p["confidence"]),
            })
    missing = len(gt["images"]) - len(seen)
    if missing:
        skipped["images_without_response"] = missing
    return rows, dict(skipped)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    z = sub.add_parser("zip", help="build data/roboflow/<name>.zip only")
    z.add_argument("dataset")
    u = sub.add_parser("upload", help="build the zip and upload it (needs ROBOFLOW_API_KEY)")
    u.add_argument("dataset")
    u.add_argument("--project", required=True, help="project slug; created as private if missing")
    u.add_argument("--workspace", default=None, help="workspace slug (default: the key's workspace)")
    p = sub.add_parser("predictions", help="Roboflow responses -> COCO results for eval_seg")
    p.add_argument("dataset")
    p.add_argument("responses", help="directory with one response JSON per image (<image stem>.json)")
    p.add_argument("--split", default="test")
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        root = synthetic_dataset(args.dataset)
        if args.command == "upload" and not os.environ.get("ROBOFLOW_API_KEY"):
            raise Refused("ROBOFLOW_API_KEY is not set")
        if args.command == "predictions":
            if not allowed_output(args.out):
                raise Refused(f"refusing to write {args.out}: use data/ or a path outside the repo")
            gt = json.loads((root / args.split / "_annotations.coco.json").read_text())
            rows, skipped = roboflow_predictions(args.responses, gt)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(rows))
            print(f"{len(rows)} predictions -> {args.out}; skipped {skipped or 'nothing'}")
            print(f"score: python -m training.eval_seg --gt {root / args.split / '_annotations.coco.json'} "
                  f"--pred {args.out} --out {Path(args.out).with_name('metrics_' + args.split + '.json')}")
            return 0
        zip_path, counts = build_zip(root)
        print(f"{zip_path} ({zip_path.stat().st_size / 2 ** 20:.1f} MiB): {counts}")
        if args.command == "upload":
            print(json.dumps(upload(zip_path, args.project, args.workspace), indent=2))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
