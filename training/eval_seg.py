#!/usr/bin/env python
"""Score instance-segmentation predictions against one COCO split, the same way for every model.

    python -m training.eval_seg --gt data/datasets/seg-synth-v1/test/_annotations.coco.json \\
        --pred data/models/rfdetr-seg/<run_id>/predictions_test.json \\
        --out data/models/rfdetr-seg/<run_id>/metrics_test.json [--score-thr 0.5] [--max-dets 100]

RF-DETR-Seg (training/rfdetr_seg.py) and SAM 3 (Roboflow, see training/README.md) are scored by
this file and nothing else. Predictions are COCO results: a list of
{"image_id", "category_id", "segmentation", "score"}. A segmentation is a polygon list, an
uncompressed RLE ({"size": [h, w], "counts": [ints]}) or a compressed RLE (counts as a string).

What it reports:
- COCO mask AP (IoU .50:.05:.95), AP50, AP75 and AR at max_dets, overall and per class. Matching,
  the 101-point interpolation and the per-image, per-class detection cap follow pycocotools'
  COCOeval, which training/rfdetr_seg.py checks on every scored run.
- For each ground-truth instance, the best mask IoU with a same-class prediction, and recall at IoU
  0.5, split by the annotation's `attributes.thin` flag (thin: narrower than 6 px in the image)
  and by its `attributes.min_width_px` (<2, 2-4, 4-6, >=6 px). Reported at the operating score
  threshold and at any score, to tell "never segmented" from "segmented but under-confident".
- `class_agnostic`: the same AP and per-instance numbers with all classes pooled and the predicted
  class ignored, for models that do not know the split's classes (see `class_agnostic()`).
- A confusion summary at IoU 0.5 and the operating threshold: one-to-one matching that ignores
  class, then which ground-truth classes come back as which predicted class, which are missed, and
  which predictions match nothing.

Polygons are rasterised exactly as pycocotools does (a port of `rleFrPoly`: a pixel is inside when
its centre is), which is also how RF-DETR rasterises its training masks. Another rasteriser moves
edges by a pixel, which is most of a thin part. Pure Python + numpy: no pycocotools, no network.
The JSON goes under data/ (gitignored) or outside the repo; tracked paths are refused.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCORER_VERSION = "eval-seg-v1"
# The same constructions as pycocotools' Params, so threshold comparisons see identical floats.
IOU_THRESHOLDS = np.linspace(0.5, 0.95, int(np.round((0.95 - 0.5) / 0.05)) + 1, endpoint=True)
RECALL_THRESHOLDS = np.linspace(0.0, 1.00, int(np.round((1.00 - 0.0) / 0.01)) + 1, endpoint=True)
MATCH_IOU = 0.5
BUCKETS = ("thin", "compact", "unknown")
# By the annotation's attributes.min_width_px: the thin rule is < 6 px.
WIDTH_BUCKETS = ((0.0, 2.0, "<2"), (2.0, 4.0, "2-4"), (4.0, 6.0, "4-6"), (6.0, float("inf"), ">=6"))
ANY_CLASS = {"id": -1, "name": "any"}  # the one category of the class-agnostic pass


# ---------------------------------------------------------------- RLE (pycocotools maskApi.c)

def rle_encode(mask):
    """Column-major run lengths of a binary mask, starting with a run of zeros (`rleEncode`)."""
    flat = np.asarray(mask, dtype=bool).ravel(order="F")
    if flat.size == 0:
        return [0]
    change = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    counts = np.diff(np.concatenate(([0], change, [flat.size]))).tolist()
    return [0] + counts if flat[0] else counts


def rle_decode(counts, h, w):
    """Binary mask (h, w) from column-major run lengths that start with zeros."""
    counts = np.asarray(counts, dtype=np.int64)
    if (counts < 0).any() or int(counts.sum()) != h * w:
        raise ValueError(f"RLE counts sum to {int(counts.sum())}, expected {h}x{w}")
    values = np.zeros(counts.size, dtype=bool)
    values[1::2] = True
    return np.repeat(values, counts).reshape((w, h)).T


def rle_to_string(counts):
    """Compressed COCO RLE string (`rleToString`: LEB128-like, 6 bits a char, deltas from i-2)."""
    out = []
    for i, count in enumerate(counts):
        x = int(count) - (int(counts[i - 2]) if i > 2 else 0)
        more = True
        while more:
            c = x & 0x1F
            x >>= 5
            more = x != -1 if c & 0x10 else x != 0
            out.append(chr((c | 0x20 if more else c) + 48))
    return "".join(out)


def rle_from_string(text):
    """Run lengths from a compressed COCO RLE string (`rleFrString`)."""
    if isinstance(text, bytes):
        text = text.decode("ascii")
    counts, p = [], 0
    while p < len(text):
        x = k = 0
        while True:
            if p >= len(text):
                raise ValueError("truncated RLE string")
            c = ord(text[p]) - 48
            x |= (c & 0x1F) << (5 * k)
            p += 1
            k += 1
            if not c & 0x20:
                if c & 0x10:
                    x |= -1 << (5 * k)
                break
        if len(counts) > 2:
            x += counts[-2]
        if x < 0:
            raise ValueError("RLE string decodes to a negative run")
        counts.append(x)
    return counts


def _trunc(values):
    """C's (int) cast: truncation toward zero, not floor."""
    return np.trunc(values).astype(np.int64)


