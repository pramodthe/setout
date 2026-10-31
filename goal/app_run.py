"""Run the app on a section, as a user would, and score what it builds.

    python -m goal.app_run <held-out name> <server url>
The server is told nothing but the picture. Its tokens are what a run really costs.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from annotation.upload import pdf_of

from . import judge
from .families import family_of
from .reference import DATA

SECTIONS = DATA / "reference-3d" / "sections"
TERMINAL = ("succeeded", "needs_review", "failed", "cancelled")


def _call(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=120) as answer:
        return json.loads(answer.read())


def upload(base, picture) -> dict:
    pdf = pdf_of(Image.open(picture).convert("L"))
    edge = uuid.uuid4().hex
    body = (f"--{edge}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"section.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + pdf + f"\r\n--{edge}--\r\n".encode()
    return _call(f"{base}/api/documents", body, {"Content-Type": f"multipart/form-data; boundary={edge}"})


def pieces_of(scene) -> list[dict]:
    """What the app built, as the judge reads it. A zone is drawn, not built."""
    out = []
    for solid in scene["solids"]:
        triangles = np.array(solid["tris"], float).reshape(-1, 3, 3)
        if solid.get("representation") == "zone" or not len(triangles):
            continue
        out.append({"triangles": triangles / 1000.0,
                    "family": family_of([solid.get("class_id"), solid.get("source_label"), solid.get("name"), solid.get("id")])})
    return out


def main(name, base):
    made = upload(base, SECTIONS / f"{name}.png")
    record = _call(f"{base}/api/documents/{made['id']}")
    crop = (record.get("crops") or record.get("figures") or record["images"])[0]
    started = time.time()
    job = _call(f"{base}/api/generate", json.dumps({"doc_id": made["id"], "crop_id": crop["crop_id"]}).encode(),
                {"Content-Type": "application/json"})
    print(json.dumps(job)[:300], flush=True)
    ident = job.get("job_id") or job["id"]
    while True:
        state = _call(f"{base}/api/jobs/{ident}")
        if state["state"] in TERMINAL:
            break
        time.sleep(20)
    result = state.get("result") or {}
    scene = _call(f"{base}/api/versions/{result['version_id']}/scene")
    built = pieces_of(scene)
    score = judge.score(built, judge.load(name))
    run = {"name": name, "job": ident, "state": state["state"], "stop": result.get("stop_reason"), "built": len(built),
           "families": sorted({p["family"] for p in built}), "minutes": round((time.time() - started) / 60, 1), "score": score}
    (DATA / "reference-3d" / f"app-{name}.json").write_text(json.dumps(run, indent=1))
    print("RESULT", json.dumps(run), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
