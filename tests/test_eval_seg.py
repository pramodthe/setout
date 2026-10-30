"""The model-agnostic segmentation scorer on tiny COCO fixtures built inside the test: no files, no network.

Also what surrounds it, with no GPU, torch or rfdetr: the Roboflow side (training/roboflow_upload.py),
what may be packaged and how SAM 3's responses come back as COCO results; and from
training/rfdetr_seg.py the GPU ledger and budget, and how model outputs become COCO results.
"""
import json
import sys
import types

import numpy as np
import pytest

from training import eval_seg, rfdetr_seg, roboflow_upload

H = W = 40
CATEGORIES = [
    {"id": 1, "name": "block", "supercategory": "component"},
    {"id": 2, "name": "strip", "supercategory": "component"},
    {"id": 3, "name": "plate", "supercategory": "component"},
]


def rect(x0, y0, x1, y1):
    """A closed rectangle polygon in pixel coordinates; pycocotools fills [x0, x1) x [y0, y1)."""
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def ann(ann_id, image_id, category_id, box, thin):
    x0, y0, x1, y1 = box
    return {
        "id": ann_id, "image_id": image_id, "category_id": category_id,
        "segmentation": [rect(*box)], "bbox": [x0, y0, x1 - x0, y1 - y0],
        "area": (x1 - x0) * (y1 - y0), "iscrowd": 0,
        "attributes": {"thin": thin, "min_width_px": float(min(x1 - x0, y1 - y0)), "part_id": f"p{ann_id}"},
    }


def ground_truth(*annotations, images=(1, 2)):
    return {
        "images": [{"id": i, "file_name": f"t{i}.png", "width": W, "height": H} for i in images],
        "categories": CATEGORIES,
        "annotations": list(annotations),
    }


def pred(image_id, category_id, box, score=0.9):
    return {"image_id": image_id, "category_id": category_id, "segmentation": [rect(*box)], "score": score}


def fixture():
    """Two images; compact blocks and plates, a 2 px and a 1 px strip."""
    return ground_truth(
        ann(1, 1, 1, (5, 5, 15, 15), False),
        ann(2, 1, 2, (20, 5, 22, 30), True),
        ann(3, 2, 1, (2, 2, 12, 12), False),
        ann(4, 2, 3, (20, 20, 35, 30), False),
        ann(5, 2, 2, (5, 33, 35, 34), True),
    )


def test_rasterises_like_pycocotools():
    """Known answers traced through pycocotools' rleFrPoly: a pixel is in when its centre is."""
    mask = eval_seg.rle_decode(eval_seg.rle_from_polygon(rect(2, 1, 5, 4), 6, 6), 6, 6)
    assert eval_seg.rle_from_polygon(rect(2, 1, 5, 4), 6, 6) == [13, 3, 3, 3, 3, 3, 8]
    assert np.argwhere(mask).tolist() == [[y, x] for y in (1, 2, 3) for x in (2, 3, 4)]
    one_px = eval_seg.rle_decode(eval_seg.rle_from_polygon(rect(2, 1, 3, 5), 6, 6), 6, 6)
    assert np.argwhere(one_px).tolist() == [[1, 2], [2, 2], [3, 2], [4, 2]]
    over_centre = eval_seg.rle_from_polygon(rect(2.2, 1, 2.6, 5), 6, 6)
    off_centre = eval_seg.rle_from_polygon(rect(2.0, 1, 2.4, 5), 6, 6)
    assert eval_seg.rle_decode(over_centre, 6, 6).sum() == 4
    assert eval_seg.rle_decode(off_centre, 6, 6).sum() == 0
    outside = eval_seg.rle_from_polygon(rect(-9, -9, -2, -2), 6, 6)
    assert outside == [36]


