"""
Three-bar benchmark: cold vs grounded vs verifier-retry.

    python3 run.py --dry-run             # no API calls, checks questions resolve
    python3 run.py --limit 4             # spend ~12 calls, smoke the pipeline
    python3 run.py                       # full run, writes results.json + chart.html

Model: MODEL_API_MODEL, else META_MODEL, else muse-spark-1.3.
Needs META_API_KEY (or MODEL_API_KEY). Stdlib only.

Scoring is exact-match on (answer_mm, operator). The cold score is published
whatever it is — a benchmark you can only win is not a benchmark.
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
QUESTIONS = ROOT / "ncc" / "benchmark" / "questions.jsonl"
RULES_PATH = ROOT / "ncc" / "rules" / "rules.json"
API_BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
MODEL = (os.environ.get("MODEL_API_MODEL")
         or os.environ.get("META_MODEL")
         or "muse-spark-1.3")

ANSWER_FORMAT = {
    "type": "json_schema",
    "name": "numeric_answer",
    "schema": {
        "type": "object",
        "properties": {
            "answer_mm": {"type": "number"},
            "operator": {"type": "string", "enum": [">=", "<="]},
        },
        "required": ["answer_mm", "operator"],
        "additionalProperties": False,
    },
}


def load_questions():
    qs = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            qs.append(json.loads(line))
    return qs


def load_rules():
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def rules_context(doc):
    """Compact rules block for the grounded conditions."""
    lines = []
    for r in doc["rules"]:
        lines.append(
            f"- {r['id']} [{r['applies_to']}/{r['parameter']}]: "
            f"{r['operator']} {r['value']} {r['unit']}. {r['text']}")
    return "\n".join(lines)


def call_model(api_key, prompt):
    """One structured request. Returns the parsed {answer_mm, operator}."""
    payload = {
        "model": MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt}]}],
        "text": {"format": ANSWER_FORMAT},
        "stream": True,
    }
    req = urllib.request.Request(
        f"{API_BASE}/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST",
    )
    text, done = [], False
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            ev = json.loads(data)
            kind = ev.get("type")
            if kind == "response.output_text.delta":
                text.append(ev.get("delta") or "")
            elif kind == "response.completed":
                done = True
            elif kind in ("error", "response.failed"):
                raise RuntimeError(json.dumps(ev)[:300])
    if not done:
        raise RuntimeError("stream ended before response.completed")
    return json.loads("".join(text))
def correct(q, ans):
    try:
        return (float(ans["answer_mm"]) == float(q["answer_mm"])
                and ans["operator"] == q["operator"])
    except (KeyError, TypeError, ValueError):
        return False


COLD_TMPL = """Answer this building-code question with the exact required number.

Question: {q}

Reply with the numeric answer in millimetres and whether it is a minimum (>=) or maximum (<=)."""

GROUNDED_TMPL = """Answer this building-code question using ONLY the manual rules below.
If no rule covers it, still give your best numeric answer.

Manual rules:
{rules}

Question: {q}

Reply with the numeric answer in millimetres and whether it is a minimum (>=) or maximum (<=)."""

RETRY_TMPL = """Your previous answer ({prev} mm, {op}) was REJECTED: no rule in the manual
supports that number for this question. Re-read the manual and answer again.
Do not repeat a rejected answer.

Manual rules:
{rules}

Question: {q}

