"""Deterministic verification: spec -> geometry -> report.

Stages: A = drawing understanding (spec recovery vs reference),
B = geometry/assembly (spec -> meshes), C = end-to-end (source fidelity).
Every check reports PASS / FAIL / UNVERIFIED / N/A. Assumptions and
unresolved items can never produce a PASS on a source-accuracy check.
Tolerances are per-parameter, from the spec — never a universal value.
"""

from __future__ import annotations

from .geometry import Mesh
from .spec import CheckResult, DetailSpec, Provenance, find_install_cycle

CONTACT_TOL_MM = 2.0  # largest accepted contact gap; thin pairs get less (_pair_tolerance)


def _c(cid, stage, desc, status, expected=None, actual=None, tolerance=None,
       evidence=None) -> CheckResult:
    return CheckResult(id=cid, stage=stage, description=desc, status=status,
                       expected=expected, actual=actual, tolerance=tolerance,
                       evidence=evidence)


def verify_spec_structure(spec: DetailSpec) -> list[CheckResult]:
    """Stage A/B structural checks (Pydantic validation already passed)."""
    out = [_c("S-schema", "B", "Specification validates against schema v1",
              "PASS", evidence="DetailSpec model_validator passed")]
    ids = [c.id for c in spec.components]
    out.append(_c("S-ids-unique", "B", "Component IDs unique", "PASS"
                  if len(set(ids)) == len(ids) else "FAIL",
                  expected=len(ids), actual=len(set(ids))))
    known = set(ids)
    bad = [r for r in spec.relations
           if r.source not in known or r.target not in known]
    out.append(_c("S-refs", "B", "Every relation endpoint exists",
                  "PASS" if not bad else "FAIL",
                  expected=0, actual=len(bad)))
    cyc = find_install_cycle(spec)
    out.append(_c("S-acyclic", "B", "Install dependencies acyclic",
                  "PASS" if not cyc else "FAIL",
                  actual=" -> ".join(cyc) if cyc else "no cycle"))
    blocked = [u.id for u in spec.unresolved if u.blocks_geometry]
    out.append(_c("S-unblocked", "B", "No unresolved item blocks geometry",
                  "PASS" if not blocked else "FAIL", actual=blocked or "none"))
    return out


def _measure(meshes: dict[str, Mesh], cid: str):
    m = meshes.get(cid)
    return m.bounds() if m else None


def _tri_top_at(tri: list[float], x: float, z: float,
                  eps: float = 1e-6) -> float | None:
    """Surface y of triangle `tri` (9 floats) above plan point (x, z), or
    None when the point falls outside the triangle's plan projection."""
    ax, ay, az, bx, by, bz, cx, cy, cz = tri
    d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
    if abs(d) < eps:
        return None  # edge-on to plan (vertical face)
    l1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d
    l2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d
    l3 = 1.0 - l1 - l2
    if l1 < -eps or l2 < -eps or l3 < -eps:
        return None
    return l1 * ay + l2 * by + l3 * cy


def _contact_gap(meshes: dict[str, Mesh], top_id: str, bottom_id: str,
                 band_mm: float = 20.0) -> float | None:
    """Gap between top's underside and bottom's top surface, sampled at the
    corners + centre of top's plan footprint against bottom's TRIANGLES
    (vertex-only sampling misses long quads). Returns None when no bearing
    surface is found within band_mm below top's underside."""
    top, bottom = meshes.get(top_id), meshes.get(bottom_id)
    if not top or not bottom or not top.positions or not bottom.positions:
        return None
    bt = top.bounds()
    assert bt is not None
    x0, y0, z0, x1, _, z1 = bt
    pos = bottom.positions
    bearing = []
    # (a) top-down: bottom surface under top's footprint samples
    for sx, sz in ((x0, z0), (x0, z1), (x1, z0), (x1, z1),
                   ((x0 + x1) / 2, (z0 + z1) / 2)):
        tops = []
        for i in range(0, len(pos), 9):
            y = _tri_top_at(pos[i:i + 9], sx, sz)
            if y is not None and -band_mm <= y0 - y <= band_mm:
                tops.append(y)
        if tops:
            bearing.append(max(tops))
    # (b) bottom-up: bottom top-face centroids inside top's footprint
    # (catches small supports, e.g. a nut under a wide sub-sill)
    for i in range(0, len(pos), 9):
        t = pos[i:i + 9]
        cx = (t[0] + t[3] + t[6]) / 3
        cz = (t[2] + t[5] + t[8]) / 3
        cy = (t[1] + t[4] + t[7]) / 3
        if (x0 - 1.0 <= cx <= x1 + 1.0 and z0 - 1.0 <= cz <= z1 + 1.0
                and -band_mm <= y0 - cy <= band_mm
                and _tri_top_at(t, cx, cz) is not None):
            bearing.append(cy)
    if not bearing:
        return None
    return y0 - max(bearing)