def _polygon_boundary(xs, ys):
    """Every upsampled boundary point of a closed polygon, walking each edge start to end."""
    us, vs = [], []
    for j in range(len(xs) - 1):
        x0, x1, y0, y1 = int(xs[j]), int(xs[j + 1]), int(ys[j]), int(ys[j + 1])
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        flip = (dx >= dy and x0 > x1) or (dx < dy and y0 > y1)
        if flip:
            x0, x1, y0, y1 = x1, x0, y1, y0
        if dx >= dy:
            # A zero-length edge gives NaN slope in C; its point never changes column, so it is unused.
            slope = (y1 - y0) / dx if dx else 0.0
            t = np.arange(dx + 1)
            t = dx - t if flip else t
            us.append(t + x0)
            vs.append(_trunc(y0 + slope * t + 0.5))
        else:
            slope = (x1 - x0) / dy
            t = np.arange(dy + 1)
            t = dy - t if flip else t
            vs.append(t + y0)
            us.append(_trunc(x0 + slope * t + 0.5))
    return np.concatenate(us), np.concatenate(vs)


def rle_from_polygon(xy, h, w):
    """Run lengths of one polygon [x1, y1, x2, y2, ...], exactly as pycocotools `rleFrPoly`."""
    scale = 5.0
    pts = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    xs = _trunc(scale * pts[:, 0] + 0.5)
    ys = _trunc(scale * pts[:, 1] + 0.5)
    u, v = _polygon_boundary(np.append(xs, xs[0]), np.append(ys, ys[0]))
    # Column crossings of the boundary, downsampled back to pixel columns.
    j = np.flatnonzero(u[1:] != u[:-1]) + 1
    xd = (np.where(u[j] < u[j - 1], u[j], u[j] - 1) + 0.5) / scale - 0.5
    keep = (np.floor(xd) == xd) & (xd >= 0) & (xd <= w - 1)
    yd = (np.minimum(v[j], v[j - 1]) + 0.5) / scale - 0.5
    yd = np.ceil(np.clip(yd, 0, h))
    starts = np.sort(np.concatenate((xd[keep].astype(np.int64) * h + yd[keep].astype(np.int64), [h * w])))
    diffs = np.diff(np.concatenate(([0], starts))).tolist()
    counts, i = [diffs[0]], 1
    while i < len(diffs):
        if diffs[i] > 0:
            counts.append(diffs[i])
            i += 1
        else:  # two crossings at one pixel cancel: fold the next run into the previous one
            i += 1
            if i < len(diffs):
                counts[-1] += diffs[i]
                i += 1
    return counts


# ---------------------------------------------------------------- masks and regions

