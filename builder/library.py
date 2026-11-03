"""The owner's standard parts: screws, ties, nuts, washers, drains, grates and the like, read from their own SketchUp models. Muse builds the layers of a detail itself, which it does
well, and places these where a drawing has them, which is where it was slowest and weakest. A part is only ever placed whole: never edited, never scaled out of shape.

    python -m builder.library ~/Desktop/3d_data      # reads every model once, into var/library

The models are the owner's own work, and the library stays out of the repo (var/ is gitignored).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import numpy as np

STORE = Path(os.environ.get("SETOUT_LIBRARY") or Path(os.environ.get("SETOUT_VAR") or "var") / "library")
LONGEST = 400.0  # mm: a standard part is small. A wall or a slab is Muse's to build
KINDS = ("screw", "bolt", "nut", "washer", "rod", "anchor", "tie", "nail", "rivet", "fixing", "fastener", "clip", "bracket", "angle", "plate", "weep", "vent", "drain", "grate",
         "gully", "outlet", "spout", "gasket", "caulk", "sealant", "backer", "rod", "spacer", "shim", "packer", "dowel", "pin", "hinge", "handle", "stud", "insert", "channel")
NOT_PARTS = re.compile(r"^(skp_camera|image_|component_\d|group_\d|instance_\d|s_\d|id\d)", re.I)


def base_name(raw: str) -> str:
    """SketchUp's name for a part, without the copy number it adds: HVY_HX_JM_WSH_NUT_1_000-12_UNF_SIMP_3 is a heavy hex jam nut, washer face."""
    name = re.sub(r"[_\s]+\d+$", "", raw).replace("_", " ").strip()
    return re.sub(r"\s+", " ", name).lower()


def kind_of(name: str) -> str | None:
    words = re.split(r"[^a-z]+", name)
    return next((k for k in KINDS if any(w == k or w.startswith(k) or w.rstrip("s") == k for w in words)), None)


def triangles(node) -> np.ndarray:
    """Every triangle under a node, in that node's own frame, as (n, 3, 3)."""
    found = []
    for bound in node.objects("geometry"):
        for primitive in bound.primitives():
            if hasattr(primitive, "triangleset"):
                primitive = primitive.triangleset()
            index = getattr(primitive, "vertex_index", None)
            if getattr(primitive, "vertex", None) is not None and index is not None and getattr(index, "ndim", 0) == 2 and index.shape[1] == 3 and len(index):  # triangles, not lines
                found.append(primitive.vertex[index])
    return np.concatenate(found) if found else np.zeros((0, 3, 3))


def walk(node, out, scale):
    from collada import scene
    if isinstance(node, scene.NodeNode):  # a use of a SketchUp component: its definition carries the name
        node = node.node
    if not isinstance(node, scene.Node):
        return
    raw = node.xmlnode.get("name") or ""
    name = base_name(raw)
    kind = kind_of(name) if raw and not NOT_PARTS.match(raw) else None
    if kind:
        tris = triangles(node) * scale
        if len(tris):
            size = np.ptp(tris.reshape(-1, 3), axis=0)
            if 0 < size.max() <= LONGEST:
                out.append((name, kind, tris))
                return  # a part is whole: its pieces inside aren't parts of their own
    for child in node.children:
        walk(child, out, scale)


def read(path: Path) -> list:
    import collada
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            inner = next((n for n in z.namelist() if n.lower().endswith(".dae")), None)
            if not inner:
                return []
            data = z.read(inner)
    else:
        data = path.read_bytes()
    model = collada.Collada(io.BytesIO(data), ignore=[collada.common.DaeUnsupportedError, collada.common.DaeBrokenRefError])
    scale = float(model.assetInfo.unitmeter or 1.0) * 1000.0  # to mm
    out = []
    if model.scene:
        for node in model.scene.nodes:
            walk(node, out, scale)
    return out


def standard(tris):
    """A part in its own frame: its longest side along z, centred across, its base at z = 0. Placing it then only needs a point and a direction."""
    points = tris.reshape(-1, 3)
    centre = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - centre, full_matrices=False)
    frame = axes  # rows: the longest direction first
    local = (points - centre) @ frame.T
    local = local[:, [1, 2, 0]]  # longest to z
    if np.linalg.det(frame[[1, 2, 0]]) < 0:
        local[:, 0] *= -1
    local[:, :2] -= (local[:, :2].max(axis=0) + local[:, :2].min(axis=0)) / 2
    local[:, 2] -= local[:, 2].min()
    return local.reshape(-1, 3, 3)


def build(source: Path, store: Path = STORE) -> dict:
    """Reads every model, keeps one of each part, and writes the index and the meshes."""
    store.mkdir(parents=True, exist_ok=True)
    parts: dict = {}
    for path in sorted(source.iterdir()):
        if path.suffix.lower() not in (".zip", ".dae") or path.name.startswith("3d_Dataset"):
            continue
        try:
            found = read(path)
        except Exception as error:  # noqa: BLE001 - one unreadable model shouldn't stop the rest
            print("skipped", path.name, type(error).__name__, str(error)[:80])
            continue
        for name, kind, tris in found:
            local = standard(tris)
            size = np.ptp(local.reshape(-1, 3), axis=0)
            key = f"{name}|{len(local)}|{'x'.join(str(round(v)) for v in sorted(size))}"
            if key in parts:
                parts[key]["copies"] += 1
                parts[key]["models"].add(path.stem)
                continue
            ident = f"p{len(parts) + 1:04d}"
            np.save(store / f"{ident}.npy", local.astype(np.float32))
            parts[key] = {"id": ident, "name": name, "kind": kind, "size": [round(float(v), 1) for v in size], "triangles": len(local), "copies": 1, "models": {path.stem}}
    index = sorted(({**p, "models": sorted(p["models"])} for p in parts.values()), key=lambda p: (p["kind"], p["name"]))
    (store / "index.json").write_text(json.dumps(index, indent=1))
    return {"parts": len(index), "kinds": sorted({p["kind"] for p in index})}


def index(store: Path = STORE) -> list:
    path = store / "index.json"
    return json.loads(path.read_text()) if path.exists() else []


def meshes(ids, store: Path = STORE) -> dict:
    """{id: triangles as a list}, for the parts a scene uses, to send to the sandbox."""
    return {i: np.load(store / f"{i}.npy").round(2).tolist() for i in sorted(set(ids)) if (store / f"{i}.npy").exists()}


if __name__ == "__main__":
    print(build(Path(sys.argv[1]).expanduser()))
