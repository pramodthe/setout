# training

What runs on the team's own GPUs through Modal, and the scorer every
segmentation model is judged by. Datasets, renders, masks, predictions and
weights never enter this repo: they live on private Modal Volumes, and anything
written locally goes under `data/`, which is gitignored.

| File | What it does |
|---|---|
| `modal_gpu.py` | One-function smoke test: asks for a B200, prints `nvidia-smi` and `torch.cuda` (`modal run training/modal_gpu.py`). |
| `eval_seg.py` | Scores COCO-results predictions against a split. Pure Python + numpy; runs locally and on Modal. |
| `rfdetr_seg.py` | Modal app: upload a dataset, fine-tune RF-DETR-Seg on one GPU, predict the test split, score it; also the "before" run with the stock pretrained weights. |
| `roboflow_upload.py` | Packages a synthetic dataset for Roboflow (SAM 3), uploads it, and turns Roboflow responses back into COCO results. |

The question these answer: which segmentation model handles construction
section drawings, and especially thin parts (membranes, flashings, sheets)?
RF-DETR-Seg (Apache-2.0 `rfdetr`, trained here) and SAM 3 (`sam3-large`,
fine-tuned on Roboflow) are trained on the same synthetic dataset and scored by
the same code on the same held-out test split.

## The dataset

`data/datasets/<name>/` in Roboflow's COCO layout: `manifest.json`, and
`train/`, `valid/`, `test/`, each with its 1008 x 1008 PNG tiles and
`_annotations.coco.json`. Splits are by drawing, never by tile. Categories are
ontology `class_id`s. Each annotation carries `attributes: {thin, min_width_px,
part_id}`; `thin` means narrower than 6 px in the tile, decided from geometry,
never from names. The masks are the parts' filled cut regions.

### Zoom

`tools/make_seg_dataset.py --zoom 2` renders the same drawings at a higher
resolution: the same paper at z x the dpi, so ink, hatches, text and blur all
scale. Every random draw stays the zoom 1 draw. With the same `--seed` and
`--drawings`, a drawing keeps its template, values, damage and layout, and its
sheet is the zoom 1 sheet with every coordinate multiplied by z. Noise and JPEG
stay per pixel. `--drawings` must stay the same, because it fixes the splits;
to make fewer drawings use `--max-train-drawings` and `--max-valid-drawings`,
which skip drawings and never move one.

Tiles stay 1008 px, so each shows less of the drawing. Widths, `thin` and the
scorer's width buckets count pixels of the render, so a 3 px part at zoom 1 is
a 6 px "compact" part at zoom 2. Never compare two zooms by those buckets alone.

A zoomed dataset also drops clipped pieces whose mask has no pixel
(`--drop-empty-masks`): `seg-synth-v1` has 9 of them, which no model can match.
The mask is the one the scorer and training see, the pycocotools rasterisation
of the stored polygon. Only a zoom 1 run without that flag reproduces v1's
bytes, and `tests/test_synthetic.py` pins that.

## Scoring: `eval_seg.py`

```sh
python -m training.eval_seg --gt data/datasets/<name>/test/_annotations.coco.json \
    --pred data/models/<model>/<run_id>/predictions_test.json \
    --out data/models/<model>/<run_id>/metrics_test.json [--score-thr 0.5] [--max-dets 100]
```

Predictions are COCO results (`image_id`, `category_id`, `segmentation`,
`score`). A segmentation can be polygons, an uncompressed RLE or a compressed
RLE. The JSON and a readable `.txt` table hold the following:

- **COCO mask AP** (IoU .50:.05:.95), AP50, AP75 and AR, overall and per class.
  At most `--max-dets` predictions are kept per image and class, by score, as
  in COCOeval.
- **Per instance**, for every ground-truth part: the best mask IoU with a
  same-class prediction, recall at IoU 0.5, and mean and median IoU. These are
  split into thin, compact, and unknown (no `thin` attribute), and by
  `min_width_px` (<2, 2-4, 4-6, >=6 px). They are reported at the operating
  threshold (`--score-thr`) and at any score, which separates "never
  segmented" from "segmented but under-confident".
