"""Local Blender, or the deployed ``setout-blender`` Modal app.

``BLENDER_TARGET=modal`` calls the deployed app. Any other value, including an
unset variable, uses the local binary. If Modal is selected but the app cannot
be called, the job runs locally. The Modal app is never given a secret.
"""
from __future__ import annotations

import os

from app.make.redact import redact


class ModalUnavailable(RuntimeError):
    """Modal could not run the job. The caller falls back to local Blender."""


def blender_target() -> str:
    """``modal`` only when the env var says so. Everything else is local."""
    value = os.environ.get("BLENDER_TARGET", "").strip().lower()
    return "modal" if value == "modal" else "local"


def run(script: str, overrides: dict | None = None, timeout: int = 90, **kwargs) -> dict:
    if blender_target() == "modal" and not kwargs.get("blender_bin"):
        try:
            return _tag(_modal("run", script, overrides or {}, min(int(timeout), 90)), "modal")
        except ModalUnavailable:
            pass
    from app.make.worker import run as local_run
    return _tag(local_run(script, overrides, timeout, **kwargs), "local", fallback=blender_target() == "modal")


def render(script: str, overrides: dict | None = None, quality: str = "draft",
           timeout: int = 540, **kwargs) -> dict:
    if blender_target() == "modal" and not kwargs.get("blender_bin"):
        try:
            return _tag(
                _modal("render", script, overrides or {}, quality, min(int(timeout), 540)),
                "modal",
            )
        except ModalUnavailable:
            pass
    from app.make.worker import render as local_render
    return _tag(
        local_render(script, overrides, quality, timeout, **kwargs),
        "local",
        fallback=blender_target() == "modal",
    )


def _modal(name: str, *args):
    try:
        import modal
    except ImportError as exc:
        raise ModalUnavailable("modal is not installed") from exc
    try:
        function = modal.Function.from_name("setout-blender", name)
        return function.remote(*args)
    except Exception as exc:
        raise ModalUnavailable(redact(f"{type(exc).__name__}: {exc}")) from None


def _tag(result: dict, backend: str, *, fallback: bool = False) -> dict:
    if not isinstance(result, dict):
        result = {"ok": False, "traceback": "blender returned no result", "files": {}}
    out = dict(result)
    out["backend"] = backend
    if fallback:
        warnings = list(out.get("warnings") or [])
        warnings.append("Modal was unavailable; used local Blender")
        out["warnings"] = warnings
    return out
