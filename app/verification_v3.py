"""Verification of exact version manifests, source features and layer interfaces."""
from __future__ import annotations

import json
import re

from . import database as d
from .cad_components import measure_features, check_component
from .geometry import Mesh, build_assembly, export_scene_json
from .interfaces import measure_interface, matches_brep_planes
from .native_program import NativeProgram
from .program import evaluate, expr_names
from .repository import VERIFIER_VERSION, canonical, sha
from .spec import DetailSpec
from .verify import verify_all
from .versioning import archive_scene


def check(ident, status, reason=None, required=True, **values):
    return dict(id=ident, stage="C" if ident.startswith("source-") else "B", description=reason or ident,
                status=status, reason=reason, required=required, **values)


# Words that say where a piece of a layer sits, not what it is made of.
# Splitting one layer into wall/floor or upturn pieces keeps its material.
POSITION_WORDS = {"wall", "floor", "ceiling", "roof", "upturn", "upstand", "turn", "return", "corner",
                  "left", "right", "top", "bottom", "front", "back", "inner", "outer", "upper",
                  "lower", "vertical", "horizontal", "side", "end", "layer", "part", "piece", "section"}


def material_tokens(part_type):
    """The material words of a part type: its tokens minus position words."""
    tokens = set(re.findall(r"[a-z]+", part_type.lower())) - POSITION_WORDS
    return tokens or {part_type.lower()}


def label_coverage(program, read):
    """Drawing labels the READ classed as a layer or object that no part
    carries. Informational: it never blocks completion, but the agent and the
    reviewer see what is missing. Older reads without categories are skipped."""
    wanted = {e["label_verbatim"] for e in read.get("elements", [])
              if e.get("label_verbatim") and e.get("category") in ("layer", "object")}
    if not wanted:
        return None
    missing = sorted(wanted - {part.label for part in program.parts if part.label})
    return check("label-coverage", "UNVERIFIED" if missing else "PASS",
                 "Every drawing label for a layer or object is carried by a part", required=False,
                 missing=missing)


def object_form_check(part, measured):
    """A part declared shaped must not be its own envelope block. Nothing is
    inferred from names: the author declares form=shaped or form=block."""
    if part.role != "object" or getattr(part, "form", None) != "shaped":
        return None
    bounds = measured.get("bounds",[])
    volume = measured.get("volume")
    if len(bounds) != 6 or volume is None:
        return check("object-form-"+part.id,"UNVERIFIED","trusted solid volume/bounds are required")
    envelope = (bounds[3]-bounds[0])*(bounds[4]-bounds[1])*(bounds[5]-bounds[2])
    ratio = volume/envelope if envelope > 0 else 1
    return check("object-form-"+part.id,"PASS" if 0 < ratio < .9 else "FAIL",
        "A shaped object cannot be a solid envelope block; visual form is also checked by the critic.",
        solid_fraction=ratio)


def assembly(repo, version):
    if version["format_version"] == "legacy-v2":
        return archive_scene(repo, version), None, None
    program = NativeProgram.model_validate(version["program"])
    spec = DetailSpec.model_validate(version["spec"])
    primitive = spec.model_copy(deep=True)
    primitive.components = [c for c in spec.components if c.generator.id != "cad_mesh"]
    meshes = build_assembly(primitive)
    env = {p.name: p.value for p in program.params}
    placeholders = {}
    for component in spec.components:
        if component.generator.id != "cad_mesh":
            continue
        part = next(p for p in program.parts if p.id == component.id)
        binding = version["bindings"].get(part.id)
        if not binding or binding.get("placeholder"):
            state = "failed" if binding and binding.get("placeholder") else "pending"
            component.note = f"placeholder (CAD {state}): " + (binding.get("placeholder", "not built") if binding else "not built")
            component.color = "#f28c45"
            placeholders[part.id] = state
            box = component.model_copy(deep=True)
            box.generator.id = "box"
            holder = spec.model_copy(deep=True)
            holder.components = [box]
            meshes.update(build_assembly(holder))
            continue
        build = repo.get(d.component_builds, binding["build_id"])
        data = json.loads(repo.artifacts.read(build["artifacts"]["mesh"]))
        offset = [evaluate(part.placement.get(axis, "0"), env) for axis in ("x", "y", "z")]
        positions = [v + offset[i % 3] for i, v in enumerate(data["positions"])]
        meshes[part.id] = Mesh(part.id, positions=positions, color=component.color)
    spec.context["placeholders"] = placeholders
    return spec, program, meshes