def test_rle_string_codec():
    assert eval_seg.rle_to_string([1, 3]) == "13"
    assert eval_seg.rle_to_string([100]) == "T3"
    assert eval_seg.rle_to_string([5, 3, 7, 2]) == "537O"  # the fourth run is stored as -1 from the second
    assert eval_seg.rle_from_string(b"537O") == [5, 3, 7, 2]
    rng = np.random.default_rng(0)
    for _ in range(100):
        h, w = (int(v) for v in rng.integers(1, 30, 2))
        mask = rng.random((h, w)) < rng.random()
        counts = eval_seg.rle_encode(mask)
        assert eval_seg.rle_decode(eval_seg.rle_from_string(eval_seg.rle_to_string(counts)), h, w).tolist() \
            == mask.tolist()
    with pytest.raises(ValueError):
        eval_seg.rle_decode([3, 2], 2, 2)


def as_rle(gt, a):
    mask = eval_seg.segmentation_mask(a["segmentation"], H, W)
    return {"size": [H, W], "counts": eval_seg.rle_to_string(eval_seg.rle_encode(mask))}


@pytest.mark.parametrize("form", ["polygon", "rle"])
def test_perfect_predictions_score_one(form):
    gt = fixture()
    preds = [{"image_id": a["image_id"], "category_id": a["category_id"], "score": 0.9,
              "segmentation": a["segmentation"] if form == "polygon" else as_rle(gt, a)}
             for a in gt["annotations"]]
    result = eval_seg.evaluate(gt, preds)
    # Precision is tp / (tp + fp + eps), as in pycocotools, so "1" is 1 - 2e-16.
    assert result["coco"] == pytest.approx({"AP": 1.0, "AP50": 1.0, "AP75": 1.0, "AR100": 1.0})
    assert {name: row["AP"] for name, row in result["per_class"].items()} == \
        pytest.approx({"block": 1.0, "strip": 1.0, "plate": 1.0})
    for bucket in ("all", "thin", "compact"):
        assert result["instances"][bucket]["recall50"] == 1.0
        assert result["instances"][bucket]["mean_iou"] == 1.0
    assert result["instances"]["thin"]["n"] == 2 and result["instances"]["compact"]["n"] == 3
    assert result["confusion"]["swaps"] == [] and result["confusion"]["false_positives"] == {}
    assert all(row["missed"] == 0 for row in result["confusion"]["per_class"].values())
    assert result["notes"] == {}


def test_shifted_mask_lowers_iou_and_ap():
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), images=(1,))
    result = eval_seg.evaluate(gt, [pred(1, 1, (12, 10, 22, 20))])
    iou = 80 / 120  # 8 of 10 columns overlap
    assert result["instances"]["compact"]["mean_iou"] == pytest.approx(iou)
    assert result["instances"]["compact"]["recall50"] == 1.0
    # A true positive at IoU 0.50, 0.55, 0.60 and 0.65; a miss at 0.70 and above.
    assert result["coco"]["AP"] == pytest.approx(0.4)
    assert result["coco"]["AP50"] == pytest.approx(1.0) and result["coco"]["AP75"] == 0.0


def test_wrong_class_counts_as_confusion():
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), images=(1,))
    result = eval_seg.evaluate(gt, [pred(1, 3, (10, 10, 20, 20))])
    assert result["per_class"]["block"]["AP"] == 0.0
    assert result["per_class"]["plate"]["AP"] is None  # no plate in this split
    assert result["coco"]["AP"] == 0.0
    assert result["instances"]["all"]["recall50"] == 0.0
    assert result["instances_any_score"]["all"]["mean_iou"] == 0.0  # class-aware: the plate does not count
    conf = result["confusion"]
    assert conf["swaps"] == [{"gt": "block", "pred": "plate", "count": 1, "share_of_gt": 1.0}]
    assert conf["per_class"]["block"] == {"n_gt": 1, "correct": 0, "swapped": 1, "missed": 0}
    assert conf["false_positives"] == {}