def segmentation_mask(segm, h, w, notes=None):
    """Binary mask of a COCO segmentation: polygons (unioned), uncompressed or compressed RLE.

    Polygons with fewer than three points are skipped and counted in `notes`.
    """
    if isinstance(segm, dict):
        size = [int(s) for s in segm.get("size", ())]
        if size != [h, w]:
            raise ValueError(f"RLE size {size} does not match the image {h}x{w}")
        counts = segm["counts"]
        if isinstance(counts, (str, bytes)):
            counts = rle_from_string(counts)
        return rle_decode(counts, h, w)
    mask = np.zeros((h, w), dtype=bool)
    for poly in segm or []:
        if len(poly) < 6 or len(poly) % 2:
            if notes is not None:
                notes["degenerate_polygons"] += 1
            continue
        mask |= rle_decode(rle_from_polygon(poly, h, w), h, w)
    return mask


@dataclass(frozen=True)
class Region:
    """A mask cropped to its bounding box: crop covers rows y0.., columns x0.. of the image."""

    y0: int
    x0: int
    crop: np.ndarray
    area: int


def region(mask):
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return Region(0, 0, np.zeros((0, 0), dtype=bool), 0)
    cols = np.flatnonzero(mask.any(axis=0))
    crop = mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1].copy()
    return Region(int(rows[0]), int(cols[0]), crop, int(crop.sum()))


def intersection(a, b):
    y0, x0 = max(a.y0, b.y0), max(a.x0, b.x0)
    y1 = min(a.y0 + a.crop.shape[0], b.y0 + b.crop.shape[0])
    x1 = min(a.x0 + a.crop.shape[1], b.x0 + b.crop.shape[1])
    if y1 <= y0 or x1 <= x0:
        return 0
    wa = a.crop[y0 - a.y0:y1 - a.y0, x0 - a.x0:x1 - a.x0]
    wb = b.crop[y0 - b.y0:y1 - b.y0, x0 - b.x0:x1 - b.x0]
    return int(np.count_nonzero(wa & wb))


def iou_matrix(dts, gts, crowd):
    """IoU of every prediction (rows) with every ground truth (columns), as `rleIou`.

    For a crowd ground truth the union is the prediction's own area.
    """
    out = np.zeros((len(dts), len(gts)))
    for d, dt in enumerate(dts):
        for g, gt in enumerate(gts):
            inter = intersection(dt, gt)
            if inter:
                union = dt.area if crowd[g] else dt.area + gt.area - inter
                out[d, g] = inter / union
    return out


# ---------------------------------------------------------------- loading

def _bucket(ann):
    thin = (ann.get("attributes") or {}).get("thin")
    return "unknown" if thin is None else ("thin" if thin else "compact")


def _width_bucket(ann):
    """The WIDTH_BUCKETS label of the annotation's min_width_px, or None when it has none."""
    width = (ann.get("attributes") or {}).get("min_width_px")
    if not isinstance(width, (int, float)) or isinstance(width, bool) or width < 0:
        return None
    return next(label for lo, hi, label in WIDTH_BUCKETS if lo <= width < hi)


def predictions_by_image(gt, preds):
    """Each image's predictions in file order. Unknown image ids raise, as pycocotools' loadRes does."""
    image_ids = {img["id"] for img in gt["images"]}
    grouped = defaultdict(list)
    for pred in preds:
        if pred["image_id"] not in image_ids:
            raise ValueError(f"prediction for image_id {pred['image_id']} not in the ground truth")
        grouped[pred["image_id"]].append(pred)
    return grouped


def _ranked(preds, indices):
    """Indices by descending score: a stable sort, so ties keep file order, as COCOeval's mergesort."""
    return sorted(indices, key=lambda i: -float(preds[i]["score"]))


def select_predictions(preds, category_ids, max_dets, notes):
    """Which of one image's predictions each pass scores, as two lists of indices in file order.

    Per class: known categories only (unknown ids are counted and dropped), at most max_dets per
    category. Class-agnostic: every prediction, at most max_dets for the image.
    """
    by_category = defaultdict(list)
    for i, pred in enumerate(preds):
        if pred["category_id"] in category_ids:
            by_category[pred["category_id"]].append(i)
        else:
            notes["unknown_category_predictions"] += 1
    per_class = []
    for indices in by_category.values():
        ranked = _ranked(preds, indices)
        if len(ranked) > max_dets:
            notes["capped_predictions"] += len(ranked) - max_dets
        per_class.extend(ranked[:max_dets])
    return sorted(per_class), sorted(_ranked(preds, range(len(preds)))[:max_dets])