def _thickness(spec: DetailSpec, meshes: dict[str, Mesh], cid: str) -> float | None:
    """A sheet's physical thickness; any other part's built height, the
    direction a contact gap is measured in."""
    for p in spec.component(cid).params:
        if p.name == "thickness_physical":
            return p.value
    b = _measure(meshes, cid)
    return b[4] - b[1] if b else None


def _pair_tolerance(spec: DetailSpec, meshes: dict[str, Mesh], top: str,
                    bottom: str) -> float:
    """min(CONTACT_TOL_MM, half the thinner part): a flat 2 mm band would
    hide a 1.5 mm membrane that is floating or missing."""
    known = [t for t in (_thickness(spec, meshes, top),
                         _thickness(spec, meshes, bottom)) if t]
    return min([CONTACT_TOL_MM] + [0.5 * t for t in known])


def verify_dimensions(spec: DetailSpec, meshes: dict[str, Mesh]
                      ) -> list[CheckResult]:
    """Stage B: built mesh dims vs DRAWING-provenance params only."""
    out: list[CheckResult] = []
    by_id = {c.id: c for c in spec.components}

    def drawing_params(cid: str):
        return [p for p in by_id[cid].params
                if p.provenance == Provenance.DRAWING
                and p.role.value == "physical"]

    # step_height on slab_lower: one-sided minimum when the spec note says
    # MINIMUM (D3: "100mm minimum step"), else two-sided (D4: 80mm set-down).
    if "slab_lower" in by_id and any(
            p.name == "step_height" for p in by_id["slab_lower"].params):
        p = by_id["slab_lower"].param("step_height")
        bu, bl = _measure(meshes, "slab_upper"), _measure(meshes, "slab_lower")
        if bu and bl and p.provenance == Provenance.DRAWING:
            actual = bu[4] - bl[4]  # top faces
            tol = p.tolerance_mm or 0
            if (p.note or "").startswith("MINIMUM"):
                ok, exp = actual + tol >= p.value, f">= {p.value}"
                desc = f"Step down >= {p.value:g}mm drawing minimum"
            else:
                ok, exp = abs(actual - p.value) <= tol, f"== {p.value:g}"
                desc = f"Set-down == {p.value:g}mm drawing dimension"
            out.append(_c("B-step", "B", desc, "PASS" if ok else "FAIL",
                          expected=exp, actual=round(actual, 3),
                          tolerance=p.tolerance_mm,
                          evidence=f"slab_upper.top={bu[4]}, "
                                   f"slab_lower.top={bl[4]}"))
    # sealant width: checked only when the drawing dimensions the gap
    if "sealant" in by_id and any(
            p.name == "width" for p in by_id["sealant"].params):
        sp = by_id["sealant"].param("width")
        bs = _measure(meshes, "sealant")
        if bs and sp.provenance == Provenance.DRAWING:
            actual = bs[3] - bs[0]
            ok = abs(actual - sp.value) <= (sp.tolerance_mm or 0)
            out.append(_c("B-gap", "B",
                          f"Sealant gap width == {sp.value:g}mm",
                          "PASS" if ok else "FAIL",
                          expected=sp.value, actual=round(actual, 3),
                          tolerance=sp.tolerance_mm))
        elif bs:
            out.append(_c("B-gap", "B", "Sealant gap width (drawing)",
                          "N/A", evidence="gap not dimensioned in drawing"))
    # screed fall ratio: only details with a screed-to-falls
    if "screed" in by_id and any(
            p.name == "fall_ratio" for p in by_id["screed"].params):
        fp = by_id["screed"].param("fall_ratio")
        bx = _measure(meshes, "screed")
        if bx:
            pos = meshes["screed"].positions
            xs = pos[0::3]
            ys = pos[1::3]
            x0, x1 = min(xs), max(xs)
            top_lo = max(y for x, y in zip(xs, ys) if x < x0 + 1.0)
            top_hi = max(y for x, y in zip(xs, ys) if x > x1 - 1.0)
            actual = (top_lo - top_hi) / (x1 - x0)
            ok = actual + (fp.tolerance_mm or 0) >= fp.value
            out.append(_c("B-fall", "B", "Screed fall >= 1:80 toward outlet",
                          "PASS" if ok else "FAIL",
                          expected=f">= {fp.value}", actual=round(actual, 6),
                          tolerance=fp.tolerance_mm,
                          evidence=f"top@{x0:.1f}={top_lo:.2f}, "
                                   f"top@{x1:.1f}={top_hi:.2f}"))
    else:
        out.append(_c("B-fall", "B", "Screed fall (drawing)", "N/A",
                      evidence="no screed-to-falls in this detail"))
    # every drawing param must be covered by a check above; report coverage
    covered = {"step_height", "width", "fall_ratio"}
    for c in spec.components:
        for p in drawing_params(c.id):
            if p.name not in covered:
                out.append(_c(f"B-dim-{c.id}-{p.name}", "B",
                              f"Drawing param {c.id}.{p.name} built & checked",
                              "UNVERIFIED", expected=p.value,
                              evidence="no dimensional check implemented"))
    # assumption params are explicitly NOT verified
    n_assump = sum(1 for c in spec.components for p in c.params
                   if p.provenance == Provenance.ASSUMPTION
                   and p.role.value == "physical")
    out.append(_c("B-assumptions", "B",
                  f"{n_assump} assumption params excluded from verification",
                  "N/A", actual=n_assump,
                  evidence="assumptions never count as verified dims"))
    return out


