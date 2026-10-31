"""Run a Blender script in a local subprocess and return the artifact bytes.

The child environment has credential-shaped variables removed, stdout is kept
to the last 64 KB, and a wall-clock timeout kills the process group. The same
function is what the Modal ``setout-blender`` app calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import select
import shutil
import signal
import subprocess
import tempfile
import time

from app.make.ast_check import check
from app.make.redact import redact

HARNESS = Path(__file__).resolve().parent / "harness.py"
MAX_STDOUT = 64 * 1024
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION")


class BlenderMissing(RuntimeError):
    """No Blender binary was found. The message names where to look, not a secret."""


def resolve_blender(explicit: str | None = None) -> str | None:
    if explicit and Path(explicit).is_file():
        return explicit
    env = os.environ.get("BLENDER_BIN", "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("blender")
    if found:
        return found
    bundled = Path("/opt/blender/blender")
    if bundled.is_file():
        return str(bundled)
    return None


def blender_version(binary: str) -> str:
    out = subprocess.check_output([binary, "--version"], text=True, timeout=60, stderr=subprocess.STDOUT)
    return out.splitlines()[0].strip()


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment with credential-shaped names removed."""
    source = env if env is not None else os.environ
    clean: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        clean[name] = value
    clean["PYTHONUNBUFFERED"] = "1"
    return clean


def run(script: str, overrides: dict | None = None, timeout: int = 90, *,
        preview: bool = True, blender_bin: str | None = None,
        mode: str = "preview", quality: str = "draft") -> dict:
    """Execute ``script`` and return ``{ok, traceback, stdout_tail, warnings, files}``.

    ``files`` maps artifact names to bytes. A failed or rejected script returns
    an empty files map. Traceback and stdout are redacted.
    """
    faults = check(script or "")
    if not faults.ok:
        return _rejected(faults.text)
    binary = resolve_blender(blender_bin)
    if binary is None:
        raise BlenderMissing(
            "Blender was not found. Set BLENDER_BIN, install `blender` on PATH, "
            "or use the 4.2 LTS build at /opt/blender/blender."
        )
    cpu_seconds = max(int(timeout) * 8, 60)
    with tempfile.TemporaryDirectory(prefix="setout-make-") as tmp_name:
        tmp = Path(tmp_name)
        out = tmp / "out"
        out.mkdir()
        job = {
            "script": script,
            "overrides": overrides or {},
            "out_dir": str(out),
            "preview": preview,
            "mode": mode,
            "quality": quality,
        }
        job_path = tmp / "job.json"
        job_path.write_text(json.dumps(job))
        cmd = _command(binary, job_path)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=scrub_env(),
            start_new_session=True,
            preexec_fn=_limits(cpu_seconds),
        )
        stdout, killed, returncode = _capture(proc, float(timeout))
        tail = redact(stdout.decode("utf-8", errors="replace"))
        payload = _read_result(out)
        if killed:
            payload["ok"] = False
            payload["traceback"] = f"killed at timeout after {timeout}s\n" + (payload.get("traceback") or "")
        elif returncode != 0 and not payload.get("ok"):
            payload["traceback"] = (payload.get("traceback") or "") + f"\nblender exit {returncode}"
        files = _read_files(out, preview=preview, mode=mode) if payload.get("ok") and not killed else {}
        if payload.get("ok") and not _required_files(files, preview=preview, mode=mode):
            payload["ok"] = False
            payload["traceback"] = (payload.get("traceback") or "") + "\nharness did not write the expected files"
            files = {}
        return {
            "ok": bool(payload.get("ok")),
            "traceback": redact(payload.get("traceback") or ""),
            "stdout_tail": tail,
            "warnings": [redact(str(item)) for item in payload.get("warnings") or []],
            "secret_env_present": list(payload.get("secret_env_present") or []),
            "engine": payload.get("engine") or "",
            "files": files,
        }


def render(script: str, overrides: dict | None = None, quality: str = "draft",
           timeout: int = 540, *, blender_bin: str | None = None) -> dict:
    """Photoreal still. Draft is EEVEE 1280×960; high is Cycles (see render_settings)."""
    return run(script, overrides, timeout, preview=False, blender_bin=blender_bin,
               mode="render", quality=quality)


def _rejected(text: str) -> dict:
    return {
        "ok": False,
        "traceback": redact(text),
        "stdout_tail": "",
        "warnings": [],
        "secret_env_present": [],
        "engine": "",
        "files": {},
        "blocked": "ast",
    }


def _command(binary: str, job_path: Path) -> list[str]:
    cmd = [binary, "-b", "--factory-startup", "--python", str(HARNESS), "--", str(job_path)]
    if os.environ.get("SETOUT_BLENDER_XVFB") == "1":
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            return [xvfb, "-a", *cmd]
    return cmd


def _limits(cpu_seconds: int):
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            want = 4096 if hard == resource.RLIM_INFINITY else min(4096, hard)
            ceiling = want if hard == resource.RLIM_INFINITY else hard
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, ceiling))
        except (ValueError, OSError):
            pass
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    return apply


def _capture(proc: subprocess.Popen, timeout: float) -> tuple[bytes, bool, int]:
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    buf = bytearray()
    start = time.monotonic()
    killed = False
    while proc.poll() is None:
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            killed = True
            _kill(proc)
            break
        readable, _, _ = select.select([fd], [], [], min(0.2, remaining))
        if not readable:
            continue
        chunk = os.read(fd, 8192)
        if not chunk:
            break
        _append(buf, chunk)
    _drain(fd, buf)
    try:
        returncode = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill(proc)
        returncode = proc.wait(timeout=5)
        killed = True
    return bytes(buf), killed, returncode


def _drain(fd: int, buf: bytearray) -> None:
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.05)
        if not readable:
            continue
        chunk = os.read(fd, 8192)
        if not chunk:
            return
        _append(buf, chunk)


def _append(buf: bytearray, chunk: bytes) -> None:
    buf.extend(chunk)
    if len(buf) > MAX_STDOUT:
        del buf[: len(buf) - MAX_STDOUT]


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _read_result(out: Path) -> dict:
    path = out / "result.json"
    if not path.is_file():
        return {"ok": False, "traceback": "", "warnings": [], "secret_env_present": []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"ok": False, "traceback": "harness result.json was not valid json", "warnings": []}
    if not isinstance(data, dict):
        return {"ok": False, "traceback": "harness result.json was not an object", "warnings": []}
    return data


def _read_files(out: Path, *, preview: bool, mode: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name in ("scene.glb", "tree.json"):
        path = out / name
        if path.is_file():
            files[name] = path.read_bytes()
    extra = "render.png" if mode == "render" else "preview.png"
    if (preview or mode == "render") and (out / extra).is_file():
        files[extra] = (out / extra).read_bytes()
    return files


def _required_files(files: dict[str, bytes], *, preview: bool, mode: str) -> bool:
    if "scene.glb" not in files or "tree.json" not in files:
        return False
    if mode == "render":
        return "render.png" in files
    if preview:
        return "preview.png" in files
    return True
