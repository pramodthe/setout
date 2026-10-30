"""The dashboard page: one HTML file with inline CSS, inline SVG and an inline script.

Anything without a value says "not run yet". Validation numbers appear only under a validation
label, and test numbers only come from a run's metrics_test.json. Everything that reaches the page
from a file is escaped.
"""
from collections import Counter
from html import escape as esc
from pathlib import Path

from dashboard import charts, runs, tiles

HERE = Path(__file__).resolve().parent
NOT_RUN = '<span class="pending">not run yet</span>'
FEW_PARTS = 30  # an AP over fewer test parts than this moves a lot on a single part
CONFUSED_PAIRS = 8
WIDTH_LABELS = {"<2": "1–2 px", "2-4": "2–4 px", "4-6": "4–6 px"}
OUTCOMES = {
    "ok": ("good", "✓", "Ran"),
    "failed": ("critical", "✕", "Failed"),
    "cancelled_unscheduled": ("serious", "▲", "Never got a GPU"),
}
STAGES = {
    "training": ("active", "●", "Training"),
    "queued": ("warning", "◔", "Waiting for a GPU"),
    "queued_or_predicting": ("warning", "◔", "Waiting for a GPU, or predicting"),
    "predicting": ("active", "●", "Predicting the test split"),
    "scoring": ("active", "●", "Scoring the test split"),
    "finished": ("good", "✓", "Finished"),
    "failed": ("critical", "✕", "Failed"),
    "stopped": ("serious", "▲", "Stopped"),
    "trained": ("serious", "▲", "Trained, not tested yet"),
    "not_started": ("serious", "▲", "Not started"),
}


# ---------------------------------------------------------------- small formats

def num(value, digits=3):
    return NOT_RUN if value is None else f"{value:.{digits}f}"


def duration(seconds):
    if seconds is None:
        return None
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def money(value):
    if value is None:
        return None
    return "under $0.01" if 0 < value < 0.01 else f"${value:,.2f}"


def clock(when, now):
    """Local time; the date too when it is not today."""
    if when is None:
        return "not known"
    local, today = when.astimezone(), now.astimezone()
    return local.strftime("%H:%M") if local.date() == today.date() else local.strftime("%d %b %H:%M")


def table(head, rows, classes=""):
    """A table inside a scroller, so a wide one never scrolls the page sideways."""
    return f'<div class="scroll"><table class="{classes}"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'


def tr(*cells):
    return f"<tr>{''.join(cells)}</tr>"


def td(content, cls=""):
    return f'<td class="{cls}">{content}</td>' if cls else f"<td>{content}</td>"


def th(content, cls=""):
    return f'<th scope="col" class="{cls}">{content}</th>' if cls else f'<th scope="col">{content}</th>'


def swatch(style):
    return f'<span class="sw {style.fill}" style="--c:{style.color}"></span>'


def status(cls, icon, text):
    return f'<span class="state {cls}"><span class="icon" aria-hidden="true">{icon}</span>{esc(text)}</span>'


def card(section_id, title, body, intro=""):
    note = f'<p class="note">{intro}</p>' if intro else ""
    return f'<section id="{section_id}"><h2>{title}</h2>{note}{body}</section>'


def run_tag(run):
    return f'<code title="{esc(run.run_id)}">{esc(runs.short_run(run.run_id))}</code>'


# ---------------------------------------------------------------- 1. what's happening now

def limit_sentence(plan):
    """Which limit ends the run, in words, while it runs."""
    cap = f"{plan.cap_s / 60:.0f}-minute" if plan.cap_s else None
    if plan.limit == "time cap" and plan.epochs:
        return f"The {cap} time cap comes before the {plan.epochs}-pass limit."
    if plan.limit == "time cap":
        return f"The {cap} time cap ends it."
    if plan.limit == "pass limit" and plan.cap_s:
        return f"The {plan.epochs}-pass limit comes before the {cap} time cap."
    if plan.limit == "pass limit":
        return f"The {plan.epochs}-pass limit ends it."
    return ""


def end_sentence(plan):
    """Which limit ended the run, in words, once it has finished."""
    if plan.limit == "time cap":
        return f"Stopped by time cap: the {plan.cap_s / 60:.0f}-minute limit came before the {plan.epochs}-pass limit."
    if plan.limit == "early stopping":
        return f"Early stopping ended it: {plan.patience} passes without a better validation score."
    return f"It reached the {plan.epochs}-pass limit."


def best_sentence(plan, run):
    if not plan.best_pass:
        return ""
    weights = " on the EMA weights" if plan.best_ema else ""
    scored = " That checkpoint was scored on test." if run.metrics else ""
    return f"Best validation mask mAP: {plan.best_score:.3f} at pass {plan.best_pass}{weights}.{scored}"


