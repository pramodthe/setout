"""RF-DETR-Seg on Modal: train on a synthetic segmentation dataset, predict its test split, score it.

    modal run training/rfdetr_seg.py --smoke                          # tiny pillow dataset, 1 epoch, end to end
    modal run --detach training/rfdetr_seg.py --dataset seg-synth-v1  # the real run
    modal run training/rfdetr_seg.py --action predict --run-id <id>   # predict + score a trained run again
    modal run training/rfdetr_seg.py --action predict --weights pretrained   # the "before" run, no checkpoint of ours
    modal run training/rfdetr_seg.py --action score --run-id <id>     # re-score saved predictions (CPU only)
    modal run training/rfdetr_seg.py --action download --run-id <id>  # fetch metrics and the checkpoint
    modal run training/rfdetr_seg.py --action parity                  # eval_seg vs pycocotools (CPU only)
    modal run training/rfdetr_seg.py --action budget                  # GPU seconds and dollars so far

Flow: data/datasets/<name> (train/valid/test, each with _annotations.coco.json) -> the
`setout-seg-datasets` Volume (skipped when its fingerprint matches) -> rfdetr's RFDETRSeg*.train on
one GPU -> checkpoints and logs on the `setout-seg-runs` Volume -> test-split predictions in COCO
results format -> training/eval_seg.py, cross-checked against pycocotools ->
data/models/rfdetr-seg/<run_id>/ (gitignored): metrics, table, predictions, overlays, checkpoint.

GPUs are chosen by cost per job, not speed. Training defaults to an L40S and prediction to an A10;
--gpu and --predict-gpu override them, and H100 or B200 are for when someone asks. A B200 was
overkill here: batch 8 at 1008 px peaked at 37.0 GiB, and it trained roughly 1.4x faster per tile
than the H100 smoke at nearly 1.6x the price.

Settings (rfdetr 1.11.2, read from its source; see training/README.md for the reasoning):
- RFDETRSeg2XLarge at resolution 1008, the tiles' own size, so nothing is downscaled: a 2 px
  membrane stays 2 px for the backbone. All Seg sizes share the DINOv2-S/12 backbone, so at a
  fixed resolution 2XLarge costs little more than Large; its COCO checkpoint was trained at 768,
  the closest to 1008 (least positional-embedding interpolation), and it keeps 300 queries.
- multi_scale "off" and scale_jitter False: the default jitter shrinks a tile to 400-600 px
  before cropping, which turns a 3 px part into ~1.5 px. Augmentation is the default horizontal
  flip only; the synthetic generator already varies the drawings.
- batch 8 x grad accumulation 2 (effective 16, the batch rfdetr's lr 1e-4 / 1.5e-4 assume), bf16,
  constant learning rate (rfdetr's step schedule drops at epoch 100) with EMA, best checkpoint by
  validation mask mAP, early stopping after 10 epochs without improvement.

GPU budget: each GPU call is reserved in data/models/rfdetr-seg/gpu_ledger.json at its hard
timeout and settled at the time measured in its container, with its price, cost and (for
training) cost per epoch; a call that could take the total past --budget-usd (default 3
B200-hours at list price) is shortened or refused. Training also stops cleanly at a time limit
(PyTorch Lightning max_time), so the best checkpoint is written before the hard timeout.
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATASETS = ROOT / "data" / "datasets"
MODELS = ROOT / "data" / "models" / "rfdetr-seg"
LEDGER = MODELS / "gpu_ledger.json"

TRAIN_GPU = "L40S"  # 48 GB, about 45 GiB usable: batch 8 at 1008 px peaked at 37.0 GiB on the B200 run
# 24 GB; batch-8 inference peaks at 6 GiB. L4 is cheaper per second, but three L4 requests for 4 or
# more CPUs got no container in 4.5-7 min each, while an A10 with the same request started in 30 s.
# The job takes under 4 minutes, so the wait is not worth the 2 cents. A bare L4 did schedule.
PREDICT_GPU = "A10"
# Modal list prices in $ per GPU-second, checked 30 Oct 2026 (`modal billing rates`). CPU and
# memory are billed on top: they added 20% to the B200 run.
GPU_PRICE_PER_S = {"B200": 0.001736, "H100": 0.001097, "A100-80GB": 0.000694, "L40S": 0.000542,
                   "A10": 0.000306, "L4": 0.000222}
DEFAULT_BUDGET_USD = 18.75  # the experiment's 3 B200-hours at list price
WEIGHTS = ("trained", "pretrained")
# The pretrained checkpoint's COCO class ids must not collide with a split's categories 1..N.
PRETRAINED_CATEGORY_OFFSET = 1000
RFDETR_VERSION = "1.11.2"
# rfdetr 1.11.2 was published 2026-10-04: everything not pinned below resolves as of that day.
RESOLVED_BEFORE = "2026-10-05T00:00:00Z"
PINS = [
    "torch==2.14.1",
    "torchvision==0.29.1",
    f"rfdetr[train]=={RFDETR_VERSION}",
    "pycocotools==2.0.11",
    "pytorch-lightning==2.6.6",
    "torchmetrics==1.8.2",
    "transformers==5.17.0",
    "supervision==0.30.6",
    "vernier==0.5.4",
    "numpy==2.5.3",
]
MODEL_CLASSES = {
    "seg-medium": "RFDETRSegMedium",
    "seg-large": "RFDETRSegLarge",
    "seg-xlarge": "RFDETRSegXLarge",
    "seg-2xlarge": "RFDETRSeg2XLarge",
}
DEFAULT_MODEL = "seg-2xlarge"
WEIGHTS_DIR = "/weights"
DATA_MOUNT = "/datasets"
RUNS_MOUNT = "/runs"
UPLOAD_MARKER = ".upload.json"
SPLITS = ("train", "valid", "test")

SMOKE_NAME = "_smoke"
SMOKE_TRAIN_MINUTES = 5
SETUP_SLACK_MIN = 12  # container start, model build, last validation, best-checkpoint promotion
PREDICT_TIMEOUT_MIN = 20
MIN_TRAIN_MIN = 3
# Billed container time outside the function body: cold start (image load, imports) and the idle
# scale-down window. The H100 smoke was billed 5.9 min for 0.6 min of function time over two calls.
STARTUP_ALLOWANCE_S = 180
SCALEDOWN_S = 10  # one-shot GPU calls: do not keep an idle GPU container warm

app = modal.App("setout-rfdetr-seg")
datasets_volume = modal.Volume.from_name("setout-seg-datasets", create_if_missing=True, version=2)
runs_volume = modal.Volume.from_name("setout-seg-runs", create_if_missing=True, version=2)
VOLUMES = {DATA_MOUNT: datasets_volume, RUNS_MOUNT: runs_volume}


def _fetch_pretrained_weights():
    """Image build step: the COCO-pretrained weights land in RF_HOME, MD5-checked by rfdetr."""
    import rfdetr

    getattr(rfdetr, MODEL_CLASSES[DEFAULT_MODEL])()


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")  # opencv, pulled in by supervision
    .uv_pip_install(*PINS, extra_options=f"--exclude-newer {RESOLVED_BEFORE}")
    .env({"RF_HOME": WEIGHTS_DIR, "PYTHONUNBUFFERED": "1"})
    .run_function(_fetch_pretrained_weights)
    .add_local_file(HERE / "eval_seg.py", "/root/eval_seg.py")
)


# ---------------------------------------------------------------- remote: training

def _environment():
    import importlib.metadata as md

    import torch

    packages = {d.metadata["Name"].lower(): d.version for d in md.distributions()}
    keys = ("torch", "torchvision", "rfdetr", "pytorch-lightning", "torchmetrics", "transformers",
            "supervision", "pycocotools", "vernier", "numpy", "pillow")
    env = {"python": sys.version.split()[0], "cuda": torch.version.cuda,
           "packages": {k: packages.get(k) for k in keys}, "all_packages": dict(sorted(packages.items()))}
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        env["gpu"] = {"name": p.name, "capability": f"{p.major}.{p.minor}",
                      "memory_gib": round(p.total_memory / 2 ** 30, 1)}
    return env


def _progress_callback(path):
    """A Lightning callback appending one JSON line per validated epoch: time, step speed, memory, metrics."""
    import pytorch_lightning as pl
    import torch

    class ProgressLog(pl.Callback):
        def on_train_start(self, trainer, pl_module):
            self.fit_start = time.monotonic()

        def on_train_epoch_start(self, trainer, pl_module):
            self.epoch_start, self.last, self.steps = time.monotonic(), None, []
            torch.cuda.reset_peak_memory_stats()

        def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
            now = time.monotonic()
            if self.last is not None:
                self.steps.append(now - self.last)
            self.last = now

        def on_validation_start(self, trainer, pl_module):
            self.val_start = time.monotonic()

        def on_validation_end(self, trainer, pl_module):
            if trainer.sanity_checking:
                return
            now = time.monotonic()
            metrics = {}
            for key, value in trainer.callback_metrics.items():
                try:
                    metrics[key] = round(float(value), 5)
                except (TypeError, ValueError, RuntimeError):
                    pass
            steps = sorted(self.steps)
            line = {
                "epoch": trainer.current_epoch,
                "elapsed_s": round(now - self.fit_start, 1),
                "train_s": round(self.val_start - self.epoch_start, 1),
                "val_s": round(now - self.val_start, 1),
                "micro_batches": len(steps) + 1,
                "median_batch_s": round(steps[len(steps) // 2], 3) if steps else None,
                "peak_mem_gib": round(torch.cuda.max_memory_allocated() / 2 ** 30, 1),
                "metrics": metrics,
            }
            with open(path, "a") as f:
                f.write(json.dumps(line) + "\n")
            print("progress", json.dumps({k: v for k, v in line.items() if k != "metrics"}), flush=True)

    return ProgressLog()


@contextmanager
def _trainer_hooks(max_minutes, progress_path):
    """RFDETR.train() builds its own Lightning Trainer and takes no trainer arguments.

    Wrap the builder it looks up at call time so training stops cleanly at max_time (inside the GPU
    budget; BestModelCallback still writes checkpoint_best_total.pth at fit end) and logs progress.
    """
    import rfdetr.training as rt

    original = rt.build_trainer

    def build_trainer(train_config, model_config, **kwargs):
        training = kwargs.get("include_training_callbacks", True)
        if training:
            kwargs.setdefault("max_time", {"seconds": int(max_minutes * 60)})
        trainer = original(train_config, model_config, **kwargs)
        if training:
            trainer.callbacks.append(_progress_callback(progress_path))
        return trainer

    rt.build_trainer = build_trainer
    try:
        yield
    finally:
        rt.build_trainer = original


def _read_progress(path):
    """One dict per validated epoch; a line cut short by a snapshot taken mid-write is skipped."""
    rows = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def _epoch_seconds(epochs):
    """Mean seconds of a full epoch, validation included, and how many full epochs that covers.

    The time limit stops training mid-epoch and rfdetr then forces one last validation, so a short
    final row would pull the mean down.
    """
    full = [e for e in epochs if e["median_batch_s"] is not None]
    if not full:
        return None, 0
    return round(sum(e["train_s"] + e["val_s"] for e in full) / len(full), 1), len(full)


@app.function(image=image, gpu=TRAIN_GPU, cpu=16.0, memory=65536, timeout=4 * 3600, volumes=VOLUMES,
              scaledown_window=SCALEDOWN_S)
def train(cfg: dict) -> dict:
    """Fine-tune the COCO-pretrained RF-DETR-Seg checkpoint on the dataset; logs and checkpoints to the runs Volume."""
    # Before torch loads: an L40S has about 8 GiB of headroom over this job's peak, so avoid fragmentation.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    import torch

    import rfdetr

    started = time.monotonic()
    datasets_volume.reload()
    dataset_dir = Path(DATA_MOUNT) / cfg["dataset"]
    run_dir = Path(RUNS_MOUNT) / cfg["run_id"]
    out_dir = run_dir / "train"
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = run_dir / "progress.jsonl"
    progress.unlink(missing_ok=True)  # a reused run id starts a fresh log
    marker = json.loads((dataset_dir / UPLOAD_MARKER).read_text())
    (run_dir / "run_config.json").write_text(json.dumps(
        {"config": cfg, "dataset_upload": marker, "environment": _environment()}, indent=2))

    # num_select only sets how many (query, class) pairs post-processing keeps: validation then
    # decodes the same top max_dets the test scoring uses, not the variant's 300.
    model = getattr(rfdetr, MODEL_CLASSES[cfg["model"]])(num_select=cfg["max_dets"])
    kwargs = {
        "dataset_dir": str(dataset_dir),
        "output_dir": str(out_dir),
        "resolution": cfg["resolution"],
        "epochs": cfg["epochs"],
        "batch_size": cfg["batch_size"],
        "grad_accum_steps": cfg["grad_accum"],
        "lr": cfg["lr"],
        "lr_encoder": cfg["lr_encoder"],
        "num_workers": cfg["num_workers"],
        "multi_scale": cfg["multi_scale"],
        "scale_jitter": cfg["scale_jitter"],
        "eval_interval": cfg["eval_interval"],
        "early_stopping": cfg["early_stopping_patience"] > 0,
        "early_stopping_patience": max(cfg["early_stopping_patience"], 1),
        "seed": cfg["seed"],
        "log_per_class_metrics": True,
        "tensorboard": False,
        "progress_bar": None,
    }
    fit_start = time.monotonic()
    with _trainer_hooks(cfg["max_train_minutes"], progress):
        model.train(**kwargs)
    fit_s = time.monotonic() - fit_start

    epochs = _read_progress(progress)
    best = sorted(p.name for p in out_dir.glob("checkpoint_best_*.pth"))
    epoch_s, full_epochs = _epoch_seconds(epochs)
    summary = {
        "run_id": cfg["run_id"],
        "fit_s": round(fit_s, 1),
        "function_s": round(time.monotonic() - started, 1),
        "epochs_validated": len(epochs),
        "epochs_full": full_epochs,
        "epoch_s": epoch_s,
        "epochs_requested": cfg["epochs"],
        "stopped_by_time": fit_s >= cfg["max_train_minutes"] * 60,
        "best_checkpoints": best,
        "peak_mem_gib": max((e["peak_mem_gib"] for e in epochs), default=None),
        "median_batch_s": epochs[-1]["median_batch_s"] if epochs else None,
        "last_epoch": epochs[-1] if epochs else None,
        "gpu": torch.cuda.get_device_name(0),
    }
    (run_dir / "train_summary.json").write_text(json.dumps(summary, indent=2))
    runs_volume.commit()
    return summary


# ---------------------------------------------------------------- remote: predict and score

def _best_checkpoint(train_dir):
    for name in ("checkpoint_best_total.pth", "checkpoint_best_ema.pth",
                 "checkpoint_best_regular.pth", "last_ema.pth"):
        if (train_dir / name).exists():
            return train_dir / name
    raise FileNotFoundError(f"no checkpoint under {train_dir}")


def _category_ids(class_names, gt):
    """Model class index -> the split's category id, by name (rfdetr indexes train categories by id)."""
    by_name = {c["name"]: c["id"] for c in gt["categories"]}
    missing = [n for n in class_names if n not in by_name]
    if missing:
        raise ValueError(f"model classes {missing} are not categories of this split")
    return {i: by_name[n] for i, n in enumerate(class_names)}


