"""The facts behind the training dashboard: GPU ledger, run files, stage, pass estimate, cost.

Read-only on everything it points at. Run files come from `data/models/<model>/<run_id>/` when they
exist there, else from a cache of the Modal Volume under the dashboard's output folder. Nothing
here draws anything, and a value that does not exist yet stays None: the page turns None into
"not run yet", never into 0.
"""
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Modal list prices in $ per GPU-second. GPU only: CPU, memory and cold start are billed on top.
GPU_PRICES_CHECKED = "30 Oct 2026"
GPU_PRICE_PER_S = {
    "B200": 0.001736,
    "H100": 0.001097,
    "A100-80GB": 0.000694,
    "L40S": 0.000542,
    "A10": 0.000306,
    "L4": 0.000222,
    "T4": 0.000164,
}

# Modal's bill. The ledger holds GPU seconds only, so what CPU and memory added lives here, with its
# date and source; update it by hand from a new bill.
MODAL_BILL = {
    "date": "30 Oct 2026",
    "source": "Modal's billing report, as given in the training agent's report",
    "gpu_usd": 25.17,
    "cpu_memory_usd": 9.02,
    "all_in_usd": 34.19,
    "training": {"seg-synth-v1-seg-2xlarge-20261030T0431Z": {"gpu_usd": 16.16, "all_in_usd": 19.44},
                 "seg-synth-v2-zoom-seg-2xlarge-20261030T1112Z": {"gpu_usd": 7.95, "all_in_usd": 13.13}},
}

VOLUME = "setout-seg-runs"
# Modal app per folder under data/models: how a container is tied to a ledger.
APP_BY_FOLDER = {"rfdetr-seg": "setout-rfdetr-seg"}
MODEL_NAMES = {"rfdetr-seg": "RF-DETR-Seg", "sam3": "SAM 3"}
PLANNED_MODELS = ("RF-DETR-Seg", "SAM 3")  # listed even before they have run

SMALL_FILES = ("progress.jsonl", "run_config.json", "train_summary.json", "predict_summary_test.json",
               "metrics_test.json", "parity_test.json")
PREDICTIONS = "predictions_test.json"

MAP_KEYS = ("val/ema_segm_mAP_50_95", "val/segm_mAP_50_95")
MAP50_KEYS = ("val/ema_segm_mAP_50", "val/segm_mAP_50")
CLASS_AP_PREFIXES = ("val/ema_AP/", "val/AP/")
LOSS_KEY = "loss"

# Under this share of a usual pass's batches, a last line is the time cap's stub, not a pass.
STUB_SHARE = 0.1
LIVE_STAGES = {"training", "queued", "predicting", "queued_or_predicting", "scoring"}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_time(text):
    """A ledger or Volume timestamp as an aware datetime, or None."""
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def file_time(path):
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def read_json(path):
    """Parsed JSON, or None when the file is absent or still being written."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def read_progress(path):
    """The validated passes in a progress.jsonl. A partial last line is skipped."""
    try:
        raw = Path(path).read_text(errors="replace")
    except OSError:
        return []
    lines = []
    for line in raw.splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "epoch" in parsed:
            lines.append(parsed)
    return lines


# ---------------------------------------------------------------- runs

@dataclass
class Stage:
    kind: str  # training, queued, predicting, queued_or_predicting, scoring, finished, failed,
    #            stopped, trained, not_started
    job: dict | None = None  # the ledger entry it is about


@dataclass
class Run:
    run_id: str
    folder: str
    entries: list = field(default_factory=list)
    progress: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    train_summary: dict | None = None
    predict_summary: dict | None = None
    metrics: dict | None = None
    parity: dict | None = None
    predictions: Path | None = None
    files: dict = field(default_factory=dict)  # file name -> modified time, None when not known
    stage: Stage = field(default_factory=lambda: Stage("not_started"))

    @property
    def smoke(self):
        return bool(self.config.get("smoke")) or self.run_id.startswith("smoke-") or \
            str(self.dataset or "").startswith("_")

    @property
    def dataset(self):
        name = self.config.get("dataset")
        gt = ((self.metrics or {}).get("files") or {}).get("gt")
        if not name and gt:
            parts = Path(gt).parts
            name = parts[-3] if len(parts) >= 3 else None
        return name

    @property
    def weights(self):
        """"pretrained" or "trained" from the run's own file; a run that trained says so by training."""
        stated = (self.predict_summary or {}).get("weights")
        if stated:
            return stated
        if self.train_summary or any(e.get("function") == "train" for e in self.entries):
            return "trained"
        return None

    @property
    def model_name(self):
        return MODEL_NAMES.get(self.folder, self.folder)

    @property
    def variant(self):
        return self.config.get("model") or (self.predict_summary or {}).get("model")

    @property
    def score_thr(self):
        return ((self.metrics or {}).get("params") or {}).get("score_thr", 0.5)

    @property
    def live(self):
        return self.stage.kind in LIVE_STAGES

    @property
    def last_seen(self):
        times = [parse_time(e.get("started_at")) for e in self.entries]
        times += list(self.files.values())
        return max((t for t in times if t), default=EPOCH)