def stage_detail(run, plan, now):
    stage, job = run.stage, run.stage.job or {}
    gpu = esc(str(job.get("gpu", "GPU")))
    asked = clock(runs.parse_time(job.get("started_at")), now)
    waited = duration(runs.wait_s(job, now))
    if stage.kind == "training":
        if not plan.done:
            cap = f" or {plan.cap_s / 60:.0f} minutes" if plan.cap_s else ""
            limits = f"Limit: {plan.epochs} passes{cap}, whichever comes first." if plan.epochs else ""
            return f"First pass under way on {gpu}. {limits}"
        patience = ""
        if plan.patience and plan.since_best is not None:
            patience = f" Early stopping: {plan.since_best} of {plan.patience} passes without a better score."
        return f"Pass {plan.done} of about {plan.about}. {limit_sentence(plan)}{patience}"
    if stage.kind == "queued":
        return f"Asked for {gpu} at {asked}, {waited} ago. No container has started."
    if stage.kind == "queued_or_predicting":
        return (f"Asked for {gpu} at {asked}, {waited} ago. Prediction writes nothing until it ends and the "
                "container list could not be read, so this page cannot tell which it is.")
    if stage.kind == "predicting":
        return f"On {gpu}, asked for at {asked}. Prediction writes nothing until it ends, so there is no count to show."
    if stage.kind == "scoring":
        return "Predictions are in. The test scores are not, yet."
    if stage.kind == "finished":
        if run.train_summary:
            return f"Trained for {plan.done} passes. {end_sentence(plan)} {best_sentence(plan, run)}"
        return "Predicted and scored without training." if run.weights == "pretrained" else "Test scores are in."
    if stage.kind == "failed":
        return f"The {esc(str(job.get('function', '')))} call on {gpu}, asked for at {asked}, failed. Nothing is running."
    if stage.kind == "stopped":
        return f"The last request, for {gpu} at {asked}, was cancelled before a container started. Nothing is running."
    if stage.kind == "trained":
        return f"Training finished after {plan.done} passes. Prediction on the test split has not started."
    return "Nothing has run yet."


def overdue_note(run, now):
    """A call open longer than its hard timeout has queued for ages, or its client died."""
    job = run.stage.job or {}
    started = runs.parse_time(job.get("started_at"))
    if job.get("status") != "running" or not started or not job.get("reserved_s"):
        return ""
    open_s = (now - started).total_seconds()
    if open_s <= job["reserved_s"]:
        return ""
    return (f'<p class="warn">This call has been open {duration(open_s)}, past its {duration(job["reserved_s"])} '
            "limit. It is either still queued or its client has died; the ledger cannot say which.</p>")


def meter(plan):
    """Progress against the limit that applies; only while a run trains and the limit is known."""
    if not plan.about or not plan.done:
        return ""
    share = min(1.0, plan.done / plan.about) * 100
    return (f'<div class="meter" role="img" aria-label="Pass {plan.done} of about {plan.about}">'
            f'<i style="width:{share:.1f}%"></i></div>')


def stat_tile(label, value, sub=""):
    shown = value if value is not None else NOT_RUN
    return f'<div class="tile"><p class="label">{label}</p><p class="value">{shown}</p><p class="sub">{sub}</p></div>'


def time_left_tile(run, plan, now):
    if run.stage.kind == "training":
        left = runs.time_left_s(plan, runs.elapsed_now(run, now))
        if left is None:
            return stat_tile("Time left", "not known yet", "needs one finished pass")
        return stat_tile("Time left", f"about {duration(left)}", f"to the {plan.limit}, an estimate")
    if run.stage.kind == "queued":
        return stat_tile("Time left", "no estimate", "waiting for a machine")
    if run.live:
        return stat_tile("Time left", "no estimate", "this step logs no progress")
    return stat_tile("Time left", "none", "nothing is running")


def now_block(run, now):
    plan = runs.plan_passes(run.progress, run.config, run.train_summary)
    cls, icon, title = STAGES[run.stage.kind]
    bits = [esc(b) for b in (run.model_name, run.variant, run.dataset) if b]
    if run.weights == "pretrained":
        bits.append("before training")
    elif run.weights and run.stage.kind not in ("training", "queued"):
        bits.append("after training")
    totals = runs.run_totals(run)
    by_gpu = ", ".join(f"{esc(str(g))} {duration(s)}" for g, s in sorted(totals.seconds_by_gpu.items()))
    if totals.unmeasured:
        by_gpu = f"{by_gpu}, plus a call still open" if by_gpu else "a call still open"
    seconds = sum(totals.seconds_by_gpu.values())
    memory, device = runs.peak_memory(run), (run.environment.get("gpu") or {})
    of = f"of {device['memory_gib']} GiB on {esc(str(device.get('name', 'the GPU')))}" if device.get("memory_gib") else ""
    memory_text = f"{memory} GiB" if memory is not None else ("not recorded" if seconds else None)
    bill = runs.MODAL_BILL["training"].get(run.run_id)
    cost_note = f"training was {money(bill['all_in_usd'])} all-in" if bill else "CPU and memory are extra"
    tiles_html = (
        time_left_tile(run, plan, now)
        + stat_tile("GPU time so far", gpu_time_text(seconds) if seconds or not totals.unmeasured else None, by_gpu)
        + stat_tile("GPU-only cost so far", money(totals.cost), cost_note)
        + stat_tile("Peak GPU memory", memory_text, of)
    )
    return (f'<article class="status"><p class="stage">{status(cls, icon, title)}</p>'
            f'<p class="what">{" · ".join(bits)} <code>{esc(run.run_id)}</code></p>'
            f'<p>{stage_detail(run, plan, now)}</p>{overdue_note(run, now)}'
            f'{meter(plan) if run.stage.kind == "training" else ""}<div class="kpis">{tiles_html}</div></article>')