def _coco_results(img, det, category_of, eval_seg):
    """supervision Detections -> COCO results rows with compressed-RLE masks. Returns (rows, dropped).

    category_of maps rfdetr's class_id to a category id; None drops the row (its no-object slot).
    """
    h, w = int(img["height"]), int(img["width"])
    if det.mask is not None and det.mask.shape[1:] != (h, w):
        raise ValueError(f"mask {det.mask.shape[1:]} != image {h}x{w}")
    rows, dropped = [], 0
    for i in range(len(det)):
        category = category_of(int(det.class_id[i]))
        if category is None:
            dropped += 1
            continue
        x0, y0, x1, y1 = (float(v) for v in det.xyxy[i])
        counts = eval_seg.rle_encode(det.mask[i])
        rows.append({
            "image_id": img["id"],
            "category_id": category,
            "class_name": str(det.data["class_name"][i]),
            "segmentation": {"size": [h, w], "counts": eval_seg.rle_to_string(counts)},
            "bbox": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)],
            "score": round(float(det.confidence[i]), 5),
        })
    return rows, dropped


def _load_for_prediction(cfg, run_dir, gt):
    """The model, the name of its weights, and how its class ids become category ids.

    Trained: our run's best checkpoint, classes mapped to the split's by name. Pretrained: the stock
    COCO checkpoint at the same size and resolution as a trained run, positional embeddings
    interpolated as they are for training. Its COCO ids mean nothing here, so they are moved out of
    the split's range: the per-class scores see no prediction of theirs, class_agnostic sees them all.
    """
    import rfdetr

    if cfg["weights"] == "pretrained":
        model = getattr(rfdetr, MODEL_CLASSES[cfg["model"]])(resolution=cfg["resolution"], num_select=cfg["max_dets"])
        return model, Path(model.model_config.pretrain_weights).name, lambda c: PRETRAINED_CATEGORY_OFFSET + c
    checkpoint = _best_checkpoint(run_dir / "train")
    # Our own checkpoint on our own Volume; rfdetr loads its best-total files the same way.
    model = rfdetr.from_checkpoint(str(checkpoint), trust_checkpoint=True, num_select=cfg["max_dets"])
    return model, checkpoint.name, _category_ids(model.class_names, gt).get