def full_passes(progress):
    """Validated passes without the stub the time cap leaves at the end of a log.

    When PyTorch Lightning's max_time trips, the epoch in progress stops after one batch and still
    validates, so the log can end with a line that trained nothing and repeats the pass before it.
    """
    if len(progress) < 2:
        return list(progress)
    usual = statistics.median(p.get("micro_batches") or 0 for p in progress[:-1])
    return list(progress[:-1]) if (progress[-1].get("micro_batches") or 0) < STUB_SHARE * usual else list(progress)


def metric(line, keys):
    metrics = line.get("metrics") or {}
    return next((metrics[k] for k in keys if metrics.get(k) is not None), None)


def class_ap(line, name):
    return metric(line, [prefix + name for prefix in CLASS_AP_PREFIXES])


def class_names(progress):
    names = {key.split("/", 2)[2] for line in progress for key in (line.get("metrics") or {})
             if key.startswith(CLASS_AP_PREFIXES)}
    return sorted(names)


@dataclass
class PassPlan:
    done: int = 0
    about: int | None = None  # the pass this run should stop at
    limit: str | None = None  # "time cap", "pass limit" or "early stopping"
    pass_s: float | None = None
    val_s: float | None = None
    cap_s: float | None = None
    epochs: int | None = None
    best_pass: int | None = None
    best_score: float | None = None
    best_ema: bool = False  # the score is on the EMA weights
    since_best: int | None = None
    patience: int | None = None


def plan_passes(progress, config, summary=None):
    """How far a run has got and which limit ends it.

    While it runs, the time cap decides when cap / seconds per pass comes before the pass limit.
    Lightning checks max_time after each batch, so the pass the cap lands in is the last one.
    Once it has finished, train_summary says what happened.
    """
    passes = full_passes(progress)
    plan = PassPlan(done=len(passes), epochs=config.get("epochs"),
                    patience=config.get("early_stopping_patience") or None)
    plan.cap_s = (config.get("max_train_minutes") or 0) * 60 or None
    if passes:
        plan.pass_s = statistics.median((p.get("train_s") or 0) + (p.get("val_s") or 0) for p in passes)
        plan.val_s = statistics.median(p.get("val_s") or 0 for p in passes)
        scored = [(metric(p, MAP_KEYS), i + 1) for i, p in enumerate(passes) if metric(p, MAP_KEYS) is not None]
        if scored:
            plan.best_score, plan.best_pass = max(scored, key=lambda s: (s[0], -s[1]))
            plan.since_best = plan.done - plan.best_pass
            plan.best_ema = MAP_KEYS[0] in (passes[plan.best_pass - 1].get("metrics") or {})
    plan.about, plan.limit = plan.epochs, "pass limit" if plan.epochs else None
    if plan.cap_s and not plan.pass_s:
        plan.about, plan.limit = None, None  # which limit comes first needs one timed pass
    elif plan.cap_s:
        by_cap = math.ceil(plan.cap_s / plan.pass_s)
        if plan.epochs is None or by_cap < plan.epochs:
            plan.about, plan.limit = by_cap, "time cap"
    if summary:
        plan.about = plan.done
        if summary.get("stopped_by_time"):
            plan.limit = "time cap"
        elif plan.epochs and plan.done < plan.epochs:
            plan.limit = "early stopping"
        else:
            plan.limit = "pass limit"
    return plan


