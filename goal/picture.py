"""Draw assemblies side by side, to look at them. A score can pass something a person wouldn't."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

COLOURS = {"brick": (190, 85, 65), "block": (150, 152, 170), "mortar": (226, 216, 180), "concrete": (130, 130, 130),
           "insulation": (238, 190, 220), "flashing": (40, 120, 220), "metal": (70, 170, 215), "plaster": (165, 225, 165),
           "fixing": (35, 35, 35), "unknown": (205, 205, 205), "glass": (170, 235, 250), "timber": (190, 140, 80),
           "board": (220, 185, 125), "reinforcement": (250, 120, 0), "weep": (250, 0, 190), "drainage": (0, 190, 120),
           "tile": (120, 200, 130), "stone": (180, 170, 150), "sealant": (100, 50, 150), "thinset": (210, 210, 200)}


def view(pieces, size=760, azimuth=35.0, elevation=22.0):
    """Orthographic, far faces first."""
    triangles = np.vstack([p["triangles"] for p in pieces])
    families = [p["family"] for p in pieces for _ in range(len(p["triangles"]))]
    a, e = np.radians(azimuth), np.radians(elevation)
    spin = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    tilt = np.array([[1, 0, 0], [0, np.cos(e), -np.sin(e)], [0, np.sin(e), np.cos(e)]])
    turned = (tilt @ spin @ triangles.reshape(-1, 3).T).T.reshape(-1, 3, 3)
    flat = turned[:, :, [0, 2]]
    low, high = flat.reshape(-1, 2).min(0), flat.reshape(-1, 2).max(0)
    scale = (size - 20) / max(high - low)
    points = (flat - low) * scale + 10
    points[:, :, 1] = size - points[:, :, 1]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12
    light = 0.55 + 0.45 * np.abs(normals @ np.array([0.35, -0.55, 0.76]))
    image = Image.new("RGB", (size, size), "white")
    pen = ImageDraw.Draw(image)
    for k in np.argsort(turned[:, :, 1].mean(1))[::-1]:
        colour = np.array(COLOURS.get(families[k], (205, 205, 205))) * light[k]
        pen.polygon([tuple(q) for q in points[k]], fill=tuple(colour.astype(int)))
    return image


def side_by_side(left, right, path, **options):
    a, b = view(left, **options), view(right, **options)
    sheet = Image.new("RGB", (a.width + b.width + 12, a.height), "white")
    sheet.paste(a, (0, 0))
    sheet.paste(b, (a.width + 12, 0))
    sheet.save(path)
