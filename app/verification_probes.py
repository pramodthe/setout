"""Probe lines through built meshes: the order parts are met along an axis.

A relation with explicit interface faces says which side of the other each
part lies on along the relation's axis. Lines through the overlap of the two
parts' footprints must meet them in that order whatever their shapes, so a
swapped or misplaced layer fails even when some face of it still touches
something. Works on the flat triangles of `geometry.Mesh`; no mesh library.
"""
from __future__ import annotations

AXES = {"x": 0, "y": 1, "z": 2}
# Relation kinds that put two parts in contact across an interface.
CONTACT_KINDS = ("covers", "supported_by", "bonded_to", "sealed_to", "terminates_against", "laps_over")
# Sample positions across each side of an overlap, clear of its edges.
FRACTIONS = (0.15, 0.5, 0.85)


def line_hits(mesh, axis, points):
    """For each (u, v) in `points` (the two coordinates other than `axis`, in
    axis order): the sorted coordinates along `axis` where the line through it
    crosses the mesh's triangles. Triangles edge-on to the line never count."""
    i, j = [k for k in range(3) if k != axis]
    hits = [[] for _ in points]
    positions = mesh.positions
    for start in range(0, len(positions), 9):
        p0, p1, p2 = (positions[start + n:start + n + 3] for n in (0, 3, 6))
        d = (p1[i] - p0[i]) * (p2[j] - p0[j]) - (p2[i] - p0[i]) * (p1[j] - p0[j])
        if abs(d) < 1e-9:
            continue
        for n, (u, v) in enumerate(points):
            w1 = ((u - p0[i]) * (p2[j] - p0[j]) - (p2[i] - p0[i]) * (v - p0[j])) / d
            w2 = ((p1[i] - p0[i]) * (v - p0[j]) - (u - p0[i]) * (p1[j] - p0[j])) / d
            w0 = 1 - w1 - w2
            if min(w0, w1, w2) >= -1e-9:
                hits[n].append(w0 * p0[axis] + w1 * p1[axis] + w2 * p2[axis])
    return [sorted(h) for h in hits]


def probe_points(a, b, axis):
    """Sample points over the overlap of two bounding boxes projected along
    `axis`; empty when the footprints do not overlap."""
    i, j = [k for k in range(3) if k != axis]
    low = [max(a[k], b[k]) for k in (i, j)]
    high = [min(a[k + 3], b[k + 3]) for k in (i, j)]
    if high[0] - low[0] <= 1e-9 or high[1] - low[1] <= 1e-9:
        return []
    return [(low[0] + f * (high[0] - low[0]), low[1] + g * (high[1] - low[1]))
            for f in FRACTIONS for g in FRACTIONS]


def implied_order(relation):
    """(first, second, axis): the part a line along +axis meets first, then
    the other, as the relation's declared faces imply. None when the relation
    declares no axis, or faces that imply no order (two min or two max)."""
    settings = relation.constraints
    axis = AXES.get(settings.get("axis"))
    if axis is None or relation.kind not in CONTACT_KINDS:
        return None
    faces = (settings.get("source_face"), settings.get("target_face"))
    if faces == ("min", "max"):
        return relation.target, relation.source, axis
    if faces == ("max", "min"):
        return relation.source, relation.target, axis
    return None


def layer_order_probes(program, meshes):
    """One probe per ordered pair the relations imply: id, status, reason and
    the measurements, as plain dicts for `verification_v3.check`."""
    results, seen = [], set()
    # A placeholder's position is not evidence of what lies where.
    inferred = {part.id for part in program.parts if getattr(part, "provenance", "drawing") == "inferred"}
    for relation in program.relations:
        order = implied_order(relation)
        if order is None or {relation.source, relation.target} & inferred:
            continue
        first, second, axis = order
        ident = f"layer-order:{first}:{second}"
        if ident in seen:
            continue
        seen.add(ident)
        name = "xyz"[axis]
        values = dict(axis=name, order=[first, second])
        a, b = meshes.get(first), meshes.get(second)
        bounds = [m.bounds() if m else None for m in (a, b)]
        # A probe that cannot be cast is informational: the interface measurement
        # still decides contact. A probe that crosses both parts is required.
        if None in bounds:
            results.append(dict(id=ident, status="UNVERIFIED", reason="missing probe geometry", required=False, **values))
            continue
        points = probe_points(bounds[0], bounds[1], axis)
        probed = violations = 0
        example = None
        for point, before, after in zip(points, line_hits(a, axis, points), line_hits(b, axis, points)):
            if not before or not after:
                continue  # a hole, a slope or an edge: this line misses one part
            probed += 1
            if before[0] > after[0] + 1e-6 or before[-1] > after[-1] + 1e-6:
                violations += 1
                example = example or {"point": list(point), "first": [before[0], before[-1]],
                                      "second": [after[0], after[-1]]}
        values.update(probes=probed, violations=violations, example=example)
        if not probed:
            results.append(dict(id=ident, status="UNVERIFIED", required=False,
                                reason=f"no probe line along +{name} crosses both parts", **values))
        elif violations:
            results.append(dict(id=ident, status="FAIL", reason=f"{second} must lie beyond {first} along +{name}: "
                                f"{violations} of {probed} probe lines meet them in the other order", **values))
        else:
            results.append(dict(id=ident, status="PASS",
                                reason=f"probe lines along +{name} meet {first} before {second}", **values))
    return results