def elapsed_now(run, now):
    """Seconds since fit started: the last validated pass, plus the time since it was written."""
    if not run.progress:
        return None
    elapsed = run.progress[-1].get("elapsed_s") or 0
    written = run.files.get("progress.jsonl")
    return elapsed + max(0.0, (now - written).total_seconds()) if written else elapsed


def time_left_s(plan, elapsed_s):
    """About how long a live training run has left, or None with nothing to go on yet.

    The cap stops the batches; one more validation follows. An estimate, and labelled one on the page.
    """
    if plan.pass_s is None or elapsed_s is None:
        return None
    left = []
    if plan.about is not None and plan.limit == "pass limit":
        left.append(plan.about * plan.pass_s - elapsed_s)
    if plan.cap_s:
        left.append(plan.cap_s + (plan.val_s or 0) - elapsed_s)
    return max(0.0, min(left)) if left else None


# ---------------------------------------------------------------- stage

def _fresh(files, name, entry):
    """The file exists and was not left over from before this call started."""
    if name not in files:
        return False
    written, started = files[name], parse_time((entry or {}).get("started_at"))
    return written is None or started is None or written >= started


def derive_stage(entries, progress, files, containers=None):
    """What a run is doing, from its ledger entries, progress lines and file names.

    `containers` is how many containers of the run's app have started: None when that could not be
    read. Only a container can tell a predict that runs from one still queued, because predict
    writes nothing until it ends.
    """
    running = [e for e in entries if e.get("status") == "running"]
    if running:
        job = running[-1]
        if job.get("function") == "train":
            started = bool(progress) or _fresh(files, "run_config.json", job)
            return Stage("training" if started or containers else "queued", job)
        if _fresh(files, "predict_summary_test.json", job):
            return Stage("scoring", job)
        if containers is None:
            return Stage("queued_or_predicting", job)
        return Stage("predicting" if containers else "queued", job)
    last = entries[-1] if entries else None
    has_scores = "metrics_test.json" in files
    if has_scores and _scores_current(files, entries):
        return Stage("finished", last)
    if last and last.get("status") == "failed":
        return Stage("failed", last)
    if last and last.get("status") == "cancelled_unscheduled":
        return Stage("finished" if has_scores else "stopped", last)
    if PREDICTIONS in files or "predict_summary_test.json" in files or \
            (last and last.get("function") == "predict" and last.get("status") == "ok"):
        return Stage("scoring", last)
    if last and last.get("status") == "ok":
        return Stage("trained", last)
    return Stage("finished" if has_scores else "not_started", last)


def _scores_current(files, entries):
    """False when the metrics file predates the end of the last prediction, so a new score is due.

    Times of the two files cannot be compared directly: a download writes metrics first and
    predictions after them, whatever order they were made in.
    """
    written = files.get("metrics_test.json")
    done = [e for e in entries if e.get("function") == "predict" and e.get("status") == "ok"]
    started = parse_time(done[-1].get("started_at")) if done else None
    if not written or not started:
        return True
    return written.timestamp() >= started.timestamp() + (done[-1].get("wall_s") or 0)


# ---------------------------------------------------------------- time and money

def gpu_cost(gpu, seconds):
    """Dollars at list price, or None for a GPU not in the table or an unmeasured call."""
    price = GPU_PRICE_PER_S.get(gpu)
    return None if price is None or seconds is None else seconds * price


def entry_seconds(entry, run=None):
    """GPU seconds of a call: billed when known, else measured; a live train call, to its last pass."""
    if entry.get("status") == "running":
        if entry.get("function") == "train" and run and run.progress:
            return run.progress[-1].get("elapsed_s")
        return None
    return entry.get("billed_s") or entry.get("gpu_s")


def entry_cost(entry, run=None):
    """GPU dollars of a call. billed_s wins, even at 0: a call can be billed under another one."""
    billed = entry.get("billed_s")
    return gpu_cost(entry.get("gpu"), billed if billed is not None else entry_seconds(entry, run))


@dataclass
class Totals:
    seconds_by_gpu: dict = field(default_factory=dict)
    cost: float | None = None
    unpriced: list = field(default_factory=list)  # GPU types not in the price table
    unmeasured: int = 0  # calls with no measured seconds yet