def queue_finding(entries):
    """The queue waits in one or two sentences, from the ledger."""
    if not entries:
        return ""
    never = [e for e in entries if e.get("status") == "cancelled_unscheduled"]
    counts = Counter(e.get("status") for e in entries)
    parts = [f"{n} {words}" for n, words in ((counts["ok"], "ran"), (counts["running"], "still open"),
                                             (counts["failed"], "failed"), (len(never), "never got a machine")) if n]
    told = " and ".join(parts) if len(parts) < 3 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    text = f"{len(entries)} GPU request{'' if len(entries) == 1 else 's'} so far: {told}."
    if never:
        longest = max(never, key=lambda e: e.get("wall_s") or 0)
        waited = sum(e.get("wall_s") or 0 for e in never)
        text += (f" Cancelled requests spent {duration(waited)} queued in all; the longest wait was "
                 f"{duration(longest.get('wall_s'))} for {esc(str(longest.get('gpu')))}.")
    return text


def wait_text(entry, run, now):
    waited = runs.attempt_wait(entry, run, now)
    return duration(waited) if waited is not None else "not known"


def gpu_time_text(seconds):
    if seconds is None:
        return "not measured"
    return duration(seconds) if seconds else "none"


def attempt_outcome(entry, by_run):
    if entry.get("status") == "running":
        run = by_run.get(entry.get("run_id"))
        waiting = run is not None and run.stage.kind in ("queued", "queued_or_predicting")
        return status("warning", "◔", "Waiting") if waiting else status("active", "●", "Running")
    return status(*OUTCOMES.get(entry.get("status"), ("serious", "▲", str(entry.get("status")))))


def attempts_table(world):
    by_run = {r.run_id: r for r in world.runs}
    rows = []
    for entry in reversed(world.entries):
        run = by_run.get(entry.get("run_id"))
        seconds = runs.entry_seconds(entry, run)
        job = entry.get("function", "")
        job = job if job in ("train", "predict") else "probe"
        tip = esc(str(entry.get("note") or ""))
        outcome = attempt_outcome(entry, by_run)
        outcome = f'<span data-tip="{tip}" tabindex="0">{outcome}</span>' if tip else outcome
        rows.append(tr(
            td(clock(runs.parse_time(entry.get("started_at")), world.now), "n"),
            td(esc(str(entry.get("gpu")))), td(esc(job)), td(wait_text(entry, run, world.now), "n"), td(outcome),
            td(gpu_time_text(seconds), "n"), td(money(runs.entry_cost(entry, run)) or "not known", "n"),
            td(f'<code title="{esc(str(entry.get("run_id")))}">{esc(runs.short_run(str(entry.get("run_id"))))}</code>')))
    head = tr(th("Asked", "n"), th("GPU"), th("Job"), th("Queue and start-up", "n"), th("Outcome"),
              th("GPU time", "n"), th("GPU-only cost", "n"), th("Run"))
    return table(head, rows, "attempts")


def section_now(world):
    focus = runs.focus_runs(world.runs)
    blocks = "".join(now_block(run, world.now) for run in focus) or '<p class="pending">No run in the ledger yet.</p>'
    if focus and not any(run.live for run in focus):
        blocks = f"<p>Nothing is running right now. The latest runs:</p>{blocks}"
    finding = queue_finding(world.entries)
    attempts = attempts_table(world) if world.entries else ""
    return card("now", "What's happening now", f'{blocks}<h3>GPU attempts</h3><p class="note">{finding} '
                'Queue and start-up is the wall time around the function, so it includes container start.</p>' + attempts)


# ---------------------------------------------------------------- 2. score curves

def curve_charts(run, registry):
    passes = runs.full_passes(run.progress)
    xs = list(range(1, len(passes) + 1))
    plan = runs.plan_passes(run.progress, run.config, run.train_summary)
    x_top = plan.about if run.stage.kind == "training" else None
    scores = [{"name": "mAP50", "cls": "s2", "digits": 3, "values": [runs.metric(p, runs.MAP50_KEYS) for p in passes]},
              {"name": "mAP", "cls": "s1", "digits": 3, "values": [runs.metric(p, runs.MAP_KEYS) for p in passes]}]
    loss = {"name": "Training loss", "cls": "s3", "digits": 2,
            "values": [(p.get("metrics") or {}).get(runs.LOSS_KEY) for p in passes]}
    best = (plan.best_pass, plan.best_score, f"best: pass {plan.best_pass}") if plan.best_pass else None
    key = "".join(c if c.isalnum() else "-" for c in run.run_id)
    score_id, loss_id = f"score-{key}", f"loss-{key}"
    registry[score_id] = {"x": xs, "xLabel": "Pass", "series": scores}
    registry[loss_id] = {"x": xs, "xLabel": "Pass", "series": [loss]}
    top = max((v for v in loss["values"] if v is not None), default=1.0)
    score_svg = charts.line_chart(
        score_id, xs, [{**s, "fmt": "{:.3f}"} for s in scores], [0, 0.25, 0.5, 0.75, 1.0], x_top, best,
        "Validation mask mAP and mAP50 by pass")
    loss_svg = charts.line_chart(
        loss_id, xs, [{"name": "loss", "cls": "s3", "values": loss["values"], "fmt": "{:.2f}"}],
        charts.nice_ticks(top), x_top, None, "Training loss by pass")
    return (f'<div class="charts"><figure class="chart"><figcaption>Mask score on the validation split</figcaption>'
            f'{charts.legend(scores)}{score_svg}</figure>'
            f'<figure class="chart"><figcaption>Training loss, not a validation number</figcaption>'
            f'<div class="legend-gap"></div>{loss_svg}</figure></div>')