def _pycocotools_scores(gt_path, results, max_dets):
    """The reference numbers: pycocotools COCOeval (segm) on the same predictions.

    Read from ev.eval at area 'all' and the last maxDets, with summarize()'s formulas: evaluate()
    sorts params.maxDets (its last entry is the cap) and summarize() hard-codes 100 for AP.
    """
    import contextlib
    import copy

    import numpy as np
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    with contextlib.redirect_stdout(io.StringIO()):
        coco_gt = COCO(str(gt_path))
        coco_dt = coco_gt.loadRes(copy.deepcopy(results))
        ev = COCOeval(coco_gt, coco_dt, "segm")
        ev.params.maxDets = sorted({1, min(10, max_dets), max_dets})
        ev.evaluate()
        ev.accumulate()
    precision = ev.eval["precision"][:, :, :, 0, -1]  # IoU x recall x class
    recall = ev.eval["recall"][:, :, 0, -1]

    def mean(values):
        valid = values[values > -1]
        return float(valid.mean()) if valid.size else None

    iou_75 = int(np.argmin(np.abs(ev.params.iouThrs - 0.75)))
    return {"AP": mean(precision), "AP50": mean(precision[0]), "AP75": mean(precision[iou_75]),
            f"AR{max_dets}": mean(recall),
            "per_class_AP": {coco_gt.cats[c]["name"]: mean(precision[:, :, k]) for k, c in enumerate(ev.params.catIds)},
            # pycocotools stores matches as ids, so a ground truth with id 0 never counts as matched.
            "gt_id_zero": any(a["id"] == 0 for a in coco_gt.dataset["annotations"])}


