"""What a model's geometry says, with no opinion in it: which parts float, which pass through each other, and whether the relations read from the source hold. Only touching, order and overlap are
tested, never a size, because sizes are assumptions. It travels with each call and runs in the sandbox, since long work on the server starves the job heartbeat.
"""
from __future__ import annotations

import numpy as np
import trimesh

TOUCH, NEAR = 4.0, 40.0  # mm: parts this close touch, as Muse leaves a hair between layers, and a lap is judged on the parts of a sheet this close to the other
SHARED, SHARE = 1.0, 0.05  # mm³ that count as overlapping, and the share of the smaller part that is worth saying
SAMPLES, OUTSIDE, LAYERS = 3000, 0.6, 2


def solid(vertices, triangles) -> trimesh.Trimesh:
    mesh = trimesh.Trimesh(np.asarray(vertices, dtype=float), np.asarray(triangles, dtype=int), process=True)
    mesh.fix_normals()
    return mesh


def within(a, b, margin) -> bool:
    return bool(np.all(a.bounds[0] - margin <= b.bounds[1]) and np.all(b.bounds[0] - margin <= a.bounds[1]))


def sample(a):
    return trimesh.sample.sample_surface(a, SAMPLES, seed=0)[:2]


def gap(a, b) -> float:
    return min(float(trimesh.proximity.closest_point(q, np.vstack([p.vertices, sample(p)[0]]))[1].min()) for p, q in ((a, b), (b, a)))


def shared(a, b):
    """The volume two parts share. None when one of them isn't closed, as nothing can be said."""
    if not (a.is_watertight and b.is_watertight):
        return None
    try:
        return float(abs(trimesh.boolean.intersection([a, b], engine="manifold").volume))
    except (ValueError, RuntimeError):
        return None


def facing(a, b):
    """The normals of the parts of a that lie flat against b, with the point each is at. The edge of a side face also comes close to b, but it doesn't bear on it. A mesh that isn't closed can have its normals
    inside out, so only that the two faces are parallel is asked of them, and never which way they point."""
    if not within(a, b, TOUCH):
        return np.zeros((0, 3)), np.zeros((0, 3))
    points, faces = sample(a)
    _, distance, triangles = trimesh.proximity.closest_point(b, points)
    normals = a.face_normals[faces]
    flat = (distance <= TOUCH) & (np.abs(np.einsum("ij,ij->i", normals, b.face_normals[triangles])) > 0.5)
    return normals[flat], points[flat]


def rests(a, b) -> bool:
    """Flat on b, with the middle of a above where they meet."""
    normals, points = facing(a, b)
    level = points[np.abs(normals[:, 2]) > 0.5]
    return len(level) >= 3 and float(a.bounds.mean(axis=0)[2]) > float(np.median(level[:, 2]))


def beside(a, b) -> bool:
    return int((np.abs(facing(a, b)[0][:, 2]) < 0.5).sum()) >= 3


def turns(a, b) -> bool:
    normals = facing(a, b)[0]
    return sum(int((normals[:, k] > 0.5).sum()) >= 3 for k in range(3)) + sum(int((normals[:, k] < -0.5).sum()) >= 3 for k in range(3)) >= 2


def outside(a, b):
    """Whether a lies on the outside of b where they come close. None when they don't."""
    if not within(a, b, NEAR):
        return None
    points = sample(a)[0]
    closest, distance, triangles = trimesh.proximity.closest_point(b, points)
    close = (distance <= NEAR) & (distance > 0.05)
    if close.sum() < 5:
        return None
    outward = b.face_normals[triangles[close]]
    if not b.is_watertight:  # its normals can't be trusted, so outward is away from its middle
        outward = outward * np.sign(np.einsum("ij,ij->i", outward, closest[close] - b.bounds.mean(axis=0)))[:, None]
    return bool((np.einsum("ij,ij->i", points[close] - closest[close], outward) > 0).mean() >= OUTSIDE)


def whole(group):
    return np.min([m.bounds[0] for m in group], axis=0), np.max([m.bounds[1] for m in group], axis=0)


def crosses(group_a, group_b) -> bool:
    """Whether a reaches into the whole of b, as a pipe does through a slab that was cut into pieces around it."""
    (low_a, high_a), (low_b, high_b) = whole(group_a), whole(group_b)
    return bool(np.all(low_a + TOUCH < high_b) and np.all(low_b + TOUCH < high_a))


def stacked(left, right, others) -> bool:
    """Whether a sits on b, directly or on layers that sit on b."""
    reached, rising = {id(b) for b in right}, list(right)
    for _ in range(LAYERS + 1):
        rising = [c for c in others if id(c) not in reached and any(within(c, b, TOUCH) and rests(c, b) for b in rising)]
        reached |= {id(c) for c in rising}
        if any(id(a) in reached for a in left):
            return True
    return False


def holds(relation, left, right, others=()) -> bool:
    """A part can be several objects, as a pipe and its flange. A relation holds if any pair shows it, but apart needs them all, and a cover needs every object that comes near, so one good sheet can't answer for a bad one."""
    pairs = [(a, b) for a in left for b in right]
    if relation == "left_of":  # +y is on the viewer's left. Parts that share some of their width can't be called the wrong way round
        return bool(whole(left)[1][1] > whole(right)[0][1])
    if relation == "apart":
        return all(not within(a, b, TOUCH) or (not (shared(a, b) or 0) > SHARED and gap(a, b) > TOUCH) for a, b in pairs)
    if relation == "through":
        return crosses(left, right) or any(within(a, b, 0) and (shared(a, b) or 0) > SHARED for a, b in pairs)
    if relation == "against":
        return any(beside(a, b) for a, b in pairs)
    if relation == "wraps":
        return any(turns(a, b) for a, b in pairs)
    if relation == "on":
        return stacked(left, right, [*left, *others])
    if relation == "covers":
        votes = [[v for v in (outside(a, b) for b in right) if v is not None] for a in left]
        return any(votes) and all(any(v) for v in votes if v)
    raise ValueError(relation)


def islands(names, links) -> list:
    parent = {n: n for n in names}

    def root(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for a, b in links:
        parent[root(a)] = root(b)
    groups: dict = {}
    for n in names:
        groups.setdefault(root(n), []).append(n)
    return sorted(groups.values(), key=len, reverse=True)


def measure(solids, relations=None) -> dict:
    """`solids` is {name: (vertices, triangles)} in mm. `relations` is {"checks": [{a, relation, b}]}, with a and b lists of objects."""
    meshes = {name: solid(*pair) for name, pair in solids.items() if len(pair[1])}
    names, overlaps, links = list(meshes), [], []
    for k, first in enumerate(names):
        for second in names[k + 1:]:
            a, b = meshes[first], meshes[second]
            if not within(a, b, TOUCH):
                continue
            volume = shared(a, b)
            if volume is not None and volume > SHARED:
                links.append((first, second))
                share = volume / max(min(abs(a.volume), abs(b.volume)), 1e-9)
                if share >= SHARE:
                    overlaps.append({"a": first, "b": second, "mm3": round(volume), "share": round(float(min(share, 1.0)), 2)})
            elif gap(a, b) <= TOUCH:
                links.append((first, second))
    found = {"loose": islands(names, links)[1:], "overlaps": sorted(overlaps, key=lambda o: -o["share"])}
    if relations:
        answers = []
        for check in relations["checks"]:
            left, right = [meshes[n] for n in check["a"] if n in meshes], [meshes[n] for n in check["b"] if n in meshes]
            answers.append(holds(check["relation"], left, right, list(meshes.values())) if left and right else None)
        found["relations"] = answers
    return found
