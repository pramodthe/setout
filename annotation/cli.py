#!/usr/bin/env python
"""Turn CAD drawings into a labelled segmentation dataset without tracing outlines.

    python annotation/cli.py propose data/drawings/sheet.dxf      # one or a few sheets
    python annotation/cli.py batch data/drawings --workers 8      # a folder, resumable
    python annotation/cli.py review                               # http://127.0.0.1:8765/
    python annotation/cli.py export --out data/datasets/owner-v1 --project-folders
    python annotation/cli.py measure --sheets 100 --seed 7        # score on synthetic truth

Everything it writes goes under data/ (gitignored) or outside any repository. Nothing here
calls a model or the network. See annotation/README.md.
"""
import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
# `python annotation/cli.py` puts annotation/ first on the path, where export.py or names.py
# would shadow anything of that name. The repo root belongs there instead.
sys.path[:] = [str(ROOT)] + [p for p in sys.path if Path(p or ".").resolve() != HERE]

from annotation import contact, export, measure, pipeline, report, review, sheets  # noqa: E402
from tools.dxf_census import find_dxf  # noqa: E402

DEFAULT_MEASURE = sheets.DEFAULT_STORE / "measure"


def _roots(paths) -> dict:
    """{dxf file: the folder it was found under, or None}."""
    out = {}
    for raw in paths:
        path = Path(raw)
        for found in find_dxf([path]):
            out[found] = path if path.is_dir() else None
    return out


def _one(job) -> dict:
    path, store, root, force = job
    started = time.perf_counter()
    try:
        records = pipeline.propose_file(path, store, root=root, force=force)
    except pipeline.AlreadyReviewed as kept:
        return {"file": str(path), "kept": str(kept)}
    except Exception as error:  # one bad drawing never stops a batch
        return {"file": str(path), "error": f"{type(error).__name__}: {error}"}
    every = [r for record in records for r in record["regions"]]
    live = [r for r in every if not r["dropped"]]
    callouts = [c for record in records for c in record["callouts"]]
    return {"file": str(path), "sheet_id": records[0]["source"]["file_id"] if records else None, "details": len(records),
            "regions": len(live), "set_aside": len(every) - len(live),
            "named": sum(1 for r in live if r["proposal"]["class_id"] != "unknown"), "callouts": len(callouts),
            "callouts_unplaced": sum(1 for c in callouts if c["region"] is None),
            "seconds": round(time.perf_counter() - started, 3)}


def _run(jobs, workers, log):
    rows = []
    if workers > 1:
        with mp.get_context("spawn").Pool(workers) as pool:
            for k, row in enumerate(pool.imap_unordered(_one, jobs), 1):
                rows.append(row)
                if k % 25 == 0 or k == len(jobs):
                    log(f"  {k}/{len(jobs)} sheets")
    else:
        rows = [_one(job) for job in jobs]
    return sorted(rows, key=lambda r: r["file"])


def _report(rows, log):
    done = [r for r in rows if "sheet_id" in r]
    for row in rows:
        if "error" in row:
            log(f"  failed  {row['file']}: {row['error']}")
        elif "kept" in row:
            log(f"  kept    {row['file']}: {row['kept']}")
        else:
            log(f"  {row['details']:>3} details, {row['regions']:>4} regions, {row['named']:>3} named, "
                f"{row['set_aside']:>3} set aside, {row['seconds']:>6.2f}s  {row['file']}")
    if done:
        regions, named = sum(r["regions"] for r in done), sum(r["named"] for r in done)
        log(f"{len(done)} file(s), {sum(r['details'] for r in done)} detail(s): {regions} regions, "
            f"{named} with a proposed name, {sum(r['callouts_unplaced'] for r in done)} callout(s) landing nowhere")


def _propose(args, workers):
    files = _roots(args.paths)
    if not files:
        sys.exit("No .dxf files found under: " + ", ".join(args.paths))
    store = sheets.require_store(args.store)
    jobs = [(str(path), str(store), str(root) if root else None, args.force) for path, root in files.items()]
    rows = _run(jobs, workers, print)
    _report(rows, print)
    print(f"store: {store}")
    return rows