@dataclass
class ImageEval:
    """Everything the metrics need from one image: categories, scores and the IoU matrix."""

    gt_cats: list
    gt_crowd: list
    gt_buckets: list
    gt_widths: list
    dt_cats: list
    dt_scores: list
    ious: np.ndarray  # predictions x ground truths


def evaluate_image(img, anns, preds, per_class, agnostic, notes):
    """The per-class and class-agnostic views of one image; each mask is decoded once."""
    h, w = int(img["height"]), int(img["width"])
    gts = []
    for ann in anns:
        reg = region(segmentation_mask(ann["segmentation"], h, w, notes))
        if reg.area == 0:
            notes["empty_gt_masks"] += 1
        gts.append(reg)
    used = sorted(set(per_class) | set(agnostic))
    dts = [region(segmentation_mask(preds[i]["segmentation"], h, w, notes)) for i in used]
    crowd = [bool(a.get("iscrowd", 0)) for a in anns]
    ious = iou_matrix(dts, gts, crowd)
    row_of = {i: row for row, i in enumerate(used)}

    def view(indices, gt_cats, dt_cats):
        return ImageEval(
            gt_cats=gt_cats,
            gt_crowd=crowd,
            gt_buckets=[_bucket(a) for a in anns],
            gt_widths=[_width_bucket(a) for a in anns],
            dt_cats=dt_cats,
            dt_scores=[float(preds[i]["score"]) for i in indices],
            ious=ious[[row_of[i] for i in indices]],
        )

    return (view(per_class, [a["category_id"] for a in anns], [preds[i]["category_id"] for i in per_class]),
            view(agnostic, [ANY_CLASS["id"]] * len(anns), [ANY_CLASS["id"]] * len(agnostic)))


# ---------------------------------------------------------------- COCO AP (pycocotools COCOeval)

def match_category(ev, cat, max_dets):
    """COCOeval.evaluateImg for one image and category, area range 'all'.

    Returns (scores, dt_matched T x D, dt_ignored T x D, gt_ignored G), or None when the image
    has neither ground truth nor predictions of this category.
    """
    g_idx = [g for g, c in enumerate(ev.gt_cats) if c == cat]
    d_idx = [d for d, c in enumerate(ev.dt_cats) if c == cat]
    if not g_idx and not d_idx:
        return None
    g_idx = [g_idx[i] for i in np.argsort([ev.gt_crowd[g] for g in g_idx], kind="mergesort")]
    d_idx = [d_idx[i] for i in np.argsort([-ev.dt_scores[d] for d in d_idx], kind="mergesort")][:max_dets]
    ious = ev.ious[np.ix_(d_idx, g_idx)]
    ignored = np.array([ev.gt_crowd[g] for g in g_idx], dtype=bool)
    crowd = ignored  # crowd is the only ignore reason for the 'all' area range
    T, D, G = len(IOU_THRESHOLDS), len(d_idx), len(g_idx)
    gtm = np.zeros((T, G), dtype=bool)
    dtm = np.zeros((T, D), dtype=bool)
    dt_ig = np.zeros((T, D), dtype=bool)
    for t, thr in enumerate(IOU_THRESHOLDS):
        for d in range(D):
            best, m = min(thr, 1 - 1e-10), -1
            for g in range(G):
                if gtm[t, g] and not crowd[g]:
                    continue
                if m > -1 and not ignored[m] and ignored[g]:
                    break
                if ious[d, g] < best:
                    continue
                best, m = ious[d, g], g
            if m == -1:
                continue
            dt_ig[t, d] = ignored[m]
            dtm[t, d] = True
            gtm[t, m] = True
    return np.array([ev.dt_scores[d] for d in d_idx]), dtm, dt_ig, ignored


