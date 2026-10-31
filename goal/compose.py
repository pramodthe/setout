"""Build a 3D assembly from a section's regions, once each region's kind is known.

A section shows one slice, so what each part does along the depth is the understanding a drawing
leaves out. Continuous parts run the full depth, units repeat with joints, and spaced parts recur.
Sizes here are rough shapes of common construction, ratios only, and never claims about a project.
"""
from __future__ import annotations

import numpy as np

# Unit length along the depth, as a multiple of its height in the section.
UNIT_LENGTH = {"brick": 3.4, "block": 2.05, "stone": 3.0, "tile": 1.0, "board": 12.0}
JOINT = 0.17  # head joint, as a share of unit height
UNIT_FAMILIES = tuple(UNIT_LENGTH)
SPACED_FAMILIES = ("fixing", "reinforcement", "weep")
DEPTH_SHARE = 0.8  # depth of the assembly, as a share of the section's longest side


def _block(x0, x1, y0, y1, z0, z1):
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    return v[np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                       (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)])]


def _extrude(region, height, y0, y1) -> np.ndarray:
    return np.vstack([_block(x0, x1, y0, y1, height - b, height - a) for x0, x1, a, b in region.bands()])


def build(regions, labels, size) -> list[dict]:
    """Pieces in picture units: x across, z up, y the depth. `labels` maps region id to
    {"family", "depth"}, depth being continuous, units, spaced or empty."""
    width, height = size
    depth = DEPTH_SHARE * max(width, height)
    pieces = []
    spacing = depth / 3
    rows = sorted({r.box[1] for r in regions})
    for region in regions:
        label = labels.get(region.id) or {"family": "unknown", "depth": "continuous"}
        family, how = label["family"], label["depth"]
        if how == "empty":
            continue
        if how == "units" and family in UNIT_FAMILIES:
            unit = UNIT_LENGTH[family] * region.height
            joint = JOINT * region.height
            course = rows.index(region.box[1])
            start = -(unit + joint) / 2 if course % 2 else 0.0
            y = start
            while y < depth:
                a, b = max(y, 0), min(y + unit, depth)
                if b > a:
                    pieces.append({"triangles": _extrude(region, height, a, b), "family": family})
                    if b + joint < depth:
                        pieces.append({"triangles": _extrude(region, height, b, b + joint), "family": "mortar"})
                y += unit + joint
        elif how == "spaced" or family in SPACED_FAMILIES:
            length = max(region.width, region.height) * 1.0
            for y in np.arange(spacing / 2, depth, spacing):
                pieces.append({"triangles": _extrude(region, height, y - length / 2, y + length / 2), "family": family})
        else:
            pieces.append({"triangles": _extrude(region, height, 0, depth), "family": family})
    return pieces
