"""Exact planar triangle interfaces for trusted linear primitive meshes."""
from math import isfinite

MESH_SAG = 0.05  # how far inside a curve a CAD mesh's chords can lie: its export tolerance (cad_components)


def cross(a,b,c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def area(polygon):
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(polygon,polygon[1:]+polygon[:1]))) / 2


def clip(subject, boundary):
    if cross(*boundary) < 0:
        boundary = list(reversed(boundary))
    result = subject
    for a,b in zip(boundary,boundary[1:]+boundary[:1]):
        points, result = result, []
        if not points:
            break
        previous = points[-1]
        for current in points:
            before, after = cross(a,b,previous),cross(a,b,current)
            if (before >= -1e-9) != (after >= -1e-9):
                fraction = before / (before-after)
                result.append([previous[i]+fraction*(current[i]-previous[i]) for i in range(2)])
            if after >= -1e-9:
                result.append(current)
            previous = current
    return result


def surfaces(mesh, axis, side, level=None, band=.0001):
    """The triangles of a part's face along `axis`: what shows from that side, or
    only those within `band` of the plane at `level`."""
    projection = [i for i in range(3) if i != axis]
    triangles = []
    for offset in range(0,len(mesh.positions),9):
        points = [mesh.positions[offset+i:offset+i+3] for i in (0,3,6)]
        if level is not None and not all(abs(point[axis]-level) <= band for point in points):
            continue
        uv = [[p[i] for i in projection] for p in points]
        denominator = cross(*uv)
        if abs(denominator) < 1e-8:
            continue
        def plane(point, uv=uv, points=points, denominator=denominator):
            weights = [cross(uv[1],uv[2],point)/denominator,
                       cross(uv[2],uv[0],point)/denominator,
                       cross(uv[0],uv[1],point)/denominator]
            return sum(weight*p[axis] for weight,p in zip(weights,points))
        triangles.append((uv,plane))
    if len(triangles) > (256 if level is None else 8192):
        raise ValueError("interface is too complex for the planar primitive verifier")
    if level is not None:
        return triangles
    selected = []
    for uv,plane in triangles:
        center = [sum(p[i] for p in uv)/3 for i in range(2)]
        # Test both windings for point containment.
        hits = [other_plane(center) for other,other_plane in triangles if
                all(cross(other[i],other[(i+1)%3],center) >= -1e-7 for i in range(3)) or
                all(cross(other[i],other[(i+1)%3],center) <= 1e-7 for i in range(3))]
        extreme = (max(hits) if side == "max" else min(hits)) if hits else plane(center)
        if abs(plane(center)-extreme) <= 1e-6:
            selected.append((uv,plane))
    return selected


def measure_interface(source, target, axis, source_side, target_side, covers=True, source_level=None, target_level=None,
                      source_band=None, target_band=None):
    """How two faces meet: `coverage` of the target's face (covers) or the source's,
    `contact` of the smaller face whichever way round they are named, `overlap` in
    mm2, and the gap band. Which face is smaller does not depend on the relation.

    A band says a side is a curve, whose face is the strip within that distance of its
    extreme. A curve leaves the other face by shape, so its gap is the closest it comes,
    less what its mesh's chords may have taken off."""
    a = surfaces(source,axis,source_side,source_level,source_band or .0001)
    b = surfaces(target,axis,target_side,target_level,target_band or .0001)
    source_area,target_area = sum(area(uv) for uv,_ in a),sum(area(uv) for uv,_ in b)
    total = target_area if covers else source_area
    covered, gaps = 0.,[]
    direction = 1 if source_side == "min" else -1
    for uv,plane in a:
        for other,other_plane in b:
            overlap = clip(uv,other)
            size = area(overlap)
            if size <= 1e-8:
                continue
            covered += size
            gaps += [(plane(point)-other_plane(point))*direction for point in overlap]
    if not total or not gaps or not all(isfinite(value) for value in gaps):
        return {"coverage":0., "contact":0., "overlap":0., "min_gap":None,"max_gap":None}
    low,high = min(gaps),max(gaps)
    if source_band or target_band:
        low = high = low if low <= 0 else max(0.,low-MESH_SAG)
    return {"coverage":min(1.,covered/total),"contact":min(1.,covered/min(source_area,target_area)),"overlap":covered,
            "min_gap":low,"max_gap":high}
