"""Render sizes for the preview and the later photoreal pass.

Preview is the harness EEVEE still. Draft and high are the render job.
``RENDER_GPU=1`` selects Cycles on GPU with more samples; the Modal function
does not request a GPU unless that deploy sets it.
"""
from __future__ import annotations

import os


def render_settings(quality: str) -> dict:
    if quality == "high":
        gpu = os.environ.get("RENDER_GPU") == "1"
        return {
            "engine": "CYCLES",
            "samples": 256 if gpu else 64,
            "device": "GPU" if gpu else "CPU",
            "denoise": True,
            "width": 1280,
            "height": 960,
        }
    return {
        "engine": "BLENDER_EEVEE_NEXT",
        "samples": 16,
        "device": "CPU",
        "denoise": False,
        "width": 1280,
        "height": 960,
    }


PREVIEW = {
    "engine": "BLENDER_EEVEE_NEXT",
    "samples": 16,
    "width": 800,
    "height": 600,
}
