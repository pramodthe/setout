"""
Lock the rules.json schema (v1.0) and cross-check its consumers.

Usage:
    python3 validate_rules.py
    python3 validate_rules.py --rules ../rules/rules.json
    python3 validate_rules.py --self-test    # negative fixtures, must all fail loudly

Exit 0 when the rule set is shippable, 1 with reasons when it is not.
Checks the SCHEMA only. Whether a number is TRUE is a human review step
against the published source - see .claude/rules/source-documents.md.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = ROOT / "rules" / "rules.json"
DEFAULT_BENCH = ROOT / "benchmark" / "questions.jsonl"

REQUIRED_RULE_KEYS = frozenset({
    "id", "source", "applies_to", "parameter", "operator",
    "value", "unit", "text", "drives_geometry",
})
# resolve() in generator/detail_generator.py only understands these two.
OPERATORS = frozenset({">=", "<="})
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.]*(-[A-Za-z0-9.]+)+$")
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate(doc, bench_lines):
    """Return a list of error strings; empty means shippable."""
    errs = []

    if not isinstance(doc, dict):
        return ["top level must be an object"]
    if doc.get("schema_version") != "1.0":
        errs.append(f"schema_version must be '1.0', got {doc.get('schema_version')!r}")
    sources = doc.get("sources")
    rules = doc.get("rules")
    if not isinstance(sources, list) or not sources:
        errs.append("'sources' must be a non-empty array")
        sources = []
    if not isinstance(rules, list) or not rules:
        errs.append("'rules' must be a non-empty array")
        rules = []

    known_sources = set()
    for i, s in enumerate(sources):
        where = f"sources[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{where}: must be an object")
            continue
        for k in ("id", "title", "url"):
            if not s.get(k):
                errs.append(f"{where}: missing {k!r}")
        url = s.get("url", "")
        if url and not url.startswith(("http://", "https://")):
            errs.append(f"{where}: url must be checkable http(s), got {url!r}")
        if s.get("id"):
            known_sources.add(s["id"])

    seen_ids, seen_pairs = set(), set()
    for i, r in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{where}: must be an object")
            continue
        missing = REQUIRED_RULE_KEYS - set(r)
        if missing:
            errs.append(f"{where}: missing keys {sorted(missing)}")
        rid = r.get("id", f"#{i}")
        if "id" in r:
            if not isinstance(r["id"], str) or not ID_RE.match(r["id"]):
                errs.append(f"{where}: id {r['id']!r} must look like 'SRC-part-slug'")
            if r["id"] in seen_ids:
                errs.append(f"{where}: duplicate id {r['id']!r}")
            seen_ids.add(r["id"])
        if r.get("source") not in known_sources:
            errs.append(f"{rid}: source {r.get('source')!r} resolves to no entry")
        for k in ("applies_to", "parameter"):
            v = r.get(k, "")
            if not isinstance(v, str) or not SNAKE_RE.match(v):
                errs.append(f"{rid}: {k} {v!r} must be snake_case")
        pair = (r.get("applies_to"), r.get("parameter"))
        if pair in seen_pairs:
            errs.append(f"{rid}: duplicate applies_to/parameter {pair}")
        seen_pairs.add(pair)
        if r.get("operator") not in OPERATORS:
            errs.append(f"{rid}: operator must be one of {sorted(OPERATORS)}")
        v = r.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            errs.append(f"{rid}: value must be a positive number")
        if r.get("unit") != "mm":
            errs.append(f"{rid}: unit must be 'mm' (millimetres everywhere)")
        t = r.get("text", "")
        if not isinstance(t, str) or not (20 <= len(t) <= 500):
            errs.append(f"{rid}: text must be a 20-500 char paraphrase")
        elif not t.endswith("."):
            errs.append(f"{rid}: text must be one sentence ending in '.'")
        if not isinstance(r.get("drives_geometry"), bool):
            errs.append(f"{rid}: drives_geometry must be true/false")

    for n, line in enumerate(bench_lines, 1):
        try:
            q = json.loads(line)
        except json.JSONDecodeError:
            errs.append(f"benchmark line {n}: not valid JSON")
            continue
        if q.get("rule_id") not in seen_ids:
            errs.append(f"benchmark {q.get('id', f'line {n}')}: "
                        f"rule_id {q.get('rule_id')!r} matches no rule")
    return errs


def _min_doc():
    return {
        "schema_version": "1.0",
        "sources": [{"id": "S", "title": "T", "url": "https://example.com/x",
                     "authority": "A"}],
        "rules": [{"id": "S-1-a", "source": "S", "applies_to": "some_part",
                   "parameter": "some_mm", "operator": ">=", "value": 10,
                   "unit": "mm", "text": "Some part must be at least 10 mm.",
                   "drives_geometry": True}],
    }


def self_test():
    """Every fixture below MUST fail validation; silence is a bug."""
    import copy
    bench = ['{"id":"q","rule_id":"S-1-a"}']
    cases = {
        "bad operator": (lambda d: d["rules"][0].update(operator=">"), "operator"),
        "dangling source": (lambda d: d["rules"][0].update(source="ZZZ"), "source"),
        "duplicate id": (lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])),
                         "duplicate"),
        "string value": (lambda d: d["rules"][0].update(value="10"), "value"),
        "long text": (lambda d: d["rules"][0].update(text="x" * 501), "text"),
        "no period": (lambda d: d["rules"][0].update(
            text="Some part must be at least 10 mm"), "text"),
        "wrong unit": (lambda d: d["rules"][0].update(unit="cm"), "unit"),
        "missing key": (lambda d: d["rules"][0].pop("parameter"), "missing"),
        "bad id": (lambda d: d["rules"][0].update(id="no dashes"), "id"),
        "non-snake": (lambda d: d["rules"][0].update(applies_to="SomePart"),
                      "snake_case"),
        "bad url": (lambda d: d["sources"][0].update(url="not a url"), "url"),
        "old version": (lambda d: d.update(schema_version="0.1"), "schema_version"),
    }
    failed = 0
    if validate(_min_doc(), bench):
        print("SELF-TEST BUG: minimal valid doc rejected: "
              f"{validate(_min_doc(), bench)}")
        failed += 1
    for name, (mut, expect) in cases.items():
        d = _min_doc()
        mut(d)
        errs = validate(d, bench)
        if not any(expect in e for e in errs):
            print(f"SELF-TEST BUG: {name!r} passed validation (want {expect!r})")
            failed += 1
    if not any("rule_id" in e for e in
               validate(_min_doc(), ['{"id":"q","rule_id":"NOPE"}'])):
        print("SELF-TEST BUG: dangling benchmark rule_id passed")
        failed += 1
    print("self-test: all fixtures fail loudly" if not failed
          else f"self-test: {failed} BUG(S)")
    return failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--benchmark", default=str(DEFAULT_BENCH))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(1 if self_test() else 0)

    doc = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    bench = [ln for ln in
             Path(args.benchmark).read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    errs = validate(doc, bench)
    if errs:
        print(f"{args.rules}: {len(errs)} problem(s)")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    n_geo = sum(1 for r in doc["rules"] if r["drives_geometry"])
    print(f"OK: {len(doc['rules'])} rules ({n_geo} drive geometry), "
          f"{len(bench)} benchmark questions resolve")


if __name__ == "__main__":
    main()