def accumulate(per_image, max_dets):
    """COCOeval.accumulate for one category: precision (T x R) and recall (T), -1 when no ground truth."""
    T, R = len(IOU_THRESHOLDS), len(RECALL_THRESHOLDS)
    precision, recall = -np.ones((T, R)), -np.ones(T)
    if not per_image:
        return precision, recall
    scores = np.concatenate([e[0][:max_dets] for e in per_image])
    order = np.argsort(-scores, kind="mergesort")
    dtm = np.concatenate([e[1][:, :max_dets] for e in per_image], axis=1)[:, order]
    dt_ig = np.concatenate([e[2][:, :max_dets] for e in per_image], axis=1)[:, order]
    npig = int(np.count_nonzero(~np.concatenate([e[3] for e in per_image])))
    if npig == 0:
        return precision, recall
    tp_sum = np.cumsum(dtm & ~dt_ig, axis=1).astype(float)
    fp_sum = np.cumsum(~dtm & ~dt_ig, axis=1).astype(float)
    for t in range(T):
        tp, fp = tp_sum[t], fp_sum[t]
        nd = len(tp)
        rc = tp / npig
        pr = tp / (fp + tp + np.spacing(1))
        recall[t] = rc[-1] if nd else 0
        pr = np.maximum.accumulate(pr[::-1])[::-1] if nd else pr
        idx = np.searchsorted(rc, RECALL_THRESHOLDS, side="left")
        precision[t] = [pr[i] if i < nd else 0.0 for i in idx]
    return precision, recall


def _mean_valid(values):
    values = np.asarray(values, dtype=float)
    valid = values[values > -1]
    return float(valid.mean()) if valid.size else None


def coco_metrics(evals, categories, max_dets):
    """Overall and per-category AP / AP50 / AP75 / AR, images taken in ascending id order."""
    precision, recall = {}, {}
    for cat in categories:
        per_image = [r for r in (match_category(evals[i], cat["id"], max_dets) for i in sorted(evals))
                     if r is not None]
        precision[cat["id"]], recall[cat["id"]] = accumulate(per_image, max_dets)
    all_p = np.stack([precision[c["id"]] for c in categories], axis=-1) if categories else np.empty((0,))
    all_r = np.stack([recall[c["id"]] for c in categories], axis=-1) if categories else np.empty((0,))
    overall = {
        "AP": _mean_valid(all_p),
        "AP50": _mean_valid(all_p[0]) if categories else None,
        "AP75": _mean_valid(all_p[5]) if categories else None,
        f"AR{max_dets}": _mean_valid(all_r),
    }
    per_class = {
        cat["name"]: {
            "AP": _mean_valid(precision[cat["id"]]),
            "AP50": _mean_valid(precision[cat["id"]][0]),
            "AP75": _mean_valid(precision[cat["id"]][5]),
            f"AR{max_dets}": _mean_valid(recall[cat["id"]]),
        }
        for cat in categories
    }
    return overall, per_class


# ---------------------------------------------------------------- per-instance and confusion

def _summary(ious):
    ious = np.asarray(ious, dtype=float)
    if ious.size == 0:
        return {"n": 0, "recall50": None, "mean_iou": None, "median_iou": None}
    return {
        "n": int(ious.size),
        "recall50": float(np.mean(ious >= MATCH_IOU)),
        "mean_iou": float(ious.mean()),
        "median_iou": float(np.median(ious)),
    }