- **Class-agnostic** (top-level key `class_agnostic`): the same AP, AP50, AP75
  and AR with all classes pooled and the predicted class ignored, capped at
  `--max-dets` per image, plus `recall_iou50` and `mean_iou` as `{thin,
  compact}`, `by_min_width_px`, and `at_score_thr`. `recall_iou50` and
  `mean_iou` count any score, because an untrained model's scores mean nothing
  on drawings. This is the only fair before/after: a pretrained model does not
  know our classes, so its per-class AP is 0 by definition.
- **Confusion**: one-to-one matching at IoU 0.5 that ignores class, among
  predictions at or above the threshold. It reports which classes come back
  under which other class, what is missed, and which predictions match nothing.

Polygons are rasterised exactly as pycocotools does: a port of `rleFrPoly`,
where a pixel is inside when its centre is. RF-DETR rasterises its training
masks the same way. A rasteriser that fills edges inclusively (PIL's
`ImageDraw.polygon`) makes a 2 px part 3 px wide, so the convention matters
most for thin parts. The matching, interpolation and caps follow pycocotools'
COCOeval. `modal run training/rfdetr_seg.py --action parity` checks this
against pycocotools 2.0.11 on random cases: rasterisation, RLE strings, AP,
AP50, AP75, AR, per-class AP, and class-agnostic AP against COCOeval on the
same data folded into one class. Every scored run also writes
`parity_<split>.json` with the same comparison on its real predictions.
`tests/test_eval_seg.py` covers the scorer on hand-made fixtures.

## RF-DETR-Seg on Modal: `rfdetr_seg.py`

```sh
modal run training/rfdetr_seg.py --smoke                          # tiny pillow dataset (data/datasets/_smoke), 1 epoch
modal run --detach training/rfdetr_seg.py --dataset seg-synth-v1  # upload, train, predict, score, download
modal run training/rfdetr_seg.py --action predict --run-id <id>   # predict + score again (e.g. after a timeout)
modal run training/rfdetr_seg.py --action predict --weights pretrained   # the "before" run: no checkpoint of ours
modal run training/rfdetr_seg.py --action score --run-id <id>     # re-score saved predictions, CPU only
modal run training/rfdetr_seg.py --action download --run-id <id>
modal run training/rfdetr_seg.py --action budget                  # GPU seconds, dollars, cost per epoch
```

Add `--weights pretrained` to `score` or `download` of a before run.

The run proceeds in this order:

1. The dataset is checked locally. Its split folders are uploaded to the
   `setout-seg-datasets` Volume, unless a copy with the same fingerprint is
   already there: the fingerprint covers file names and sizes plus every JSON
   file's content. Root files such as `drawings.jsonl` stay local; the
   manifest travels inside the upload marker.
2. The `train` call runs on one GPU (L40S by default). Checkpoints,
   `metrics.csv` and `training_config.json` go to
   `setout-seg-runs/<run_id>/train/`, with one line per validated epoch in
   `progress.jsonl`: time, step speed, peak memory, validation metrics.
3. `predict` (A10 by default) runs the best checkpoint over the test split and
   writes COCO results, with masks as compressed RLE.
4. `score` (CPU) runs `eval_seg`, the pycocotools cross-check, and overlays of
   ground truth beside predictions for the tiles with the most thin parts.
5. Everything is copied to `data/models/rfdetr-seg/<run_id>/`: metrics JSON
   and table, parity, predictions, overlays, `progress.jsonl`, the training
   config, and the best checkpoint.

### Settings and why

The settings below are from rfdetr 1.11.2, read from its source.

- **Model `RFDETRSeg2XLarge`, resolution 1008.** All RF-DETR-Seg sizes share
  the DINOv2-S backbone with 12 px patches (33.6-38.6 M parameters). They
  differ in default resolution (312-768), decoder depth and query count. At a
  fixed resolution the extra cost of 2XLarge is small. Its COCO checkpoint was
  trained at 768, the closest to 1008, so the positional embeddings stretch
  least. It keeps 300 queries for crowded tiles. Resolution must be a multiple
  of 12 x 2 windows = 24, and 1008 is one.
