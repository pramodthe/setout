"""Put a Blender scene into the app, so it can be opened on the normal detail page.

    modal run -m goal.blender_view::publish --scene <file> --ident blender-x --title "..." --origin "..." --drawing <png> --store <var>
A model's scene is checked, then run with the network blocked.
"""
from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path

from PIL import Image

from .blender_agent import HERE, check
from .blender_modal import app, export_model_code


@app.local_entrypoint()
def publish(scene: str, ident: str, title: str, origin: str, drawing: str, store: str):
    os.environ["SETOUT_VAR"] = store
    from app import blender_details
    blender_details.VAR = store
    code = Path(scene).read_text()
    check(code)
    chunks: dict = {}
    for name, piece in export_model_code.remote_gen((HERE / "blender_kit.py").read_text(), code):
        chunks.setdefault(name, []).append(piece)
    print(b"".join(chunks.get("log.txt", [])).decode()[-400:])
    parts = json.loads(gzip.decompress(b"".join(chunks["parts.json.gz"])))
    out = io.BytesIO()
    Image.open(drawing).convert("RGB").save(out, "PNG")
    print(blender_details.publish(ident, title, origin, parts, out.getvalue()), len(parts), "rows", sum(p["pieces"] for p in parts), "pieces")
