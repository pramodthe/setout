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

ROOT = Path(__file__).resolve().parent.parent.parent
RULES_PATH = ROOT / "ncc" / "rules" / "rules.json"
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
        note="Structural substrate, bathroom side (product dimension, no governing rule)",
        x=0, y=-slab_thickness, z=0, w=700, h=slab_thickness, d=length))

    solids.append(solid(
        "Shower floor substrate", "substrate", "box", "#8d8d8d",
        rule_id=r_stepdown,
        note=f"Set down {stepdown} mm below finished floor level",
        x=wall_x, y=shower_y - slab_thickness, z=0,
        w=shower_run, h=slab_thickness, d=length))

    solids.append(solid(
        "Shower wall substrate", "substrate", "box", "#9a9a9a",
        note="Wall substrate behind membrane (product dimension, no governing rule)",
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
        note="Bond breaker at wall/floor junction and all internal corners (product dimension, no governing rule)",
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
        note="Screed to falls over the membrane (product dimension, no governing rule)",
        x=wall_x, y=shower_y + membrane_t, z=0,
        w=shower_run - hob_width, h=22, d=length))

    solids.append(solid(
        "Bathroom floor tiles", "screed", "box", "#cfc9bd",
        note="Floor tile finish (product dimension, no governing rule)",
        x=0, y=membrane_t, z=0, w=700, h=8, d=length))

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
        "rules_used": _rules_used(solids, checks, rules),
    }


def _rules_used(solids, checks, rules):
    """Every rule referenced by a solid, a polyline vertex, or a check."""
    ids = set()
    for s in solids:
        if s.get("rule_id"):
            ids.add(s["rule_id"])
        for v in s.get("path", []) or []:
            if v.get("rule_id"):
                ids.add(v["rule_id"])
    for c in checks or []:
        if c.get("rule_id"):
            ids.add(c["rule_id"])
    return {rid: rules[rid] for rid in sorted(ids) if rid in rules}


def build_wall_floor_junction(rules, overrides=None):
    """
    Wall/floor junction with flashing.

    Cross-section profile, extruded along +z. FFL at y=0, wall substrate
    face at x=0. The flashing L is placed by rules; substrates and tile
    finish are product dimensions.
    """
    overrides = overrides or {}
    length = 1200
    floor_run = 600
    wall_h = 400
    slab_thickness = 60
    wall_t = 60
    flash_t = 6          # drawn flashing thickness so it is visible

    vleg, r_vleg, ok_vleg = resolve(
        rules, "wall_floor_junction", "flashing_vertical_leg_above_ffl_mm",
        overrides.get("flashing_vertical_leg_above_ffl_mm"))
    hleg, r_hleg, ok_hleg = resolve(
        rules, "wall_floor_junction", "flashing_horizontal_leg_mm",
        overrides.get("flashing_horizontal_leg_mm"))

    solids = []
    solids.append(solid(
        "Floor substrate", "substrate", "box", "#8d8d8d",
        note="Structural substrate (product dimension, no governing rule)",
        x=0, y=-slab_thickness, z=0, w=floor_run, h=slab_thickness, d=length))
    solids.append(solid(
        "Wall substrate", "substrate", "box", "#9a9a9a",
        note="Wall substrate (product dimension, no governing rule)",
        x=-wall_t, y=0, z=0, w=wall_t, h=wall_h, d=length))
    solids.append(solid(
        "Flashing vertical leg", "membrane", "box", "#2f6fb8",
        rule_id=r_vleg,
        note=f"Flashing up the wall {vleg:g} mm above FFL",
        x=-flash_t, y=0, z=0, w=flash_t, h=vleg, d=length))
    solids.append(solid(
        "Flashing horizontal leg", "membrane", "box", "#2f6fb8",
        rule_id=r_hleg,
        note=f"Flashing {hleg:g} mm out onto the floor",
        x=0, y=0, z=0, w=hleg, h=flash_t, d=length))
    solids.append(solid(
        "Floor tiles", "screed", "box", "#cfc9bd",
        note="Floor tile finish (product dimension, no governing rule)",
        x=hleg, y=0, z=0, w=floor_run - hleg, h=8, d=length))

    checks = [
        {"parameter": "flashing_vertical_leg_above_ffl_mm", "value": vleg,
         "rule_id": r_vleg, "satisfied": ok_vleg},
        {"parameter": "flashing_horizontal_leg_mm", "value": hleg,
         "rule_id": r_hleg, "satisfied": ok_hleg},
    ]

    return {
        "detail_id": "wall_floor_junction",
        "title": "Wall/floor junction — flashing",
        "units": "mm",
        "layer_order": ["substrate", "membrane", "screed"],
        "solids": solids,
        "checks": checks,
        "rules_used": _rules_used(solids, checks, rules),
    }


