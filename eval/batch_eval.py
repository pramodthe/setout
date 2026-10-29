"""Batch evaluation: run the AI pipeline on every drawing in one PDF.

    python eval/batch_eval.py --pdf path/to/manual.pdf --var var/eval-run [--workers 4]

Uses its own storage folder (--var) so it never overwrites the working
library. Writes results.json (one row per drawing) and summary.md into
<var>/eval/. Rows hold ids, counts and timings only; no source text.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--var", default="var/eval-run")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="", help="comma-separated crop ids")
    args = ap.parse_args()
    os.environ["SETOUT_VAR"] = args.var  # before app.store is imported
    sys.path.insert(0, str(ROOT))
    from app import generate as gen
    from app import store
    from app.geometry import build_assembly
    from app.spec import DetailSpec
    from app.verify import summarize, verify_all

    pdf = Path(args.pdf)
    doc = store.store_pdf(pdf.name, pdf.read_bytes())
    figs = [f for f in doc["figures"]
            if not args.only or f["crop_id"] in args.only.split(",")]
    out = Path(args.var) / "eval"
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(figs)} drawings, {args.workers} workers -> {out}", flush=True)

    def run(fig: dict) -> dict:
        t0 = time.time()
        crop = fig["crop_id"]
        try:
            rec = gen.generate(doc["id"], crop)
            spec = DetailSpec.model_validate(rec["spec"])
            res = verify_all(spec, build_assembly(spec), doc["sha256"])
            counts = summarize(res)["counts"]
            prog = rec["program"]
            row = {
                "crop": crop, "ok": True, "seconds": round(time.time() - t0, 1),
                "tokens": sum((u or {}).get("total_tokens", 0) for u in rec["usage"]),
                "kind": prog["kind"], "drawing_type": rec["read"]["drawing_type"],
                "read_labels": len(rec["read"]["elements"]),
                "read_dims": len(rec["read"]["dimensions"]),
                "parts": len(spec.components),
                "shapes": dict(Counter(p["shape"] for p in prog["parts"])),
                "drawing_values": sum(1 for q in prog["params"] if q["provenance"] == "drawing"),
                "traced": sum(1 for r in res if r.id.startswith("C-trace-") and r.status == "PASS"),
                "labels_placed": sum(1 for c in spec.components if c.source_label),
                "labels_used": len({c.source_label for c in spec.components if c.source_label}
                                   & {e["label_verbatim"] for e in rec["read"]["elements"]}),
                "gate_notes": len(rec["gate_notes"]),
                "cad_parts": len(spec.context.get("cad_parts") or {}),
                "pass": counts.get("PASS", 0), "fail": counts.get("FAIL", 0),
                "unverified": counts.get("UNVERIFIED", 0),
            }
        except Exception as err:  # noqa: BLE001 - a failed drawing is a result
            row = {"crop": crop, "ok": False, "seconds": round(time.time() - t0, 1),
                   "error": str(err)[:300]}
        print(json.dumps(row), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run, figs))
    (out / "results.json").write_text(json.dumps(rows, indent=1))
    (out / "summary.md").write_text(summary(rows))
    print(summary(rows), flush=True)


def summary(rows: list[dict]) -> str:
    ok = [r for r in rows if r["ok"]]
    if not ok:
        return f"0/{len(rows)} drawings generated.\n"
    lines = [
        f"# Batch evaluation ({len(rows)} drawings)", "",
        f"- Generated and built: **{len(ok)}/{len(rows)}** "
        f"({100 * len(ok) / len(rows):.0f}%)",
        f"- Verification FAILs across all details: **{sum(r['fail'] for r in ok)}**",
        f"- Printed values confirmed against the drawing: **{sum(r['drawing_values'] for r in ok)}** "
        f"(of which **{sum(r['traced'] for r in ok)}** drive geometry)",
        f"- Distinct drawing labels used verbatim: **{sum(r['labels_used'] for r in ok)}** "
        f"of {sum(r['read_labels'] for r in ok)} read; parts carrying a label: "
        f"{sum(r['labels_placed'] for r in ok)} of {sum(r['parts'] for r in ok)}",
        f"- Values demoted or labels dropped by the gates: **{sum(r['gate_notes'] for r in ok)}**",
        f"- Median time per drawing: **{statistics.median(r['seconds'] for r in ok):.0f} s**; "
        f"median tokens: **{statistics.median(r['tokens'] for r in ok):,.0f}**",
        f"- Shapes used: {dict(sum((Counter(r['shapes']) for r in ok), Counter()))}",
        "", "| Drawing | Type | Parts | Printed values | Labels | PASS | FAIL | Seconds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["ok"]:
            lines.append(f"| {r['crop']} | {r['drawing_type']} | {r['parts']} | "
                         f"{r['drawing_values']} | {r['labels_used']}/{r['read_labels']} | "
                         f"{r['pass']} | {r['fail']} | {r['seconds']:.0f} |")
        else:
            lines.append(f"| {r['crop']} | failed: {r['error'][:60]} | | | | | | {r['seconds']:.0f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
