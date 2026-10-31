"""Reference 3D models as a parts database, read as data. Nothing in a model file is run.

A model is a COLLADA scene: a tree of nodes with transforms and meshes. Each node holding a mesh
is a piece. Output goes under gitignored data/reference-3d/db: these models are third-party, so
neither they nor anything cut from them is ever committed.

    python -m goal.reference        # every model under data/reference-3d/raw
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
# A worktree has no data/ of its own: everything lives in the main checkout.
DATA = next(p / "data" for p in (ROOT, *ROOT.parents) if (p / "data" / "reference-3d").is_dir())
RAW, DB = DATA / "reference-3d" / "raw", DATA / "reference-3d" / "db"
GENERIC = ("group_", "instance_", "sketchup", "skp", "id", "node", "component_", "s_", "ls_", "lh_", "image_")
SHAPE_DECIMALS = 1  # model units (inches): two pieces this alike count as the same shape


def read_model(path) -> tuple[list[dict], np.ndarray, float]:
    """(pieces, triangles in metres shaped n×3×3, metres per unit). A piece's `tris` is a
    [start, stop) range into the triangles, and its `path` the node names above it."""
    root = ET.parse(path).getroot()
    ns = root.tag.split("}")[0] + "}"
    q = lambda tag: ns + tag  # noqa: E731
    asset = root.find(q("asset"))
    metres = next((float(u.get("meter", 1)) for u in asset.iter(q("unit"))), 1.0) if asset is not None else 1.0
    z_up = next((e.text == "Z_UP" for e in asset.iter(q("up_axis"))), True) if asset is not None else True
    materials = {m.get("id"): m.get("name") or m.get("id") for m in root.iter(q("material"))}
    meshes = {g.get("id"): _mesh(g.find(q("mesh")), q) for g in root.iter(q("geometry")) if g.find(q("mesh")) is not None}
    library = {n.get("id"): n for n in root.iter(q("node")) if n.get("id")}
    pieces, chunks, count = [], [], 0
    visits = 0

    def walk(node, matrix, trail, depth):
        nonlocal count, visits
        visits += 1
        visit = visits  # a shared definition is walked once per use, so this tells its uses apart
        block = node.find(q("matrix"))
        if block is not None and block.text:
            matrix = matrix @ np.array(block.text.split(), float).reshape(4, 4)
        here = [*trail, node.get("name") or ""]
        for instance in node.findall(q("instance_geometry")):
            bound = {b.get("symbol"): b.get("target", "#")[1:] for b in instance.iter(q("instance_material"))}
            parts = meshes.get(instance.get("url", "#")[1:], [])
            start, used = count, {}
            for symbol, triangles in parts:
                world = triangles @ matrix[:3, :3].T + matrix[:3, 3]
                chunks.append(world)
                count += len(world)
                name = materials.get(bound.get(symbol), symbol or "")
                used[name] = used.get(name, 0) + len(world)
            if count > start:
                pieces.append({"path": [n for n in here if n], "node": visit, "tris": [start, count], "materials": used})
        if depth < 60:
            for child in node.findall(q("node")):
                walk(child, matrix, here, depth + 1)
            for instance in node.findall(q("instance_node")):
                target = library.get(instance.get("url", "#")[1:])
                if target is not None:
                    walk(target, matrix, here, depth + 1)

    for scene in root.iter(q("visual_scene")):
        for node in scene.findall(q("node")):
            walk(node, np.eye(4), [], 0)
    triangles = np.vstack(chunks) * metres if chunks else np.zeros((0, 3, 3))
    if not z_up:
        triangles = triangles[..., [0, 2, 1]]
    return pieces, triangles, metres


def _mesh(mesh, q) -> list:
    sources = {}
    for source in mesh.findall(q("source")):
        values = source.find(q("float_array"))
        if values is not None and values.text:
            sources[source.get("id")] = np.array(values.text.split(), float)
    vertices = {}
    for vertex in mesh.findall(q("vertices")):
        for item in vertex.findall(q("input")):
            if item.get("semantic") == "POSITION":
                vertices[vertex.get("id")] = item.get("source")[1:]
    out = []
    for group in mesh.findall(q("triangles")):
        inputs = group.findall(q("input"))
        block = group.find(q("p"))
        if not inputs or block is None or not block.text:
            continue
        stride = max(int(i.get("offset")) for i in inputs) + 1
        vertex = next(i for i in inputs if i.get("semantic") == "VERTEX")
        positions = sources.get(vertices.get(vertex.get("source")[1:]))
        if positions is None:
            continue
        index = np.array(block.text.split(), int)[int(vertex.get("offset"))::stride].reshape(-1, 3)
        out.append((group.get("material"), positions.reshape(-1, 3)[index]))
    return out


def extents(triangles) -> tuple[list, list]:
    """(world box, sizes along the piece's own principal axes, largest first). Sizes don't change
    when a piece is turned or moved, so two copies read the same wherever they sit."""
    points = np.unique(triangles.reshape(-1, 3).round(6), axis=0)
    low, high = points.min(0), points.max(0)
    centred = points - points.mean(0)
    if len(points) < 4:
        return [*low, *high], sorted((high - low).tolist(), reverse=True)
    _, _, axes = np.linalg.svd(centred, full_matrices=False)
    spans = np.ptp(centred @ axes.T, axis=0)
    return [*low, *high], sorted(spans.tolist(), reverse=True)


def shape_key(sizes, triangle_count, metres) -> str:
    rounded = [round(s / metres, SHAPE_DECIMALS) for s in sizes] + [triangle_count]
    return hashlib.sha1(json.dumps(rounded).encode()).hexdigest()[:12]


def real_name(path) -> str | None:
    """The nearest name a person gave: most nodes are numbered by the tool."""
    for name in reversed(path):
        if not name.lower().startswith(GENERIC):
            return name
    return None


def ingest(folder) -> dict:
    """Read one model folder into the database. Returns its index row."""
    path = next(folder.rglob("*.dae"))
    pieces, triangles, metres = read_model(path)
    for piece in pieces:
        piece["box"], piece["sizes"] = extents(triangles[piece["tris"][0]:piece["tris"][1]])
        piece["shape"] = shape_key(piece["sizes"], piece["tris"][1] - piece["tris"][0], metres)
        piece["name"] = real_name(piece["path"])
    np.savez_compressed(DB / f"{folder.name}.npz", triangles=triangles.astype(np.float32))
    (DB / f"{folder.name}.json").write_text(json.dumps({"name": folder.name, "metres_per_unit": metres, "pieces": pieces}))
    shapes = {}
    for piece in pieces:
        shapes[piece["shape"]] = shapes.get(piece["shape"], 0) + 1
    box = np.array([p["box"] for p in pieces]) if pieces else np.zeros((1, 6))
    return {"name": folder.name, "pieces": len(pieces), "triangles": len(triangles),
            "unique_shapes": len(shapes), "most_repeated": max(shapes.values(), default=0),
            "size_m": (box[:, 3:].max(0) - box[:, :3].min(0)).round(2).tolist(),
            "named_share": round(sum(p["name"] is not None for p in pieces) / max(len(pieces), 1), 2)}


def build() -> list[dict]:
    DB.mkdir(parents=True, exist_ok=True)
    rows = [ingest(folder) for folder in sorted(p for p in RAW.iterdir() if p.is_dir())]
    (DB / "index.json").write_text(json.dumps(rows, indent=1))
    return rows


def take_in(path) -> dict | None:
    """Copy a new .dae, or the .dae inside a .zip, into RAW and ingest it, once. Only the model is taken:
    a zip's other files are left where they are. None if it is already in."""
    name = path.stem
    if (DB / f"{name}.json").exists():
        return None
    folder = RAW / name
    folder.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(path):  # by content: some .dae files are zips
        with zipfile.ZipFile(path) as archive:
            member = next((m for m in archive.namelist() if m.lower().endswith(".dae") and "__MACOSX" not in m), None)
            if member is None:
                shutil.rmtree(folder)
                print("no model in", path.name, flush=True)
                return None
            (folder / "model.dae").write_bytes(archive.read(member))
    else:
        shutil.copyfile(path, folder / "model.dae")
    try:
        row = ingest(folder)
    except (ET.ParseError, StopIteration, ValueError) as error:
        shutil.rmtree(folder)
        print("could not read", path.name, str(error)[:80], flush=True)
        return None
    index = DB / "index.json"
    index.write_text(json.dumps([*json.loads(index.read_text()), row], indent=1))
    return row


if __name__ == "__main__":
    table = build()
    for row in table:
        print(f"{row['name'][:36]:36} {row['pieces']:5} pieces {row['unique_shapes']:4} shapes  x{row['pieces'] / max(row['unique_shapes'], 1):4.1f}  "
              f"top {row['most_repeated']:4}  {row['size_m']} m  named {row['named_share']}")
    total, unique = sum(r["pieces"] for r in table), sum(r["unique_shapes"] for r in table)
    print(f"{len(table)} models, {total} pieces, {unique} shapes within models: x{total / max(unique, 1):.1f}", file=sys.stderr)