def test_thin_and_compact_reported_separately():
    gt = ground_truth(ann(1, 1, 1, (2, 2, 12, 12), False), ann(2, 1, 2, (20, 2, 22, 30), True), images=(1,))
    preds = [pred(1, 1, (2, 2, 12, 12)), pred(1, 2, (18, 2, 24, 30))]  # the strip comes back 6 px wide
    inst = eval_seg.evaluate(gt, preds)["instances"]
    assert inst["compact"] == {"n": 1, "recall50": 1.0, "mean_iou": 1.0, "median_iou": 1.0}
    assert inst["thin"]["n"] == 1 and inst["thin"]["recall50"] == 0.0
    assert inst["thin"]["mean_iou"] == pytest.approx(1 / 3)
    assert inst["per_class"]["strip"]["thin"]["recall50"] == 0.0
    assert "compact" not in inst["per_class"]["strip"]
    # The same instances by attributes.min_width_px: the 2 px strip and the 10 px block.
    assert inst["by_min_width_px"]["2-4"]["mean_iou"] == pytest.approx(1 / 3)
    assert inst["by_min_width_px"][">=6"]["recall50"] == 1.0
    assert set(inst["by_min_width_px"]) == {"2-4", ">=6"}


def test_min_width_bucket_edges():
    def width(value):
        return eval_seg._width_bucket({"attributes": {"min_width_px": value}})

    assert [width(v) for v in (1.0, 1.99, 2.0, 3.9, 4.0, 5.99, 6.0, 80)] == \
        ["<2", "<2", "2-4", "2-4", "4-6", "4-6", ">=6", ">=6"]
    assert width(None) is None and width(True) is None and width(-1) is None
    assert eval_seg._width_bucket({}) is None


def test_precision_recall_curve_known_answer():
    """TP 0.9, FP 0.8, TP 0.7 on two instances: precision 1 up to recall .5, then 2/3."""
    gt = ground_truth(ann(1, 1, 1, (2, 2, 10, 10), False), ann(2, 1, 1, (20, 20, 28, 28), False), images=(1,))
    preds = [pred(1, 1, (2, 2, 10, 10), 0.9), pred(1, 1, (30, 2, 38, 10), 0.8), pred(1, 1, (20, 20, 28, 28), 0.7)]
    result = eval_seg.evaluate(gt, preds)
    expected = (51 * 1.0 + 50 * (2 / 3)) / 101
    assert result["coco"]["AP"] == pytest.approx(expected)
    assert result["coco"]["AR100"] == 1.0
    assert result["confusion"]["false_positives"] == {"block": 1}


def test_operating_threshold_and_any_score():
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), images=(1,))
    result = eval_seg.evaluate(gt, [pred(1, 1, (10, 10, 20, 20), score=0.3)], score_thr=0.5)
    assert result["coco"]["AP"] == pytest.approx(1.0)  # AP ranks by score; the threshold does not apply
    assert result["instances"]["all"]["recall50"] == 0.0
    assert result["instances_any_score"]["all"]["recall50"] == 1.0
    assert result["confusion"]["per_class"]["block"]["missed"] == 1


def test_unknown_ids_and_detection_cap():
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), images=(1,))
    with pytest.raises(ValueError):
        eval_seg.evaluate(gt, [pred(9, 1, (10, 10, 20, 20))])
    preds = [
        pred(1, 99, (0, 0, 5, 5)),  # a class the split does not have
        pred(1, 1, (0, 0, 5, 5), 0.1),  # lowest score: dropped by the cap of 2
        pred(1, 1, (0, 0, 5, 5), 0.2),
        pred(1, 1, (10, 10, 20, 20), 0.3),
    ]
    result = eval_seg.evaluate(gt, preds, max_dets=2)
    assert result["notes"] == {"unknown_category_predictions": 1, "capped_predictions": 1}
    assert result["counts"]["predictions_scored"] == 2
    # The TP at 0.3 ranks above the FP at 0.2: precision 1 at every recall.
    assert result["coco"]["AP"] == pytest.approx(1.0)