def _dependencies(program, before, after):
    by_id = {p.id: p for p in program.parts}
    seen = set()
    def visit(ident):
        if ident == before:
            return True
        if ident in seen:
            return False
        seen.add(ident)
        return any(visit(dep) for dep in by_id[ident].install_after)
    return visit(after)


def spatial_checks(program, meshes, exposed=None, cad_planes=None):
    output = []
    by_id = {p.id: p for p in program.parts}
    for i, relation in enumerate(program.relations):
        ident = f"assembly-{i}-{relation.kind}"
        if relation.kind == "installed_before":
            output.append(check(ident, "PASS" if _dependencies(program, relation.source, relation.target) else "FAIL"))
            continue
        if relation.kind not in ("covers", "supported_by", "bonded_to", "sealed_to", "terminates_against", "laps_over"):
            output.append(check(ident, "UNVERIFIED", "unsupported spatial interface"))
            continue
        settings = relation.constraints
        required_fields = {"axis", "source_face", "target_face", "min_gap", "max_gap", "min_coverage"}
        if not required_fields <= settings.keys():
            output.append(check(ident, "UNVERIFIED", "missing explicit interface constraints"))
            continue
        axis = {"x": 0, "y": 1, "z": 2}.get(settings["axis"])
        if axis is None or settings["source_face"] not in ("min", "max") or settings["target_face"] not in ("min", "max"):
            output.append(check(ident, "FAIL", "invalid interface selector"))
            continue
        if any(by_id[k].shape != "box" for k in (relation.source, relation.target)):
            if any(by_id[k].shape not in ("box","wedge","sheet","profile","cad") for k in (relation.source,relation.target)):
                output.append(check(ident,"UNVERIFIED","interface requires supported planar primitives"))
                continue
            try:
                source_extremal = by_id[relation.source].shape == "cad" and relation.kind == "supported_by"
                target_extremal = by_id[relation.target].shape == "cad" and relation.kind == "supported_by"
                for key,side in ((relation.source,settings["source_face"]),(relation.target,settings["target_face"])):
                    extremal = source_extremal if key == relation.source else target_extremal
                    if by_id[key].shape == "cad" and not matches_brep_planes(meshes[key],axis,side,(cad_planes or {}).get(key,[]),extremal):
                        raise ValueError("selected CAD interface is not an independently measured B-Rep plane")
                measurement = measure_interface(meshes[relation.source],meshes[relation.target],axis,
                              settings["source_face"],settings["target_face"],relation.kind in ("covers","laps_over"),source_extremal,target_extremal)
            except ValueError as error:
                output.append(check(ident,"UNVERIFIED",str(error)))
                continue
            ordering = _dependencies(program,relation.target,relation.source)
            ok = (measurement["min_gap"] is not None and settings["min_gap"] <= measurement["min_gap"] + 1e-6
                  and measurement["max_gap"] <= settings["max_gap"] + 1e-6
                  and measurement["coverage"] + 1e-6 >= settings["min_coverage"] and ordering)
            output.append(check(ident,"PASS" if ok else "FAIL",**measurement,order_valid=ordering))
            continue
        a, b = meshes[relation.source].bounds(), meshes[relation.target].bounds()
        if a is None or b is None:
            output.append(check(ident, "FAIL", "missing interface geometry"))
            continue
        af = a[axis + (3 if settings["source_face"] == "max" else 0)]
        bf = b[axis + (3 if settings["target_face"] == "max" else 0)]
        direction = 1 if settings["source_face"] == "min" else -1
        gap = (af - bf) * direction
        projection = [j for j in range(3) if j != axis]
        area = 1.0
        overlap = 1.0
        for j in projection:
            base = b if relation.kind in ("covers","laps_over") else a
            area *= max(0, base[j + 3] - base[j])
            overlap *= max(0, min(a[j + 3], b[j + 3]) - max(a[j], b[j]))
        coverage = overlap / area if area else 0
        spatial = settings["min_gap"] <= gap <= settings["max_gap"] and coverage >= settings["min_coverage"]
        ordering = (_dependencies(program, relation.target, relation.source) if relation.kind == "covers" else
                    _dependencies(program, relation.target, relation.source))
        output.append(check(ident, "PASS" if spatial and ordering else "FAIL", gap=gap,
                            coverage=coverage, order_valid=ordering))
    for part in program.parts:
        if part.role == "covered_layer" and not any(r.target == part.id and r.kind in
                ("covers", "terminates_against") for r in program.relations):
            accepted = part.id in (exposed or set())
            output.append(check(f"coverage-{part.id}", "N/A" if accepted else "UNVERIFIED",
                          "reviewer accepted an exposed layer" if accepted else "covered layer has no covering relationship", required=not accepted))
    return output