- **No downscaling.** The tiles are 1008 px and so is the model input, so a
  2 px membrane stays 2 px for the backbone. The mask head still builds masks
  from stride-12 features upsampled to stride 4, with no high-resolution image
  path, so parts a few pixels wide are approximated by thresholding a smooth
  logit field. That is a structural limit to keep in mind when reading the thin
  numbers. `mask_downsample_ratio` stays at 4: changing it only resamples the
  same features, and rfdetr flags it as breaking the pretrained head.
- **No scale jitter, no multi-scale.** The default jitter shrinks a tile to
  400-600 px before cropping, which turns a 3 px part into about 1.5 px.
  Multi-scale (888-1128 px at this resolution) resamples every batch.
  Augmentation is rfdetr's default horizontal flip only; the synthetic
  generator already varies the drawings.
- **Batch 8 x gradient accumulation 2.** That is an effective batch of 16, the
  batch rfdetr's default learning rates (1e-4, encoder 1.5e-4) assume, in
  bf16.
- **Learning rate and stopping.** rfdetr's step schedule drops only at epoch
  100, so the learning rate is constant, with EMA weights (decay 0.993). The
  best checkpoint is chosen by validation mask mAP. Early stopping triggers
  after 10 validated epochs without improvement.
- **Time limit.** `RFDETR.train()` builds its own Lightning Trainer, so
  `rfdetr_seg.py` wraps the builder it calls to add PyTorch Lightning's
  `max_time`. Training stops cleanly inside the budget, and `rfdetr` still
  writes `checkpoint_best_total.pth`.
- **Predictions.** The top 100 (query, class) pairs per tile are kept, through
  `num_select=100` (in training too, so validation decodes the same top 100)
  and `predict(threshold=0.0)`. rfdetr's extra no-object slot is dropped.
  `num_select` affects only post-processing.
- **The before run.** `--weights pretrained` predicts with the stock COCO
  checkpoint at the same size and resolution, positional embeddings
  interpolated as they are when training starts. It gets its own run id,
  `pretrained-seg-2xlarge-<stamp>`, and the same files as a trained run, with
  `"weights": "pretrained"` in `predict_summary_<split>.json` (`"trained"` for
  a trained run). Its `category_id`s are COCO class ids plus 1000, so they
  cannot collide with a split's 1..N: per-class scores see none of them and
  `class_agnostic` sees them all. Each row also carries `class_name`.