def test_class_agnostic_ignores_the_predicted_class():
    gt = ground_truth(ann(1, 1, 1, (2, 2, 12, 12), False), ann(2, 1, 2, (20, 2, 22, 30), True), images=(1,))
    # Exactly the right masks under the wrong classes.
    result = eval_seg.evaluate(gt, [pred(1, 3, (2, 2, 12, 12)), pred(1, 1, (20, 2, 22, 30))])
    assert result["coco"]["AP"] == 0.0
    ca = result["class_agnostic"]
    assert {"AP", "AP50", "recall_iou50", "mean_iou"} <= set(ca)
    assert ca["AP"] == pytest.approx(1.0) and ca["AP50"] == pytest.approx(1.0)
    assert ca["recall_iou50"] == {"thin": 1.0, "compact": 1.0}
    assert ca["mean_iou"] == {"thin": 1.0, "compact": 1.0}


def test_class_agnostic_scores_categories_the_split_does_not_have():
    """A pretrained model's COCO classes: dropped per class, scored when the class is ignored."""
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), images=(1,))
    preds = [pred(1, 1001, (10, 10, 20, 20), 0.2), pred(1, 1002, (30, 30, 35, 35), 0.1)]
    result = eval_seg.evaluate(gt, preds)
    assert result["coco"]["AP"] == 0.0
    assert result["notes"] == {"unknown_category_predictions": 2}
    assert result["counts"]["predictions_scored"] == 0
    ca = result["class_agnostic"]
    assert ca["AP"] == pytest.approx(1.0)  # the true positive at 0.2 ranks above the false positive
    assert ca["recall_iou50"] == {"thin": None, "compact": 1.0}
    assert ca["mean_iou"]["compact"] == 1.0
    assert ca["at_score_thr"]["recall_iou50"]["compact"] == 0.0  # 0.2 is under the operating 0.5


def test_class_agnostic_lowers_with_shift_and_splits_thin_from_compact():
    gt = ground_truth(ann(1, 1, 1, (2, 2, 12, 12), False), ann(2, 1, 2, (20, 2, 22, 30), True), images=(1,))
    preds = [pred(1, 2, (4, 2, 14, 12)), pred(1, 3, (18, 2, 24, 30))]  # a shifted block, a strip 6 px wide
    ca = eval_seg.evaluate(gt, preds)["class_agnostic"]
    assert ca["mean_iou"]["compact"] == pytest.approx(80 / 120)
    assert ca["mean_iou"]["thin"] == pytest.approx(1 / 3)
    assert ca["recall_iou50"] == {"thin": 0.0, "compact": 1.0}
    assert ca["by_min_width_px"]["2-4"]["n"] == 1 and ca["by_min_width_px"][">=6"]["n"] == 1


def test_class_agnostic_caps_per_image_not_per_class():
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), images=(1,))
    preds = [pred(1, 1, (0, 0, 5, 5), 0.9), pred(1, 2, (10, 10, 20, 20), 0.8)]
    result = eval_seg.evaluate(gt, preds, max_dets=1)
    assert result["counts"]["predictions_scored"] == 2  # one per class fits the per-class cap
    assert result["class_agnostic"]["AP"] == 0.0  # only the false positive at 0.9 survives the image cap
    assert result["class_agnostic"]["recall_iou50"]["compact"] == 0.0
    # A duplicate of a true positive is one false positive, as in COCOeval: it ranks below, so AP stays 1.
    twice = [pred(1, 1, (10, 10, 20, 20), 0.9), pred(1, 2, (10, 10, 20, 20), 0.8)]
    assert eval_seg.evaluate(gt, twice)["class_agnostic"]["AP"] == pytest.approx(1.0)


def test_cli_writes_json_and_table(tmp_path):
    gt = fixture()
    preds = [pred(a["image_id"], a["category_id"], (0, 0, 4, 4)) for a in gt["annotations"]]
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    (tmp_path / "pred.json").write_text(json.dumps(preds))
    out = tmp_path / "metrics" / "m.json"
    code = eval_seg.main(["--gt", str(tmp_path / "gt.json"), "--pred", str(tmp_path / "pred.json"),
                          "--out", str(out), "--score-thr", "0.4"])
    assert code == 0
    result = json.loads(out.read_text())
    assert result["coco"]["AP"] == 0.0 and result["params"]["score_thr"] == 0.4
    assert result["class_agnostic"]["AP"] == 0.0
    table = out.with_suffix(".txt").read_text()
    assert "mask AP" in table and "class-agnostic AP" in table
    tracked = eval_seg.ROOT / "training" / "should_not_exist.json"
    assert eval_seg.main(["--gt", str(tmp_path / "gt.json"), "--pred", str(tmp_path / "pred.json"),
                          "--out", str(tracked)]) == 2
    assert not tracked.exists()


