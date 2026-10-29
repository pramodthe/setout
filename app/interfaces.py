"""Exact planar triangle interfaces for trusted linear primitive meshes."""
from math import isfinite


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


def surfaces(mesh, axis, side, extremal=False):
    projection = [i for i in range(3) if i != axis]
    triangles = []
    bounds = mesh.bounds()
    extreme = bounds[axis+(3 if side == "max" else 0)] if bounds else None
    for offset in range(0,len(mesh.positions),9):
        points = [mesh.positions[offset+i:offset+i+3] for i in (0,3,6)]
        if extremal and not all(abs(point[axis]-extreme) <= .0001 for point in points):
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
    if len(triangles) > (8192 if extremal else 256):
        raise ValueError("interface is too complex for the planar primitive verifier")
    if extremal:
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


def measure_interface(source, target, axis, source_side, target_side, covers=True, source_extremal=False, target_extremal=False):
    a,b = surfaces(source,axis,source_side,source_extremal),surfaces(target,axis,target_side,target_extremal)
    total = sum(area(uv) for uv,_ in (b if covers else a))
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
        return {"coverage":0., "min_gap":None,"max_gap":None}
    return {"coverage":min(1.,covered/total),"min_gap":min(gaps),"max_gap":max(gaps)}


def matches_brep_planes(mesh, axis, side, planes, extremal=False):
    projection = [i for i in range(3) if i != axis]
    selected = surfaces(mesh,axis,side,extremal)
    for uv,plane in selected:
        points = []
        for point in uv:
            xyz = [0.,0.,0.]
            xyz[axis] = plane(point)
            for i,j in enumerate(projection):
                xyz[j] = point[i]
            points.append(xyz)
        if not any(all(abs(sum(normal[i]*(point[i]-center[i]) for i in range(3))) <= .001
                           for point in points) for normal,center in planes):
            return False
    return bool(selected)