def _diff(a, b):
    """Absolute difference; a value on one side and none on the other counts as a full disagreement."""
    if a is None or b is None:
        return 0.0 if a is b else 1.0
    return abs(a - b)


def _pycocotools_agnostic(gt, results, max_dets):
    """COCOeval with every class folded into one, which is what ignoring the class means.

    Folded rather than useCats=0: that mode pools ground truth by category, so its order on equal
    IoUs differs, and it still skips predictions whose category the ground truth does not list.
    """
    folded = {**gt, "categories": [{"id": 1, "name": "any"}],
              "annotations": [{**a, "category_id": 1} for a in gt["annotations"]]}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "gt.json"
        path.write_text(json.dumps(folded))
        return _pycocotools_scores(path, [{**r, "category_id": 1} for r in results], max_dets)


def _parity(gt_path, gt, results, ours, max_dets):
    """Largest absolute difference between eval_seg and pycocotools: per class, overall, class-agnostic."""
    reference = _pycocotools_scores(gt_path, results, max_dets)
    agnostic = _pycocotools_agnostic(gt, results, max_dets)
    overall = {k: _diff(reference[k], v) for k, v in ours["coco"].items()}
    per_class = {n: _diff(v, ours["per_class"][n]["AP"]) for n, v in reference["per_class_AP"].items()}
    folded = {k: _diff(agnostic[k], ours["class_agnostic"][k]) for k in ours["coco"]}
    return {"pycocotools": reference,
            "eval_seg": ours["coco"],
            "pycocotools_class_agnostic": {k: agnostic[k] for k in ours["coco"]},
            "max_abs_diff": max(overall.values(), default=0.0),
            "per_class_max_abs_diff": max(per_class.values(), default=0.0),
            "class_agnostic_max_abs_diff": max(folded.values(), default=0.0)}


PALETTE = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180), (70, 240, 240),
           (240, 50, 230), (210, 245, 60), (0, 128, 128), (170, 110, 40), (128, 0, 0), (0, 0, 128)]


def _paint(base, items, eval_seg):
    import numpy as np

    h, w = base.shape[:2]
    out = base.astype(np.float32)
    for segm, cat in items:
        mask = eval_seg.segmentation_mask(segm, h, w)
        out[mask] = 0.35 * out[mask] + 0.65 * np.array(PALETTE[cat % len(PALETTE)], dtype=np.float32)
    return out.astype(np.uint8)


def _save_overlays(split_dir, gt, results, out_dir, score_thr, eval_seg, count=6):
    """Ground truth (left) beside predictions >= score_thr (right) for the tiles with the most thin parts."""
    import numpy as np
    from PIL import Image

    thin = {}
    for a in gt["annotations"]:
        thin[a["image_id"]] = thin.get(a["image_id"], 0) + int(bool((a.get("attributes") or {}).get("thin")))
    chosen = sorted(gt["images"], key=lambda i: (-thin.get(i["id"], 0), i["id"]))[:count]
    out_dir.mkdir(parents=True, exist_ok=True)
    for img in chosen:
        base = np.array(Image.open(split_dir / img["file_name"]).convert("RGB"))
        left = _paint(base, [(a["segmentation"], a["category_id"]) for a in gt["annotations"]
                             if a["image_id"] == img["id"]], eval_seg)
        right = _paint(base, [(r["segmentation"], r["category_id"]) for r in results
                              if r["image_id"] == img["id"] and r["score"] >= score_thr], eval_seg)
        Image.fromarray(np.concatenate([left, right], axis=1)).save(out_dir / f"{Path(img['file_name']).stem}.png")


@app.function(image=image, gpu=PREDICT_GPU, cpu=4.0, memory=16384, timeout=3600, volumes=VOLUMES,
              scaledown_window=SCALEDOWN_S)