def plane_coordinate(repo, version, part, mesh, axis, side, env):
    bounds = mesh.bounds()
    if bounds is None or side not in ("min", "max"):
        return None
    coordinate = bounds[axis + (3 if side == "max" else 0)]
    if part.shape == "cad":
        binding = version["bindings"].get(part.id, {})
        if not binding.get("build_id"):
            return None
        build = repo.get(d.component_builds, binding["build_id"])
        offset = evaluate(part.placement.get(("x", "y", "z")[axis], "0"), env)
        for face in build["measured"].get("faces", []):
            if face["kind"] != "PLANE":
                continue
            normal = [face["normal"][0], face["normal"][2], -face["normal"][1]]
            center = [face["center"][0], face["center"][2], -face["center"][1]]
            if abs(normal[axis]) > 0.999999 and abs(center[axis] + offset - coordinate) <= .05:
                # The exact B-Rep plane, rather than tessellated extrema.
                return center[axis] + offset
        return None
    # Trusted primitive meshes: require a real non-degenerate face at the
    # selected extremum, not merely a corner of a sloped/curved part's AABB.
    positions = mesh.positions
    projection = [i for i in range(3) if i != axis]
    for start in range(0, len(positions), 9):
        points = [positions[start+i:start+i+3] for i in (0,3,6)]
        if not all(abs(p[axis] - coordinate) <= .0001 for p in points):
            continue
        i, j = projection
        area = abs((points[1][i]-points[0][i])*(points[2][j]-points[0][j]) -
                   (points[2][i]-points[0][i])*(points[1][j]-points[0][j]))
        if area > 1e-8:
            return coordinate
    return None


def cross_part_feature(repo, version, program, meshes, part, feature, env):
    selector = feature.selector
    axis = {"x":0, "y":1, "z":2}.get(selector.get("axis"))
    by_id = {p.id: p for p in program.parts}
    other = by_id.get(selector.get("other_part"))
    result = check(feature.id, "UNVERIFIED", "unsupported or missing planar face selector",
                   source_dimension_id=feature.source_dimension_id)
    if axis is None or other is None or feature.kind != "face_distance":
        return result
    first = plane_coordinate(repo, version, part, meshes[part.id], axis, selector.get("face"), env)
    second = plane_coordinate(repo, version, other, meshes[other.id], axis, selector.get("other_face"), env)
    if first is None or second is None:
        return result
    actual = first - second
    expected = evaluate(feature.expected_expr, env)
    ok = (abs(actual-expected) <= feature.tolerance if feature.comparison == "eq" else
          actual + feature.tolerance >= expected if feature.comparison == "min" else actual - feature.tolerance <= expected)
    return {**result, "status": "PASS" if ok else "FAIL", "reason": None,
            "expected": expected, "actual": actual, "tolerance": feature.tolerance}