Reply with the numeric answer in millimetres and whether it is a minimum (>=) or maximum (<=)."""


def run_condition(api_key, qs, context, mode):
    """Run one condition over all questions. Returns list of row dicts."""
    rows = []
    for q in qs:
        try:
            if mode == "cold":
                ans = call_model(api_key, COLD_TMPL.format(q=q["question"]))
                rows.append({"id": q["id"], "ok": correct(q, ans), "answer": ans,
                             "retried": False})
            elif mode == "grounded":
                ans = call_model(api_key, GROUNDED_TMPL.format(rules=context, q=q["question"]))
                rows.append({"id": q["id"], "ok": correct(q, ans), "answer": ans,
                             "retried": False})
            else:  # retry: grounded, plus one verifier rejection + second attempt
                first = call_model(api_key, GROUNDED_TMPL.format(rules=context, q=q["question"]))
                if correct(q, first):
                    rows.append({"id": q["id"], "ok": True, "answer": first, "retried": False})
                else:
                    second = call_model(api_key, RETRY_TMPL.format(
                        prev=first.get("answer_mm"), op=first.get("operator"),
                        rules=context, q=q["question"]))
                    rows.append({"id": q["id"], "ok": correct(q, second),
                                 "answer": second, "retried": True,
                                 "first": first})
        except urllib.error.HTTPError as e:
            rows.append({"id": q["id"], "ok": False, "error": f"HTTP {e.code}"})
        except Exception as e:  # noqa: BLE001 - one bad row must not kill the run
            rows.append({"id": q["id"], "ok": False, "error": str(e)[:200]})
        mark = "ok " if rows[-1]["ok"] else "miss"
        print(f"  [{mode:>8}] {q['id']} {mark}  {rows[-1].get('answer') or rows[-1].get('error')}",
              flush=True)
    return rows


def accuracy(rows):
    return sum(1 for r in rows if r["ok"]) / len(rows) if rows else 0.0


def write_chart(path, results, model):
    """Self-contained SVG bar chart, viewable from file://."""
    bars = [("cold", "#8fa0b5"), ("grounded", "#4ea1ff"), ("retry", "#3ecf8e")]
    labels = {"cold": "Cold", "grounded": "+ rules", "retry": "+ retry"}
    W, H, base, top = 560, 340, 280, 40
    bw, gap = 110, 40
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Setout benchmark</title></head>",
        "<body style='margin:0;background:#0e1116;color:#e7edf5;font-family:sans-serif'>",
        "<div style='max-width:600px;margin:24px auto;padding:0 16px'>",
        f"<h2 style='font-size:16px'>Benchmark: exact-match accuracy ({model})</h2>",
        f"<p style='color:#8fa0b5;font-size:13px'>{results['n']} questions from NCC 2022 Part 10.2. Cold score published whatever it is.</p>",
        f"<svg viewBox='0 0 {W} {H}' width='100%' role='img'>",
    ]
    x = gap
    for key, color in bars:
        acc = results["conditions"][key]["accuracy"]
        h = max((base - top) * acc, 2)
        parts.append(
            f"<rect x='{x}' y='{base - h}' width='{bw}' height='{h}' rx='6' fill='{color}'/>"
            f"<text x='{x + bw / 2}' y='{base - h - 8}' fill='#e7edf5' font-size='20' "
            f"text-anchor='middle' font-weight='bold'>{acc:.0%}</text>"
            f"<text x='{x + bw / 2}' y='{base + 24}' fill='#8fa0b5' font-size='15' "
            f"text-anchor='middle'>{labels[key]}</text>")
        x += bw + gap
    parts.append(f"<line x1='0' y1='{base}' x2='{W}' y2='{base}' stroke='#263040'/>")
    parts.append("</svg></div></body></html>")
    path.write_text("".join(parts), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "ncc" / "benchmark" / "results.json"))
    args = ap.parse_args()

    qs = load_questions()
    doc = load_rules()
    by_id = {r["id"]: r for r in doc["rules"]}
    bad = [q["id"] for q in qs if q.get("rule_id") not in by_id]
    if bad:
        sys.exit(f"{len(bad)} question(s) reference no rule: {bad}")
    if args.limit:
        qs = qs[:args.limit]
    print(f"Model : {MODEL}\nURL   : {API_BASE}/responses")
    print(f"Questions: {len(qs)}   Rules in context: {len(doc['rules'])}")

    if args.dry_run:
        print("dry-run: all questions resolve. No API calls made.")
        return

    api_key = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY")
    if not api_key:
        sys.exit("META_API_KEY is not set. No calls made, no results faked.")

    context = rules_context(doc)
    results = {"model": MODEL, "n": len(qs), "conditions": {}}
    for mode in ("cold", "grounded", "retry"):
        rows = run_condition(api_key, qs, context, mode)
        results["conditions"][mode] = {"accuracy": accuracy(rows), "rows": rows}

    out = Path(args.out)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    chart = out.parent / "chart.html"
    write_chart(chart, results, MODEL)
    print(f"\nwrote {out}\nwrote {chart}")
    for mode in ("cold", "grounded", "retry"):
        print(f"  {mode:>8}: {results['conditions'][mode]['accuracy']:.0%}")


if __name__ == "__main__":
    main()