def run_totals(run):
    totals = Totals()
    for entry in run.entries:
        gpu, seconds = entry.get("gpu"), entry_seconds(entry, run)
        if seconds is None:
            totals.unmeasured += 1
            continue
        if seconds:
            totals.seconds_by_gpu[gpu] = totals.seconds_by_gpu.get(gpu, 0.0) + seconds
        cost = entry_cost(entry, run)
        if cost is None:
            totals.unpriced.append(gpu)
        else:
            totals.cost = (totals.cost or 0.0) + cost
    return totals


def ledger_cost(entries):
    """GPU dollars of every ledger entry, probes included."""
    return sum(entry_cost(entry) or 0.0 for entry in entries)


def wait_s(entry, now):
    """Seconds between asking for a GPU and the function starting (queue plus container start)."""
    wall, gpu = entry.get("wall_s"), entry.get("gpu_s")
    if entry.get("status") == "running":
        started = parse_time(entry.get("started_at"))
        return (now - started).total_seconds() if started else None
    if wall is None:
        return None
    if entry.get("status") == "cancelled_unscheduled":
        return wall
    return max(0.0, wall - gpu) if gpu is not None else None


def attempt_wait(entry, run, now):
    """wait_s for the attempts list: an open call stops waiting once its container has started.

    Training writes run_config.json as it starts, which gives the moment. Prediction writes nothing
    until it ends, so a running predict has no known wait.
    """
    if entry.get("status") != "running" or run is None or run.stage.kind in ("queued", "queued_or_predicting"):
        return wait_s(entry, now)
    asked = parse_time(entry.get("started_at"))
    began = run.files.get("run_config.json") if entry.get("function") == "train" else None
    return (began - asked).total_seconds() if asked and began and began >= asked else None


def peak_memory(run):
    """Peak GPU memory (GiB) seen across training and prediction, or None."""
    values = [p.get("peak_mem_gib") for p in run.progress]
    values += [(run.train_summary or {}).get("peak_mem_gib"), (run.predict_summary or {}).get("peak_mem_gib")]
    values = [v for v in values if v is not None]
    return max(values) if values else None


# ---------------------------------------------------------------- Modal

class ModalError(RuntimeError):
    pass


