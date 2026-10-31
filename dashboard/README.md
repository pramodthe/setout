# dashboard

One local HTML page that shows what a segmentation training run is doing now,
how it went, and test scores before and after training.

```sh
python dashboard/build.py                # pull from the Modal Volume, build once
python dashboard/build.py --watch        # pull and rebuild until stopped (--every 60)
python dashboard/build.py --no-pull      # local files only, no Modal calls
open data/dashboard/index.html
```

The page has no server, CDN or external script, so it opens from `file://` and
works offline. Images sit next to it with relative paths. While a run is live it
carries a meta refresh.

## Where the output goes

Output goes to `data/dashboard/` (gitignored): `index.html`, `images/`, and
`cache/` for what was pulled from the Volume. These pages will one day show the
owner's private drawings, so `build.py` refuses any other path inside a repo.
`--out` may point outside any repo.

## What it reads

Everything is read-only. It never starts, stops or changes a Modal run.

| Input | Used for |
|---|---|
| `data/models/*/gpu_ledger.json` | GPU attempts, queue waits, cost, the stage |
| `data/models/<model>/<run_id>/` | `progress.jsonl`, `run_config.json`, `train_summary.json`, `metrics_test.json`, `predict_summary_test.json`, `parity_test.json`, `predictions_test.json` |
| Volume `setout-seg-runs` | The same files, for a run with no local copy yet (`modal volume ls` / `get`) |
| `modal container list` | Tells a queued call from a running one |
| `data/datasets/<name>/` | `manifest.json`, `test/_annotations.coco.json`, the tiles |

A local copy wins over the Volume, except `progress.jsonl` while a run trains.
Mask decoding comes from `training/eval_seg.py`, so the page and the scorer agree.

## Files

| File | What it does |
|---|---|
| `build.py` | The CLI, the output guard, one build from start to end |
| `runs.py` | Ledger, run files, stage, pass estimate, cost, Modal CLI calls, the price table |
| `tiles.py` | Picks the test tiles, gives each class its look, paints the panels with Pillow |
| `charts.py` | Inline SVG marks |
| `page.py`, `page.css`, `page.js` | The page; CSS and JS are inlined at build time |

## Rules it keeps

- Anything not run yet shows as "not run yet", never as 0, and is never estimated.
  Time left and "pass N of about M" are the only estimates, and they say so.
- Validation numbers only appear under a validation label. Test numbers only
  come from a run's `metrics_test.json`. `loss` is a training number, because
  the runs log no validation loss.
- A log can end with a one-batch stub: when the time cap trips, the current epoch
  stops after a batch and still validates. It trained nothing, so it is not
  counted as a pass and is not charted.
- If the Volume or the ledger cannot be read, the page shows the last known data
  and says so.
- Every model is drawn by the same rule: its strongest masks, as many as the tile
  has correct parts. A score cut-off is not fair, because an untrained model's
  scores are not calibrated on drawings. Its COCO labels are not our classes, so
  those masks are outlined and stay out of the class legend.

## Costs

Run costs are GPU only: `billed_s` from the ledger when present, else measured
seconds, at the list prices in `runs.GPU_PRICE_PER_S` (with their check date). A
GPU that is not in the table shows as "not known", never as $0. Modal bills CPU
and memory on top. The ledger does not hold that, so the all-in figures live in
`runs.MODAL_BILL` with their date and source. Update it by hand from a new bill.

## Adding a model

No code changes. Put a run under `data/models/<model>/<run_id>/` in the same
layout, with `"weights": "pretrained"` or `"trained"` in
`predict_summary_test.json`. Before and after come from `weights`; runs group by
the `<model>` folder, and by dataset once a model has runs on more than one, so
a stock run is never set against a trained run on another dataset. The images use
the test split of the newest scored run's dataset. `runs.MODEL_NAMES` only gives
the folder a display name.
`runs.PLANNED_MODELS` lists models that show as "not run yet" before they have
run.

## Tests

`python -m pytest tests/test_dashboard.py -q`. Fixtures are built inside the
tests, with no network.