def test_roboflow_packaging_refuses_anything_not_synthetic(tmp_path, monkeypatch):
    datasets = tmp_path / "data" / "datasets"
    monkeypatch.setattr(roboflow_upload, "DATASETS", datasets)
    for name, source in (("synth", "synthetic"), ("owner", "owner-drawings")):
        (datasets / name).mkdir(parents=True)
        (datasets / name / "manifest.json").write_text(json.dumps({"source": source}))
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "manifest.json").write_text(json.dumps({"source": "synthetic"}))
    assert roboflow_upload.synthetic_dataset("synth") == (datasets / "synth").resolve()
    for refused in ("owner", "missing", str(tmp_path / "elsewhere")):
        with pytest.raises(roboflow_upload.Refused):
            roboflow_upload.synthetic_dataset(refused)


def test_roboflow_responses_come_back_as_coco_results(tmp_path):
    """A SAM 3 / Roboflow instance-segmentation response per image, scored by eval_seg."""
    gt = ground_truth(ann(1, 1, 1, (10, 10, 20, 20), False), ann(2, 2, 2, (5, 5, 30, 7), True))
    corners = [{"x": x, "y": y} for x, y in ((10, 10), (20, 10), (20, 20), (10, 20))]
    (tmp_path / "t1.json").write_text(json.dumps({"image": {"width": W, "height": H}, "predictions": [
        {"x": 15, "y": 15, "width": 10, "height": 10, "confidence": 0.9, "class": "block", "points": corners},
        {"x": 1, "y": 1, "width": 2, "height": 2, "confidence": 0.8, "class": "not-a-class", "points": corners},
    ]}))
    # A response at half resolution is scaled back to the image.
    half = [{"x": x / 2, "y": y / 2} for x, y in ((5, 5), (30, 5), (30, 7), (5, 7))]
    (tmp_path / "t2.json").write_text(json.dumps({"image": {"width": W // 2, "height": H // 2}, "predictions": [
        {"x": 8.75, "y": 3, "width": 12.5, "height": 1, "confidence": 0.7, "class": "strip", "points": half},
    ]}))
    rows, skipped = roboflow_upload.roboflow_predictions(tmp_path, gt)
    assert skipped == {"unknown_class": 1}
    assert [(r["image_id"], r["category_id"]) for r in rows] == [(1, 1), (2, 2)]
    result = eval_seg.evaluate(gt, rows)
    assert result["coco"]["AP"] == pytest.approx(1.0)
    assert result["instances"]["thin"]["mean_iou"] == 1.0


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "gpu_ledger.json"
    monkeypatch.setattr(rfdetr_seg, "LEDGER", path)
    return path


def test_ledger_charges_billed_then_measured_then_worst_case():
    charged, base = rfdetr_seg._charged_s, {"gpu": "L40S", "reserved_s": 1200, "status": "ok"}
    assert charged({**base, "billed_s": 275.5, "gpu_s": 228.3}) == 275.5
    assert charged({**base, "gpu_s": 100.0}) == 100.0 + rfdetr_seg.STARTUP_ALLOWANCE_S
    # A failed call ran for at most its local wall time, and at most its timeout.
    assert charged({**base, "status": "failed", "gpu_s": None, "wall_s": 300.0}) == 300.0
    assert charged({**base, "status": "failed", "gpu_s": None, "wall_s": 5000.0}) == 1200
    assert charged({**base, "status": "running", "gpu_s": None, "wall_s": None}) == 1200  # the client died
    assert charged({**base, "status": "cancelled_unscheduled", "gpu_s": 0, "wall_s": 400.0}) == 0