def verify_program_dims(spec: DetailSpec, meshes: dict[str, Mesh]
                        ) -> list[CheckResult]:
    """Stage B/C for spec v2 (program-compiled) details: every box is built
    to its program extents, and every drawing value drives some geometry."""
    out: list[CheckResult] = []
    for c in spec.components:
        m = meshes.get(c.id)
        if not m or not m.positions:
            continue
        b = m.bounds()
        assert b is not None
        g = {p.name: p.value for p in c.params}
        gid = c.generator.id
        if gid == "box" and all(k in g for k in ("x0", "x1", "y0", "y1")):
            want = (g["x0"], g["y0"], g.get("z0", b[2]),
                    g["x1"], g["y1"], g.get("z1", b[5]))
        elif gid == "profile" and c.path:
            want = (min(p.x for p in c.path), min(p.y for p in c.path), g.get("z0", b[2]),
                    max(p.x for p in c.path), max(p.y for p in c.path), g.get("z1", b[5]))
        elif gid == "revolve" and c.path:
            r = max(p.x for p in c.path)
            want = (g["cx"] - r, min(p.y for p in c.path), g["cz"] - r,
                    g["cx"] + r, max(p.y for p in c.path), g["cz"] + r)
        elif gid.startswith("fixture_") or gid == "cad_mesh":
            env = (g["x0"], g["y0"], g["z0"], g["x1"], g["y1"], g["z1"])
            over = max([env[i] - b[i] for i in range(3)] + [b[i] - env[i] for i in range(3, 6)])
            out.append(_c(f"B-built-{c.id}", "B",
                          f"{c.id}: " + ("AI-modelled part" if gid == "cad_mesh"
                                         else f"{gid[8:]} fixture") + " stays inside its envelope",
                          "PASS" if over <= 0.01 else "FAIL",
                          expected=[round(v, 2) for v in env], actual=round(max(over, 0), 4),
                          tolerance=0.01, evidence="mm outside the program envelope"))
            if gid == "cad_mesh":
                info = (spec.context.get("cad_parts") or {}).get(c.id, {})
                out.append(_c(f"C-cad-{c.id}", "C",
                              f"{c.id}: AI-modelled geometry is illustrative only",
                              "N/A", actual=f"scale {info.get('scale', 1):.3g}",
                              evidence=f"CadQuery in a no-network sandbox; code sha "
                                       f"{str(info.get('code_sha', ''))[:12]}; never a "
                                       "verified dimension"))
            continue
        else:
            continue
        err = max(abs(a - w) for a, w in zip(b, want))
        out.append(_c(f"B-built-{c.id}", "B",
                      f"{c.id}: built extents match the program",
                      "PASS" if err <= 0.01 else "FAIL",
                      expected=[round(v, 2) for v in want],
                      actual=round(err, 4), tolerance=0.01,
                      evidence="max |mesh bound - program value| in mm"))
    for q in spec.context.get("drawing_params", []):
        used = q.get("used_by") or []
        out.append(_c(f"C-trace-{q['name']}", "C",
                      f"Drawing value {q['value']:g} {q['unit']} "
                      f"({q['name']}) drives geometry",
                      "PASS" if used else "UNVERIFIED",
                      expected=q.get("source_quote"),
                      actual=", ".join(used) or "not used by any part",
                      evidence=f"kind: {q.get('kind', 'exact')}"))
    n_assump = sum(1 for c in spec.components for p in c.params
                   if p.provenance == Provenance.ASSUMPTION)
    out.append(_c("B-assumptions", "B",
                  f"{n_assump} assumption values excluded from verification",
                  "N/A", actual=n_assump,
                  evidence="assumptions never count as verified dims"))
    return out