def curve_table(run):
    rows = []
    for i, p in enumerate(runs.full_passes(run.progress), 1):
        rows.append(tr(td(i, "n"), td(num(runs.metric(p, runs.MAP_KEYS)), "n"),
                       td(num(runs.metric(p, runs.MAP50_KEYS)), "n"),
                       td(num((p.get("metrics") or {}).get(runs.LOSS_KEY), 2), "n"),
                       td(duration((p.get("train_s") or 0) + (p.get("val_s") or 0)), "n")))
    head = tr(th("Pass", "n"), th("Validation mAP", "n"), th("Validation mAP50", "n"), th("Training loss", "n"), th("Pass time", "n"))
    return f"<details><summary>Table of these numbers</summary>{table(head, rows)}</details>"


def stub_note(run):
    passes = runs.full_passes(run.progress)
    if len(passes) == len(run.progress):
        return ""
    stub = run.progress[-1]
    return (f'<p class="note">The time cap stopped training {stub.get("micro_batches")} batch into pass {len(run.progress)}. '
            f"That forced one last validation, which repeats pass {len(passes)}, so it is left out here.</p>")


def best_note(run):
    plan = runs.plan_passes(run.progress, run.config, run.train_summary)
    if not plan.best_pass:
        return ""
    return (f'<p class="note">{best_sentence(plan, run)} Passes count from 1; the run\'s log counts epochs from 0, '
            f"so this is epoch {plan.best_pass - 1} there.</p>")


def section_curves(world, registry):
    trained = sorted((r for r in world.runs if r.progress and not r.smoke), key=lambda r: r.last_seen, reverse=True)
    body = "".join(f'<p class="what">{run_tag(r)}</p>{curve_charts(r, registry)}{best_note(r)}{stub_note(r)}{curve_table(r)}'
                   for r in trained)
    intro = ("A pass is one full run through the training tiles. mAP averages how well masks match across "
             "strict and loose overlap limits, where 1.0 is perfect; mAP50 only asks for half the mask to overlap. "
             "Both are on the validation split, never the test split. Loss is a training number, the last value "
             "logged in each pass, so it is noisy; these runs log no validation loss.")
    return card("curves", "Score curves, validation split", body or NOT_RUN, intro)


# ---------------------------------------------------------------- 3. before and after

def instance_value(metrics, block, group, field):
    data = (metrics or {}).get(block) or {}
    row = data.get(group) or (data.get("by_min_width_px") or {}).get(group)
    return row.get(field) if row else None


def agnostic_value(metrics, group, field):
    data = (metrics or {}).get("class_agnostic") or {}
    if group in ("thin", "compact"):
        return (data.get("recall_iou50" if field == "recall50" else "mean_iou") or {}).get(group)
    row = (data.get("by_min_width_px") or {}).get(group)
    return row.get(field) if row else None


def coco_value(metrics, key):
    return ((metrics or {}).get("coco") or {}).get(key)


def agnostic_score(metrics, key):
    return ((metrics or {}).get("class_agnostic") or {}).get(key)


def before_after_rows(before, after):
    """(label, class-based pair, class-agnostic pair). Per-part rows count any score on both sides."""
    b, a = (before.metrics if before else None), (after.metrics if after else None)
    rows = [("Mask AP", coco_value, agnostic_score, "AP"), ("AP50", coco_value, agnostic_score, "AP50")]
    out = [(label, (cf(b, key), cf(a, key)), (af(b, key), af(a, key))) for label, cf, af, key in rows]
    for label, group, field in (("Recall at IoU 0.5, thin", "thin", "recall50"),
                                ("Recall at IoU 0.5, compact", "compact", "recall50"),
                                ("Mean IoU, thin", "thin", "mean_iou"), ("Mean IoU, compact", "compact", "mean_iou")):
        out.append((label, (instance_value(b, "instances_any_score", group, field),
                            instance_value(a, "instances_any_score", group, field)),
                    (agnostic_value(b, group, field), agnostic_value(a, group, field))))
    return out


