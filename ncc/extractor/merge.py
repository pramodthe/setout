"""
Merge HUMAN-VERIFIED candidates into rules.json. Assigns stable rule ids.

    python3 merge.py --candidates ../data/candidates.reviewed.json \\
        --source-id ARDEX-WPM-2024 --title "Ardex waterproofing manual" \\
        --url https://example.com/manual --dry-run
    python3 merge.py ... --apply     # actually writes rules/rules.json

Workflow: extract.py emits candidates -> a human confirms EVERY number against
the printed source (fixing text/clause_ref in a .reviewed.json copy) -> this
script assigns ids and merges. The model never assigns ids and this script
never invents numbers: it only carries reviewed values across, refusing
anything that fails the schema gates below.

Refuses to merge a candidate when: unit is not mm, value is not positive,
operator is not >=/<=, applies_to/parameter is not snake_case, text is not a
20-500 char sentence ending in '.', or its applies_to/parameter pair already
exists. Refuses to write when the merged document fails validate_rules.
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_rules import validate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RULES = ROOT / "ncc" / "rules" / "rules.json"
DEFAULT_BENCH = ROOT / "ncc" / "benchmark" / "questions.jsonl"
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def slug(text):
    return re.sub(r"_+", "-", text.strip().lower())


def assign_id(source_short, cand, taken):
    """Stable, human-readable id. Model output never supplies this."""
    base = f"{source_short}-{slug(cand['applies_to'])}-{slug(cand['parameter'])}"
    base = re.sub(r"-mm$", "", base)
    rid, n = base, 2
    while rid in taken:
        rid, n = f"{base}-{n}", n + 1
    taken.add(rid)
    return rid


def check_candidate(cand, pairs):
    """Return an error string, or None when the candidate is shippable."""
    for k in ("applies_to", "parameter", "operator", "value", "unit", "text"):
        if k not in cand:
            return f"missing {k!r}"
    for k in ("applies_to", "parameter"):
        if not isinstance(cand[k], str) or not SNAKE_RE.match(cand[k]):
            return f"{k} {cand[k]!r} must be snake_case"
    if (cand["applies_to"], cand["parameter"]) in pairs:
        return f"duplicate {cand['applies_to']}/{cand['parameter']}"
    if cand["operator"] not in (">=", "<="):
        return f"operator {cand['operator']!r} must be >= or <="
    v = cand["value"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return f"value {v!r} must be a positive number"
    if cand["unit"] != "mm":
        return f"unit {cand['unit']!r} must be 'mm' (never convert silently)"
    t = cand["text"]
    if not isinstance(t, str) or not (20 <= len(t) <= 500):
        return "text must be a 20-500 char paraphrase"
    if not t.endswith("."):
        return "text must end in '.'"
    if not isinstance(cand.get("drives_geometry", False), bool):
        return "drives_geometry must be true/false"
    return None
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--source-id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--authority", default="")
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--benchmark", default=str(DEFAULT_BENCH))
    ap.add_argument("--apply", action="store_true",
                    help="write rules.json; without it, print the plan only")
    args = ap.parse_args()

    if not args.url.startswith(("http://", "https://")):
        sys.exit(f"url must be checkable http(s), got {args.url!r}")
    cands = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    rules_list = cands.get("rules", cands if isinstance(cands, list) else [])
    doc = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    bench = [ln for ln in Path(args.benchmark).read_text(encoding="utf-8").splitlines()
             if ln.strip()]

    if any(s.get("id") == args.source_id for s in doc.get("sources", [])):
        sys.exit(f"source {args.source_id!r} already in {args.rules} - "
                 "use a new source id per manual revision")

    pairs = {(r.get("applies_to"), r.get("parameter")) for r in doc["rules"]}
    taken = {r["id"] for r in doc["rules"]}
    source_short = re.sub(r"[^A-Za-z0-9.]+", "-", args.source_id).strip("-")
    merged, refused = [], []
    for i, cand in enumerate(rules_list):
        err = check_candidate(cand, pairs)
        if err:
            refused.append(f"candidate[{i}] ({cand.get('applies_to')}/{cand.get('parameter')}): {err}")
            continue
        rule = {
            "id": assign_id(source_short, cand, taken),
            "source": args.source_id,
            "applies_to": cand["applies_to"],
            "parameter": cand["parameter"],
            "operator": cand["operator"],
            "value": cand["value"],
            "unit": "mm",
            "text": cand["text"],
            "drives_geometry": bool(cand.get("drives_geometry", False)),
        }
        if cand.get("clause_ref"):
            rule["clause_ref"] = cand["clause_ref"]
        merged.append(rule)
        pairs.add((rule["applies_to"], rule["parameter"]))

    print(f"candidates: {len(rules_list)}   mergeable: {len(merged)}   refused: {len(refused)}")
    for r in refused:
        print(f"  REFUSED: {r}")
    for r in merged:
        print(f"  + {r['id']}  {r['operator']} {r['value']} mm")

    staged = copy.deepcopy(doc)
    staged["sources"].append({
        "id": args.source_id, "title": args.title, "url": args.url,
        "authority": args.authority or args.title,
    })
    staged["rules"].extend(merged)
    errs = validate(staged, bench)
    if errs:
        print(f"merged document FAILS validation ({len(errs)}):")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print(f"merged document validates: {len(staged['rules'])} rules, "
          f"{len(staged['sources'])} sources")

    if not args.apply:
        print("dry run - rules.json untouched. Re-run with --apply to write.")
        return
    Path(args.rules).write_text(json.dumps(staged, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.rules}")


if __name__ == "__main__":
    main()