def verify_meshes(spec: DetailSpec, meshes: dict[str, Mesh]) -> list[CheckResult]:
    out: list[CheckResult] = []
    for c in spec.components:
        m = meshes.get(c.id)
        if c.representation == "annotation":
            out.append(_c(f"B-mesh-{c.id}", "B",
                          f"{c.id}: annotation has no geometry by design",
                          "PASS" if m is not None and m.tri_count == 0
                          else "FAIL",
                          expected=0, actual=m.tri_count if m else None))
        else:
            ok = m is not None and m.tri_count > 0
            out.append(_c(f"B-mesh-{c.id}", "B",
                          f"{c.id}: non-empty mesh generated",
                          "PASS" if ok else "FAIL",
                          expected="> 0 tris",
                          actual=m.tri_count if m else None))
    # intended contacts: bearing surface of `bottom` sampled under the
    # footprint of `top` (whole-mesh AABBs are meaningless for a sheet that
    # runs upstand-to-slab). The accepted gap is per pair: CONTACT_TOL_MM or
    # half the thinner part, whichever is less, so a thin layer cannot float
    # unseen. Pairs are declared per-detail in spec.context["contact_checks"].
    contacts = spec.context.get("contact_checks", [])
    for top, bottom, desc in contacts:
        gap = _contact_gap(meshes, top, bottom)
        if gap is None:
            out.append(_c(f"B-contact-{top}", "B", desc, "UNVERIFIED",
                          evidence="no bearing surface under footprint"))
            continue
        tol = _pair_tolerance(spec, meshes, top, bottom)
        ok = -tol <= gap <= tol
        out.append(_c(f"B-contact-{top}", "B", desc,
                      "PASS" if ok else "FAIL",
                      expected=f"|gap| <= {tol:g}mm",
                      actual=round(gap, 3), tolerance=tol,
                      evidence=f"min({CONTACT_TOL_MM:g}mm, half the thinner part)"))
    out.append(_c("B-interference", "B",
                  "No unintended volumetric interference (AABB-level v0)",
                  "UNVERIFIED",
                  evidence="full triangle-level interference deferred; "
                           "AABB overlaps exist by design at bonded faces"))
    return out


def verify_end_to_end(spec: DetailSpec, doc_sha256: str | None
                      ) -> list[CheckResult]:
    out: list[CheckResult] = []
    if doc_sha256 is None:
        out.append(_c("C-source", "C",
                      "Built from the uploaded source document", "UNVERIFIED",
                      expected=spec.source.sha256[:16] + "…", actual=None,
                      evidence="no source document linked to this build"))
    else:
        match = doc_sha256 == spec.source.sha256
        out.append(_c("C-source", "C",
                      "Built from the uploaded source document",
                      "PASS" if match else "FAIL",
                      expected=spec.source.sha256[:16] + "…",
                      actual=doc_sha256[:16] + "…"))
    n_quoted = sum(1 for c in spec.components if c.source_label)
    out.append(_c("C-labels", "C",
                  f"{n_quoted}/{len(spec.components)} components carry "
                  "exact drawing labels",
                  "PASS" if n_quoted else "FAIL", actual=n_quoted,
                  evidence="unlabeled components keep source_label=null"))
    n_null = sum(1 for c in spec.components
                 if c.product is not None and c.product.ref is None)
    out.append(_c("C-products", "C",
                  "Product assignments unresolved (honest nulls, no claims)",
                  "UNVERIFIED" if n_null else "PASS", actual=f"{n_null} null",
                  evidence="no manufacturer system selected"))
    out.append(_c("C-sequence", "C", "Install sequence acyclic & complete",
                  "PASS", actual=len(spec.install_order()),
                  evidence=" -> ".join(spec.install_order())))
    out.append(_c("C-waterproof", "C", "Watertight CAD != waterproof system",
                  "N/A",
                  evidence="no waterproof-performance claim is made"))
    return out


def verify_all(spec: DetailSpec, meshes: dict[str, Mesh],
               doc_sha256: str | None) -> list[CheckResult]:
    dims = (verify_program_dims if spec.context.get("program_version")
            else verify_dimensions)
    results = (verify_spec_structure(spec)
               + dims(spec, meshes)
               + verify_meshes(spec, meshes)
               + verify_end_to_end(spec, doc_sha256))
    spec.verification = results
    return results


def summarize(results: list[CheckResult]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    fails = [r.id for r in results if r.status == "FAIL"]
    return {"counts": counts, "failed": fails,
            "verdict": "FAIL" if fails else "PASS (with UNVERIFIED items)"
            if counts.get("UNVERIFIED") else "PASS"}