def instance_metrics(evals, names, score_thr=None):
    """Best same-class mask IoU for every non-crowd ground truth.

    Only predictions scoring >= score_thr count; score_thr None counts every prediction.
    """
    best = defaultdict(list)  # (class name, bucket) -> best IoUs
    by_width = defaultdict(list)  # min_width_px bucket -> best IoUs
    for ev in evals.values():
        for g, cat in enumerate(ev.gt_cats):
            if ev.gt_crowd[g]:
                continue
            same = [d for d, c in enumerate(ev.dt_cats)
                    if c == cat and (score_thr is None or ev.dt_scores[d] >= score_thr)]
            iou = float(ev.ious[same, g].max()) if same else 0.0
            best[names[cat], ev.gt_buckets[g]].append(iou)
            if ev.gt_widths[g] is not None:
                by_width[ev.gt_widths[g]].append(iou)
    pooled = {b: [v for (_, bucket), vals in best.items() if bucket == b for v in vals] for b in BUCKETS}
    out = {"score_thr": score_thr, "all": _summary([v for vals in best.values() for v in vals])}
    out.update({b: _summary(pooled[b]) for b in BUCKETS if pooled[b]})
    out["by_min_width_px"] = {label: _summary(by_width[label]) for _, _, label in WIDTH_BUCKETS
                              if label in by_width}
    out["per_class"] = {
        name: {b: _summary(best[name, b]) for b in BUCKETS if (name, b) in best}
        for name in sorted({name for name, _ in best})
    }
    return out


def class_agnostic(evals, score_thr, max_dets):
    """COCO AP and per-instance metrics with every class pooled into one.

    This is the only fair number for a model that does not know the split's classes, such as the
    pretrained checkpoint. `evals` are the class-agnostic views: all predictions, whatever their
    category id, capped at max_dets per image. recall_iou50 and mean_iou count any score, because
    an untrained model's scores are not calibrated on drawings; at_score_thr has the same at the
    operating threshold.
    """
    overall, _ = coco_metrics(evals, [ANY_CLASS], max_dets)
    names = {ANY_CLASS["id"]: ANY_CLASS["name"]}
    any_score, at_thr = instance_metrics(evals, names), instance_metrics(evals, names, score_thr)

    def pick(inst, key):
        return {b: inst[b][key] if b in inst else None for b in ("thin", "compact")}

    return {
        **overall,
        "recall_iou50": pick(any_score, "recall50"),
        "mean_iou": pick(any_score, "mean_iou"),
        "by_min_width_px": any_score["by_min_width_px"],
        "at_score_thr": {"score_thr": score_thr, "recall_iou50": pick(at_thr, "recall50"),
                         "mean_iou": pick(at_thr, "mean_iou")},
    }


def confusion(evals, names, score_thr):
    """One-to-one class-agnostic matching at IoU >= 0.5 among predictions >= score_thr.

    Pairs are taken by IoU, then score: a query reported under two classes with the same mask
    matches its higher-scoring class. Unmatched ground truth is "missed"; an unmatched prediction
    is a false positive ("background" row).
    """
    pairs = Counter()
    for ev in evals.values():
        keep_d = [d for d, s in enumerate(ev.dt_scores) if s >= score_thr]
        keep_g = [g for g in range(len(ev.gt_cats)) if not ev.gt_crowd[g]]
        cand = sorted(
            ((ev.ious[d, g], ev.dt_scores[d], d, g) for d in keep_d for g in keep_g
             if ev.ious[d, g] >= MATCH_IOU),
            key=lambda c: (-c[0], -c[1], c[2], c[3]),
        )
        used_d, used_g = set(), set()
        for _, _, d, g in cand:
            if d in used_d or g in used_g:
                continue
            used_d.add(d)
            used_g.add(g)
            pairs[names[ev.gt_cats[g]], names[ev.dt_cats[d]]] += 1
        for g in keep_g:
            if g not in used_g:
                pairs[names[ev.gt_cats[g]], "missed"] += 1
        for d in keep_d:
            if d not in used_d:
                pairs["background", names[ev.dt_cats[d]]] += 1
    gt_names = sorted({gt for gt, _ in pairs if gt != "background"})
    totals = {gt: sum(n for (g, _), n in pairs.items() if g == gt) for gt in gt_names}
    swaps = sorted(
        ({"gt": gt, "pred": pred, "count": n, "share_of_gt": n / totals[gt]}
         for (gt, pred), n in pairs.items() if gt != "background" and pred not in (gt, "missed")),
        key=lambda s: (-s["count"], s["gt"], s["pred"]),
    )
    per_class = {
        gt: {
            "n_gt": totals[gt],
            "correct": pairs[gt, gt],
            "swapped": sum(n for (g, p), n in pairs.items() if g == gt and p not in (gt, "missed")),
            "missed": pairs[gt, "missed"],
        }
        for gt in gt_names
    }
    false_pos = {pred: n for (gt, pred), n in sorted(pairs.items()) if gt == "background"}
    return {
        "score_thr": score_thr,
        "iou": MATCH_IOU,
        "pairs": [{"gt": gt, "pred": pred, "count": n} for (gt, pred), n in sorted(pairs.items())],
        "swaps": swaps,
        "per_class": per_class,
        "false_positives": false_pos,
    }


