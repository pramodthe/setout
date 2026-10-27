"""
Parametric detail generator.

THE POINT: 3D geometry is BUILT FROM the numbers in rules.json.
Nothing is modelled by hand. Every emitted solid carries the rule id that set its
dimensions, so the viewer can show provenance down to the clause.

Feed it a new rule set -> it produces new geometry. That is what makes this a
system rather than a diorama.

Usage:
    python detail_generator.py --detail shower_hob --out ../viewer/scene.json
    python detail_generator.py --detail shower_hob --violate membrane_return_onto_floor_mm=20
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "rules" / "rules.json"
DETAILS_DIR = Path(__file__).resolve().parent / "details"

# Millimetres throughout. Origin at the OUTSIDE FACE of the hob, at finished floor
# level. +x runs away from the shower (into the bathroom). +y is up.
# +z runs along the length of the hob.


def load_rules():
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["rules"]}


def rules_for(rules, applies_to, parameter):
    """Find the rule governing a given parameter of a given component."""
    for r in rules.values():
        if r["applies_to"] == applies_to and r["parameter"] == parameter:
            return r
    return None


def resolve(rules, applies_to, parameter, chosen=None):
    """
    Resolve a dimension.

    Returns (value, rule_id, satisfied). If `chosen` is None we take the code
    minimum straight from the rule - the geometry is literally the rule.
    If `chosen` is given (e.g. a real product spec, or a deliberate violation for
    the demo) we keep it but record whether it satisfies the rule.
    """
    rule = rules_for(rules, applies_to, parameter)
    if rule is None:
        if chosen is None:
            raise KeyError(f"No rule and no value for {applies_to}.{parameter}")
        return chosen, None, True

    minimum = rule["value"]
    if chosen is None:
        return minimum, rule["id"], True

    op = rule["operator"]
    ok = (chosen >= minimum) if op == ">=" else (chosen <= minimum)
    return chosen, rule["id"], ok


def solid(name, layer, kind, color, rule_id=None, note=None, **geom):
    """One piece of geometry plus the provenance that produced it."""
    s = {
        "name": name,
        "layer": layer,
        "kind": kind,
        "color": color,
        "rule_id": rule_id,
        "note": note,
    }
    s.update(geom)
    return s


def build_shower_hob(rules, overrides=None):
    """
    Shower hob with waterproofing membrane.

    Cross-section profile, extruded along +z. The membrane is a polyline whose
    every vertex is placed by a rule, not by eye.
    """
    overrides = overrides or {}
    length = 1200          # hob length along z, presentation only
    hob_width = 90         # typical brick-on-edge hob, a product dimension
    hob_height = 100       # product dimension
    slab_thickness = 60
    shower_run = 900       # how far into the shower we draw the floor
    membrane_t = 4         # drawn thickness so it is visible

    # --- dimensions that COME FROM THE RULES -------------------------------
    stepdown, r_stepdown, ok_stepdown = resolve(
        rules, "shower_floor", "stepdown_below_ffl_mm",
        overrides.get("stepdown_below_ffl_mm"))

    ret, r_ret, ok_ret = resolve(
        rules, "shower_hob", "membrane_return_onto_floor_mm",
        overrides.get("membrane_return_onto_floor_mm"))

    wall_h, r_wall, ok_wall = resolve(
        rules, "shower_wall", "membrane_above_finished_tile_mm",
        overrides.get("membrane_above_finished_tile_mm"))
    # -----------------------------------------------------------------------

    shower_y = -stepdown          # shower floor sits below bathroom FFL
    wall_x = -shower_run          # shower wall plane

    solids = []

    solids.append(solid(
        "Bathroom floor substrate", "substrate", "box", "#8d8d8d",
        note="Structural substrate, bathroom side",
        x=0, y=-slab_thickness, z=0, w=700, h=slab_thickness, d=length))

    solids.append(solid(
        "Shower floor substrate", "substrate", "box", "#8d8d8d",
        rule_id=r_stepdown,
        note=f"Set down {stepdown} mm below finished floor level",
        x=wall_x, y=shower_y - slab_thickness, z=0,
        w=shower_run, h=slab_thickness, d=length))

    solids.append(solid(
        "Shower wall substrate", "substrate", "box", "#9a9a9a",
        note="Wall substrate behind membrane",
        x=wall_x - 60, y=shower_y, z=0, w=60, h=wall_h + 200, d=length))

    solids.append(solid(
        "Hob", "substrate", "box", "#a8a093",
        note=f"{hob_width} x {hob_height} mm hob (product dimension)",
        x=-hob_width, y=0, z=0, w=hob_width, h=hob_height, d=length))

    solids.append(solid(
        "Primer", "primer", "coat", "#d8c48a",
        note="Substrate primer, per manufacturer manual",
        follows="membrane", offset=-1))

    solids.append(solid(
        "Bond breaker", "bond_breaker", "coat", "#c98f4a",
        note="Bond breaker at wall/floor junction and all internal corners",
        x=wall_x, y=shower_y, z=0, w=12, h=12, d=length))

    # --- the membrane: every vertex placed by a rule ------------------------
    path = [
        {"x": wall_x, "y": shower_y + wall_h,
         "rule_id": r_wall,
         "label": f"Membrane {wall_h} mm up the wall"},
        {"x": wall_x, "y": shower_y,
         "rule_id": r_wall,
         "label": "Down the wall to the shower floor"},
        {"x": -hob_width, "y": shower_y,
         "rule_id": r_stepdown,
         "label": f"Across the shower floor, {stepdown} mm below FFL"},
        {"x": -hob_width, "y": hob_height,
         "rule_id": r_ret,
         "label": "Up the inside face of the hob"},
        {"x": 0, "y": hob_height,
         "rule_id": r_ret,
         "label": "Over the top of the hob"},
        {"x": 0, "y": 0,
         "rule_id": r_ret,
         "label": "Down the outside face"},
        {"x": ret, "y": 0,
         "rule_id": r_ret,
         "label": f"Terminate {ret} mm onto the floor"},
    ]

    solids.append(solid(
        "Waterproofing membrane", "membrane", "polyline", "#2f6fb8",
        rule_id=r_ret,
        note="Continuous membrane. Each vertex is placed by a rule.",
        path=path, thickness=membrane_t, d=length))
    # -----------------------------------------------------------------------

    solids.append(solid(
        "Screed", "screed", "box", "#bdb6a8",
        note="Screed to falls over the membrane",
        x=wall_x, y=shower_y + membrane_t, z=0,
        w=shower_run - hob_width, h=22, d=length))

    checks = [
        {"parameter": "stepdown_below_ffl_mm", "value": stepdown,
         "rule_id": r_stepdown, "satisfied": ok_stepdown},
        {"parameter": "membrane_return_onto_floor_mm", "value": ret,
         "rule_id": r_ret, "satisfied": ok_ret},
        {"parameter": "membrane_above_finished_tile_mm", "value": wall_h,
         "rule_id": r_wall, "satisfied": ok_wall},
    ]

    return {
        "detail_id": "shower_hob",
        "title": "Shower hob — waterproofing membrane",
        "units": "mm",
        "layer_order": ["substrate", "primer", "bond_breaker", "membrane", "screed"],
        "solids": solids,
        "checks": checks,
        "rules_used": _rules_used(solids, rules),
    }


def _rules_used(solids, rules):
    """Every rule referenced by a solid OR by a polyline vertex."""
    ids = set()
    for s in solids:
        if s.get("rule_id"):
            ids.add(s["rule_id"])
        for v in s.get("path", []) or []:
            if v.get("rule_id"):
                ids.add(v["rule_id"])
    return {rid: rules[rid] for rid in ids if rid in rules}


BUILDERS = {"shower_hob": build_shower_hob}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default="shower_hob", choices=sorted(BUILDERS))
    ap.add_argument("--out", default=str(ROOT / "viewer" / "scene.json"))
    ap.add_argument("--violate", action="append", default=[],
                    help="param=value, e.g. membrane_return_onto_floor_mm=20")
    args = ap.parse_args()

    overrides = {}
    for item in args.violate:
        k, v = item.split("=", 1)
        overrides[k.strip()] = float(v)

    rules = load_rules()
    scene = BUILDERS[args.detail](rules, overrides)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scene, indent=2), encoding="utf-8")

    # Also emit a plain <script> payload so the viewer works from file://
    # without a local server (fetch() is blocked on file:// by CORS).
    js = out.with_suffix(".js")
    js.write_text("window.SCENE = " + json.dumps(scene, indent=2) + ";",
                  encoding="utf-8")

    print(f"wrote {out}")
    print(f"wrote {js}")
    print(f"  solids: {len(scene['solids'])}   rules used: {len(scene['rules_used'])}")
    for c in scene["checks"]:
        flag = "PASS" if c["satisfied"] else "FAIL"
        print(f"  [{flag}] {c['parameter']} = {c['value']:g} mm   ({c['rule_id']})")


if __name__ == "__main__":
    main()