def predict(cfg: dict) -> dict:
    """Predict a split with cfg["weights"]; COCO results (compressed-RLE masks) to the runs Volume."""
    import eval_seg
    import torch
    from PIL import Image

    started = time.monotonic()
    runs_volume.reload()
    datasets_volume.reload()
    split = cfg["split"]
    run_dir = Path(RUNS_MOUNT) / cfg["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    split_dir = Path(DATA_MOUNT) / cfg["dataset"] / split
    gt = json.loads((split_dir / "_annotations.coco.json").read_text())
    model, weights_file, category_of = _load_for_prediction(cfg, run_dir, gt)

    results, dropped = [], 0
    images = sorted(gt["images"], key=lambda i: i["id"])
    t0 = time.monotonic()
    for start in range(0, len(images), cfg["predict_batch"]):
        batch = images[start:start + cfg["predict_batch"]]
        pil = [Image.open(split_dir / img["file_name"]).convert("RGB") for img in batch]
        dets = model.predict(pil, threshold=0.0, include_source_image=False)
        for img, det in zip(batch, dets if isinstance(dets, list) else [dets]):
            rows, n = _coco_results(img, det, category_of, eval_seg)
            results.extend(rows)
            dropped += n
    (run_dir / f"predictions_{split}.json").write_text(json.dumps(results))
    summary = {"split": split, "images": len(images), "predictions": len(results),
               "dropped_no_object": dropped, "weights": cfg["weights"], "checkpoint": weights_file,
               "model": cfg["model"], "resolution": model.model_config.resolution,
               "class_names": list(model.class_names), "num_select": cfg["max_dets"],
               "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
               "peak_mem_gib": round(torch.cuda.max_memory_allocated() / 2 ** 30, 1)
               if torch.cuda.is_available() else None,
               "predict_s": round(time.monotonic() - t0, 1), "function_s": round(time.monotonic() - started, 1)}
    (run_dir / f"predict_summary_{split}.json").write_text(json.dumps(summary, indent=2))
    runs_volume.commit()
    return summary


@app.function(image=image, cpu=8.0, memory=32768, timeout=3600, volumes=VOLUMES)
def score(cfg: dict) -> dict:
    """CPU only: eval_seg on the run's predictions, the pycocotools cross-check, and overlay images."""
    import eval_seg

    runs_volume.reload()
    datasets_volume.reload()
    split = cfg["split"]
    run_dir = Path(RUNS_MOUNT) / cfg["run_id"]
    split_dir = Path(DATA_MOUNT) / cfg["dataset"] / split
    gt_path = split_dir / "_annotations.coco.json"
    gt = json.loads(gt_path.read_text())
    results = json.loads((run_dir / f"predictions_{split}.json").read_text())
    predicted = json.loads((run_dir / f"predict_summary_{split}.json").read_text())
    t0 = time.monotonic()
    result = eval_seg.evaluate(gt, results, score_thr=cfg["score_thr"], max_dets=cfg["max_dets"])
    result["files"] = {"gt": f"{cfg['dataset']}/{split}/_annotations.coco.json",
                       "pred": f"predictions_{split}.json"}
    result["model"] = {"family": "RF-DETR-Seg", "run_id": cfg["run_id"], "checkpoint": predicted["checkpoint"],
                       "weights": predicted["weights"], "class_names": predicted["class_names"]}
    eval_seg.write_report(result, run_dir / f"metrics_{split}.json")
    score_s = time.monotonic() - t0
    parity = _parity(gt_path, gt, results, result, cfg["max_dets"]) if results else None
    (run_dir / f"parity_{split}.json").write_text(json.dumps(parity, indent=2))
    _save_overlays(split_dir, gt, results, run_dir / f"overlays_{split}", cfg["score_thr"], eval_seg)
    runs_volume.commit()
    return {"table": eval_seg.format_table(result), "parity": parity, "score_s": round(score_s, 1)}


# ---------------------------------------------------------------- remote: scorer parity (CPU)

def _random_polygon(rng, w, h):
    """Rectangles, thin strips (some under a pixel), random convex blobs; some cross the image edge."""
    import numpy as np

    kind = rng.integers(3)
    cx, cy = rng.uniform(-5, w + 5), rng.uniform(-5, h + 5)
    angle = rng.uniform(0, np.pi)
    if kind == 2:
        r = rng.uniform(2, 25, 7)
        t = np.sort(rng.uniform(0, 2 * np.pi, 7))
        pts = np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], axis=1)
    else:
        length, width = (rng.uniform(5, 60), rng.uniform(0.3, 5)) if kind else rng.uniform(3, 40, 2)
        c, s = np.cos(angle), np.sin(angle)
        corners = np.array([[-length, -width], [length, -width], [length, width], [-length, width]]) / 2
        pts = corners @ np.array([[c, s], [-s, c]]) + [cx, cy]
    return [round(float(v), 2) for v in pts.ravel()]


def _random_case(rng, eval_seg, n_images):
    """Ground truth with sparse ids, a few crowd regions, and noisy predictions.

    Predictions are shifted, relabelled, tied on score, and some carry a category the split does not
    have, as the pretrained checkpoint's do.
    """
    import numpy as np

    cats = [1, 2, 3, 7]
    pred_cats = cats + [PRETRAINED_CATEGORY_OFFSET + 1]
    gt = {"images": [], "annotations": [], "categories": [{"id": c, "name": f"c{c}"} for c in cats]}
    preds = []
    for i in range(n_images):
        img = {"id": 3 * i + 2, "file_name": f"{i}.png", "width": 64 + i % 9, "height": 60}
        gt["images"].append(img)
        w, h = img["width"], img["height"]
        for _ in range(int(rng.integers(0, 7))):
            poly, cat = _random_polygon(rng, w, h), int(rng.choice(cats))
            mask = eval_seg.segmentation_mask([poly], h, w)
            crowd = rng.random() < 0.08
            segm = {"size": [h, w], "counts": eval_seg.rle_encode(mask)} if crowd else [poly]
            gt["annotations"].append({"id": len(gt["annotations"]) + 1, "image_id": img["id"],
                                      "category_id": cat, "segmentation": segm, "area": float(mask.sum()),
                                      "bbox": [0, 0, 1, 1], "iscrowd": int(crowd)})
            for _ in range(int(rng.integers(0, 3))):
                jitter = (np.array(poly) + rng.normal(0, rng.uniform(0, 2), len(poly))).round(2).tolist()
                pcat = cat if rng.random() > 0.15 else int(rng.choice(pred_cats))
                preds.append((img, jitter, pcat))
        for _ in range(int(rng.integers(0, 3))):
            preds.append((img, _random_polygon(rng, w, h), int(rng.choice(pred_cats))))
    rows = []
    for img, poly, cat in preds:
        mask = eval_seg.segmentation_mask([poly], img["height"], img["width"])
        rows.append({"image_id": img["id"], "category_id": cat, "bbox": [0, 0, 1, 1],
                     "score": round(float(rng.random()), 2),  # two decimals: ties are common
                     "segmentation": {"size": [img["height"], img["width"]],
                                      "counts": eval_seg.rle_to_string(eval_seg.rle_encode(mask))}})
    return gt, rows


@app.function(image=image, cpu=4.0, memory=8192, timeout=1800)
def scorer_parity(seed: int = 0, n_images: int = 60) -> dict:
    """eval_seg against pycocotools on random cases: polygon rasterisation, RLE strings, AP."""
    import eval_seg
    import numpy as np
    from pycocotools import mask as mask_utils

    rng = np.random.default_rng(seed)
    gt, preds = _random_case(rng, eval_seg, n_images)
    sizes = {img["id"]: (img["height"], img["width"]) for img in gt["images"]}
    raster_diff = string_diff = polygons = 0
    for a in gt["annotations"]:
        if a["iscrowd"]:
            continue
        h, w = sizes[a["image_id"]]
        ours = eval_seg.segmentation_mask(a["segmentation"], h, w)
        ref = mask_utils.decode(mask_utils.merge(mask_utils.frPyObjects(a["segmentation"], h, w))).astype(bool)
        raster_diff += int((ours != ref).sum())
        encoded = mask_utils.encode(np.asfortranarray(ours.astype(np.uint8)))["counts"].decode()
        string_diff += int(encoded != eval_seg.rle_to_string(eval_seg.rle_encode(ours)))
        polygons += 1
    with tempfile.TemporaryDirectory() as tmp:
        gt_path = Path(tmp) / "gt.json"
        gt_path.write_text(json.dumps(gt))
        cases = {f"max_dets={n}": _parity(gt_path, gt, preds, eval_seg.evaluate(gt, preds, max_dets=n), n)
                 for n in (100, 2)}
    return {"polygons": polygons, "pixels_differing": raster_diff, "rle_strings_differing": string_diff,
            "images": len(gt["images"]), "gt": len(gt["annotations"]), "predictions": len(preds), "ap": cases}


