"""Modal app ``setout-blender``: headless Blender, no secrets, no network.

This is separate from ``deploy/modal_app.py`` (``setout-demo``). Do not attach
the ``setout-llm`` secret here. The worker receives a script and returns bytes;
the web side writes the volume.

    modal deploy deploy/blender_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.make.blender_pin import BLENDER_SHA256, BLENDER_URL, BLENDER_VERSION  # noqa: E402

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "ca-certificates",
        "curl",
        "xz-utils",
        "libx11-6",
        "libxi6",
        "libxxf86vm1",
        "libxfixes3",
        "libxrender1",
        "libxkbcommon0",
        "libsm6",
        "libglib2.0-0",
        "libgl1",
        "libegl1",
        "libgomp1",
        "libgl1-mesa-dri",
        "xvfb",
    )
    .run_commands(
        f"curl -fsSL -o /tmp/blender.tar.xz {BLENDER_URL}",
        f"echo '{BLENDER_SHA256}  /tmp/blender.tar.xz' | sha256sum -c",
        "mkdir -p /opt && tar -xJf /tmp/blender.tar.xz -C /opt",
        f"ln -sfn /opt/blender-{BLENDER_VERSION}-linux-x64 /opt/blender",
        "rm -f /tmp/blender.tar.xz",
    )
    .env({
        "BLENDER_BIN": "/opt/blender/blender",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "SETOUT_BLENDER_XVFB": "1",
    })
    .add_local_dir(str(ROOT / "app" / "make"), remote_path="/root/app/make", ignore=["__pycache__"])
)

app = modal.App("setout-blender", image=image)


@app.function(
    timeout=120,
    memory=4096,
    cpu=2,
    block_network=True,
    max_inputs=1,
    single_use_containers=True,
)
def run(script: str, overrides: dict | None = None, timeout: int = 90) -> dict:
    """Build one scene. No volume and no secrets. Returns artifact bytes."""
    from app.make.worker import run as run_local

    return run_local(script, overrides or {}, timeout=min(int(timeout), 90))


@app.function(
    timeout=600,
    memory=4096,
    cpu=2,
    block_network=True,
    max_inputs=1,
    single_use_containers=True,
)
def render(script: str, overrides: dict | None = None, quality: str = "draft",
           timeout: int = 540) -> dict:
    """Draft EEVEE or high Cycles still. Same isolation as ``run``."""
    from app.make.worker import render as render_local

    return render_local(script, overrides or {}, quality=quality, timeout=min(int(timeout), 540))
