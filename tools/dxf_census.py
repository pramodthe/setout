#!/usr/bin/env python
"""Census of DXF files: what the owner's drawings actually use.

    python tools/dxf_census.py <files or dirs> [--json out.json]

Prints a readable table and writes the full census as JSON. The JSON goes under
data/ by default (gitignored) or wherever --json points outside the repo tree.
Writing it into a tracked path is refused: see .claude/rules/source-documents.md.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import dxf_ingest  # noqa: E402

DEFAULT_JSON = ROOT / "data" / "census" / "dxf_census.json"


def find_dxf(paths):
    files = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() == ".dxf"))
        else:
            files.append(path)
    return files


def allowed_output(out):
    """Outside the repo, or under data/: never a tracked path."""
    out = Path(out).resolve()
    data = (ROOT / "data").resolve()
    return ROOT not in out.parents or out == data or data in out.parents


def _counts(counter, indent="  ", limit=None):
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    if limit and len(items) > limit:
        items = items[:limit] + [(f"... {len(items) - limit} more", "")]
    width = max((len(str(k)) for k, _ in items), default=0)
    return [f"{indent}{k:<{width}}  {v:>8}" for k, v in items]


def table(c):
    lines = [f"DXF census: {c['file_count']} file(s), {c.get('entities', 0)} modelspace entities"
             + (f", {len(c['errors'])} unreadable" if c["errors"] else "")]
    if not c["file_count"]:
        return "\n".join(lines + [f"  {p}: {m}" for p, m in c["errors"].items()])
    lines += ["", "Entity types"] + _counts(c["entity_types"])
    lines += ["", f"Linetypes (explicit {c['linetype_explicit']}, inherited {c['linetype_inherited']})"]
    width = max((len(k) for k in c["linetypes"]), default=0)
    for name, split in sorted(c["linetypes"].items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(f"  {name:<{width}}  explicit {split['explicit']:>6}  inherited {split['inherited']:>6}")
    lines += ["", "Hatch patterns"] + (_counts(c["hatch_patterns"]) or ["  none"])
    leaders = c["leaders"]
    lines += ["", "Leaders",
              f"  LEADER {leaders['LEADER']}   MLEADER {leaders['MLEADER']}   line+text {leaders['line_plus_text']}"]
    dims = c["dimensions"]
    lines += ["", "Dimensions",
              f"  total {dims['total']}   measured {dims['measured']}   override {dims['override']}   suppressed {dims['suppressed']}"]
    blocks = c["blocks"]
    lines += ["", f"Blocks (definitions {blocks['definitions']}, anonymous {blocks['anonymous']})"]
    lines += _counts(blocks["inserts"], limit=20) or ["  no inserts"]
    text = c["text"]
    lines += ["", f"Text ({text['entities']} entities)", "  styles:"] + _counts(text["styles"], indent="    ")
    lines += ["  fonts:"] + _counts(text["fonts"], indent="    ")
    layers = c["layers"]
    lines += ["", f"Layers ({layers['count']}; names are not labels)"] + _counts(layers["linetypes"])
    lines += ["", "Per file"]
    for path, f in c["files"].items():
        lines.append(f"  {path}: {f['entities']} entities, {f['units_name']}, {f['dxfversion']}, "
                     f"linetypes explicit {f['linetype_explicit']} / inherited {f['linetype_inherited']}, "
                     f"dims {f['dimensions']['total']} ({f['dimensions']['override']} override), "
                     f"leaders {f['leaders']['LEADER']}+{f['leaders']['MLEADER']}+{f['leaders']['line_plus_text']}")
    if c["errors"]:
        lines += ["", "Unreadable"] + [f"  {p}: {m}" for p, m in c["errors"].items()]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="DXF files or directories (searched recursively)")
    ap.add_argument("--json", default=str(DEFAULT_JSON),
                    help="where to write the census JSON (default: data/census/dxf_census.json)")
    args = ap.parse_args(argv)
    files = find_dxf(args.paths)
    if not files:
        sys.exit("No .dxf files found under: " + ", ".join(args.paths))
    out = Path(args.json)
    if not allowed_output(out):
        sys.exit(f"Refusing --json inside the repo tree outside data/: {out}\n"
                 "Census output describes the drawings; it must never land in tracked files.")
    result = dxf_ingest.census(files)
    print(table(result))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