# ---------------------------------------------------------------- scoring entry points

def check_ground_truth(gt):
    """Every annotation must point at a listed image and category, and class names must be unique."""
    image_ids = {img["id"] for img in gt["images"]}
    category_ids = {cat["id"] for cat in gt["categories"]}
    names = [cat["name"] for cat in gt["categories"]]
    if len(set(names)) != len(names):
        raise ValueError("category names must be unique")
    for ann in gt["annotations"]:
        if ann["image_id"] not in image_ids or ann["category_id"] not in category_ids:
            raise ValueError(f"annotation {ann.get('id')} points at an unknown image or category")


def evaluate(gt, preds, score_thr=0.5, max_dets=100):
    """Score COCO-results predictions against a COCO ground-truth dict. Returns a JSON-able dict."""
    check_ground_truth(gt)
    notes = Counter()
    categories = sorted(gt["categories"], key=lambda c: c["id"])
    names = {c["id"]: c["name"] for c in categories}
    anns_by_image = defaultdict(list)
    for ann in gt["annotations"]:
        anns_by_image[ann["image_id"]].append(ann)
    preds_by_image = predictions_by_image(gt, preds)
    evals, agnostic, scored = {}, {}, 0
    for img in sorted(gt["images"], key=lambda i: i["id"]):
        image_preds = preds_by_image.get(img["id"], [])
        per_class_idx, agnostic_idx = select_predictions(image_preds, set(names), max_dets, notes)
        scored += len(per_class_idx)
        evals[img["id"]], agnostic[img["id"]] = evaluate_image(
            img, anns_by_image[img["id"]], image_preds, per_class_idx, agnostic_idx, notes)
    overall, per_class = coco_metrics(evals, categories, max_dets)
    gt_counts = Counter((names[a["category_id"]], _bucket(a)) for a in gt["annotations"] if not a.get("iscrowd"))
    for cat in categories:
        per_class[cat["name"]].update({
            "category_id": cat["id"],
            "n_gt": sum(gt_counts[cat["name"], b] for b in BUCKETS),
            "n_gt_thin": gt_counts[cat["name"], "thin"],
        })
    return {
        "scorer": SCORER_VERSION,
        "params": {
            "iou_thresholds": [round(float(t), 2) for t in IOU_THRESHOLDS],
            "max_dets": max_dets,
            "score_thr": score_thr,
            "match_iou": MATCH_IOU,
            "rasteriser": "pycocotools rleFrPoly port (pixel centre inside the polygon)",
        },
        "counts": {
            "images": len(evals),
            "gt_instances": sum(len(v) for v in anns_by_image.values()),
            "predictions": len(preds),
            "predictions_scored": scored,
        },
        "coco": overall,
        "per_class": per_class,
        "instances": instance_metrics(evals, names, score_thr),
        "instances_any_score": instance_metrics(evals, names, None),
        "class_agnostic": class_agnostic(agnostic, score_thr, max_dets),
        "confusion": confusion(evals, names, score_thr),
        "notes": dict(notes),
    }


def _fmt(value, width=7, digits=3):
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.{digits}f}"