def test_ledger_records_cost_per_epoch_with_the_gpu_price(ledger):
    with rfdetr_seg.ledger_entry("run", "train", "L40S", 3600) as record:
        record.update(gpu_s=1000.0, epochs=4, epoch_s=200.0)
    entry = json.loads(ledger.read_text())[0]
    assert entry["status"] == "ok" and entry["price_per_s"] == 0.000542
    assert entry["epochs"] == 4 and entry["epoch_s"] == 200.0
    assert entry["cost_per_epoch_usd"] == pytest.approx(200.0 * 0.000542)
    assert entry["cost_usd"] == pytest.approx((1000.0 + rfdetr_seg.STARTUP_ALLOWANCE_S) * 0.000542, abs=1e-4)


def test_a_failed_call_stays_on_the_ledger(ledger):
    with pytest.raises(RuntimeError):
        with rfdetr_seg.ledger_entry("run", "predict", "A10", 1200):
            raise RuntimeError("container died")
    entry = json.loads(ledger.read_text())[0]
    assert entry["status"] == "failed" and entry["gpu_s"] is None
    assert "cost_per_epoch_usd" not in entry


def test_budget_guard_counts_dollars_across_gpu_types(ledger):
    ledger.write_text(json.dumps([{"gpu": "B200", "status": "ok", "reserved_s": 9600, "billed_s": 9309.0}]))
    spent = 9309.0 * 0.001736
    assert rfdetr_seg.usd_charged() == pytest.approx(spent)
    minutes, hard_s = rfdetr_seg.plan_training(0, 18.75, "L40S", "A10")
    assert hard_s == pytest.approx((minutes + rfdetr_seg.SETUP_SLACK_MIN) * 60, abs=6)  # minutes is rounded
    # The training call's worst case plus the prediction reservation fits what is left.
    worst = hard_s * 0.000542 + rfdetr_seg.PREDICT_TIMEOUT_MIN * 60 * 0.000306
    assert worst == pytest.approx(18.75 - spent, abs=0.01) and worst <= 18.75 - spent
    assert rfdetr_seg.plan_training(10, 18.75, "L40S", "A10")[0] == 10  # a shorter request stands
    with pytest.raises(SystemExit):
        rfdetr_seg.plan_training(0, spent + 0.1, "L40S", "A10")
    with pytest.raises(SystemExit):
        rfdetr_seg.require_budget(20, spent + 0.1, "H100")
    assert rfdetr_seg.require_budget(20, spent + 0.5, "A10") is None


def test_epoch_seconds_ignore_the_epoch_the_time_limit_cut_off():
    rows = [{"train_s": 100.0, "val_s": 50.0, "median_batch_s": 0.4},
            {"train_s": 110.0, "val_s": 50.0, "median_batch_s": 0.4},
            {"train_s": 1.2, "val_s": 50.0, "median_batch_s": None}]  # one batch, then the forced validation
    assert rfdetr_seg._epoch_seconds(rows) == (155.0, 2)
    assert rfdetr_seg._epoch_seconds([]) == (None, 0)


def test_read_progress_skips_a_line_cut_short(tmp_path):
    path = tmp_path / "progress.jsonl"
    path.write_text('{"epoch": 0}\n{"epoch": 1}\n{"epo')
    assert [row["epoch"] for row in rfdetr_seg._read_progress(path)] == [0, 1]
    assert rfdetr_seg._read_progress(tmp_path / "missing.jsonl") == []


class FakeDetections:
    """The parts of supervision's Detections that _coco_results reads: every mask is one 10 x 10 box."""

    def __init__(self, class_ids, names):
        n = len(class_ids)
        self.class_id = np.array(class_ids)
        self.confidence = np.linspace(0.9, 0.6, n)
        self.xyxy = np.array([[5.0, 5.0, 15.0, 15.0]] * n)
        self.mask = np.zeros((n, H, W), dtype=bool)
        self.mask[:, 5:15, 5:15] = True
        self.data = {"class_name": np.array(names, dtype=object)}

    def __len__(self):
        return len(self.class_id)