# ---------------------------------------------------------------- local: datasets

def check_dataset(root):
    """Fail before any GPU time when the dataset is not the contract's layout."""
    manifest = json.loads((root / "manifest.json").read_text())
    report, train_categories = {}, None
    for split in SPLITS:
        coco = json.loads((root / split / "_annotations.coco.json").read_text())
        categories = sorted((c["id"], c["name"]) for c in coco["categories"])
        train_categories = train_categories or categories
        if categories != train_categories:
            raise ValueError(f"{split}: categories differ from train")
        missing = [i["file_name"] for i in coco["images"] if not (root / split / i["file_name"]).exists()]
        if not coco["images"] or missing:
            raise ValueError(f"{split}: {len(coco['images'])} images, {len(missing)} missing files")
        thin = sum(bool((a.get("attributes") or {}).get("thin")) for a in coco["annotations"])
        report[split] = {"images": len(coco["images"]), "instances": len(coco["annotations"]), "thin": thin}
    return {"source": manifest.get("source"), "classes": [n for _, n in train_categories], "splits": report}


def dataset_files(root):
    """What training reads: manifest.json and the split folders (not drawings.jsonl, run.json, ...)."""
    return [root / "manifest.json"] + sorted(p for split in SPLITS for p in (root / split).rglob("*") if p.is_file())


def dataset_fingerprint(root):
    """sha256 over each training file's path and size, and the full content of every JSON file."""
    digest = hashlib.sha256()
    for path in dataset_files(root):
        digest.update(f"{path.relative_to(root).as_posix()}\0{path.stat().st_size}\0".encode())
        if path.suffix == ".json":
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _read_volume_json(volume, path):
    try:
        return json.loads(b"".join(volume.read_file(path)))
    except (modal.exception.NotFoundError, FileNotFoundError):
        return None


def upload_dataset(name):
    """Copy the split folders of data/datasets/<name> to the datasets Volume.

    Skipped when the same fingerprint is already there. The manifest travels inside the upload marker.
    """
    root = DATASETS / name
    fingerprint = dataset_fingerprint(root)
    remote = _read_volume_json(datasets_volume, f"{name}/{UPLOAD_MARKER}")
    if remote and remote.get("fingerprint") == fingerprint:
        print(f"dataset {name}: already on the Volume ({fingerprint[:12]})")
        return remote
    try:
        datasets_volume.remove_file(name, recursive=True)  # a stale or partial copy
    except (modal.exception.NotFoundError, FileNotFoundError):
        pass
    t0 = time.monotonic()
    with datasets_volume.batch_upload(force=True) as batch:
        for split in SPLITS:
            batch.put_directory(str(root / split), f"/{name}/{split}")
    marker = {"fingerprint": fingerprint, "name": name, "uploaded_at": _now(),
              "files": len(dataset_files(root)) - 1,
              "manifest": json.loads((root / "manifest.json").read_text())}
    with datasets_volume.batch_upload(force=True) as batch:  # written last: a partial copy has no marker
        batch.put_file(io.BytesIO(json.dumps(marker).encode()), f"/{name}/{UPLOAD_MARKER}")
    print(f"dataset {name}: uploaded {marker['files']} files in {time.monotonic() - t0:.0f} s")
    return marker


SMOKE_CLASSES = ("smoke-block", "smoke-strip", "smoke-slab")
SMOKE_SPLITS = {"train": 48, "valid": 8, "test": 8}
TILE, CELL = 1008, 336


def _smoke_shape(rng, x0, y0):
    """A rotated rectangle inside one 336 px cell: block, thin strip (1.5-5 px) or flat slab."""
    import numpy as np

    kind = int(rng.integers(3))
    if kind == 0:
        length, width = rng.uniform(60, 200, 2)
        angle = rng.uniform(0, np.pi)
    elif kind == 1:
        length, width = rng.uniform(150, 290), rng.uniform(1.5, 5.0)
        angle = float(rng.choice([0.0, np.pi / 2, rng.uniform(0, np.pi)]))
    else:
        length, width = rng.uniform(200, 290), rng.uniform(20, 60)
        angle = float(rng.choice([0.0, np.pi / 2]))
    c, s = np.cos(angle), np.sin(angle)
    corners = np.array([[-length, -width], [length, -width], [length, width], [-length, width]]) / 2
    pts = corners @ np.array([[c, s], [-s, c]]) + [x0 + CELL / 2, y0 + CELL / 2]
    return pts.round(2).ravel(), kind + 1, float(min(length, width))


def make_smoke_dataset(root, seed=0):
    """A tiny COCO dataset of random rectangles drawn with pillow, to prove the pipeline. Not drawings."""
    import numpy as np
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    categories = [{"id": i + 1, "name": n, "supercategory": "component"} for i, n in enumerate(SMOKE_CLASSES)]
    counts = {}
    for split, n_images in SMOKE_SPLITS.items():
        (root / split).mkdir(parents=True, exist_ok=True)
        images, anns = [], []
        for i in range(n_images):
            name = f"smoke_{split}_{i:03d}.png"
            canvas = Image.new("L", (TILE, TILE), 255)
            draw = ImageDraw.Draw(canvas)
            for cell in rng.permutation(9)[: int(rng.integers(4, 9))]:
                row, col = divmod(int(cell), 3)
                poly, cat, width = _smoke_shape(rng, col * CELL, row * CELL)
                draw.polygon([tuple(p) for p in poly.reshape(-1, 2)], fill=60 if cat == 2 else 200, outline=0)
                xs, ys = poly[0::2], poly[1::2]
                anns.append({
                    "id": len(anns) + 1, "image_id": i + 1, "category_id": cat,
                    "segmentation": [poly.tolist()],
                    "bbox": [float(xs.min()), float(ys.min()), float(np.ptp(xs)), float(np.ptp(ys))],
                    "area": float(0.5 * abs(xs @ np.roll(ys, 1) - ys @ np.roll(xs, 1))), "iscrowd": 0,
                    "attributes": {"thin": width < 6, "min_width_px": round(width, 2),
                                   "part_id": f"{split}-{i:03d}-{len(anns) + 1}"},
                })
            canvas.save(root / split / name)
            images.append({"id": i + 1, "file_name": name, "width": TILE, "height": TILE,
                           "drawing_id": f"smoke-{split}-{i:03d}", "tile_xy": [0, 0]})
        coco = {"images": images, "categories": categories, "annotations": anns}
        (root / split / "_annotations.coco.json").write_text(json.dumps(coco))
        counts[split] = {"images": len(images), "instances": len(anns)}
    manifest = {
        "name": SMOKE_NAME, "source": "synthetic",
        "purpose": "pipeline smoke test: random rectangles, not drawings; numbers mean nothing",
        "generator": "training/rfdetr_seg.py make_smoke_dataset", "seed": seed, "tile_px": TILE,
        "classes": list(SMOKE_CLASSES), "counts": counts, "thin_rule": "short side < 6 px",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))