def format_table(result):
    """A short readable summary of `evaluate`'s result."""
    c, p, inst = result["coco"], result["params"], result["instances"]
    any_score, ca = result["instances_any_score"], result["class_agnostic"]
    ar = next(k for k in c if k.startswith("AR"))
    lines = [
        f"mask AP {_fmt(c['AP'], 0)}  AP50 {_fmt(c['AP50'], 0)}  AP75 {_fmt(c['AP75'], 0)}  "
        f"{ar} {_fmt(c[ar], 0)}   ({result['counts']['images']} images, "
        f"{result['counts']['gt_instances']} instances, {result['counts']['predictions']} predictions)",
        f"class-agnostic AP {_fmt(ca['AP'], 0)}  AP50 {_fmt(ca['AP50'], 0)}  any score: "
        f"rec@.5 thin {_fmt(ca['recall_iou50']['thin'], 0)} compact {_fmt(ca['recall_iou50']['compact'], 0)}, "
        f"mean IoU thin {_fmt(ca['mean_iou']['thin'], 0)} compact {_fmt(ca['mean_iou']['compact'], 0)}",
        "",
        f"per instance: best same-class mask IoU   score >= {p['score_thr']:.2f}        any score",
        f"  {'':<10}{'n':>6}  {'rec@.5':>7}{'mean':>7}{'median':>7}   {'rec@.5':>7}{'mean':>7}",
    ]
    rows = [(bucket, inst[bucket], any_score[bucket]) for bucket in ("all",) + BUCKETS if bucket in inst]
    rows += [(f"w {label} px", row, any_score["by_min_width_px"][label])
             for label, row in inst["by_min_width_px"].items()]
    for label, a, b in rows:
        lines.append(f"  {label:<10}{a['n']:>6}  {_fmt(a['recall50'])}{_fmt(a['mean_iou'])}"
                     f"{_fmt(a['median_iou'])}   {_fmt(b['recall50'])}{_fmt(b['mean_iou'])}")
    width = max([len(n) for n in result["per_class"]] + [5])
    lines += ["", f"  {'class':<{width}}  {'n_gt':>5} {'thin':>5}  {'AP':>6} {'AP50':>6}"
                  f"  {'rec@.5 compact':>14} {'rec@.5 thin':>11}"]
    for name, row in sorted(result["per_class"].items()):
        per = inst["per_class"].get(name, {})
        comp, thin = per.get("compact", {}).get("recall50"), per.get("thin", {}).get("recall50")
        lines.append(f"  {name:<{width}}  {row['n_gt']:>5} {row['n_gt_thin']:>5}  {_fmt(row['AP'], 6)} "
                     f"{_fmt(row['AP50'], 6)}  {_fmt(comp, 14)} {_fmt(thin, 11)}")
    conf = result["confusion"]
    lines += ["", f"swapped classes (IoU >= {conf['iou']}, score >= {conf['score_thr']:.2f}, gt -> predicted):"]
    lines += [f"  {s['gt']} -> {s['pred']}: {s['count']} ({s['share_of_gt']:.0%} of {s['gt']})"
              for s in conf["swaps"][:10]] or ["  none"]
    if result["notes"]:
        lines += ["", "notes: " + ", ".join(f"{k}={v}" for k, v in sorted(result["notes"].items()))]
    return "\n".join(lines)


def write_report(result, out):
    """Write the JSON result and, next to it, the readable table (.txt)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    out.with_suffix(".txt").write_text(format_table(result) + "\n")


def allowed_output(out):
    """Outside the repo, or under data/: never a tracked path."""
    out = Path(out).resolve()
    data = (ROOT / "data").resolve()
    return ROOT not in out.parents or out == data or data in out.parents


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gt", required=True, help="a split's _annotations.coco.json")
    ap.add_argument("--pred", required=True, help="COCO results JSON (list of predictions)")
    ap.add_argument("--out", required=True, help="metrics JSON; the table goes next to it as .txt")
    ap.add_argument("--score-thr", type=float, default=0.5, help="operating threshold for per-instance metrics")
    ap.add_argument("--max-dets", type=int, default=100, help="predictions kept per image and class")
    args = ap.parse_args(argv)
    if not allowed_output(args.out):
        print(f"refusing to write {args.out}: use data/ or a path outside the repo", file=sys.stderr)
        return 2
    gt = json.loads(Path(args.gt).read_text())
    preds = json.loads(Path(args.pred).read_text())
    result = evaluate(gt, preds, score_thr=args.score_thr, max_dets=args.max_dets)
    result["files"] = {"gt": str(args.gt), "pred": str(args.pred)}
    write_report(result, args.out)
    print(format_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
