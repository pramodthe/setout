"""Reproducible v3 evaluation in its own store, with persisted jobs and artifacts.

python -m eval.agent_eval --own-drawings --var var/eval-v3-own --limit 1
python -m eval.agent_eval --pdf manual.pdf --var var/eval-v3 --baseline var/eval-run/eval/results.json
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
from pathlib import Path
import socket
import statistics
import math
import subprocess
import sys
import time
import urllib.request


def write_review(repo,rows,output,embed=False):
    from app import database as d
    escape = lambda value:html.escape(str(value))
    def picture(ident):
        if not embed:
            return "../artifacts/"+ident
        ref = repo.get(d.artifacts,ident)
        return "data:"+ref["media_type"]+";base64,"+base64.b64encode(repo.artifacts.read(ident)).decode()
    sections = []
    for item in rows:
        version_id = item.get("version_id")
        version = repo.get(d.versions,version_id) if version_id else None
        figure_id = version["figure_id"] if version else repo.get(d.jobs,item["job_id"])["payload"].get("figure_id")
        figure = repo.get(d.figures,figure_id) if figure_id else None
        reports = repo.list(d.verifications,d.verifications.c.version_id == version_id,order=d.verifications.c.created_at) if version else []
        report = reports[-1] if reports else None
        critics = repo.list(d.critics,d.critics.c.version_id == version_id,order=d.critics.c.created_at) if version else []
        renders = repo.list(d.renders,d.renders.c.version_id == version_id,d.renders.c.manifest_sha == report["manifest_sha"]) if report else []
        images = "".join(f'<figure><figcaption>{escape(r["view"])}</figcaption><img src="{escape(picture(r["artifact_id"]))}"></figure>'
                         for r in renders if not r["tags"])
        source = f'<img src="{escape(picture(figure["artifact_id"]))}">' if figure else "Source unavailable"
        checks = [c for c in report["checks"] if c["status"] == "FAIL" or (c.get("required") and c["status"] != "PASS")] if report else []
        failed = "".join(f'<li><b>{escape(c["id"])}</b>: {escape(c.get("reason") or c.get("description"))}</li>' for c in checks)
        critique = "".join(f'<li>{escape(i["reason"])}</li>' for i in critics[-1]["issues"]) if critics else "<li>Not run</li>"
        sections.append(f'<section><h2>{escape(item["crop"])} · {escape(item["state"])}</h2><p>Version {escape(version_id)} · '
            f'{escape(item.get("required_checks",{}))} · critic {escape(item.get("critic_verdict","not_run"))}</p>'
            f'<div class="pair"><figure><figcaption>Source drawing</figcaption>{source}</figure><div>{images or "No matching renders"}</div></div>'
            f'<details open><summary>Required checks needing attention ({len(checks)})</summary><ul>{failed}</ul></details>'
            f'<details><summary>Independent critic</summary><ul>{critique}</ul></details></section>')
    page = '<!doctype html><meta charset="utf-8"><title>V3 evidence review</title><style>' \
        'body{font:16px/1.5 system-ui;margin:24px;color:#172133;background:#f4f6f8}section{background:white;padding:24px;margin:24px 0;border-radius:10px}' \
        '.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}figure{margin:0 0 16px}img{width:100%;height:auto}p{overflow-wrap:anywhere}' \
        'summary{cursor:pointer;font-weight:600}@media(max-width:800px){.pair{grid-template-columns:1fr}}</style>' \
        '<h1>V3 evidence review</h1><p>Source drawings, exact-version renders, verification and independent critic results. Human fidelity review is pending.</p>' + ''.join(sections)
    (output/"review.html").write_text(page)


def summary(rows):
    times = sorted(r["seconds"] for r in rows)
    return {"drawings": len(rows), "states": dict(Counter(r["state"] for r in rows)),
            "buildable": sum(r["buildable"] for r in rows),
            "required_checks": dict(sum((Counter(r["required_checks"]) for r in rows), Counter())),
            "source_dimensions_pass": sum(r["source_dimensions_pass"] for r in rows),
            "tokens": sum(r["tokens"] for r in rows),
            "median_seconds": statistics.median(times) if times else None,
            "p95_seconds": times[max(0, math.ceil(len(times) * .95) - 1)] if times else None,
            "critic_verdicts":dict(Counter(r["critic_verdict"] for r in rows)),
            "labels_read":sum(r["labels_read"] for r in rows),"labels_retained":sum(r["labels_retained"] for r in rows),
            "cad_attempts":sum(r["cad_attempts"] for r in rows),"render_attempts":sum(r["render_attempts"] for r in rows),
            "token_usage":dict(sum((Counter(r["token_usage"]) for r in rows),Counter())),
            "reviewer_fidelity":"pending human review"}


def run(args):
    root = Path(args.var).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["SETOUT_VAR"] = str(root)
    os.environ["SETOUT_DATABASE_URL"] = "sqlite:///" + str(root / "v3.db")
    os.environ["SETOUT_ARTIFACT_ROOT"] = str(root / "artifacts")
    os.environ["SETOUT_WORKER"] = "external"
    from app import database as d, meta_client, store
    from app.repository import DEFAULT_BUDGET, VERIFIER_VERSION, Repository, TERMINAL, canonical, sha, uid
    from app.versioning import register_figure
    from app.worker import Worker
    from app.eval_metrics import result_row
    from app.agent import INSTRUCTIONS
    from app.cad_components import TOOLCHAIN
    from app import generate
    if args.own_drawings:
        from scripts.demo_drawing import section, elevation
        pages = [section(), elevation()]
        pdf = root / "own-drawings.pdf"
        if not args.revise_drafts or not pdf.is_file():
            pages[0].save(pdf, "PDF", resolution=200, save_all=True, append_images=pages[1:])
    else:
        pdf = Path(args.pdf)
    source = pdf.read_bytes()
    document = store.store_pdf(pdf.name, source)
    figures = document["figures"][:args.limit or None]
    repo = Repository()
    dataset = [{"figure_id": register_figure(repo, document["id"], f["crop_id"]), "crop": f["crop_id"]} for f in figures]
    manifest = {"source_sha": hashlib.sha256(source).hexdigest(), "model": meta_client.default_model(),
                "budget": DEFAULT_BUDGET, "memory_mode": args.memory, "figures": dataset,
                "prompt_sha":sha({"agent":INSTRUCTIONS,"read":generate.READ_PROMPT}),
                "toolchain":TOOLCHAIN,"agent_reasoning_effort":{"generate":"low","revise":"medium"},
                "verifier_version":VERIFIER_VERSION,
                "memories":repo.list(d.memories,d.memories.c.state == "active"),
                "components":[{k:c[k] for k in ("id","code_sha","family_key","param_schema")} for c in repo.list(d.components)],
                "crop_hashes":{f["figure_id"]:repo.get(d.figures,f["figure_id"])["artifact_id"] for f in dataset},
                "config_sha": sha({"budget": DEFAULT_BUDGET, "model": meta_client.default_model(), "memory": args.memory})}
    implementation = [(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted((Path(__file__).resolve().parent.parent / "app").glob("*.py"))]
    manifest["implementation_sha"] = sha(implementation)
    output = root / "eval"
    output.mkdir(exist_ok=True)
    previous = json.loads((output/"results.json").read_text()) if args.revise_drafts else []
    if previous:
        history = output/"attempts.json"
        attempts = json.loads(history.read_text()) if history.is_file() else []
        attempts.append({"manifest":json.loads((output/"manifest.json").read_text()),"rows":previous})
        history.write_text(json.dumps(attempts,indent=2))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if args.baseline:
        baseline_path = Path(args.baseline)
        baseline = json.loads(baseline_path.read_text())
        # The original v2 evaluator stored the PDF identity in its adjacent DB.
        import sqlite3
        with sqlite3.connect(baseline_path.parent.parent / "setout.db") as con:
            hashes = {r[0] for r in con.execute("SELECT sha256 FROM documents")}
        if manifest["source_sha"] not in hashes:
            raise ValueError("baseline/source PDF hashes do not match")
        (output / "baseline.json").write_text(json.dumps({"source_sha": manifest["source_sha"], "rows": baseline,
            "results_sha":hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "commit":"not recorded by the original v2 evaluator"}, indent=2))
    run_id = uid()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    os.environ["SETOUT_RENDER_URL"] = base
    log = (output / "server.log").open("w")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], stdout=log, stderr=log)
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/api/ai/status", timeout=1).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("evaluation viewer did not start; see server.log")
                time.sleep(.05)
        jobs = []
        for figure in dataset:
            old = next((r for r in previous if r["crop"] == figure["crop"] and r.get("version_id")),None)
            if old:
                base_version = repo.get(d.versions,old["version_id"])
                if old["state"] == "succeeded":
                    original_job = repo.get(d.jobs,old["job_id"])
                    final = original_job["result"] or {}
                    with d.transaction(repo.engine) as con:
                        valid = not repo.completion_errors(con,base_version["id"],final["verification_id"],final["critic_verdict_id"])
                    if valid:
                        jobs.append({"saved_result":old})
                        continue
                job = repo.enqueue("revise_detail",{"detail_id":base_version["detail_id"],"base_version_id":base_version["id"],
                    "requested_changes":args.request or "Correct the remaining verification failures. Preserve printed values and labels. Split wall/floor layers into separate covered parts when needed; keep every material and explicit covering/installation relation. Render and independently critique the corrected version before finishing.",
                    "memory_mode":args.memory},key=f"eval-{run_id}-{figure['figure_id']}")
            else:
                job = repo.enqueue("generate_detail",{"figure_id":figure["figure_id"],"memory_mode":args.memory},
                    key=f"eval-{run_id}-{figure['figure_id']}")
            jobs.append(job)
        manifest["attempt_kind"] = "revise incomplete drafts" if args.revise_drafts else "generate"
        manifest["retained_successes"] = [job["saved_result"]["job_id"] for job in jobs if "saved_result" in job]
        manifest["base_versions"] = {job["id"]:repo.run(job["id"])["base_version_id"] for job in jobs if "id" in job}
        (output/"manifest.json").write_text(json.dumps(manifest,indent=2))
        print(f"{len(jobs)} drawings; model {manifest['model']}; manifest {sha(manifest)}", flush=True)
        rows = []
        def evaluate(figure,job):
            if "saved_result" in job:
                return {**job["saved_result"],"reused_terminal_result":True}
            started = time.monotonic()
            worker = Worker(repo)
            while repo.get(d.jobs, job["id"])["state"] not in TERMINAL:
                if not worker.run_once(only_ids=[job["id"]]):
                    time.sleep(.25)
            return {"crop":figure["crop"],**result_row(repo,job["id"],time.monotonic()-started)}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(evaluate,figure,job) for figure,job in zip(dataset,jobs)]
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                rows.sort(key=lambda row: next(i for i,f in enumerate(dataset) if f["crop"] == row["crop"]))
                (output / "results.json").write_text(json.dumps(rows, indent=2))
                write_review(repo,rows,output,embed=args.own_drawings)
                print(json.dumps(row), flush=True)
        result = {"manifest_sha": sha(manifest), **summary(rows)}
        (output / "summary.json").write_text(json.dumps(result, indent=2))
        if args.baseline:
            comparison = {"v2":{"drawings":len(baseline),"buildable":sum(bool(r.get("ok")) for r in baseline),
                "tokens":sum(r.get("tokens",0) for r in baseline),"traced":sum(r.get("traced",0) for r in baseline),
                "fail":sum(r.get("fail",0) for r in baseline),"critic":"not recorded"},"v3":result,
                "notes":["V2 buildable/trace counts used weaker checks and are not equivalent to V3 done or measured-source PASS.",
                         "Baseline is the previously recorded V2 run; its commit and reviewer fidelity were not recorded.",
                         "Third-party drawings are excluded from published metrics."]}
            (output / "comparison.json").write_text(json.dumps(comparison,indent=2))
        print(json.dumps(result, indent=2), flush=True)
        return result
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf")
    source.add_argument("--own-drawings", action="store_true")
    parser.add_argument("--var", default="var/eval-v3")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--baseline")
    parser.add_argument("--memory", choices=["on", "off"], default="on")
    parser.add_argument("--workers",type=int,choices=range(1,5),default=1)
    parser.add_argument("--revise-drafts",action="store_true",help="Revise existing candidates, retaining the previous attempt's manifest and results")
    parser.add_argument("--request",help="Concrete revision request for the retained drafts")
    run(parser.parse_args())