def build_internal_corner(rules, overrides=None):
    """
    Internal (concave) wall corner with flashing.

    Two wall faces meet at a vertical corner line at x=0, z=0, FFL at y=0.
    Wall A runs in +x, wall B runs in +z. A flashing leg on each face extends
    `overlap` mm out from the corner and `flash_h` mm up from FFL — both
    placed by rules. Substrates are product dimensions.
    """
    overrides = overrides or {}
    wall_run = 500        # how far each wall face is drawn from the corner
    floor_run = 500
    slab_thickness = 60
    wall_t = 60
    flash_t = 6           # drawn flashing thickness so it is visible
    margin = 100          # substrate drawn past the flashing top (product)

    overlap, r_overlap, ok_overlap = resolve(
        rules, "internal_corner", "flashing_leg_overlap_mm",
        overrides.get("flashing_leg_overlap_mm"))
    flash_h, r_flash_h, ok_flash_h = resolve(
        rules, "shower_wall", "vertical_flashing_above_ffl_mm",
        overrides.get("vertical_flashing_above_ffl_mm"))

    solids = []
    solids.append(solid(
        "Floor substrate", "substrate", "box", "#8d8d8d",
        note="Structural substrate (product dimension, no governing rule)",
        x=0, y=-slab_thickness, z=0,
        w=floor_run, h=slab_thickness, d=floor_run))
    solids.append(solid(
        "Wall A substrate", "substrate", "box", "#9a9a9a",
        note="Wall substrate (product dimension, no governing rule)",
        x=0, y=0, z=-wall_t, w=wall_run, h=flash_h + margin, d=wall_t))
    solids.append(solid(
        "Wall B substrate", "substrate", "box", "#9a9a9a",
        note="Wall substrate (product dimension, no governing rule)",
        x=-wall_t, y=0, z=0, w=wall_t, h=flash_h + margin, d=wall_run))
    solids.append(solid(
        "Bond breaker", "bond_breaker", "box", "#c98f4a",
        note="Bond breaker fillet up the corner (product dimension, no governing rule)",
        x=0, y=0, z=0, w=12, h=flash_h, d=12))
    solids.append(solid(
        "Flashing leg on wall A", "membrane", "box", "#2f6fb8",
        rule_id=r_overlap,
        note=f"Flashing {overlap:g} mm out from the corner, {flash_h:g} mm up from FFL",
        x=0, y=0, z=0, w=overlap, h=flash_h, d=flash_t))
    solids.append(solid(
        "Flashing leg on wall B", "membrane", "box", "#2f6fb8",
        rule_id=r_overlap,
        note=f"Flashing {overlap:g} mm out from the corner, {flash_h:g} mm up from FFL",
        x=0, y=0, z=0, w=flash_t, h=flash_h, d=overlap))

    checks = [
        {"parameter": "flashing_leg_overlap_mm", "value": overlap,
         "rule_id": r_overlap, "satisfied": ok_overlap},
        {"parameter": "vertical_flashing_above_ffl_mm", "value": flash_h,
         "rule_id": r_flash_h, "satisfied": ok_flash_h},
    ]

    return {
        "detail_id": "internal_corner",
        "title": "Internal corner — flashing",
        "units": "mm",
        "layer_order": ["substrate", "bond_breaker", "membrane"],
        "solids": solids,
        "checks": checks,
        "rules_used": _rules_used(solids, checks, rules),
    }


BUILDERS = {
    "shower_hob": build_shower_hob,
    "wall_floor_junction": build_wall_floor_junction,
    "internal_corner": build_internal_corner,
}

# Canonical "built wrong" demo per detail, shipped as scenes_violate.js for the
# Jarvis violation toggle. Each override MUST fail its rule; main() asserts it.
VIOLATIONS = {
    "shower_hob": {"membrane_return_onto_floor_mm": 20},
    "wall_floor_junction": {"flashing_horizontal_leg_mm": 20},
    "internal_corner": {"flashing_leg_overlap_mm": 20},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default="shower_hob", choices=sorted(BUILDERS))
    ap.add_argument("--out", default=str(ROOT / "ncc" / "viewer" / "scene.json"))
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

    # Every detail in one payload - feeds the viewer's detail switcher.
    # Overrides apply only to the detail being (mis)built.
    scenes = {k: BUILDERS[k](rules, overrides if k == args.detail else {})
              for k in sorted(BUILDERS)}
    scenes_js = ROOT / "ncc" / "viewer" / "scenes.js"
    scenes_js.write_text("window.SCENES = " + json.dumps(scenes, indent=2) + ";",
                         encoding="utf-8")

    # Canonical wrong builds for the Jarvis violation toggle. A violation
    # payload that accidentally passes its rule would lie in the demo.
    violated = {k: BUILDERS[k](rules, dict(v)) for k, v in VIOLATIONS.items()}
    for k, s in violated.items():
        if all(c["satisfied"] for c in s["checks"]):
            raise SystemExit(f"VIOLATIONS[{k}] passes all checks - fix the override")
    viol_js = ROOT / "ncc" / "viewer" / "scenes_violate.js"
    viol_js.write_text("window.SCENES_VIOLATE = " + json.dumps(violated, indent=2) + ";",
                       encoding="utf-8")

    print(f"wrote {out}")
    print(f"wrote {js}")
    print(f"wrote {scenes_js} ({', '.join(scenes)})")
    print(f"wrote {viol_js} ({', '.join(sorted(violated))})")
    print(f"  solids: {len(scene['solids'])}   rules used: {len(scene['rules_used'])}")
    for c in scene["checks"]:
        flag = "PASS" if c["satisfied"] else "FAIL"
        print(f"  [{flag}] {c['parameter']} = {c['value']:g} mm   ({c['rule_id']})")


if __name__ == "__main__":
    main()