def before_after_table(name, group):
    before, after = group["before"], group["after"]
    if not (before and before.metrics) and not (after and after.metrics):
        return f'<h3>{esc(name)}</h3><p>{NOT_RUN}</p>'
    head = (f'<tr><th scope="col" rowspan="2">Test split</th><th scope="colgroup" colspan="2" class="band">Class-based: the label must match</th>'
            f'<th class="track" rowspan="2"></th><th scope="colgroup" colspan="2" class="band">Class-agnostic: labels ignored</th>'
            f'<th class="track" rowspan="2"></th></tr>' + tr(th("Before", "n"), th("After", "n"), th("Before", "n"), th("After", "n")))
    rows = []
    for label, cls_pair, agn_pair in before_after_rows(before, after):
        rows.append(tr(td(label), td(num(cls_pair[0]), "n"), td(num(cls_pair[1]), "n"),
                       td(charts.dumbbell(*cls_pair), "track"), td(num(agn_pair[0]), "n"), td(num(agn_pair[1]), "n"),
                       td(charts.dumbbell(*agn_pair), "track")))
    who = []
    for word, run in (("Before", before), ("After", after)):
        who.append(f"{word}: {run_tag(run)}" if run else f"{word}: {NOT_RUN}")
    older = [run_tag(run) for run in group["older"] if run.metrics]
    if older:
        who.append(f"older runs not shown: {', '.join(older)}")
    return f'<h3>{esc(name)}</h3><p class="what">{" · ".join(who)}</p>{table(head, rows, "ba")}'


def parity_note(models):
    notes = []
    for group in models.values():
        for run in (group["before"], group["after"]):
            if run and run.parity and run.parity.get("max_abs_diff") is not None:
                notes.append(f"{run_tag(run)} matches pycocotools to within {run.parity['max_abs_diff']:.6f}")
    return f'<p class="note">Scorer check: {"; ".join(notes)}.</p>' if notes else ""


def unplaced_note(world):
    """A scored run that does not say its weights is neither before nor after: say so."""
    left_out = [run_tag(r) for r in world.runs if r.metrics and not r.smoke and r.weights is None]
    if not left_out:
        return ""
    return (f'<p class="warn">Scored but left out, because neither its predict_summary_test.json nor its ledger says '
            f'which weights it used: {", ".join(left_out)}.</p>')


def section_before_after(world, models):
    intro = ("All on the held-out test split. Before is the model out of the box; after is the same model fine-tuned "
             "on our tiles. An untrained model's class score is about zero by definition, because it only knows other "
             "classes and none of its labels can match ours. Class-agnostic scoring ignores the label and asks only "
             "whether the mask is right, so it is the fair comparison. Recall and mean IoU here count masks at any "
             "score, because an untrained model's scores mean little on drawings. The test tiles come from the same "
             "generator as the training tiles, so read these scores as an upper bound for real drawings.")
    legend = ('<p class="ba-key"><span class="dot-key before"></span>Before<span class="dot-key after"></span>After '
              '<span class="muted">on a track from 0 to 1</span></p>')
    body = "".join(before_after_table(name, group) for name, group in models.items())
    return card("before-after", "Before and after, test split", legend + body + unplaced_note(world) + parity_note(models), intro)


# ---------------------------------------------------------------- 4. per class

def class_rows(run, manifest):
    """One dict per class from the run's test metrics and validation log."""
    passes = runs.full_passes(run.progress)
    plan = runs.plan_passes(run.progress, run.config, run.train_summary)
    best = passes[plan.best_pass - 1] if plan.best_pass else None
    per_class = (run.metrics or {}).get("per_class") or {}
    split = (((manifest or {}).get("splits") or {}).get("test") or {}).get("per_class") or {}
    names = sorted(set(per_class) | set(runs.class_names(passes)) | set(split))
    rows = []
    for name in names:
        row, counts = per_class.get(name) or {}, split.get(name) or {}
        parts = row.get("n_gt", counts.get("instances"))
        thin = row.get("n_gt_thin", counts.get("thin"))
        rows.append({"name": name, "ap": row.get("AP"), "ap50": row.get("AP50"), "parts": parts,
                     "thin_share": thin / parts if parts else None,
                     "val_ap": runs.class_ap(best, name) if best else None,
                     "history": [runs.class_ap(p, name) for p in passes]})
    return sorted(rows, key=lambda r: (r["ap"] is None and r["val_ap"] is None,
                                       r["ap"] if r["ap"] is not None else (r["val_ap"] or 0.0), r["name"]))


def history_tip(row):
    points = [(i, v) for i, v in enumerate(row["history"], 1) if v is not None]
    if not points:
        return ""
    best_i, best_v = max(points, key=lambda p: (p[1], -p[0]))
    return (f"Validation AP: pass {points[0][0]} {points[0][1]:.3f}, best {best_v:.3f} at pass {best_i}, "
            f"pass {points[-1][0]} {points[-1][1]:.3f}")