def cmd_propose(args):
    rows = _propose(args, 1)
    return 1 if any("error" in r for r in rows) else 0


def cmd_batch(args):
    rows = _propose(args, args.workers)
    out = Path(args.store) / "batch.json"
    out.write_text(json.dumps(rows, indent=2))
    print(f"summary: {out}")
    return 1 if any("error" in r for r in rows) else 0


def cmd_report(args):
    result = report.build(args.store, args.file)
    out = sheets.require_store(args.store) / "report.json"
    out.write_text(json.dumps(result, indent=2))
    print(report.text(result))
    print(f"report: {out}")
    return 0


def cmd_contact(args):
    out = sheets.require_store(args.store) / "contact.png"
    print(f"contact sheet: {contact.build(args.store, out)}")
    return 0


def cmd_review(args):
    review.serve(sheets.require_store(args.store), args.port)
    return 0


def cmd_export(args):
    print(f"export -> {args.out}")
    info = export.export(args.store, args.out, csv_path=args.projects, folders=args.project_folders,
                         seed=args.seed, overwrite=args.overwrite)
    print(f"manifest: {Path(args.out) / 'manifest.json'}  ({info['review']['seconds_per_part']} s per part reviewed)")
    return 0


def cmd_measure(args):
    report = measure.run(args.out, args.sheets, args.seed, keep=args.keep)
    print(measure.table(report["variants"]))
    print(report["statement"])
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def store(command):
        command.add_argument("--store", default=str(sheets.DEFAULT_STORE),
                             help="annotation store (default data/annotation)")

    one = sub.add_parser("propose", help="find regions and propose names")
    one.add_argument("paths", nargs="+", help="DXF files or folders")
    one.add_argument("--force", action="store_true", help="redo sheets a reviewer has started, losing their decisions")
    store(one)
    one.set_defaults(run=cmd_propose)

    many = sub.add_parser("batch", help="propose for a folder in parallel; a failed sheet does not stop it, "
                                        "and reviewed sheets are left alone")
    many.add_argument("paths", nargs="+", help="folders of DXF files; the first folder below one is its project")
    many.add_argument("--workers", type=int, default=4)
    many.add_argument("--force", action="store_true", help="redo sheets a reviewer has started, losing their decisions")
    store(many)
    many.set_defaults(run=cmd_batch)

    told = sub.add_parser("report", help="details, regions, names and what a reviewer faces; no truth needed")
    told.add_argument("--file", help="only this file id (the sheet ids before -dNN)")
    store(told)
    told.set_defaults(run=cmd_report)

    grid = sub.add_parser("contact", help="every detail on one page, under the store")
    store(grid)
    grid.set_defaults(run=cmd_contact)

    page = sub.add_parser("review", help="the review page, on 127.0.0.1")
    page.add_argument("--port", type=int, default=review.DEFAULT_PORT)
    store(page)
    page.set_defaults(run=cmd_review)

    out = sub.add_parser("export", help="reviewed sheets to a train/valid/test dataset, split by project")
    out.add_argument("--out", required=True, help="dataset folder under data/, e.g. data/datasets/owner-v1")
    source = out.add_mutually_exclusive_group()
    source.add_argument("--projects", help="CSV with columns sheet, project")
    source.add_argument("--project-folders", action="store_true",
                        help="the project is the first folder below the path given to propose or batch")
    out.add_argument("--seed", type=int, default=1, help="orders new projects into splits (default 1)")
    out.add_argument("--overwrite", action="store_true", help="replace a dataset already in --out")
    store(out)
    out.set_defaults(run=cmd_export)

    score = sub.add_parser("measure", help="score the proposals on synthetic DXFs with known truth")
    score.add_argument("--sheets", type=int, default=100)
    score.add_argument("--seed", type=int, default=7)
    score.add_argument("--out", default=str(DEFAULT_MEASURE), help="where the report goes (default data/annotation/measure)")
    score.add_argument("--keep", action="store_true", help="keep the sheets, DXFs and truth next to the report")
    score.set_defaults(run=cmd_measure)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.run(args) or 0
    except sheets.Refused as refused:
        sys.exit(str(refused))


if __name__ == "__main__":
    sys.exit(main())