def prepare_verification(repo, version_id, candidate=None):
    version = candidate or repo.get(d.versions, version_id)
    spec, program, meshes = assembly(repo, version)
    if program is None:
        return {"scene": spec, "checks": [check("legacy-format", "UNVERIFIED", "archived v2 version")],
                "manifest": {"version_sha": version["content_sha"], "archive": version["bindings"]}}
    read = repo.get(d.reads, version["drawing_read_id"])["response"]
    figure = repo.get(d.figures, version["figure_id"])
    document = repo.get(d.documents, figure["document_id"])
    results = verify_all(spec, meshes, document["sha256"])
    checks = []
    try:
        from hashlib import sha256
        identity = bool(document["artifact_id"]) and sha256(repo.artifacts.read(document["artifact_id"])).hexdigest() == document["sha256"]
        repo.artifacts.read(figure["artifact_id"])
    except (ValueError, FileNotFoundError, KeyError):
        identity = False
    checks.append(check("source-artifacts", "PASS" if identity else "FAIL", "source PDF/crop integrity"))
    coverage = label_coverage(program, read)
    if coverage:
        checks.append(coverage)
    for result in results:
        if result.id.startswith("C-cad-"):
            continue  # v2's illustrative-only policy does not describe v3 CAD.
        required = result.id.startswith(("S-", "B-mesh-", "B-built-", "C-trace-")) or result.id == "C-source"
        entry = {**result.model_dump(mode="json"), "required": required}
        if result.id == "C-labels" and not read.get("elements"):
            entry.update(status="N/A", required=False)
        checks.append(entry)
    env = {p.name: p.value for p in program.params}
    resolutions = repo.list(d.reviews, d.reviews.c.version_id == version_id, d.reviews.c.action == "resolve")
    feature_refs = {}
    for part in program.parts:
        if part.shape == "cad":
            binding = version["bindings"].get(part.id)
            if not binding or binding.get("placeholder"):
                checks.append(check(f"cad-{part.id}", "FAIL", "placeholder: CAD missing or failed"))
                continue
            build = repo.get(d.component_builds, binding["build_id"])
            component_code = repo.get(d.components,build["component_id"])["code"]
            try:
                check_component(component_code)
                checks.append(check("cad-code-"+part.id,"PASS","current named-value code contract"))
            except ValueError as error:
                checks.append(check("cad-code-"+part.id,"FAIL",str(error)))
            form = object_form_check(part,build["measured"])
            if form:
                checks.append(form)
            expected_params = {name: evaluate(expression, env) for name, expression in part.param_bindings.items()}
            checks.append(check(f"cad-binding-{part.id}", "PASS" if build["params"] == expected_params else "FAIL"))
            actual_bounds = meshes[part.id].bounds()
            dims = {value.name:evaluate(value.expr, env) for value in part.dims}
            expected_bounds = [dims[axis+side] for side in ("0","1") for axis in ("x","y","z")]
            envelope_ok = bool(actual_bounds) and all(abs(a-b) <= .1 for a,b in zip(actual_bounds, expected_bounds))
            checks.append(check(f"cad-envelope-{part.id}", "PASS" if envelope_ok else "FAIL",
                                actual=actual_bounds, expected=expected_bounds))
            measured = build["checks"]
        else:
            bounds = meshes[part.id].bounds()
            if bounds is None:
                measured = []
            else:
                # Native-to-CadQuery bounds for the common measurement evaluator.
                native = [bounds[0], -bounds[5], bounds[1], bounds[3], -bounds[2], bounds[4]]
                _, measured = measure_features({"bounds": native, "faces": []},
                                 [f.model_dump(mode="json") for f in part.features], env)
        cross_features = {f.id: f for f in part.features if f.selector.get("other_part")}
        measured = [m for m in measured if m["id"] not in cross_features]
        measured += [cross_part_feature(repo, version, program, meshes, part, f, env) for f in cross_features.values()]
        for feature in part.features:
            if feature.kind == "slope_ratio" and part.shape == "wedge" and feature.selector.get("axis") == "x":
                mesh = meshes[part.id]
                bounds = mesh.bounds()
                if bounds and bounds[3] > bounds[0]:
                    points = [mesh.positions[i:i+3] for i in range(0, len(mesh.positions), 3)]
                    left = max(p[1] for p in points if abs(p[0]-bounds[0]) < .0001)
                    right = max(p[1] for p in points if abs(p[0]-bounds[3]) < .0001)
                    actual = (left-right) / (bounds[3]-bounds[0])
                    expected = evaluate(feature.expected_expr, env)
                    ok = (abs(actual-expected) <= feature.tolerance if feature.comparison == "eq" else
                          actual + feature.tolerance >= expected if feature.comparison == "min" else actual - feature.tolerance <= expected)
                    measured = [m for m in measured if m["id"] != feature.id]
                    measured.append(check(feature.id, "PASS" if ok else "FAIL", actual=actual, expected=expected,
                                          tolerance=feature.tolerance, source_dimension_id=feature.source_dimension_id))
        for result in measured:
            checks.append({**result, "id": f"feature-{part.id}-{result['id']}"})
            if result.get("source_dimension_id"):
                feature_refs.setdefault(result["source_dimension_id"], []).append((part, result))
    for i, dimension in enumerate(read.get("dimensions", [])):
        ident = dimension.get("id", f"d{i}")
        features = feature_refs.get(ident, [])
        quoted = " ".join(dimension["quote_verbatim"].lower().split())
        source_params = [p for p in program.params if p.source_dimension_id == ident and p.provenance.value == "drawing"
                         and abs(p.value-dimension["value_mm"]) <= 1e-6
                         and p.source_quote and " ".join(p.source_quote.lower().split()) in quoted]
        minimum = bool(re.search(r"\bmin(?:imum)?\b|at least|not less than", quoted))
        maximum = bool(re.search(r"\bmax(?:imum)?\b|at most|not more than", quoted))
        printed_kind = "min" if minimum and not maximum else "max" if maximum and not minimum else "eq"
        status, reason = "UNVERIFIED", "source dimension must identify one measured feature"
        if len(features) == 1 and source_params:
            part, measurement = features[0]
            feature = next(f for f in part.features if f.id == measurement["id"])
            relevant_parts = [part]
            other = feature.selector.get("other_part")
            if other:
                relevant_parts += [p for p in program.parts if p.id == other]
            geometry_names = set()
            for item in relevant_parts:
                expressions = [dimension.expr for dimension in item.dims] + list(item.param_bindings.values()) + list(item.placement.values())
                expressions += [coordinate for point in item.path for coordinate in (point.x, point.y, point.z) if coordinate]
                for expression in expressions:
                    geometry_names |= expr_names(expression)
            backed = not (minimum and maximum) and feature.comparison == printed_kind and any(
                         p.name in expr_names(feature.expected_expr) and p.name in geometry_names and
                         feature.unit == p.unit and abs(evaluate(feature.expected_expr, env) - p.value) <= 1e-6 and
                         (p.kind not in ("min", "max") or feature.comparison == p.kind) for p in source_params)
            if backed:
                status, reason = measurement["status"], measurement.get("reason")
        checks.append(check(f"source-dimension-{ident}", status, reason))
    # Keep obligations from the whole ancestry: a second patch cannot launder
    # evidence or interfaces removed by the first patch.
    ancestors = []
    parent_id = version["parent_version_id"]
    while parent_id:
        parent = repo.get(d.versions, parent_id)
        ancestors.append(parent)
        parent_id = parent["parent_version_id"]
    if ancestors:
        inherited_resolutions = list(resolutions)
        for ancestor in ancestors:
            inherited_resolutions += repo.list(d.reviews, d.reviews.c.version_id == ancestor["id"], d.reviews.c.action == "resolve")
        accepted = {ident for review in inherited_resolutions if (review["data"] or {}).get("reason")
                    for ident in review["data"].get("requirement_ids", [])}
        old_programs = [v["program"] for v in ancestors]
        parent = {"program": {"params": [p for old in old_programs for p in old.get("params", [])],
                              "parts": [p for old in old_programs for p in old.get("parts", [])]}}
        old_params = {p.get("source_dimension_id") or p["name"] for p in parent["program"].get("params", []) if p["provenance"] == "drawing"}
        new_params = {p.source_dimension_id or p.name for p in program.params if p.provenance.value == "drawing"}
        old_labels = {p["label"] for p in parent["program"].get("parts", []) if p.get("label")}
        new_labels = {p.label for p in program.parts if p.label}
        loss = sorted(({"source:" + p for p in old_params - new_params} |
                       {"label:" + p for p in old_labels - new_labels}) - accepted)
        checks.append(check("evidence-retention", "FAIL" if loss else "PASS", lost=loss))
        # Printed evidence survives every draft. An AI draft's proposed
        # interfaces may be repaired; human-accepted/corrected requirements
        # remain protected until a reviewer explicitly resolves them.
        protected_ids = {review["version_id"] for review in inherited_resolutions}
        for ancestor in ancestors:
            if repo.list(d.reviews,d.reviews.c.version_id == ancestor["id"],d.reviews.c.action.in_(["accept","correct"])):
                protected_ids.add(ancestor["id"])
        protected = [ancestor["program"] for ancestor in ancestors if ancestor["id"] in protected_ids]
        old_parts = {p["id"]:p for old in protected for p in old.get("parts",[]) if p.get("role") == "covered_layer"}
        new_parts = {p.id:p for p in program.parts}
        changes = []
        covered = [part for part in program.parts if part.role == "covered_layer"]
        for ident, old in old_parts.items():
            # A protected covered layer survives if a covered layer still carries
            # its drawing label, or shares a material word with its type.
            if not any((old.get("label") and part.label == old["label"]) or
                       material_tokens(old["type"]) & material_tokens(part.type) for part in covered):
                changes.append("role:"+ident)
        new_relations = {(r.source,r.target,r.kind):r for r in program.relations}
        for relation in [r for old in protected for r in old.get("relations", [])]:
            key = (relation["source"],relation["target"],relation["kind"])
            # Splitting/renaming a layer can repair an unsupported interface.
            # The material and source evidence must survive, and every new
            # interface is independently checked below.
            if key[0] not in new_parts or key[1] not in new_parts:
                continue
            current = new_relations.get(key)
            identifier = "relation:"+":".join(key)
            if current is None:
                changes.append(identifier)
                continue
            old = relation.get("constraints", {})
            new = current.constraints
            weakened = any(k in old and new.get(k) != old[k] for k in ("axis","source_face","target_face"))
            weakened |= any(k in old and (k not in new or new[k] < old[k]) for k in ("min_gap","min_coverage"))
            weakened |= "max_gap" in old and ("max_gap" not in new or new["max_gap"] > old["max_gap"])
            if weakened:
                changes.append(identifier)
        unresolved = sorted(set(changes) - accepted)
        checks.append(check("assembly-requirements-retained", "FAIL" if unresolved else "PASS", changed=unresolved))
    exposed = {part for review in resolutions if (review["data"] or {}).get("reason")
               for part in review["data"].get("exposed_parts", [])}
    cad_planes = {}
    for part in program.parts:
        binding = version["bindings"].get(part.id,{})
        if part.shape == "cad" and binding.get("build_id"):
            build = repo.get(d.component_builds,binding["build_id"])
            offset = [evaluate(part.placement.get(axis,"0"),env) for axis in ("x","y","z")]
            cad_planes[part.id] = [([face["normal"][0],face["normal"][2],-face["normal"][1]],
                [face["center"][0]+offset[0],face["center"][2]+offset[1],-face["center"][1]+offset[2]])
                for face in build["measured"].get("faces",[]) if face["kind"] == "PLANE"]
    checks += spatial_checks(program, meshes, exposed, cad_planes)
    checks = [{"stage": "B", "description": c["id"], **c} for c in checks]
    scene = export_scene_json(spec, meshes)
    for solid in scene["solids"]:
        if solid["id"] in spec.context.get("placeholders", {}):
            solid["placeholder"] = spec.context["placeholders"][solid["id"]]
    scene["verification"] = checks
    manifest = {"version_sha": version["content_sha"], "read_sha": sha(read), "bindings": version["bindings"],
                "meshes": {key: sha(mesh.positions) for key, mesh in meshes.items()}, "policy": VERIFIER_VERSION,
                "review_resolution_ids": sorted(review["id"] for review in resolutions)}
    return {"scene": scene, "checks": checks, "manifest": manifest}


def stage_verification(repo, version_id, candidate=None):
    result = prepare_verification(repo, version_id, candidate)
    result["manifest_sha"] = sha(result["manifest"])
    result["manifest_ref"] = repo.artifacts.put(canonical(result["manifest"]).encode(), "application/json")
    result["scene_ref"] = repo.artifacts.put(canonical(result["scene"]).encode(), "application/json")
    return result