def class_table(run, manifest, styles):
    rows = class_rows(run, manifest)
    if not rows:
        return f"<p>{NOT_RUN}</p>"
    plan = runs.plan_passes(run.progress, run.config, run.train_summary)
    val_head = f"Validation AP, pass {plan.best_pass}" if plan.best_pass else "Validation AP"
    head = tr(th("Class"), th("Test parts", "n"), th("Thin share", "n"), th("Test AP", "n"), th("Test AP50", "n"),
              th(val_head, "n"), th("Validation AP by pass"))
    body = []
    blank = "no test parts" if run.metrics else NOT_RUN  # scored, and this class has nothing to score
    for row in rows:
        few = ' <span class="muted" data-tip="Few test parts: this AP moves a lot on a single part" tabindex="0">few</span>' \
            if row["parts"] is not None and row["parts"] < FEW_PARTS else ""
        share = f"{row['thin_share'] * 100:.0f}%" if row["thin_share"] is not None else blank
        spark = charts.sparkline(row["history"])
        tip = esc(history_tip(row))
        body.append(tr(
            td(f'{swatch(styles.get(row["name"], tiles.OTHER))}{esc(row["name"])}', "name"),
            td(f'{row["parts"] if row["parts"] is not None else NOT_RUN}{few}', "n"), td(share, "n"),
            td(f'{num(row["ap"]) if row["ap"] is not None else blank}{charts.bar(row["ap"])}', "n has-bar"),
            td(num(row["ap50"]) if row["ap50"] is not None else blank, "n"),
            td(num(row["val_ap"]), "n"),
            td(f'<span data-tip="{tip}" tabindex="0">{spark}</span>' if spark else NOT_RUN)))
    return table(head, body, "classes")


def class_history_table(run):
    passes = runs.full_passes(run.progress)
    names = runs.class_names(passes)
    if not names:
        return ""
    head = tr(th("Class"), *[th(i, "n") for i in range(1, len(passes) + 1)])
    rows = [tr(td(esc(name), "name"), *[td(num(runs.class_ap(p, name)), "n") for p in passes]) for name in names]
    return f"<details><summary>Table of validation AP per class and pass</summary>{table(head, rows)}</details>"


def section_classes(models, manifests, styles):
    parts = []
    for name, group in models.items():
        run = group["after"]
        if run is None:
            parts.append(f"<h3>{esc(name)}</h3><p>{NOT_RUN}</p>")
            continue
        parts.append(f'<h3>{esc(name)}</h3><p class="what">{run_tag(run)}</p>'
                     f"{class_table(run, manifests.get(run.dataset), styles)}{class_history_table(run)}")
    intro = ("Worst first, by test AP once it exists. Each swatch is the class's colour in the images below. "
             "The sparklines are validation, on one 0 to 1 scale; the dot is the last pass.")
    return card("classes", "Per class", "".join(parts), intro)


# ---------------------------------------------------------------- 5. thin vs compact

def width_groups(runs_on_show):
    """Rows from wide to narrow; the width buckets appear when the metrics have them."""
    present = set()
    for run in runs_on_show:
        present |= set(((run.metrics or {}).get("instances") or {}).get("by_min_width_px") or {})
    buckets = [(f"Thin, {text}", key) for key, text in reversed(WIDTH_LABELS.items()) if key in present]
    return [("Compact", "compact"), ("Thin, all", "thin")] + buckets


def thin_table(title, field, group, thr):
    before, after = group["before"], group["after"]
    b, a = (before.metrics if before else None), (after.metrics if after else None)
    rows = []
    for label, key in width_groups([r for r in (before, after) if r]):
        count = instance_value(a or b, "instances", key, "n")
        cells = (agnostic_value(b, key, field), agnostic_value(a, key, field),
                 instance_value(a, "instances_any_score", key, field), instance_value(a, "instances", key, field))
        rows.append(tr(td(label, "name"), td(count if count is not None else NOT_RUN, "n"),
                       *[td(f"{num(v)}{charts.bar(v)}", "n has-bar") for v in cells]))
    head = tr(th(title), th("Parts", "n"), th("Before, labels ignored", "n"), th("After, labels ignored", "n"),
              th("After, right label", "n"), th(f"After, right label, score ≥ {thr:.2f}", "n"))
    return table(head, rows, "thin")


def confused_table(run):
    conf = (run.metrics or {}).get("confusion") or {}
    swaps = (conf.get("swaps") or [])[:CONFUSED_PAIRS]
    if not swaps:
        return ""
    rows = [tr(td(esc(s["gt"]), "name"), td(esc(s["pred"]), "name"), td(s["count"], "n"),
               td(f"{s['share_of_gt'] * 100:.0f}%", "n")) for s in swaps]
    head = tr(th("Correct class"), th("Came back as"), th("Parts", "n"), th("Share of that class", "n"))
    return (f'<h4>Most-confused class pairs</h4><p class="note">Masks that matched at IoU {conf.get("iou", 0.5)} '
            f'and score ≥ {conf.get("score_thr", 0.5):.2f}, but under the wrong class.</p>{table(head, rows, "pairs")}')


def section_thin(models):
    parts = []
    for name, group in models.items():
        before, after = group["before"], group["after"]
        if not (before and before.metrics) and not (after and after.metrics):
            parts.append(f"<h3>{esc(name)}</h3><p>{NOT_RUN}</p>")
            continue
        thr = (after or before).score_thr
        parts.append(f'<h3>{esc(name)}</h3><div class="pair">'
                     f'{thin_table("Recall at IoU 0.5", "recall50", group, thr)}'
                     f'{thin_table("Mean IoU", "mean_iou", group, thr)}</div>'
                     f'{confused_table(after) if after else ""}')
    intro = ("Test split. Thin means under 6 px wide in the tile, measured from the geometry. Each part counts once, "
             "scored by its best-overlapping mask. Before against after is the fair comparison: labels ignored, any score. "
             "Right label asks for the correct class too, and the last column also needs the operating score, so a gap "
             "between the last two means the mask exists but its score is low.")
    return card("thin", "Thin vs compact, test split", "".join(parts), intro)