def test_trained_weights_map_model_classes_to_categories_by_name(tmp_path, monkeypatch):
    gt = fixture()
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "checkpoint_best_total.pth").write_bytes(b"")
    model = types.SimpleNamespace(class_names=["block", "plate", "strip"])  # not the split's id order
    loaded = {}

    def from_checkpoint(path, trust_checkpoint, num_select):
        loaded.update(path=path, trust_checkpoint=trust_checkpoint, num_select=num_select)
        return model

    monkeypatch.setitem(sys.modules, "rfdetr", types.SimpleNamespace(from_checkpoint=from_checkpoint))
    cfg = {"weights": "trained", "max_dets": 100}
    got, weights_file, category_of = rfdetr_seg._load_for_prediction(cfg, tmp_path, gt)
    assert got is model and weights_file == "checkpoint_best_total.pth" and loaded["num_select"] == 100
    assert [category_of(i) for i in (0, 1, 2)] == [1, 3, 2]
    det = FakeDetections([0, 3], ["block", "__background__"])  # 3 is rfdetr's no-object slot
    rows, dropped = rfdetr_seg._coco_results(gt["images"][0], det, category_of, eval_seg)
    assert dropped == 1 and [r["category_id"] for r in rows] == [1]
    assert rows[0]["bbox"] == [5.0, 5.0, 10.0, 10.0] and rows[0]["class_name"] == "block"
    result = eval_seg.evaluate(gt, rows)  # the box is exactly annotation 1 of image 1
    assert result["per_class"]["block"]["AP50"] == pytest.approx(0.5, abs=0.01)  # 1 of its 2 instances found
    assert result["notes"] == {}


def test_trained_weights_refuse_a_model_with_classes_the_split_lacks(tmp_path, monkeypatch):
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "checkpoint_best_ema.pth").write_bytes(b"")
    model = types.SimpleNamespace(class_names=["block", "not-in-this-split"])
    fake = types.SimpleNamespace(from_checkpoint=lambda *a, **k: model)
    monkeypatch.setitem(sys.modules, "rfdetr", fake)
    with pytest.raises(ValueError):
        rfdetr_seg._load_for_prediction({"weights": "trained", "max_dets": 100}, tmp_path, fixture())


def test_pretrained_weights_stay_out_of_the_splits_category_range(tmp_path, monkeypatch):
    class Seg2XLarge:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model_config = types.SimpleNamespace(pretrain_weights="/weights/rf-detr-seg-xxlarge.pt")

    monkeypatch.setitem(sys.modules, "rfdetr", types.SimpleNamespace(RFDETRSeg2XLarge=Seg2XLarge))
    cfg = {"weights": "pretrained", "model": "seg-2xlarge", "resolution": 1008, "max_dets": 100}
    model, weights_file, category_of = rfdetr_seg._load_for_prediction(cfg, tmp_path, fixture())
    assert model.kwargs == {"resolution": 1008, "num_select": 100}  # no checkpoint of ours is read
    assert weights_file == "rf-detr-seg-xxlarge.pt"
    gt = ground_truth(ann(1, 1, 1, (5, 5, 15, 15), False), images=(1,))
    # COCO ids 1 and 2 would be valid categories of this split without the offset.
    det = FakeDetections([1, 2], ["person", "bicycle"])
    rows, dropped = rfdetr_seg._coco_results(gt["images"][0], det, category_of, eval_seg)
    assert dropped == 0 and [r["category_id"] for r in rows] == [1001, 1002]
    assert [r["class_name"] for r in rows] == ["person", "bicycle"]
    result = eval_seg.evaluate(gt, rows)
    assert result["coco"]["AP"] == 0.0 and result["notes"] == {"unknown_category_predictions": 2}
    assert result["class_agnostic"]["AP"] == pytest.approx(1.0)