# ---------------------------------------------------------------- local: GPU budget ledger

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else []


@contextmanager
def _ledger_for_update():
    """Read-modify-write under a file lock: two clients (a smoke and a real run) may share the ledger."""
    import fcntl

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = _ledger()
        yield entries
        LEDGER.write_text(json.dumps(entries, indent=2))


def _charged_s(entry):
    """GPU seconds an entry counts for: what Modal billed if known, else measured time plus start-up.

    Failing that, the worst case. billed_s is filled in by hand from `modal billing report`.
    """
    if entry.get("billed_s") is not None:
        return entry["billed_s"]
    if entry["status"] == "cancelled_unscheduled":  # set by hand when no container ever started
        return 0
    if entry.get("gpu_s") is not None:
        return entry["gpu_s"] + STARTUP_ALLOWANCE_S
    if entry.get("wall_s") is not None:  # failed or interrupted: the container ran at most this long
        return min(entry["wall_s"], entry["reserved_s"])
    return entry["reserved_s"]  # still running, or the client died


def _price(gpu):
    """List price per second. An unlisted GPU counts at the dearest, so the budget errs high."""
    return GPU_PRICE_PER_S.get(gpu, max(GPU_PRICE_PER_S.values()))


def _costs(entry, epochs=None, epoch_s=None):
    """What a ledger entry cost at list price, GPU only; for training also per epoch, to compare GPUs."""
    price = _price(entry["gpu"])
    costs = {"price_per_s": price, "cost_usd": round(_charged_s(entry) * price, 4)}
    if epoch_s:
        costs.update(epochs=epochs, epoch_s=epoch_s, cost_per_epoch_usd=round(epoch_s * price, 4))
    return costs


def usd_charged():
    """Dollars charged so far, GPU only, across every GPU type in the ledger."""
    return sum(_charged_s(e) * _price(e["gpu"]) for e in _ledger())


@contextmanager
def ledger_entry(run_id, function, gpu, reserved_s):
    """Reserve a GPU call at its hard timeout; the caller sets record["gpu_s"] from the container."""
    entry_id = hashlib.sha256(f"{run_id}{function}{time.time_ns()}".encode()).hexdigest()[:12]
    with _ledger_for_update() as entries:
        entries.append({"id": entry_id, "run_id": run_id, "function": function, "gpu": gpu,
                        "reserved_s": reserved_s, "started_at": _now(), "wall_s": None, "gpu_s": None,
                        "status": "running"})
    record, t0, status = {}, time.monotonic(), "failed"
    try:
        yield record
        status = "ok"
    finally:
        with _ledger_for_update() as entries:
            entry = next(e for e in entries if e.get("id") == entry_id)
            entry.update(wall_s=round(time.monotonic() - t0, 1), gpu_s=record.get("gpu_s"), status=status)
            entry.update(_costs(entry, record.get("epochs"), record.get("epoch_s")))


def plan_training(requested_min, budget_usd, gpu, predict_gpu):
    """Graceful training minutes and the call's hard timeout, both inside what the budget has left."""
    left = budget_usd - usd_charged()
    room = (left - PREDICT_TIMEOUT_MIN * 60 * _price(predict_gpu)) / _price(gpu) / 60 - SETUP_SLACK_MIN
    minutes = min(requested_min, room) if requested_min > 0 else room
    if minutes < MIN_TRAIN_MIN:
        raise SystemExit(f"GPU budget: ${left:.2f} of ${budget_usd:.2f} left, not enough to train on {gpu} "
                         "and predict. Raise --budget-usd only with the coordinator.")
    return round(minutes, 1), int((minutes + SETUP_SLACK_MIN) * 60)


def require_budget(minutes, budget_usd, gpu):
    left, needed = budget_usd - usd_charged(), minutes * 60 * _price(gpu)
    if left < needed:
        raise SystemExit(f"GPU budget: ${left:.2f} left, this {gpu} call reserves ${needed:.2f}.")


# ---------------------------------------------------------------- local: results

RESULT_FILES = ("run_config.json", "train_summary.json", "progress.jsonl", "train/metrics.csv",
                "train/training_config.json")