# ---------------------------------------------------------------- 6. images

def panel(column, row):
    """A figure: the whole tile, linked at full size, or a plain placeholder."""
    painted = row.panels.get(column.key)
    if not painted:
        missing = NOT_RUN if column.source is None and column.key != "gt" else '<span class="pending">image not available</span>'
        return f'<figure class="panel"><div class="empty">{missing}</div><figcaption>{esc(column.title)}</figcaption></figure>'
    alt = f"{column.title} on test tile {row.image['file_name']}"
    box = zoom_outline(row)
    return (f'<figure class="panel"><a class="shot" href="{esc(painted["file"])}" target="_blank" rel="noopener">'
            f'<img src="{esc(painted["file"])}" alt="{esc(alt)}" width="1008" height="1008" loading="lazy">{box}</a>'
            f'<figcaption>{esc(column.title)}: {panel_count(column, painted)}. '
            f'<a href="{esc(painted["file"])}" target="_blank" rel="noopener">Full size</a></figcaption></figure>')


def panel_count(column, painted):
    masks, scores = painted["masks"], painted.get("scores")
    if column.key == "gt":
        return f'{masks} part{"" if masks == 1 else "s"}'
    if not masks:
        return "no masks"
    return f"strongest {masks}, scores {scores[0]:.2f} to {scores[1]:.2f}" if scores else f"strongest {masks}"


def zoom_outline(row):
    """Where the enlarged crop sits on the whole tile."""
    if not row.zoom:
        return ""
    x0, y0, x1, y1 = row.zoom
    w, h = row.image["width"], row.image["height"]
    return (f'<span class="zoom-box" style="left:{x0 / w * 100:.2f}%;top:{y0 / h * 100:.2f}%;'
            f'width:{(x1 - x0) / w * 100:.2f}%;height:{(y1 - y0) / h * 100:.2f}%"></span>')


def zoom_panel(column, row):
    painted = row.panels.get(column.key)
    if not painted or not painted.get("zoom"):
        return f'<figure class="panel"><div class="empty">{NOT_RUN if column.source is None and column.key != "gt" else ""}</div></figure>'
    alt = f"{column.title}, enlarged around the thin parts of {row.image['file_name']}"
    return (f'<figure class="panel"><a class="shot" href="{esc(painted["zoom"])}" target="_blank" rel="noopener">'
            f'<img src="{esc(painted["zoom"])}" alt="{esc(alt)}" width="1008" height="1008" loading="lazy"></a>'
            f'<figcaption>{esc(column.title)}, thin parts enlarged.</figcaption></figure>')


def tile_row(gallery, row):
    kind = "Thin-heavy tile" if row.kind == "thin" else "Compact tile"
    panels = "".join(panel(column, row) for column in gallery.columns)
    zoomed = "".join(zoom_panel(column, row) for column in gallery.columns) if row.zoom else ""
    zoom_row = f'<div class="panels zoom">{zoomed}</div>' if zoomed else ""
    return (f'<article class="tile-row"><h3>{kind}: {row.thin} thin, {row.compact} compact parts</h3>'
            f'<p class="what"><code>{esc(row.image["file_name"])}</code></p><div class="panels">{panels}</div>{zoom_row}</article>')


def section_images(gallery, styles):
    if gallery is None:
        return card("images", "Side by side on test tiles", f"<p>{NOT_RUN}</p>")
    keys = "".join(f"<li>{swatch(styles.get(name, tiles.OTHER))}{esc(name)}</li>" for name in gallery.classes)
    if gallery.other_classes:
        keys += f"<li>{swatch(tiles.OTHER)}outlined: a label that is not one of these classes</li>"
    intro = ("The same tiles for every model, picked by a fixed rule: the most thin parts, then the most compact parts "
             "with the fewest thin ones, at most one tile per drawing. Every model is drawn by the same rule: its "
             "strongest masks, as many as the tile has correct parts. A score cut-off would leave an untrained model "
             "almost blank, because its scores mean little on drawings. Every mask has a solid edge. Each thin-heavy "
             "tile has a second row enlarged around its thin parts, because they are only a few pixels wide; the box "
             "on the tile shows where. Any image opens at full size.")
    rows = "".join(tile_row(gallery, row) for row in gallery.rows)
    return card("images", "Side by side on test tiles", f'<ul class="legend classes">{keys}</ul>{rows}', intro)


# ---------------------------------------------------------------- 7. limits

def dataset_rows(name, manifest):
    rows = []
    for split, row in ((manifest.get("splits") or manifest.get("counts") or {}).items()):
        instances, thin = row.get("instances"), row.get("thin_instances")
        share = f"{thin / instances * 100:.0f}%" if thin is not None and instances else "not recorded"
        rows.append(tr(td(f"<code>{esc(name)}</code>"), td(esc(split)),
                       td(row.get("drawings", "not recorded"), "n"), td(row.get("images", "not recorded"), "n"),
                       td(instances if instances is not None else "not recorded", "n"), td(share, "n")))
    return rows