- **Classes.** rfdetr maps the train split's categories, sorted by id, to
  labels 0..N-1. It drops only an unannotated category whose name another
  category uses as its supercategory (Roboflow's synthetic root), so 19
  categories with supercategory "component" become 19 labels. Predictions are
  mapped back to category ids by name.

### GPUs and budget

GPUs are chosen by cost per job, the owner's rule. Training defaults to an
L40S (48 GB, about 45 GiB usable; batch 8 at 1008 px peaked at 37.0 GiB on the
B200 run) and prediction to an A10. `--gpu` and `--predict-gpu` override them;
H100 and B200 are for when someone asks. If an L40S runs out of memory, use
`--batch-size 4 --grad-accum 4`, the same effective batch.

| GPU | $ per second |
|---|---|
| B200 | 0.001736 |
| H100 | 0.001097 |
| A100-80GB | 0.000694 |
| L40S | 0.000542 |
| A10 | 0.000306 |
| L4 | 0.000222 |

Modal list prices, 30 Oct 2026, in `GPU_PRICE_PER_S`. CPU and memory are billed
on top: they added 20% to the B200 run.

The experiment's limit is 3 B200-hours at list price, $18.75, unless the
coordinator agrees more. Every GPU call is logged in
`data/models/rfdetr-seg/gpu_ledger.json`, under a lock.

- **Charging.** A call is reserved at its hard timeout. It is settled at the
  time measured inside the container plus 180 s for cold start and idle
  scale-down, or at most its local wall time if it failed. Local wall time
  includes queueing, which Modal does not bill.
- **Cost per epoch.** Each entry records `price_per_s` and `cost_usd`. Training
  entries add `epochs`, `epoch_s` (mean seconds of a full epoch, validation
  included) and `cost_per_epoch_usd`, so GPUs can be compared on the same
  dataset. `--action budget` prints them.
- **Training's share.** Training gets whatever the budget has left after a
  20-minute reservation for prediction, as its Lightning `max_time`, plus 12
  minutes of hard-timeout slack for start-up and the final checkpoint. A call
  that cannot fit is refused. `--budget-usd` changes the limit.
- **Actual cost.** `modal billing report --for today --show-resources`. Its
  dollars divided by the price give the billed seconds, which go in an entry's
  `billed_s` by hand and then replace the estimate.

### The image

The image is `debian_slim`, Python 3.12, installed with `uv` and these exact
pins: torch 2.14.1, torchvision 0.29.1, `rfdetr[train]` 1.11.2, pycocotools
2.0.11, pytorch-lightning 2.6.6, torchmetrics 1.8.2, transformers 5.17.0,
supervision 0.30.6, vernier 0.5.4, numpy 2.5.3. The rest is resolved with
`--exclude-newer 2026-10-05`, the day after rfdetr 1.11.2 was published.
Every run records the full package list in `run_config.json`. The COCO
checkpoint (`rf-detr-seg-xxlarge.pt`, from `storage.googleapis.com/rfdetr`,
MD5-checked by rfdetr) is downloaded once at image build into `RF_HOME`.

## SAM 3 on Roboflow: `roboflow_upload.py`

Roboflow training starts only after the owner enables the Roboflow plugin and
provides a key. Nothing here has been uploaded yet. The steps:

1. **Package.** `python training/roboflow_upload.py zip <name>` writes
   `data/roboflow/<name>.zip`, with `train/`, `valid/` and `test/` folders of
   tiles and `_annotations.coco.json`, cut down to standard COCO keys. It
   refuses anything outside `data/datasets/` and any dataset whose
   `manifest.json` does not say `"source": "synthetic"`.
2. **Upload.** Either use `ROBOFLOW_API_KEY=... python
   training/roboflow_upload.py upload <name> --project <slug>`, which creates a
   private instance-segmentation project and uploads the zip with roboflow SDK
   1.7.3, or upload the same zip with the plugin's upload tool. The folders set
   the splits.
3. **Version.** When generating the version, keep the uploaded splits (no
   rebalancing), so the test split stays the one RF-DETR is scored on. Leave
   the tiles at 1008 x 1008: no resize, or "Stretch to 1008x1008", which is a
   no-op. Use no augmentation, or a horizontal flip only to match RF-DETR's
   training.
4. **Train** with model id `sam3-large`, SAM 3's native 1008 x 1008 input on
   polygon labels: Train Model in the app, or `trainings_create(project_id,
   version_number, model_type="sam3-large")` through the plugin. Roboflow's
   own evaluation numbers come from a different evaluator, so do not compare
   them with these.
5. **Predict the local test tiles** (`data/datasets/<name>/test/*.png`, not
   Roboflow's copy, which may drop duplicate images) with the trained model.
   Use a low confidence threshold, about 1%, so AP sees the full ranking. Save
   each response as `<responses_dir>/<tile stem>.json`; the expected keys are
   `predictions[*].points`, `class`, `confidence`, `x`, `y`, `width`,
   `height`.
6. **Score with the same code:**
   ```sh
   python training/roboflow_upload.py predictions <name> <responses_dir> \
       --out data/models/sam3/<run_id>/predictions_test.json
   python -m training.eval_seg --gt data/datasets/<name>/test/_annotations.coco.json \
       --pred data/models/sam3/<run_id>/predictions_test.json \
       --out data/models/sam3/<run_id>/metrics_test.json
   ```
   Tiles map by file name and classes by name. Unknown classes and images
   without a response are counted, not guessed; a missing tile counts as missed
   parts.
