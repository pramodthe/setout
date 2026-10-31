"""Build the reference floor-waste scene so the demo can open without a model call.

The scene is the hand-authored script, labelled source ``seed``. It is not
model output. Re-running prints the same project id and does not rebuild.

    SETOUT_VAR=var python -m app.make.seed_demo
"""
from __future__ import annotations

import uuid

from app.make import db
from app.make.agent import _tree_text, _write_artifacts
from app.make.floor_waste_script import SCRIPT
from app.make.worker import run

DEMO_ID = "demo-floor-waste"
PROMPT = (
    "Bathroom floor waste junction: screed to falls, membrane with 100mm upturn "
    "at wall, stainless angle, tile and sealant."
)


def seed() -> str:
    if db.get_project(DEMO_ID) and db.get_version(DEMO_ID, 1):
        return DEMO_ID
    result = run(SCRIPT, {}, timeout=120, preview=True)
    if not result.get("ok"):
        raise SystemExit(result.get("traceback") or "seed build failed")
    if db.get_project(DEMO_ID) is None:
        db.create_project("Floor Waste Junction", DEMO_ID)
    paths = _write_artifacts(DEMO_ID, 1, result.get("files") or {})
    tree = _tree_text(result)
    db.insert_version({
        "id": uuid.uuid4().hex,
        "project_id": DEMO_ID,
        "n": 1,
        "parent_n": None,
        "source": "seed",
        "script": SCRIPT,
        "params": "{}",
        "overrides": "{}",
        "glb_path": paths.get("scene.glb"),
        "tree_json": tree or None,
        "preview_path": paths.get("preview.png"),
        "render_path": None,
        "profile": "",
        "model": "",
        "status": "done",
        "error": None,
    })
    db.touch_project(DEMO_ID, active_version=1)
    db.add_message(uuid.uuid4().hex, DEMO_ID, "user", PROMPT)
    db.add_message(
        uuid.uuid4().hex,
        DEMO_ID,
        "assistant",
        "Reference scene for the demo. Built from the hand-authored floor-waste script, not from a model call.",
    )
    return DEMO_ID


def main() -> None:
    print(seed())


if __name__ == "__main__":
    main()
