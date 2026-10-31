"""Parametric components: our own geometry, made from sizes, never copied from any model.

Each returns triangles in the assembly's frame. Proportions are ordinary shapes of common construction,
and a component's real size always comes from the assembly it is placed in.
"""
from __future__ import annotations

import numpy as np

from .compose import _block

CORE_WALL = 0.16  # shell of a hollow block, as a share of its width
SIDES = 10


def hollow_block(x0, x1, y0, y1, z0, z1) -> np.ndarray:
    """A block with two cores running up through it: two face shells and three webs."""
    shell = (x1 - x0) * CORE_WALL
    web = min(shell, (y1 - y0) * 0.1)
    middle = (y0 + y1) / 2
    return np.vstack([_block(x0, x0 + shell, y0, y1, z0, z1), _block(x1 - shell, x1, y0, y1, z0, z1),
                      _block(x0 + shell, x1 - shell, y0, y0 + web, z0, z1),
                      _block(x0 + shell, x1 - shell, y1 - web, y1, z0, z1),
                      _block(x0 + shell, x1 - shell, middle - web / 2, middle + web / 2, z0, z1)])


def prism(start, stop, radius, sides=SIDES) -> np.ndarray:
    """A round bar, or with six sides a nut, from `start` to `stop`, with its ends closed."""
    start, stop = np.asarray(start, float), np.asarray(stop, float)
    axis = stop - start
    length = np.linalg.norm(axis)
    if length < 1e-9:
        return np.zeros((0, 3, 3))
    axis = axis / length
    helper = np.array([0, 0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0, 0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angles = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring = np.cos(angles)[:, None] * u + np.sin(angles)[:, None] * v
    low, high = start + ring * radius, stop + ring * radius
    next_ = np.roll(np.arange(sides), -1)
    walls = np.concatenate([np.stack([low, low[next_], high[next_]], 1), np.stack([low, high[next_], high], 1)])
    caps = np.concatenate([np.stack([np.tile(start, (sides, 1)), low[next_], low], 1),
                           np.stack([np.tile(stop, (sides, 1)), high, high[next_]], 1)])
    return np.concatenate([walls, caps])


def bolt(start, stop, radius) -> np.ndarray:
    """A threaded rod with a washer and nut at each end."""
    start, stop = np.asarray(start, float), np.asarray(stop, float)
    along = (stop - start) / max(np.linalg.norm(stop - start), 1e-9)
    nut = radius * 2.0
    washer = radius * 2.6
    parts = [prism(start, stop, radius)]
    for end, sign in ((start, 1), (stop, -1)):
        parts.append(prism(end, end + along * sign * radius * 0.5, washer))
        parts.append(prism(end + along * sign * radius * 0.5, end + along * sign * radius * 1.7, nut, sides=6))
    return np.concatenate(parts)


def ladder_wire(x0, x1, y0, y1, z, radius) -> np.ndarray:
    """Joint reinforcement: two rails along the run, joined by rungs."""
    rails = [prism((x, y0, z), (x, y1, z), radius, 6) for x in (x0, x1)]
    pitch = max((x1 - x0) * 1.6, radius * 8)
    rungs = [prism((x0, y, z), (x1, y, z), radius * 0.8, 6) for y in np.arange(y0 + pitch / 2, y1, pitch)]
    return np.concatenate(rails + rungs)


def wall_tie(x0, x1, y, z, radius) -> np.ndarray:
    """A bent wire crossing a cavity, turned down at each end to key into the bed."""
    drop = (x1 - x0) * 0.16
    return np.concatenate([prism((x0, y, z - drop), (x0, y, z), radius, 6), prism((x0, y, z), (x1, y, z), radius, 6),
                           prism((x1, y, z), (x1, y, z + drop), radius, 6)])
