"""The training dashboard on fixtures built inside the test: no network, no Modal, no real runs."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from dashboard import build, charts, page, runs, tiles

NOW = datetime(2026, 10, 30, 8, 0, 0, tzinfo=timezone.utc)
RUN = "demo-seg-2xlarge-20261030T0700Z"
BEFORE = "pretrained-seg-2xlarge-20261030T0710Z"
CLASSES = ("membrane", "slab")


def entry(function="train", status="running", gpu="B200", run_id=RUN, started="2026-10-30T07:00:00Z", **extra):
    return {"id": f"{function}-{status}", "run_id": run_id, "function": function, "gpu": gpu, "reserved_s": 1200,
            "started_at": started, "wall_s": extra.pop("wall_s", None), "gpu_s": extra.pop("gpu_s", None),
            "status": status, **extra}


def progress_line(epoch, train_s=80.0, val_s=20.0, batches=700, score=0.5, per_class=0.777, **extra):
    metrics = {"loss": 9.0 - epoch, "val/ema_segm_mAP_50_95": score, "val/ema_segm_mAP_50": score + 0.1,
               **{f"val/ema_AP/{name}": per_class for name in CLASSES}}
    return {"epoch": epoch, "elapsed_s": (epoch + 1) * (train_s + val_s), "train_s": train_s, "val_s": val_s,
            "micro_batches": batches, "median_batch_s": 0.1, "peak_mem_gib": 12.5, "metrics": metrics, **extra}


def metrics_file(ap=0.6, agnostic=0.7, weights_name="RF-DETR-Seg"):
    """A metrics_test.json with the shapes training/eval_seg.py writes."""
    row = {"n": 10, "recall50": 0.8, "mean_iou": 0.6, "median_iou": 0.6}
    instances = {"score_thr": 0.5, "all": row, "thin": row, "compact": row,
                 "by_min_width_px": {"<2": row, "2-4": row, "4-6": row, ">=6": row}, "per_class": {}}
    return {
        "scorer": "eval-seg-v1",
        "params": {"score_thr": 0.5, "match_iou": 0.5},
        "counts": {"images": 2, "gt_instances": 4, "predictions": 8, "predictions_scored": 8},
        "coco": {"AP": ap, "AP50": ap + 0.1, "AP75": ap, "AR100": ap},
        "per_class": {name: {"AP": ap, "AP50": ap, "n_gt": 2, "n_gt_thin": 1} for name in CLASSES},
        "instances": instances,
        "instances_any_score": instances,
        "class_agnostic": {"AP": agnostic, "AP50": agnostic + 0.1, "recall_iou50": {"thin": 0.5, "compact": 0.9},
                           "mean_iou": {"thin": 0.4, "compact": 0.8}, "by_min_width_px": {"<2": row, "2-4": row, "4-6": row}},
        "confusion": {"iou": 0.5, "score_thr": 0.5, "swaps": [{"gt": "membrane", "pred": "slab", "count": 3, "share_of_gt": 0.5}]},
        "notes": {},
        "files": {"gt": "demo/test/_annotations.coco.json", "pred": "predictions_test.json"},
        "model": {"family": weights_name},
    }


def rectangle(x0, y0, x1, y1):
    return [[x0, y0, x1, y0, x1, y1, x0, y1]]


def annotation(ann_id, image_id, category_id, box, thin):
    return {"id": ann_id, "image_id": image_id, "category_id": category_id, "segmentation": rectangle(*box),
            "bbox": [box[0], box[1], box[2] - box[0], box[3] - box[1]], "area": 1.0, "iscrowd": 0,
            "attributes": {"thin": thin, "min_width_px": 3.0 if thin else 40.0, "part_id": str(ann_id)}}


def write_dataset(root):
    """Two 300 px tiles: one with a thin part, one with only a compact part."""
    test = root / "demo" / "test"
    test.mkdir(parents=True)
    images = [{"id": i + 1, "file_name": f"tile{i}.png", "width": 300, "height": 300, "drawing_id": f"d{i}"} for i in range(2)]
    for image in images:
        Image.new("RGB", (300, 300), "white").save(test / image["file_name"])
    coco = {"images": images, "categories": [{"id": i + 1, "name": n} for i, n in enumerate(CLASSES)],
            "annotations": [annotation(1, 1, 1, (10, 10, 200, 13), True), annotation(2, 1, 2, (10, 50, 200, 200), False),
                            annotation(3, 2, 2, (10, 10, 200, 200), False)]}
    (test / "_annotations.coco.json").write_text(json.dumps(coco))
    per_class = {name: {"instances": 2, "thin": 1, "thin_share": 0.5} for name in CLASSES}
    manifest = {"name": "demo", "source": "synthetic", "per_class": per_class,
                "splits": {"test": {"drawings": 2, "images": 2, "instances": 3, "thin_instances": 1, "per_class": per_class}}}
    (root / "demo" / "manifest.json").write_text(json.dumps(manifest))


def write_run(models, run_id, *, progress=None, config=None, metrics=None, predict=None, predictions=None, summary=None):
    home = models / "rfdetr-seg" / run_id
    home.mkdir(parents=True, exist_ok=True)
    if progress is not None:
        (home / "progress.jsonl").write_text("".join(json.dumps(p) + "\n" for p in progress))
    if config is not None:
        (home / "run_config.json").write_text(json.dumps({"config": config, "environment": {}}))
    if summary is not None:
        (home / "train_summary.json").write_text(json.dumps(summary))
    if metrics is not None:
        (home / "metrics_test.json").write_text(json.dumps(metrics))
    if predict is not None:
        (home / "predict_summary_test.json").write_text(json.dumps(predict))
    if predictions is not None:
        (home / "predictions_test.json").write_text(json.dumps(predictions))


def write_ledger(models, entries):
    (models / "rfdetr-seg").mkdir(parents=True, exist_ok=True)
    (models / "rfdetr-seg" / "gpu_ledger.json").write_text(json.dumps(entries))


@pytest.fixture
def dirs(tmp_path):
    models, datasets, out = tmp_path / "models", tmp_path / "datasets", tmp_path / "out"
    datasets.mkdir()
    write_dataset(datasets)
    return models, datasets, out


CONFIG = {"dataset": "demo", "model": "seg-2xlarge", "epochs": 100, "max_train_minutes": 10, "early_stopping_patience": 5}


def built(dirs, modal=None):
    models, datasets, out = dirs
    world = build.build(out, models, datasets, modal, every=60, now=NOW)
    return world, (out / "index.html").read_text()


def section(html, section_id):
    return html.split(f'<section id="{section_id}">')[1].split("</section>")[0]


# ---------------------------------------------------------------- stage

def test_stage_training_once_passes_are_logged():
    assert runs.derive_stage([entry()], [progress_line(0)], {}).kind == "training"


def test_stage_training_when_the_first_pass_is_still_running():
    files = {"run_config.json": NOW}
    assert runs.derive_stage([entry()], [], files).kind == "training"


def test_stage_queued_with_no_sign_of_a_container():
    assert runs.derive_stage([entry()], [], {}).kind == "queued"


def test_stage_ignores_files_left_by_an_earlier_attempt():
    before_the_call = NOW - timedelta(days=1)
    assert runs.derive_stage([entry()], [], {"run_config.json": before_the_call}).kind == "queued"


@pytest.mark.parametrize("containers, kind", [(None, "queued_or_predicting"), (0, "queued"), (1, "predicting")])
def test_stage_predict_needs_a_container_to_tell_queued_from_running(containers, kind):
    assert runs.derive_stage([entry("predict")], [], {}, containers).kind == kind


def test_stage_scoring_when_predictions_are_in_and_scores_are_not():
    done = [entry("predict", "ok", wall_s=100.0, gpu_s=90.0)]
    assert runs.derive_stage(done, [], {runs.PREDICTIONS: NOW}).kind == "scoring"


def test_stage_scoring_again_after_a_new_prediction_outdates_the_scores():
    done = [entry("predict", "ok", wall_s=100.0, gpu_s=90.0)]
    old_scores = {"metrics_test.json": runs.parse_time("2026-10-30T06:00:00Z")}
    assert runs.derive_stage(done, [], old_scores).kind == "scoring"


def test_stage_finished_even_though_downloads_write_metrics_before_predictions():
    done = [entry("predict", "ok", wall_s=100.0, gpu_s=90.0)]
    copied = {"metrics_test.json": NOW, runs.PREDICTIONS: NOW + timedelta(seconds=30)}
    assert runs.derive_stage(done, [], copied).kind == "finished"


def test_stage_failed():
    assert runs.derive_stage([entry("train", "failed", wall_s=9.0)], [progress_line(0)], {}).kind == "failed"


def test_stage_stopped_when_the_last_request_never_got_a_machine():
    entries = [entry("predict", "cancelled_unscheduled", wall_s=300.0, gpu_s=0)]
    assert runs.derive_stage(entries, [], {}).kind == "stopped"


def test_stage_running_entry_wins_over_older_scores():
    entries = [entry("predict", "ok", wall_s=9.0, gpu_s=8.0), entry("predict", "running", started="2026-10-30T07:30:00Z")]
    assert runs.derive_stage(entries, [], {"metrics_test.json": NOW}, 1).kind == "predicting"


def test_stage_of_a_run_with_no_ledger_entries_comes_from_its_files():
    assert runs.derive_stage([], [], {"metrics_test.json": NOW}).kind == "finished"
    assert runs.derive_stage([], [], {}).kind == "not_started"


# ---------------------------------------------------------------- progress

def test_partial_last_json_line_is_skipped(tmp_path):
    path = tmp_path / "progress.jsonl"
    path.write_text(json.dumps(progress_line(0)) + "\n" + json.dumps(progress_line(1))[:40])
    assert [line["epoch"] for line in runs.read_progress(path)] == [0]


def test_missing_progress_file_is_no_passes(tmp_path):
    assert runs.read_progress(tmp_path / "nothing.jsonl") == []


def test_the_time_caps_one_batch_stub_is_not_a_pass():
    log = [progress_line(0), progress_line(1), progress_line(2, batches=1, train_s=1.0)]
    assert len(runs.full_passes(log)) == 2


def test_a_full_last_pass_is_kept():
    log = [progress_line(0), progress_line(1)]
    assert len(runs.full_passes(log)) == 2


def test_class_ap_prefers_ema_values():
    line = {"metrics": {"val/ema_AP/slab": 0.9, "val/AP/slab": 0.1}}
    assert runs.class_ap(line, "slab") == 0.9


# ---------------------------------------------------------------- pass estimate

def test_pass_estimate_uses_the_time_cap_when_it_comes_first():
    log = [progress_line(i) for i in range(3)]  # 100 s a pass
    plan = runs.plan_passes(log, {"epochs": 100, "max_train_minutes": 10})
    assert (plan.done, plan.about, plan.limit) == (3, 6, "time cap")


def test_pass_estimate_uses_the_pass_limit_when_it_comes_first():
    log = [progress_line(i) for i in range(3)]
    plan = runs.plan_passes(log, {"epochs": 4, "max_train_minutes": 10})
    assert (plan.about, plan.limit) == (4, "pass limit")


def test_no_estimate_before_one_pass_is_timed():
    plan = runs.plan_passes([], {"epochs": 100, "max_train_minutes": 10})
    assert plan.about is None and plan.limit is None


def test_finished_plan_says_the_time_cap_stopped_it():
    log = [progress_line(i) for i in range(3)]
    plan = runs.plan_passes(log, {"epochs": 100, "max_train_minutes": 5}, {"stopped_by_time": True})
    assert (plan.about, plan.limit) == (3, "time cap")


def test_finished_plan_says_early_stopping_stopped_it():
    log = [progress_line(i) for i in range(3)]
    plan = runs.plan_passes(log, {"epochs": 100}, {"stopped_by_time": False})
    assert plan.limit == "early stopping"


def test_best_pass_counts_from_one_and_ignores_the_stub():
    log = [progress_line(0, score=0.4), progress_line(1, score=0.7), progress_line(2, score=0.6),
           progress_line(3, score=0.9, batches=1)]
    plan = runs.plan_passes(log, {"epochs": 100})
    assert (plan.best_pass, plan.best_score, plan.since_best) == (2, 0.7, 1)


def test_time_left_runs_to_the_cap_plus_one_validation():
    log = [progress_line(i) for i in range(3)]
    plan = runs.plan_passes(log, {"epochs": 100, "max_train_minutes": 10})
    assert runs.time_left_s(plan, 300.0) == pytest.approx(600 + 20 - 300)


def test_time_left_is_unknown_until_a_pass_is_timed():
    assert runs.time_left_s(runs.plan_passes([], {"epochs": 3}), None) is None


# ---------------------------------------------------------------- cost and waits

def test_cost_uses_the_price_table():
    assert runs.gpu_cost("B200", 1000) == pytest.approx(1000 * runs.GPU_PRICE_PER_S["B200"])
    assert runs.GPU_PRICE_PER_S == {"B200": 0.001736, "H100": 0.001097, "A100-80GB": 0.000694, "L40S": 0.000542,
                                    "A10": 0.000306, "L4": 0.000222, "T4": 0.000164}


def test_gpu_not_in_the_price_table_has_no_cost_not_a_zero():
    assert runs.gpu_cost("Z9", 1000) is None


def test_billed_seconds_win_over_measured_ones():
    call = entry("train", "ok", gpu_s=100.0, wall_s=110.0, billed_s=130.0)
    assert runs.entry_cost(call) == pytest.approx(130 * runs.GPU_PRICE_PER_S["B200"])


def test_a_call_billed_under_another_costs_nothing_but_keeps_its_time():
    call = entry("predict", "ok", gpu="H100", gpu_s=8.0, wall_s=20.0, billed_s=0.0)
    assert runs.entry_cost(call) == 0.0
    assert runs.entry_seconds(call) == 8.0


def test_a_request_that_never_got_a_machine_costs_nothing():
    assert runs.entry_cost(entry("predict", "cancelled_unscheduled", gpu_s=0, wall_s=2000.0)) == 0.0


def test_a_live_train_call_is_counted_to_its_last_pass():
    run = runs.Run(RUN, "rfdetr-seg", entries=[entry()], progress=[progress_line(0), progress_line(1)])
    totals = runs.run_totals(run)
    assert totals.seconds_by_gpu == {"B200": 200.0}
    assert totals.cost == pytest.approx(200 * runs.GPU_PRICE_PER_S["B200"])


def test_a_live_predict_call_has_no_measured_time_yet():
    run = runs.Run(RUN, "rfdetr-seg", entries=[entry("predict")])
    totals = runs.run_totals(run)
    assert totals.cost is None and totals.unmeasured == 1


def test_waits_cover_all_outcomes():
    assert runs.wait_s(entry("train", "cancelled_unscheduled", wall_s=2247.6, gpu_s=0), NOW) == 2247.6
    assert runs.wait_s(entry("train", "ok", wall_s=500.0, gpu_s=400.0), NOW) == 100.0
    assert runs.wait_s(entry("train", "running"), NOW) == 3600.0
    assert runs.wait_s(entry("train", "failed", wall_s=500.0), NOW) is None


def test_a_training_call_stops_waiting_when_its_container_starts():
    run = runs.Run(RUN, "rfdetr-seg", entries=[entry()], stage=runs.Stage("training"),
                   files={"run_config.json": runs.parse_time("2026-10-30T07:05:00Z")})
    assert runs.attempt_wait(run.entries[0], run, NOW) == 300.0


def test_a_call_still_queued_keeps_waiting():
    run = runs.Run(RUN, "rfdetr-seg", entries=[entry()], stage=runs.Stage("queued"))
    assert runs.attempt_wait(run.entries[0], run, NOW) == 3600.0


def test_a_running_predict_has_no_known_wait():
    run = runs.Run(RUN, "rfdetr-seg", entries=[entry("predict")], stage=runs.Stage("predicting"))
    assert runs.attempt_wait(run.entries[0], run, NOW) is None


def test_queue_finding_counts_what_happened():
    entries = [entry("train", "ok", gpu_s=9.0, wall_s=12.0), entry("predict", "running"),
               entry("predict", "cancelled_unscheduled", gpu_s=0, wall_s=600.0, gpu="L4")]
    text = page.queue_finding(entries)
    assert text.startswith("3 GPU requests so far: 1 ran, 1 still open and 1 never got a machine.")
    assert "10 min for L4" in text


def test_queue_finding_for_a_single_open_request():
    assert page.queue_finding([entry()]) == "1 GPU request so far: 1 still open."


# ---------------------------------------------------------------- output guard

@pytest.mark.parametrize("path", ["docs", "dashboard", "data", "data/models", "data/dashboard-old", "."])
def test_output_guard_refuses_other_paths_in_the_repo(path):
    assert not build.allowed_output(build.ROOT / path)


@pytest.mark.parametrize("path", ["data/dashboard", "data/dashboard/test"])
def test_output_guard_allows_data_dashboard(path):
    assert build.allowed_output(build.ROOT / path)


def test_output_guard_allows_outside_any_repo(tmp_path):
    assert build.allowed_output(tmp_path / "out")


def test_output_guard_refuses_any_other_git_checkout(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere")
    assert not build.allowed_output(tmp_path / "reports")
    assert build.allowed_output(tmp_path / "data" / "dashboard")


def test_cli_refuses_a_tracked_output_and_writes_nothing(capsys):
    target = build.ROOT / "docs" / "dashboard-should-not-exist"
    assert build.main(["--no-pull", "--out", str(target)]) == 2
    assert not target.exists()
    assert "refusing" in capsys.readouterr().err


# ---------------------------------------------------------------- honesty on the page

def test_pending_test_score_renders_as_not_run_yet(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, progress=[progress_line(i) for i in range(2)], config=CONFIG)
    _, html = built(dirs)
    table = section(html, "classes")
    assert page.NOT_RUN in section(html, "before-after")
    assert table.count(page.NOT_RUN) >= len(CLASSES)
    assert "0.000" not in section(html, "before-after")


def test_the_side_that_has_not_run_says_so_beside_the_side_that_has(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0)])
    metrics = metrics_file(ap=0.6)
    del metrics["class_agnostic"]  # scored before the scorer had this block
    write_run(models, RUN, metrics=metrics, predict={"weights": "trained"})
    _, html = built(dirs)
    before_after, thin = section(html, "before-after"), section(html, "thin")
    assert "0.600" in before_after and "0.000" not in before_after
    assert before_after.split("<tbody>")[1].count(page.NOT_RUN) >= 8  # both Before columns and class-agnostic After
    assert thin.split("<tbody>")[1].count(page.NOT_RUN) >= 4 and "0.000" not in thin


def test_validation_numbers_never_land_in_the_test_sections(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, progress=[progress_line(i, per_class=0.777) for i in range(2)], config=CONFIG)
    _, html = built(dirs)
    assert "0.777" in section(html, "classes")
    assert "Validation AP" in section(html, "classes")
    assert "0.777" not in section(html, "before-after") + section(html, "thin")


def test_curves_are_labelled_validation_and_loss_is_labelled_training(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, progress=[progress_line(i) for i in range(2)], config=CONFIG)
    _, html = built(dirs)
    curves = section(html, "curves")
    assert "Score curves, validation split" in curves
    assert "Mask score on the validation split" in curves
    assert "Training loss, not a validation number" in curves


def test_section_order_matches_the_brief(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    _, html = built(dirs)
    order = ["now", "curves", "before-after", "classes", "thin", "images", "limits"]
    places = [html.index(f'<section id="{name}">') for name in order]
    assert places == sorted(places)


def test_planned_model_shows_as_not_run_yet(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, metrics=metrics_file(), config=CONFIG, predict={"weights": "trained"})
    _, html = built(dirs)
    assert f"<h3>SAM 3</h3><p>{page.NOT_RUN}</p>" in section(html, "before-after")


def test_page_says_how_old_it_is(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    _, html = built(dirs)
    assert "Updated <strong>" in html
    assert NOW.astimezone().strftime("%H:%M") in html.split("</header>")[0]


def test_page_states_the_upper_bound_and_the_gpu_only_label(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("train", "ok", gpu_s=100.0, wall_s=120.0)])
    _, html = built(dirs)
    assert "upper bound for real drawings" in section(html, "limits")
    assert "GPU-only cost" in section(html, "limits")
    assert runs.MODAL_BILL["date"] in section(html, "limits")


def test_time_capped_run_says_so(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("train", "ok", gpu_s=300.0, wall_s=320.0)])
    write_run(models, RUN, progress=[progress_line(i) for i in range(3)], config=CONFIG,
              summary={"stopped_by_time": True, "fit_s": 300.0}, metrics=metrics_file(), predict={"weights": "trained"})
    _, html = built(dirs)
    assert "Stopped by time cap" in section(html, "now")


def test_file_text_is_escaped(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("train", "cancelled_unscheduled", wall_s=9.0, gpu_s=0, run_id="<b>x", note="<script>bad()</script>")])
    _, html = built(dirs)
    assert "<script>bad" not in html and "<b>x" not in html


# ---------------------------------------------------------------- self-contained page

def test_page_is_one_file_with_nothing_external(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, progress=[progress_line(i) for i in range(2)], config=CONFIG)
    _, html = built(dirs)
    assert "http://" not in html and "https://" not in html
    assert "<script src" not in html and "<link" not in html
    assert "prefers-color-scheme: dark" in html and "tabular-nums" in html
    assert 'name="viewport"' in html


def test_meta_refresh_only_while_a_run_is_live(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, progress=[progress_line(0)], config=CONFIG)
    _, live = built(dirs)
    assert '<meta http-equiv="refresh" content="60">' in live
    write_ledger(models, [entry("train", "ok", gpu_s=100.0, wall_s=120.0), entry("predict", "ok", gpu_s=9.0, wall_s=12.0)])
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"})
    _, done = built(dirs)
    assert 'http-equiv="refresh"' not in done


def test_page_write_replaces_the_old_page_in_one_step(dirs):
    models, _, out = dirs
    write_ledger(models, [entry()])
    built(dirs)
    assert (out / "index.html").exists() and not (out / "index.html.part").exists()


# ---------------------------------------------------------------- runs on disk

def test_before_and_after_come_from_weights_and_smoke_runs_stay_out(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("train", "ok", gpu_s=9.0, wall_s=12.0), entry("predict", "ok", gpu_s=9.0, wall_s=12.0, run_id=BEFORE)])
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"}, config=CONFIG)
    write_run(models, BEFORE, metrics=metrics_file(ap=0.0, agnostic=0.02), predict={"weights": "pretrained"})
    write_run(models, "smoke-seg-2xlarge-20261030T0431Z", metrics=metrics_file(), predict={"weights": "trained"},
              config={"smoke": True, "dataset": "_smoke"})  # the newest scored "after" run, yet it must not count
    world, html = built(dirs)
    group = runs.group_models(world.runs)["RF-DETR-Seg"]
    assert group["before"].run_id == BEFORE and group["after"].run_id == RUN
    assert "smoke" not in section(html, "before-after")


def test_trained_run_without_a_weights_field_counts_as_after():
    run = runs.Run(RUN, "rfdetr-seg", train_summary={"fit_s": 1.0})
    assert run.weights == "trained"


def test_scored_run_without_any_evidence_of_weights_is_left_out():
    run = runs.Run(RUN, "sam3", metrics=metrics_file())
    assert run.weights is None
    assert runs.group_models([run])["SAM 3"]["after"] is None


def test_scored_run_without_weights_is_named_on_the_page(dirs):
    models, _, _ = dirs
    write_ledger(models, [])
    home = models / "sam3" / "sam3-demo-20261030T0900Z"
    home.mkdir(parents=True)
    (home / "metrics_test.json").write_text(json.dumps(metrics_file()))
    _, html = built(dirs)
    assert "sam3-demo" in section(html, "before-after") and "left out" in section(html, "before-after")


def test_a_second_trained_run_replaces_the_first_and_the_first_is_named(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0),
                          entry("predict", "ok", gpu_s=9.0, wall_s=12.0, run_id="later-seg-2xlarge-20261030T0800Z",
                                started="2026-10-30T07:30:00Z")])
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"})
    write_run(models, "later-seg-2xlarge-20261030T0800Z", metrics=metrics_file(ap=0.9), predict={"weights": "trained"})
    _, html = built(dirs)
    assert "0.900" in section(html, "before-after")
    assert "older runs not shown" in section(html, "before-after")


def test_two_datasets_never_share_a_before_and_after(dirs):
    models, datasets, _ = dirs
    zoomed = datasets / "zoomed"
    (zoomed / "test").mkdir(parents=True)
    (zoomed / "test" / "_annotations.coco.json").write_text((datasets / "demo" / "test" / "_annotations.coco.json").read_text())
    for name in ("tile0.png", "tile1.png"):
        (zoomed / "test" / name).write_bytes((datasets / "demo" / "test" / name).read_bytes())
    manifest = json.loads((datasets / "demo" / "manifest.json").read_text())
    (zoomed / "manifest.json").write_text(json.dumps({**manifest, "name": "zoomed", "config": {"zoom": 2.0}}))
    later = "zoomed-seg-2xlarge-20261030T0800Z"
    write_ledger(models, [entry("train", "ok", gpu_s=9.0, wall_s=12.0), entry("predict", "ok", gpu_s=9.0, wall_s=12.0, run_id=BEFORE),
                          entry("train", "ok", gpu_s=9.0, wall_s=12.0, run_id=later, started="2026-10-30T07:30:00Z")])
    write_run(models, BEFORE, metrics=metrics_file(ap=0.0, agnostic=0.02), predict={"weights": "pretrained"},
              config={"dataset": "demo"})
    write_run(models, RUN, metrics=metrics_file(ap=0.6), predict={"weights": "trained"}, config=CONFIG)
    write_run(models, later, metrics=metrics_file(ap=0.9), predict={"weights": "trained"}, config={**CONFIG, "dataset": "zoomed"})
    world, html = built(dirs)
    groups = runs.group_models(world.runs)
    first, second = groups["RF-DETR-Seg on demo"], groups["RF-DETR-Seg on zoomed"]
    assert "RF-DETR-Seg" not in groups
    assert first["before"].run_id == BEFORE and first["after"].run_id == RUN
    assert second["before"] is None and second["after"].run_id == later
    assert list(groups)[:2] == ["RF-DETR-Seg on demo", "RF-DETR-Seg on zoomed"]  # the older dataset first
    table = section(html, "before-after")
    assert "older runs not shown" not in table and "0.600" in table and "0.900" in table
    thin = section(html, "thin")
    assert thin.count("rendered at 2x") == 1 and thin.index("on demo") < thin.index("rendered at 2x")
    columns = [c.title for c in build.gallery_columns(groups, "zoomed")]
    assert columns == ["Correct masks", "RF-DETR-Seg before", "RF-DETR-Seg after"]


def test_class_with_no_test_parts_is_not_called_not_run(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0)])
    metrics = metrics_file()
    metrics["per_class"]["slab"] = {"AP": None, "AP50": None, "n_gt": 0, "n_gt_thin": 0}
    write_run(models, RUN, metrics=metrics, predict={"weights": "trained"})
    _, html = built(dirs)
    assert "no test parts" in section(html, "classes")


def test_new_model_folder_needs_no_code(dirs):
    models, _, _ = dirs
    write_ledger(models, [])
    home = models / "othernet" / "run-a"
    home.mkdir(parents=True)
    (home / "metrics_test.json").write_text(json.dumps(metrics_file()))
    (home / "predict_summary_test.json").write_text(json.dumps({"weights": "trained"}))
    world, html = built(dirs)
    assert "othernet" in runs.group_models(world.runs)
    assert "<h3>othernet</h3>" in section(html, "before-after")


def test_half_written_ledger_falls_back_to_the_last_good_copy(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    built(dirs)
    (models / "rfdetr-seg" / "gpu_ledger.json").write_text('[{"run_id": "x"')
    world, html = built(dirs)
    assert [e["run_id"] for e in world.entries] == [RUN]
    assert "last good copy" in html


def test_run_config_settings_are_read_flat_or_nested(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    write_run(models, RUN, config=CONFIG)
    world, _ = built(dirs)
    assert world.runs[0].config["epochs"] == 100


# ---------------------------------------------------------------- the Volume

class FakeModal:
    """The same calls as runs.ModalCli, served from a dict; `broken` makes every call fail."""

    def __init__(self, files=None, broken=False, containers=None):
        self.files, self.broken, self.started, self.gets = files or {}, broken, containers or {}, []

    def _check(self):
        if self.broken:
            raise runs.ModalError("network down")

    def list_dir(self, path=""):
        self._check()
        if not path:
            return {name.split("/")[0]: {"size": 0, "mtime": None, "type": "dir"} for name in self.files}
        rows = {name.split("/", 1)[1]: text for name, text in self.files.items() if name.startswith(path + "/")}
        return {name: {"size": len(text), "mtime": NOW, "type": "file"} for name, text in rows.items()}

    def get(self, remote, dest, mtime=None, timeout=None):
        self._check()
        self.gets.append(remote)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(self.files[remote])

    def containers(self):
        self._check()
        return self.started


def test_a_live_run_is_read_from_the_volume_cache(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    log = json.dumps(progress_line(0)) + "\n" + json.dumps(progress_line(1)) + "\n"
    modal = FakeModal({f"{RUN}/progress.jsonl": log, f"{RUN}/run_config.json": json.dumps({"config": CONFIG})})
    world, _ = built(dirs, modal)
    run = world.runs[0]
    assert len(run.progress) == 2 and run.config["epochs"] == 100
    assert run.stage.kind == "training"


def test_volume_failure_shows_last_known_data_and_says_so(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry()])
    log = json.dumps(progress_line(0)) + "\n"
    built(dirs, FakeModal({f"{RUN}/progress.jsonl": log}))
    world, html = built(dirs, FakeModal(broken=True))
    assert world.volume.error == "network down"
    assert world.runs[0].progress
    assert "Could not read the Modal Volume" in html and "last known data" in html
    assert world.volume.last_ok is not None


def test_finished_runs_do_not_touch_the_volume(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0)])
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"}, predictions=[])
    world, html = built(dirs, FakeModal(broken=True))
    assert world.volume.error is None and "Could not read" not in html


def test_a_run_that_never_got_a_machine_does_not_touch_the_volume(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("train", "cancelled_unscheduled", wall_s=9.0, gpu_s=0)])
    world, _ = built(dirs, FakeModal(broken=True))
    assert world.volume.error is None


def test_container_list_tells_a_running_predict_from_a_queued_one(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("predict")])
    world, _ = built(dirs, FakeModal(containers={"setout-rfdetr-seg": 1}))
    assert world.runs[0].stage.kind == "predicting"
    world, _ = built(dirs, FakeModal(containers={"some-other-app": 3}))
    assert world.runs[0].stage.kind == "queued"


def test_cli_wrapper_reads_the_shapes_modal_prints(monkeypatch):
    """Rows as `modal volume ls --json` and `modal container list --json` printed them on 30 Oct 2026."""
    cli = runs.ModalCli(exe="modal")
    listing = [{"filename": f"{RUN}/metrics_test.json", "type": "file", "created_modified": "2026-10-30 19:09:55+11:00", "size": 32195}]
    monkeypatch.setattr(cli, "_run", lambda *args, **kwargs: json.dumps(listing))
    row = cli.list_dir(RUN)["metrics_test.json"]
    assert row["size"] == 32195 and row["mtime"] == datetime(2026, 10, 30, 8, 9, 55, tzinfo=timezone.utc)
    containers = [{"container_id": "ta-1", "app_id": "ap-1", "app_name": "setout-rfdetr-seg", "start_time": "2026-10-30 19:25:43+11:00"},
                  {"container_id": "ta-2", "app_id": "ap-1", "app_name": "setout-rfdetr-seg", "start_time": "Pending"}]
    monkeypatch.setattr(cli, "_run", lambda *args, **kwargs: json.dumps(containers))
    assert cli.containers() == {"setout-rfdetr-seg": 1}


def test_cli_wrapper_reports_output_it_cannot_read(monkeypatch):
    cli = runs.ModalCli(exe="modal")
    monkeypatch.setattr(cli, "_run", lambda *args, **kwargs: "not json")
    with pytest.raises(runs.ModalError):
        cli.list_dir()
    with pytest.raises(runs.ModalError):
        cli.containers()


def test_cli_wrapper_reports_a_missing_command():
    with pytest.raises(runs.ModalError):
        runs.ModalCli(exe="/nonexistent/modal").list_dir()


# ---------------------------------------------------------------- tiles

def many_tiles():
    images = [{"id": i, "file_name": f"t{i}.png", "width": 64, "height": 64, "drawing_id": f"d{i // 2}"} for i in range(8)]
    anns = []
    for image_id, thin, compact in ((0, 5, 1), (1, 6, 1), (2, 2, 3), (4, 0, 9), (5, 0, 2), (6, 1, 1), (7, 0, 7)):
        anns += [annotation(len(anns), image_id, 1, (1, 1, 2, 9), True) for _ in range(thin)]
        anns += [annotation(len(anns), image_id, 2, (1, 1, 30, 30), False) for _ in range(compact)]
    return {"images": images, "categories": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], "annotations": anns}


def test_tile_choice_is_fixed_and_mixes_thin_and_compact():
    coco = many_tiles()
    first = tiles.choose_tiles(coco, per_kind=2)
    coco["images"].reverse()
    coco["annotations"].reverse()
    second = tiles.choose_tiles(coco, per_kind=2)
    assert [(k, i["id"]) for k, i, _, _ in first] == [(k, i["id"]) for k, i, _, _ in second]
    assert [k for k, *_ in first] == ["thin", "thin", "compact", "compact"]
    assert first[0][1]["id"] == 1  # most thin parts first


def test_tile_choice_keeps_one_tile_per_drawing():
    picks = tiles.choose_tiles(many_tiles(), per_kind=2)
    drawings = [img["drawing_id"] for _, img, _, _ in picks]
    assert len(drawings) == len(set(drawings))


def test_every_class_gets_its_own_look_and_the_thinnest_stay_solid():
    shares = {f"class{i:02d}": (19 - i) / 19 for i in range(19)}
    styles = tiles.class_styles(shares)
    assert len(set(styles.values())) == 19
    assert all(styles[f"class{i:02d}"].fill == "solid" for i in range(8))
    assert all(style.color in tiles.HUES for style in styles.values())


def test_a_class_keeps_its_style_when_others_come_and_go_in_a_filter():
    shares = {"a": 0.9, "b": 0.5}
    assert tiles.class_styles(shares)["a"] == tiles.class_styles({**shares, "c": 0.1})["a"]


def test_painted_mask_changes_only_its_own_pixels():
    base = np.full((20, 20, 3), 255, dtype=np.uint8)
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:15, 5:15] = True
    out = tiles.paint(base, [(mask, tiles.Style("#2a78d6"))])
    assert (out[~mask] == 255).all() and (out[mask] != 255).any(axis=-1).all()


def test_one_pixel_mask_stays_visible():
    base = np.full((20, 20, 3), 255, dtype=np.uint8)
    mask = np.zeros((20, 20), dtype=bool)
    mask[10, 2:18] = True
    for fill in ("solid", "hatch45", "hatch135", "outline"):
        out = tiles.paint(base, [(mask, tiles.Style("#2a78d6", fill))])
        assert (out[mask] != 255).any(axis=-1).all()


def rle_row(image_id, category_id, score, box):
    """A prediction with a polygon mask, like a results row."""
    return {"image_id": image_id, "category_id": category_id, "score": score, "segmentation": rectangle(*box)}


def test_every_model_is_drawn_by_the_same_rule_and_foreign_labels_stay_out_of_the_legend(dirs):
    models, datasets, out = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0), entry("predict", "ok", gpu_s=9.0, wall_s=12.0, run_id=BEFORE)])
    trained = [rle_row(1, 2, 0.9, (10, 50, 200, 200)), rle_row(1, 1, 0.8, (10, 10, 200, 13)), rle_row(1, 1, 0.1, (0, 0, 9, 9))]
    pretrained = [rle_row(1, 1016, 0.2, (0, 0, 290, 290)), rle_row(1, 1005, 0.1, (5, 5, 90, 90)), rle_row(1, 1005, 0.01, (9, 9, 20, 20))]
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"}, predictions=trained, config=CONFIG)
    write_run(models, BEFORE, metrics=metrics_file(ap=0.0), predict={"weights": "pretrained"}, predictions=pretrained, config=CONFIG)
    world, html = built(dirs)
    images = section(html, "images")
    assert "strongest 2, scores 0.90 to 0.80" in images  # tile with two correct parts, after
    assert "strongest 2, scores 0.20 to 0.10" in images  # the same count before, at far lower scores
    assert "outlined: a label that is not one of these classes" in images
    assert "≥" not in images
    assert "strongest masks, as many as the tile has correct parts" in images
    assert (out / "images" / "panels.json").exists()
    assert list((out / "images").glob("*__zoom.png"))


def test_panels_are_not_repainted_until_their_source_changes(dirs):
    models, _, out = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0)])
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"}, predictions=[], config=CONFIG)
    built(dirs)
    panels = sorted((out / "images").glob("*.png"))
    stamps = [p.stat().st_mtime_ns for p in panels]
    built(dirs)
    assert stamps == [p.stat().st_mtime_ns for p in panels]


def test_gallery_waits_for_a_half_written_predictions_file(dirs):
    models, _, _ = dirs
    write_ledger(models, [entry("predict", "ok", gpu_s=9.0, wall_s=12.0)])
    write_run(models, RUN, metrics=metrics_file(), predict={"weights": "trained"}, config=CONFIG)
    (models / "rfdetr-seg" / RUN / "predictions_test.json").write_text('[{"image_id"')
    _, html = built(dirs)
    assert "image not available" in section(html, "images")


# ---------------------------------------------------------------- marks

def test_chart_ticks_are_clean():
    assert charts.nice_ticks(14.8) == [0, 5, 10, 15]
    assert charts.x_ticks(1, 21) == [1, 5, 10, 15, 20]


def test_chart_data_cannot_close_its_script_element():
    assert "</script>" not in charts.chart_json({"a": "</script>"})


def test_end_labels_are_dropped_when_they_would_collide():
    series = [{"name": "one", "cls": "s1", "values": [0.5, 0.5]}, {"name": "two", "cls": "s2", "values": [0.5, 0.51]}]
    assert 'class="end"' not in charts.line_chart("c", [1, 2], series, [0, 0.5, 1])


def test_missing_values_draw_nothing_rather_than_zero():
    assert charts.bar(None) == ""
    assert charts.sparkline([None, None]) == ""
    assert 'class="before"' not in charts.dumbbell(None, 0.5)
