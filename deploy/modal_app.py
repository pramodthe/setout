"""Hosted public demo on Modal: library, 3D detail pages and the AI guide.

Runs the same FastAPI app in public mode (SETOUT_PUBLIC=1): read-only, no
uploads or generation, source drawings never served (they are third-party
documents), and the guide rate-limited. The library lives on a Modal Volume.

    cd web && npm run build && cd ..                       # bundle the frontend
    modal volume create setout-demo-var                     # once
    modal volume put setout-demo-var <SETOUT_VAR>/library /library
    modal deploy deploy/modal_app.py                        # browse-only demo

To enable the AI guide on the demo, store the key in your own Modal account
once and redeploy with the guide switched on:

    modal secret create setout-meta META_API_KEY=<your key>
    SETOUT_DEMO_GUIDE=1 modal deploy deploy/modal_app.py
"""

import os
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
# Read at deploy time and baked into the image env, so the container sees the
# same value: Modal refuses to start a function whose secrets differ between
# the deploy and the container.
GUIDE = os.environ.get("SETOUT_DEMO_GUIDE") == "1"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi==0.162.0", "pydantic==2.14.1", "pypdf==6.21.3",
                 "pillow==13.0.0", "python-multipart==0.0.32")
    .env({"SETOUT_VAR": "/data", "SETOUT_PUBLIC": "1", "SETOUT_GUIDE_PER_HOUR": "60",
          "SETOUT_DEMO_GUIDE": "1" if GUIDE else "0"})
    .add_local_dir(str(ROOT / "web" / "dist"), "/root/web/dist")
    .add_local_dir(str(ROOT / "app"), "/root/app", ignore=["__pycache__"])
)
volume = modal.Volume.from_name("setout-demo-var", create_if_missing=True)
app = modal.App("setout-demo", image=image)


@app.function(
    volumes={"/data": volume},
    secrets=[modal.Secret.from_name("setout-meta", required_keys=["META_API_KEY"])] if GUIDE else [],
    scaledown_window=600,
    timeout=300,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    from app.main import app as fastapi_app

    return fastapi_app
