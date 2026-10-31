"""Parametric CAD with separate, trusted B-Rep measurement; no envelope fitting."""
from __future__ import annotations

import ast
import base64
import json
import math

from . import cad_sandbox
from .program import evaluate
from .repository import canonical, sha

TOOLCHAIN = "cadquery-2.8.0:component-measure-v1"
# Why a literal is refused, and the way out, so one retry can fix it.
LITERAL_HELP = ("Write every size and offset as an expression of p[...]; a fraction such as 0.5 * p['width'] is fine, "
                "any other number except 0 is not. build(p) never positions the part: its rigid placement does, so "
                "model it in its local frame and change the part's placement, not the code, to move it.")

MEASURE = '''
import base64, json, tempfile, os
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.TopAbs import TopAbs_REVERSED
path = os.path.join(tempfile.mkdtemp(), "shape.step")
open(path, "wb").write(base64.b64decode(STEP_DATA))
shape = cq.importers.importStep(path).val()
b = shape.BoundingBox()
mesh_path = os.path.join(os.path.dirname(path), "measured.stl")
cq.exporters.export(shape, mesh_path, tolerance=0.05, angularTolerance=0.1)
faces = []
for f in shape.Faces():
    kind = f.geomType()
    descriptor = {"kind": kind, "center": list(f.Center().toTuple())}
    surface = BRepAdaptor_Surface(f.wrapped, True)
    if kind == "CYLINDER":
        cylinder = surface.Cylinder()
        direction = cylinder.Axis().Direction()
        location = cylinder.Axis().Location()
        descriptor.update(radius=cylinder.Radius(),
            axis=[direction.X(), direction.Y(), direction.Z()],
            location=[location.X(), location.Y(), location.Z()],
            internal=f.wrapped.Orientation() == TopAbs_REVERSED)
    elif kind == "PLANE":
        descriptor["normal"] = list(f.normalAt().toTuple())
    faces.append(descriptor)
print("RESULT " + json.dumps({"valid": shape.isValid(), "volume": shape.Volume(),
    "bounds": [b.xmin,b.ymin,b.zmin,b.xmax,b.ymax,b.zmax], "faces": faces,
    "stl": base64.b64encode(open(mesh_path,"rb").read()).decode()}))
'''


