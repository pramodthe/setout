"""Model-written CadQuery, executed only inside an isolated Modal sandbox.

Used for parts the closed grammar cannot express (shaped grates, mixers,
clamping rings...). Two locks: a static AST check here (imports limited to
cadquery and math; no exec/eval/open/dunder access), then a Modal Sandbox
with networking blocked and CPU/memory/time limits. This process never runs
model code. The result is illustrative geometry: it is normalised, fitted
into the envelope the detail program reserved for it, and never carries a
verified dimension.
"""

from __future__ import annotations

import ast
import base64
import json
import struct
from collections.abc import Callable
from dataclasses import dataclass

ALLOWED_IMPORTS = {"cadquery", "math"}
BANNED_CALLS = {"exec", "eval", "compile", "open", "__import__", "globals",
                "locals", "vars", "getattr", "setattr", "delattr", "input",
                "breakpoint", "exit", "quit"}
MAX_CODE = 12_000

WRAPPER_HEAD = """\
import base64, json, os, tempfile
import cadquery as cq
"""

WRAPPER_TAIL = """
_res = build(W, H, D)
if isinstance(_res, cq.Workplane):
    _vals = [v for v in _res.vals() if isinstance(v, cq.Shape)]
    if not _vals:
        raise SystemExit("build() returned an empty Workplane")
    _shape = _vals[0] if len(_vals) == 1 else cq.Compound.makeCompound(_vals)
else:
    _shape = _res
_f = os.path.join(tempfile.mkdtemp(), "part.stl")
cq.exporters.export(_shape, _f, tolerance=0.4, angularTolerance=0.25)
_bb = _shape.BoundingBox()
print("RESULT " + json.dumps({
    "bbox": [_bb.xmin, _bb.ymin, _bb.zmin, _bb.xmax, _bb.ymax, _bb.zmax],
    "stl": base64.b64encode(open(_f, "rb").read()).decode()}))
"""


class CadCodeError(ValueError):
    """Model code rejected before or during execution (fed back to the model)."""


def check_code(code: str, nargs: int = 3) -> None:
    """Static gate before anything leaves this process."""
    if len(code) > MAX_CODE:
        raise CadCodeError(f"code longer than {MAX_CODE} characters")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise CadCodeError(f"syntax error: {e}") from e
    has_build = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise CadCodeError(f"import {a.name!r} not allowed (only cadquery, math)")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                raise CadCodeError(f"from {node.module!r} import not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in BANNED_CALLS:
            raise CadCodeError(f"call to {node.func.id}() not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise CadCodeError(f"dunder attribute {node.attr!r} not allowed")
        elif isinstance(node, ast.FunctionDef) and node.name == "build":
            has_build = len(node.args.args) == nargs
    if not has_build:
        raise CadCodeError("define exactly: def build(W, H, D)" if nargs == 3 else
                           "define exactly: def build(p)")


def wrap(code: str, W: float, H: float, D: float) -> str:
    return (WRAPPER_HEAD + f"W, H, D = {float(W)!r}, {float(H)!r}, {float(D)!r}\n"
            + code + "\n" + WRAPPER_TAIL)


# ---------------------------------------------------------------- STL

def parse_stl(data: bytes) -> list[float]:
    """Flat xyz triangle list from binary or ASCII STL."""
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            out: list[float] = []
            for i in range(n):
                out.extend(struct.unpack_from("<9f", data, 84 + 50 * i + 12))
            return out
    out = []
    for line in data.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            out.extend(float(v) for v in parts[1:])
    if not out or len(out) % 9:
        raise CadCodeError("could not read the STL produced by build()")
    return out


@dataclass
class Fitted:
    positions: list[float]  # absolute mm, Setout axes
    scale: float  # 1.0 = native size; < 1 = shrunk to fit the envelope
    native_size: tuple[float, float, float]  # w, h, d before fitting


def fit_to_envelope(cq_tris: list[float], env: tuple[float, ...]) -> Fitted:
    """CadQuery axes (X width, Y depth with front at 0, Z up) -> Setout axes
    (x, y up, z toward viewer): x = X, y = Z, z = -Y (a rotation, no mirror).
    Then normalise to the envelope: shrink uniformly if too big, centre in
    x and z, stand on the envelope floor."""
    x0, x1, y0, y1, z0, z1 = env
    pts = [(cq_tris[i], cq_tris[i + 2], -cq_tris[i + 1]) for i in range(0, len(cq_tris), 3)]
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    size = tuple(max(hi[k] - lo[k], 1e-9) for k in range(3))
    room = (x1 - x0, y1 - y0, z1 - z0)
    s = min(1.0, *(room[k] / size[k] for k in range(3)))
    off = (x0 + (room[0] - size[0] * s) / 2, y0, z0 + (room[2] - size[2] * s) / 2)
    out: list[float] = []
    for p in pts:
        out.extend(off[k] + (p[k] - lo[k]) * s for k in range(3))
    return Fitted(out, s, size)


# ---------------------------------------------------------------- runners

Runner = Callable[[str], str]  # full script -> stdout


def available() -> bool:
    try:
        import modal  # noqa: F401
    except ImportError:
        return False
    from pathlib import Path
    return Path.home().joinpath(".modal.toml").exists()


def modal_runner(script: str) -> str:
    """Execute in a fresh Modal sandbox: no network, 1 CPU, 1 GiB, 120 s."""
    import modal

    app = modal.App.lookup("setout-cad", create_if_missing=True)
    image = (modal.Image.debian_slim(python_version="3.12")
             .apt_install("libgl1", "libglib2.0-0", "libxrender1", "libxext6", "libsm6")
             .pip_install("cadquery==2.8.0"))
    sb = modal.Sandbox.create(app=app, image=image, timeout=180, block_network=True,
                              cpu=1.0, memory=1024)
    try:
        # STEP payloads exceed ARG_MAX for ordinary multi-feature objects.
        # Stream the script/data through stdin instead of a command argument.
        p = sb.exec("python", "-", timeout=120)
        encoded = script.encode()
        for start in range(0, len(encoded), 65536):
            p.stdin.write(encoded[start:start + 65536])
            p.stdin.drain()
        p.stdin.write_eof()
        p.stdin.drain()
        def bounded(stream, maximum):
            chunks, size = [], 0
            for chunk in stream:
                size += len(chunk)
                if size > maximum:
                    raise CadCodeError("sandbox output exceeds the artifact limit")
                chunks.append(chunk)
            return "".join(chunks)
        out, err = bounded(p.stdout, 96_000_000), bounded(p.stderr, 1_000_000)
        p.wait()
        if p.returncode != 0 and "RESULT " not in out:
            tail = "\n".join(err.strip().splitlines()[-12:])
            raise CadCodeError(f"build() failed in sandbox:\n{tail}")
        return out
    finally:
        sb.terminate()


def run_cad(code: str, env: tuple[float, ...], runner: Runner | None = None) -> Fitted:
    """Static check, sandboxed execution, STL back, fitted into env."""
    check_code(code)
    x0, x1, y0, y1, z0, z1 = env
    script = wrap(code, x1 - x0, y1 - y0, z1 - z0)
    out = (runner or modal_runner)(script)
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT ")), None)
    if line is None:
        raise CadCodeError("build() produced no result")
    data = json.loads(line[7:])
    tris = parse_stl(base64.b64decode(data["stl"]))
    if not tris:
        raise CadCodeError("build() produced an empty solid")
    return fit_to_envelope(tris, env)