def run_cost_rows(world):
    rows = []
    for run in sorted(world.runs, key=lambda r: r.last_seen, reverse=True):
        totals = runs.run_totals(run)
        seconds = sum(totals.seconds_by_gpu.values())
        if not run.entries:
            cost, time_text = "not in the GPU ledger", "not in the GPU ledger"
        else:
            cost, time_text = money(totals.cost) or "not known", duration(seconds) if seconds else "none"
        kind = "smoke test" if run.smoke else {"pretrained": "before", "trained": "after"}.get(run.weights, "")
        rows.append(tr(td(run_tag(run)), td(esc(kind)), td(time_text, "n"), td(cost, "n")))
    rows.append(tr(td("Whole ledger, probes included"), td(""), td(""), td(money(runs.ledger_cost(world.entries)), "n")))
    return rows


def bill_note():
    """The all-in figures, which the ledger does not hold."""
    bill = runs.MODAL_BILL
    parts = [f"Modal's bill, {bill['date']}: {money(bill['all_in_usd'])} all-in, made of {money(bill['gpu_usd'])} GPU "
             f"and {money(bill['cpu_memory_usd'])} CPU and memory."]
    parts += [f"Of that, training <code>{esc(runs.short_run(run_id))}</code> was {money(row['gpu_usd'])} GPU, "
              f"{money(row['all_in_usd'])} all-in." for run_id, row in bill["training"].items()]
    return f'<p class="note">{" ".join(parts)} Source: {esc(bill["source"])}. Not in the ledger.</p>'


def section_limits(world, manifests):
    items = [
        "<strong>Synthetic drawings.</strong> Every tile here comes from our own templates in generic drafting styles, "
        "so these scores do not predict scores on real scans. The test tiles come from the same generator as the "
        "training tiles, which makes these scores an upper bound for real drawings.",
        "<strong>Validation is not test.</strong> Curves are validation numbers and pick the checkpoint; only the test "
        "split is held out from that choice.",
        f"<strong>Cost.</strong> Costs labelled GPU are GPU only: Modal's billed GPU seconds where the ledger has them, "
        f"else measured seconds, at list price checked {runs.GPU_PRICES_CHECKED}. CPU and memory are billed on top. "
        "A request that never got a machine costs nothing.",
    ]
    dataset_table = ""
    rows = [row for name, manifest in sorted(manifests.items()) for row in dataset_rows(name, manifest)]
    if rows:
        head = tr(th("Dataset"), th("Split"), th("Drawings", "n"), th("Tiles", "n"), th("Parts", "n"), th("Thin share", "n"))
        dataset_table = f"<h3>Dataset sizes</h3>{table(head, rows)}"
    cost_head = tr(th("Run"), th(""), th("GPU time", "n"), th("GPU-only cost", "n"))
    costs = f"<h3>Cost of each run</h3>{table(cost_head, run_cost_rows(world))}{bill_note()}"
    bullets = "".join(f"<li>{item}</li>" for item in items)
    return card("limits", "Limits", f"<ul>{bullets}</ul>{dataset_table}{costs}")


# ---------------------------------------------------------------- the page

def banners(world):
    notes = []
    if world.volume.error:
        since = f"from {clock(world.volume.last_ok, world.now)}" if world.volume.last_ok else "the Volume has not been read before"
        notes.append(f"Could not read the Modal Volume: {esc(world.volume.error.rstrip('.'))}. Showing the last known data; {since}.")
    if world.ledger_note:
        notes.append(esc(world.ledger_note))
    return "".join(f'<p class="banner">{status("serious", "▲", "Old data")} {note}</p>' for note in notes)


def render(world, models, manifests, styles, gallery, every):
    """The whole page as one string."""
    now = world.now
    live = any(r.live for r in world.runs)
    registry = {}
    sections = [
        section_now(world), section_curves(world, registry), section_before_after(world, models),
        section_classes(models, manifests, styles), section_thin(models), section_images(gallery, styles),
        section_limits(world, manifests),
    ]
    refresh = f'<meta http-equiv="refresh" content="{int(every)}">' if live else ""
    css, js = (HERE / "page.css").read_text(), (HERE / "page.js").read_text()
    ledger = f" GPU ledger last changed {clock(world.ledger_time, now)}." if world.ledger_time else ""
    built = now.astimezone().isoformat(timespec="seconds")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">{refresh}
<title>Segmentation training</title>
<style>{css}</style></head>
<body><main>
<header><h1>Segmentation training</h1>
<p class="note">Updated <strong>{now.astimezone().strftime('%a %d %b, %H:%M')}</strong><span id="age" data-built="{built}" data-every="{int(every)}" data-live="{int(live)}"></span>.{ledger}</p>
<p class="banner" id="stale" hidden></p>
{banners(world)}</header>
{"".join(sections)}
</main>
<div id="tip" role="status" hidden></div>
<script type="application/json" id="chart-data">{charts.chart_json(registry)}</script>
<script>{js}</script>
</body></html>
"""