def download_run(run_id, split="test", trained=True):
    """Copy a run's metrics, predictions and overlays to data/models/; a trained run's logs and checkpoint too."""
    dest = MODELS / run_id
    names = [f"metrics_{split}.json", f"metrics_{split}.txt", f"parity_{split}.json",
             f"predict_summary_{split}.json", f"predictions_{split}.json"]
    names += list(RESULT_FILES) if trained else []
    try:
        names += [e.path.lstrip("/").removeprefix(f"{run_id}/")
                  for e in runs_volume.listdir(f"{run_id}/overlays_{split}")]
    except (modal.exception.NotFoundError, FileNotFoundError):
        pass
    if trained:
        try:
            listing = {e.path.rsplit("/", 1)[-1] for e in runs_volume.listdir(f"{run_id}/train")}
        except (modal.exception.NotFoundError, FileNotFoundError):
            listing = set()
        best = next((n for n in ("checkpoint_best_total.pth", "checkpoint_best_ema.pth",
                                 "checkpoint_best_regular.pth") if n in listing), None)
        names += [f"train/{best}"] if best else []
    missing = []
    for rel in names:
        target = (dest / rel).resolve()
        if dest.resolve() not in target.parents:
            raise ValueError(f"refusing to write {rel} outside {dest}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "wb") as f:
                for chunk in runs_volume.read_file(f"{run_id}/{rel}"):
                    f.write(chunk)
        except (modal.exception.NotFoundError, FileNotFoundError):
            target.unlink(missing_ok=True)
            missing.append(rel)
    print(f"downloaded to {dest}" + (f" (missing: {', '.join(missing)})" if missing else ""))
    return dest


def print_budget(budget_usd):
    """GPU seconds and dollars by GPU type, and cost per epoch for each training call."""
    entries = _ledger()
    for gpu in sorted({e["gpu"] for e in entries}):
        seconds = sum(_charged_s(e) for e in entries if e["gpu"] == gpu)
        print(f"{gpu}: {seconds:.0f} s charged, ${seconds * _price(gpu):.2f} at list price")
    for e in entries:
        if e.get("cost_per_epoch_usd"):
            print(f"{e['run_id']} on {e['gpu']}: {e['epoch_s']} s per epoch, ${e['cost_per_epoch_usd']} per epoch")
    print(f"total ${usd_charged():.2f} of ${budget_usd:.2f}, GPU only (ledger {LEDGER})")


def _print_progress(run_id):
    path = MODELS / run_id / "progress.jsonl"
    for line in _read_progress(path):
        segm = {k.split("/", 1)[1]: v for k, v in line["metrics"].items()
                if k.startswith("val/") and "segm_mAP" in k and "/AP/" not in k}
        print(f"  epoch {line['epoch']:>3}  train {line['train_s']:>6.0f}s  val {line['val_s']:>5.0f}s  "
              f"batch {line['median_batch_s']}s  mem {line['peak_mem_gib']} GiB  {segm}")


# ---------------------------------------------------------------- local entrypoint

@app.local_entrypoint()
def main(action: str = "all", dataset: str = "seg-synth-v1", smoke: bool = False, run_id: str = "",
         model: str = DEFAULT_MODEL, resolution: int = 1008, epochs: int = 100, batch_size: int = 8,
         grad_accum: int = 2, lr: float = 1e-4, lr_encoder: float = 1.5e-4, num_workers: int = 12,
         max_train_minutes: float = 0.0, eval_interval: int = 1, early_stopping_patience: int = 10,
         multi_scale: str = "off", scale_jitter: bool = False, seed: int = 0, split: str = "test",
         score_thr: float = 0.5, max_dets: int = 100, predict_batch: int = 8,
         budget_usd: float = DEFAULT_BUDGET_USD, gpu: str = TRAIN_GPU, predict_gpu: str = PREDICT_GPU,
         weights: str = "trained"):
    if action == "parity":
        print(json.dumps(scorer_parity.remote(), indent=2))
        return
    if action == "budget":
        print_budget(budget_usd)
        return
    if weights not in WEIGHTS:
        raise SystemExit(f"--weights must be one of {WEIGHTS}")
    if weights == "pretrained" and action not in ("predict", "score", "download"):
        raise SystemExit("--weights pretrained trains nothing: use --action predict")
    if not run_id and (action in ("score", "download") or (action == "predict" and weights == "trained")):
        raise SystemExit(f"--action {action} needs the --run-id of an existing run")
    if smoke:
        dataset, epochs = SMOKE_NAME, 1
        max_train_minutes = max_train_minutes or SMOKE_TRAIN_MINUTES
        if not (DATASETS / SMOKE_NAME / "manifest.json").exists():
            make_smoke_dataset(DATASETS / SMOKE_NAME)
    if model not in MODEL_CLASSES:
        raise SystemExit(f"--model must be one of {sorted(MODEL_CLASSES)}")
    prefix = "pretrained" if weights == "pretrained" else "smoke" if smoke else dataset
    run_id = run_id or f"{prefix}-{model}-{datetime.now(timezone.utc):%Y%m%dT%H%MZ}"
    cfg = {"run_id": run_id, "dataset": dataset, "weights": weights, "gpu": gpu, "predict_gpu": predict_gpu,
           "model": model, "resolution": resolution, "epochs": epochs,
           "batch_size": batch_size, "grad_accum": grad_accum, "lr": lr, "lr_encoder": lr_encoder,
           "num_workers": num_workers, "eval_interval": eval_interval,
           "early_stopping_patience": early_stopping_patience, "multi_scale": multi_scale,
           "scale_jitter": scale_jitter, "seed": seed, "split": split, "score_thr": score_thr,
           "max_dets": max_dets, "predict_batch": predict_batch, "smoke": smoke}

    if action in ("all", "upload", "train"):
        report = check_dataset(DATASETS / dataset)
        print(f"dataset {dataset}: source={report['source']} classes={len(report['classes'])} {report['splits']}")
        upload_dataset(dataset)
    if action in ("all", "train"):
        cfg["max_train_minutes"], hard_s = plan_training(max_train_minutes, budget_usd, gpu, predict_gpu)
        print(f"run {run_id} on {gpu}: train up to {cfg['max_train_minutes']} min "
              f"(hard timeout {hard_s // 60} min)", flush=True)
        with ledger_entry(run_id, "train", gpu, hard_s) as record:
            summary = train.with_options(gpu=gpu, timeout=hard_s).remote(cfg)
            record.update(gpu_s=summary["function_s"], epochs=summary["epochs_full"], epoch_s=summary["epoch_s"])
        print(json.dumps({k: v for k, v in summary.items() if k != "last_epoch"}, indent=2), flush=True)
    if action in ("all", "predict"):
        require_budget(PREDICT_TIMEOUT_MIN, budget_usd, predict_gpu)
        print(f"run {run_id}: predict {split} with {weights} weights on {predict_gpu}", flush=True)
        with ledger_entry(run_id, "predict", predict_gpu, PREDICT_TIMEOUT_MIN * 60) as record:
            predicted = predict.with_options(gpu=predict_gpu, timeout=PREDICT_TIMEOUT_MIN * 60).remote(cfg)
            record["gpu_s"] = predicted["function_s"]
        print(json.dumps({k: v for k, v in predicted.items() if k != "class_names"}), flush=True)
    if action in ("all", "predict", "score"):
        scored = score.remote(cfg)
        print(scored["table"])
        print("pycocotools parity:", json.dumps({k: v for k, v in (scored["parity"] or {}).items()
                                                  if k != "pycocotools"}))
    if action in ("all", "predict", "score", "download"):
        download_run(run_id, split=split, trained=weights == "trained")
        _print_progress(run_id)
    if smoke and action == "all":
        print("scorer parity:", json.dumps(scorer_parity.remote(), indent=2))
    print_budget(budget_usd)