class ModalCli:
    """Read-only calls to the `modal` command line. It never starts, stops or changes anything."""

    def __init__(self, exe=None, timeout=45):
        sibling = Path(sys.executable).with_name("modal")
        self.exe = exe or (str(sibling) if sibling.exists() else shutil.which("modal"))
        self.timeout = timeout

    def _run(self, *args, timeout=None):
        if not self.exe:
            raise ModalError("the modal command line is not installed")
        try:
            done = subprocess.run([self.exe, *args], capture_output=True, text=True,
                                  timeout=timeout or self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModalError(str(exc).splitlines()[0]) from exc
        if done.returncode:
            lines = [ln.strip() for ln in (done.stderr or done.stdout).splitlines() if ln.strip()]
            raise ModalError(lines[-1] if lines else f"modal exited with {done.returncode}")
        return done.stdout

    def list_dir(self, path=""):
        """{file name: {"size", "mtime", "type"}} of a folder on the runs Volume."""
        args = ["volume", "ls", "--json", VOLUME] + ([path] if path else [])
        try:
            rows = json.loads(self._run(*args))
        except ValueError as exc:
            raise ModalError("modal volume ls did not return JSON") from exc
        return {Path(r["filename"]).name: {"size": r.get("size"), "type": r.get("type"),
                                           "mtime": parse_time(r.get("created_modified"))} for r in rows}

    def get(self, remote, dest, mtime=None, timeout=None):
        """Copy one Volume file to `dest`, whole or not at all."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        self._run("volume", "get", "--force", VOLUME, remote, str(part), timeout=timeout)
        part.replace(dest)
        if mtime:
            os.utime(dest, (mtime.timestamp(), mtime.timestamp()))

    def containers(self):
        """{app name: containers that have started}."""
        try:
            rows = json.loads(self._run("container", "list", "--json"))
        except ValueError as exc:
            raise ModalError("modal container list did not return JSON") from exc
        started = {}
        for row in rows:
            if row.get("start_time") and row["start_time"] != "Pending":
                started[row.get("app_name")] = started.get(row.get("app_name"), 0) + 1
        return started


@dataclass
class VolumeState:
    read: bool = False  # this build asked the Volume for something
    error: str | None = None
    last_ok: datetime | None = None


def load_state(cache_dir):
    return read_json(Path(cache_dir) / "state.json") or {}


def save_state(cache_dir, state):
    path = Path(cache_dir) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(json.dumps(state, indent=2))
    part.replace(path)


def _settled(run, models_dir):
    """The Volume has nothing new for it: scored and copied home, or it never got a machine."""
    if any(e.get("status") == "running" for e in run.entries):
        return False
    local = Path(models_dir) / run.folder / run.run_id
    scored = (local / "metrics_test.json").exists() and (local / PREDICTIONS).exists()
    return scored or (bool(run.entries) and all(e.get("status") == "cancelled_unscheduled" for e in run.entries))


def pull_runs(runs, modal, models_dir, cache_dir, state):
    """Refresh the cache from the Volume for runs that still change. Returns a VolumeState."""
    result = VolumeState(last_ok=parse_time(state.get("volume_last_ok")))
    wanted = [r for r in runs if not _settled(r, models_dir)]
    if not wanted:
        return result
    result.read = True
    try:
        on_volume = modal.list_dir()
        for run in wanted:
            if run.run_id in on_volume:
                _pull_run(run, modal.list_dir(run.run_id), modal, models_dir, cache_dir)
    except ModalError as exc:
        result.error = str(exc)
        return result
    result.last_ok = datetime.now(timezone.utc)
    state["volume_last_ok"] = result.last_ok.isoformat()
    return result


def _pull_run(run, listing, modal, models_dir, cache_dir):
    live = any(e.get("status") == "running" for e in run.entries)
    for name in SMALL_FILES + (PREDICTIONS,):
        remote = listing.get(name)
        if not remote:
            continue
        local = Path(models_dir) / run.folder / run.run_id / name
        cached = Path(cache_dir) / run.run_id / name
        if local.exists() and not (live and name == "progress.jsonl"):
            continue
        if cached.exists() and cached.stat().st_size == remote["size"] and \
                remote["mtime"] and file_time(cached) >= remote["mtime"]:
            continue
        modal.get(f"{run.run_id}/{name}", cached, remote["mtime"], timeout=600 if name == PREDICTIONS else None)


# ---------------------------------------------------------------- reading everything

@dataclass
class World:
    now: datetime
    runs: list
    entries: list  # every ledger entry, oldest first, including probes
    ledger_time: datetime | None = None
    ledger_note: str | None = None  # set when a ledger could not be read
    volume: VolumeState = field(default_factory=VolumeState)


def read_ledgers(models_dir, cache_dir):
    """Every data/models/*/gpu_ledger.json. Returns (entries, newest change, note).

    A ledger is rewritten in place by the training client, so a half-written read is retried once
    and then answered from the last good copy in the cache.
    """
    entries, newest, note = [], None, None
    for path in sorted(Path(models_dir).glob("*/gpu_ledger.json")):
        folder, copy = path.parent.name, Path(cache_dir) / f"ledger-{path.parent.name}.json"
        rows = read_json(path)
        if rows is None:
            time.sleep(0.5)
            rows = read_json(path)
        if rows is None:
            rows, note = read_json(copy) or [], f"Could not read {folder}/gpu_ledger.json; using the last good copy."
        else:
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_text(json.dumps(rows))
            modified = file_time(path)
            newest = max(newest, modified) if newest else modified
        entries += [{**row, "folder": folder} for row in rows]
    entries.sort(key=lambda e: parse_time(e.get("started_at")) or EPOCH)
    return entries, newest, note


def discover_runs(models_dir, entries):
    """One Run per trained or predicted run in a ledger, and per run folder under data/models."""
    runs = {}
    for entry in entries:
        if entry.get("function") in ("train", "predict"):
            run = runs.setdefault(entry["run_id"], Run(entry["run_id"], entry["folder"]))
            run.entries.append(entry)
    for path in sorted(Path(models_dir).glob("*/*")):
        if path.is_dir() and any((path / name).exists() for name in SMALL_FILES + (PREDICTIONS,)):
            runs.setdefault(path.name, Run(path.name, path.parent.name))
    return runs


def load_run(run, models_dir, cache_dir):
    """Fill a Run from its local folder, else from the Volume cache."""
    homes = (Path(models_dir) / run.folder / run.run_id, Path(cache_dir) / run.run_id)

    def find(name):
        candidates = [home / name for home in homes if (home / name).exists()]
        live = name == "progress.jsonl" and any(e.get("status") == "running" for e in run.entries)
        return (candidates[-1] if live else candidates[0]) if candidates else None

    run.files = {name: file_time(found) for name in SMALL_FILES + (PREDICTIONS,) if (found := find(name))}
    run.progress = read_progress(find("progress.jsonl")) if find("progress.jsonl") else []
    config = read_json(find("run_config.json")) if find("run_config.json") else None
    if config:
        run.config, run.environment = config.get("config", config), config.get("environment") or {}
    for attr, name in (("train_summary", "train_summary.json"), ("predict_summary", "predict_summary_test.json"),
                       ("metrics", "metrics_test.json"), ("parity", "parity_test.json")):
        setattr(run, attr, read_json(find(name)) if find(name) else None)
    run.predictions = find(PREDICTIONS)


def collect(models_dir, cache_dir, modal=None, now=None):
    """Everything the page needs, read once. `modal` None means local files only."""
    now = now or datetime.now(timezone.utc)
    entries, ledger_time, note = read_ledgers(models_dir, cache_dir)
    runs = discover_runs(models_dir, entries)
    state = load_state(cache_dir)
    volume = VolumeState(last_ok=parse_time(state.get("volume_last_ok")))
    counts = None
    if modal is not None:
        volume = pull_runs(list(runs.values()), modal, models_dir, cache_dir, state)
        save_state(cache_dir, state)
        counts = _read_containers(modal, runs.values())
    for run in runs.values():
        load_run(run, models_dir, cache_dir)
        started = None if counts is None else counts.get(APP_BY_FOLDER.get(run.folder), 0)
        run.stage = derive_stage(run.entries, run.progress, run.files, started)
    return World(now, list(runs.values()), entries, ledger_time, note, volume)


def _read_containers(modal, runs):
    """{app: started containers}, {} with nothing in the ledger running, None when unreadable."""
    if not any(e.get("status") == "running" for r in runs for e in r.entries):
        return {}
    try:
        return modal.containers()
    except ModalError:
        return None


def short_run(run_id):
    """A run id without its time stamp, for tight spaces."""
    return re.sub(r"-\d{8}T\d{4}Z$", "", run_id)


def group_models(runs):
    """{label: {"model", "before", "after", "older"}}, non-smoke runs only, newest scored run in each slot.

    The label is the model's name. Before and after only mean something on one dataset, so a model
    with runs on several datasets gets one group per dataset instead, "<model> on <dataset>", oldest
    dataset first. A run that does not say its dataset yet joins the newest one.
    """
    placed = [r for r in sorted(runs, key=lambda r: r.last_seen) if not r.smoke and r.weights is not None]
    datasets = {}
    for run in placed:
        if run.dataset and run.dataset not in datasets.setdefault(run.model_name, []):
            datasets[run.model_name].append(run.dataset)

    def label(model, dataset):
        return model if len(datasets.get(model, ())) < 2 else f"{model} on {dataset}"

    models = {}
    for model in dict.fromkeys(PLANNED_MODELS + tuple(r.model_name for r in placed)):
        for dataset in datasets.get(model, [None]):
            models.setdefault(label(model, dataset), {"model": model, "before": None, "after": None, "older": []})
    for run in placed:
        group = models[label(run.model_name, run.dataset or datasets.get(run.model_name, [None])[-1])]
        slot = "before" if run.weights == "pretrained" else "after"
        held = group[slot]
        if held is None or run.metrics or not held.metrics:
            if held is not None:
                group["older"].append(held)
            group[slot] = run
        else:
            group["older"].append(run)
    return models


def focus_runs(runs):
    """The runs section 1 talks about: every live one, else the latest and the latest that trained."""
    live = sorted((r for r in runs if r.live), key=lambda r: r.last_seen, reverse=True)
    if live:
        return live
    latest = sorted([r for r in runs if not r.smoke] or runs, key=lambda r: r.last_seen, reverse=True)
    trained = next((r for r in latest if r.train_summary), None)
    return latest[:1] + ([trained] if trained and trained is not latest[0] else [])