def check_component(code):
    cad_sandbox.check_code(code, nargs=1)
    tree = ast.parse(code)
    assignments = {target.id:node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name)}
    def dimensional_value(node, seen=None):
        seen = seen or set()
        if isinstance(node, ast.Name) and node.id in assignments and node.id not in seen:
            return dimensional_value(assignments[node.id], seen | {node.id})
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "p":
            return "length"
        if isinstance(node, ast.Constant) and isinstance(node.value, (int,float)):
            return "zero" if node.value == 0 else "literal"
        if isinstance(node, ast.UnaryOp):
            return dimensional_value(node.operand, seen)
        if isinstance(node, (ast.Tuple, ast.List)):
            states = [dimensional_value(value, seen) for value in node.elts]
            return "literal" if "literal" in states else "length"
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr == "Vector":
            states = [dimensional_value(value,seen) for value in node.args]
            return "literal" if "literal" in states else "length"
        if isinstance(node, ast.BinOp):
            a,b = dimensional_value(node.left, seen), dimensional_value(node.right, seen)
            if isinstance(node.op, (ast.Add, ast.Sub)) and "length" in (a,b) and "literal" in (a,b):
                raise cad_sandbox.CadCodeError(f"physical offsets must use named p parameters: line {node.lineno} "
                                               f"adds a number to a size in {ast.unparse(node)}. {LITERAL_HELP}")
            return "length" if "length" in (a,b) else "zero" if a == b == "zero" else "literal"
        return "unknown"
    dimensional = {"box", "circle", "hole", "fillet", "chamfer", "sphere", "cylinder",
                   "extrude", "shell", "rect", "ellipse", "slot2D", "translate", "center", "workplane", "pushPoints",
                   "rarray", "polarArray", "moveTo", "lineTo", "polyline", "spline", "threePointArc", "radiusArc", "sagittaArc",
                   "makeBox","makeSphere","makeCylinder","makeCone","makeTorus","makeCircle","makeEllipse","makeLine","makePolygon"}
    solid_args = {"makeBox":4,"makeSphere":2,"makeCylinder":3,"makeCone":4,"makeTorus":3,"makeCircle":2,"makeEllipse":3,
                  "rarray":2,"polarArray":1,"workplane":1}
    length_keys = {"length","width","height","radius","radius1","radius2","r1","r2","diameter","depth","thickness","pnt","vec","distance","offset","origin"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("scale","transformGeometry"):
            raise cad_sandbox.CadCodeError("scaling or affine geometry fitting is not permitted")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in dimensional:
            args = list(node.args[:solid_args.get(node.func.attr,len(node.args))])
            args += [keyword.value for keyword in node.keywords if keyword.arg in length_keys]
            for arg in args:
                if (not isinstance(arg, ast.Constant) or not isinstance(arg.value, bool)) and dimensional_value(arg) == "literal":
                    raise cad_sandbox.CadCodeError(f"physical sizes must use named p parameters: line {node.lineno} "
                                                   f"passes {ast.unparse(arg)} to {node.func.attr}(). {LITERAL_HELP}")


def _result(output):
    lines = [line[7:] for line in output.splitlines() if line.startswith("RESULT ")]
    if len(lines) != 1:
        raise cad_sandbox.CadCodeError("sandbox returned an invalid result envelope")
    return json.loads(lines[0])


def _setout(vector):
    return [vector[0], vector[2], -vector[1]]


def measure_features(report, features, params):
    native = report["bounds"]
    bounds = [native[0], native[2], -native[4], native[3], native[5], -native[1]]
    checks = []
    for feature in features:
        expected = evaluate(feature["expected_expr"], params)
        selector, actual, reason = feature["selector"], None, None
        axis = {"x": 0, "y": 1, "z": 2}.get(selector.get("axis"))
        kind = feature["kind"]
        if kind == "extent" and axis is not None:
            actual = bounds[axis + 3] - bounds[axis]
        elif kind in ("hole_diameter", "radius") and axis is not None:
            matches = []
            for face in report["faces"]:
                if face["kind"] != "CYLINDER" or (kind == "hole_diameter" and not face.get("internal")):
                    continue
                direction, location = _setout(face["axis"]), _setout(face["location"])
                if abs(direction[axis]) < 0.999999:
                    continue
                center = selector.get("center")
                if center is not None:
                    point = [evaluate(str(e), params) for e in center]
                    if any(abs(point[i] - location[i]) > 0.01 for i in range(3) if i != axis):
                        continue
                matches.append(face)
            # A through-hole may have multiple split cylindrical faces: ambiguity stays explicit.
            if len(matches) == 1:
                actual = matches[0]["radius"] * (2 if kind == "hole_diameter" else 1)
            elif not matches:
                reason = "required cylindrical feature absent"
            else:
                reason = "ambiguous cylindrical feature"
        elif kind == "slope_ratio" and axis in (0, 2):
            normals = [_setout(face["normal"]) for face in report["faces"] if face["kind"] == "PLANE"]
            upward = [n for n in normals if n[1] > 1e-8]
            if len(upward) == 1:
                actual = upward[0][axis] / upward[0][1]
            else:
                reason = "unsupported or ambiguous slope plane"
        else:
            reason = "unsupported feature measurement"
        status = "UNVERIFIED"
        if reason == "required cylindrical feature absent":
            status = "FAIL"
        if actual is not None:
            tolerance = min(feature.get("tolerance", 0.01), 1e-6 if kind == "slope_ratio" else .1)
            comparison = feature.get("comparison", "eq")
            ok = (abs(actual - expected) <= tolerance if comparison == "eq" else
                  actual + tolerance >= expected if comparison == "min" else actual - tolerance <= expected)
            status = "PASS" if ok else "FAIL"
        checks.append({"id": feature["id"], "status": status, "expected": expected, "actual": actual,
                       "tolerance": feature.get("tolerance", 0.01), "reason": reason,
                       "source_dimension_id": feature.get("source_dimension_id"), "required": True})
    return bounds, checks


def build(code, params, features, runner=None, measurement_runner=None, measurement_params=None):
    check_component(code)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in params.values()):
        raise ValueError("CAD parameters must be finite numbers")
    encoded_params = repr(json.dumps(params, allow_nan=False))
    script = cad_sandbox.WRAPPER_HEAD + f"p = json.loads({encoded_params})\n" + code + "\n"
    script += '''
_res = build(p)
if not isinstance(_res, cq.Workplane):
    raise ValueError("build must return cq.Workplane")
_values = [value for value in _res.vals() if isinstance(value, cq.Shape)]
if not _values:
    raise ValueError("build returned no shapes")
_shape = _values[0] if len(_values) == 1 else cq.Compound.makeCompound(_values)
_directory = tempfile.mkdtemp()
_step = os.path.join(_directory, "part.step")
_stl = os.path.join(_directory, "part.stl")
cq.exporters.export(_shape, _step)
cq.exporters.export(_shape, _stl, tolerance=0.05, angularTolerance=0.1)
if not os.path.exists(_stl):
    raise ValueError("the solid could not be meshed, so it is degenerate or self-intersecting. A revolve is the usual "
                     "cause: its axis is in the workplane's own coordinates and must lie in the plane of the profile")
print("RESULT " + json.dumps({"step": base64.b64encode(open(_step,"rb").read()).decode(),
    "stl": base64.b64encode(open(_stl,"rb").read()).decode()}))
'''
    runner = runner or cad_sandbox.modal_runner
    exported = _result(runner(script))
    step = base64.b64decode(exported["step"], validate=True)
    stl = base64.b64decode(exported["stl"], validate=True)
    if not step or len(step) > 32_000_000 or not stl or len(stl) > 32_000_000:
        raise ValueError("empty or oversized CAD artifacts")
    # modal_runner creates/terminates a fresh sandbox for each call; generated
    # code/environment cannot mutate the trusted measurement interpreter.
    report = _result((measurement_runner or runner)(f"STEP_DATA = {exported['step']!r}\n" + MEASURE))
    if not report["valid"] or not math.isfinite(report["volume"]) or report["volume"] <= 0:
        raise ValueError("CAD is not a valid positive-volume solid")
    if len(report["bounds"]) != 6 or not all(math.isfinite(v) for v in report["bounds"]):
        raise ValueError("invalid CAD bounds")
    bounds, checks = measure_features(report, features, measurement_params or params)
    # Render only the trusted tessellation of the exact STEP that was measured.
    # Generated Python can tamper with its own exporter/stdout, so its STL is
    # never authoritative for the viewer.
    stl = base64.b64decode(report["stl"], validate=True)
    if not stl or len(stl) > 32_000_000:
        raise ValueError("invalid or oversized measured mesh")
    positions = cad_sandbox.parse_stl(stl)
    if not positions or len(positions) > 1_800_000 or not all(math.isfinite(v) for v in positions):
        raise ValueError("invalid or oversized CAD mesh")
    transformed = []
    for i in range(0, len(positions), 3):
        transformed.extend(_setout(positions[i:i+3]))
    return {"step": step, "stl": stl, "mesh": {"positions": transformed},
            "measured": {"bounds": bounds, "volume": report["volume"], "faces": report["faces"]}, "checks": checks,
            "toolchain_sha": sha(TOOLCHAIN), "input_sha": sha({"code": code, "params": params, "measurement_params": measurement_params,
                                                                      "features": features, "toolchain": TOOLCHAIN})}
