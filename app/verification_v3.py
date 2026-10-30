"""Verification of exact version manifests, source features and layer interfaces."""
from __future__ import annotations

import json
import re

from . import database as d
from .cad_components import measure_features, check_component
from .generate import _norm
from .geometry import Mesh, build_assembly, export_scene_json
from .interfaces import measure_interface
from .native_program import READ_ROLES, NativeProgram
from .program import evaluate, expr_names
from .repository import VERIFIER_VERSION, canonical, inferred_part_ids, outcome_tier, sha
from .spec import DetailSpec
from .verification_probes import CONTACT_KINDS, layer_order_probes, line_hits, lines_through, probe_points, shared_length, solid_spans
from .verify import CONTACT_TOL_MM, verify_all
from .versioning import archive_scene


def check(ident, status, reason=None, required=True, **values):
    return dict(id=ident, stage="C" if ident.startswith("source-") else "B", description=reason or ident,
                status=status, reason=reason, required=required, **values)


# The pass marks of a relation. They belong to the verifier: a relation may
# declare a stricter min_coverage, and nothing it declares lowers these. Measured
# 30 Oct on all 32 versions of a real run and on both seeds:
# - Contact, as a share of the SMALLER of the two faces, so it reads the same
#   whichever part is the source and whichever contact kind is named. True contacts were
#   0.60 or more; the false ones an agent had to lower its mark for were 0.18 or less.
#   Either single face fails true contacts: a wide plate on a small nut is 0.09 of
#   the plate, and a small object on a slab is 0.04 of the slab.
# - Covers, as a share of the target's face, because the claim is that the target's
#   face is hidden. True covers were 0.96 or more; the lowered ones 0.04 to 0.18.
CONTACT_FLOOR = 0.5
COVER_FLOOR = 0.5
# Two layers may lap with less than that, as long sheets overlap by a small part of
# their length: only layers, and only with an overlap this many times the thinner one.
# Not a construction rule, and no standard's figure: a sanity bar against a sliver,
# below the narrowest lap we have measured (16 times, one of three, all from the
# hand-modelled seeds, and all at full contact, so none needed this at all). No live
# run has ever measured a lap. A printed lap width belongs in a measured feature.
LAP_ROLES = ("covered_layer", "finish")
LAP_WIDTHS = 10


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
    """Drawing labels the READ classed as a layer, object or zone that no part
    carries. Informational: it never blocks completion, but the agent and the
    reviewer see what is missing. Older reads without categories are skipped."""
    wanted = {e["label_verbatim"] for e in read.get("elements", [])
              if e.get("label_verbatim") and e.get("category") in ("layer", "object", "zone")}
    if not wanted:
        return None
    missing = sorted(wanted - {part.label for part in program.parts if part.label})
    return check("label-coverage", "UNVERIFIED" if missing else "PASS",
                 "Every drawing label for a layer, object or zone is carried by a part", required=False,
                 missing=missing)


def label_check(read):
    """What is known about the READ's labels being verbatim. PASS only when the
    drawing's own text was checked; otherwise it says what they rest on."""
    if not read.get("elements"):
        return None
    record = read.get("label_check") or {"method": "none", "corrected": [], "unconfirmed": []}
    confirmed = record["method"] == "text_layer" and not record["unconfirmed"]
    reasons = {"text_layer": "some labels were not found in the drawing's text layer",
               "proofread": "this drawing has no text layer, so its labels are a model's reading, proofread once "
                            "at a larger size: they are not confirmed verbatim",
               "none": "these labels were not checked against the drawing's text, so they are not confirmed verbatim"}
    return check("label-verbatim", "PASS" if confirmed else "UNVERIFIED", None if confirmed else reasons[record["method"]],
                 required=False, method=record["method"], corrected=record["corrected"], unconfirmed=record["unconfirmed"])


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


def envelope_problem(actual, expected):
    """How a built solid misses its envelope, in terms of what to change."""
    if not actual:
        return "the solid has no mesh"
    span = lambda bounds: [round(bounds[i + 3] - bounds[i], 3) for i in range(3)]
    if all(abs(a - b) <= .1 for a, b in zip(span(actual), span(expected))):
        shift = dict(zip("xyz", (round(expected[i] - actual[i], 3) for i in range(3))))
        return (f"the solid is the right size but sits off its envelope: add {shift} mm to the part's placement "
                "with patch_program; the solid itself is fine, so do not remodel it")
    return (f"the solid measures {span(actual)} mm along x,y,z and its envelope is {span(expected)} mm: fix the CAD "
            "code or its param_bindings")


def part_thickness(part, mesh, axis, env):
    """A sheet's declared thickness; any other part's built extent along the
    interface axis, the direction its gap is measured in."""
    if part.shape == "sheet":
        return evaluate(part.dim("thickness"), env)
    bounds = mesh.bounds() if mesh else None
    return bounds[axis + 3] - bounds[axis] if bounds else None


def pair_tolerance(program, meshes, source, target, axis):
    """Largest accepted contact gap for a pair: min(CONTACT_TOL_MM, half the
    thinner part). A flat 2 mm band would hide a 1.5 mm membrane that is
    floating, so a relation's declared gap band is capped by this."""
    env = {p.name: p.value for p in program.params}
    by_id = {p.id: p for p in program.parts}
    known = [t for t in (part_thickness(by_id[k], meshes.get(k), axis, env) for k in (source, target)) if t]
    return min([CONTACT_TOL_MM] + [0.5 * t for t in known])


def ontology_or_error():
    """The ontology library, or the reason it cannot be read."""
    try:
        from . import ontology
        return ontology.load(), None
    except (ImportError, ValueError, OSError) as error:
        return None, f"ontology unavailable: {error}"


def ontology_version():
    ont, _ = ontology_or_error()
    return ont.version if ont else None


def slot_checks(program, read=None):
    """Completeness against the ontology: the assembly class, each part's
    class and every required slot. Keyed off class ids, declared roles and
    library data only, never part names (the engine stays trade-agnostic; the
    library may name parts). A READ label is used only for what the READ says
    of it: check_roles makes such a part take the READ's role, so a class that
    disagrees is a difference to review, not a failure the author can fix."""
    assembly_id = getattr(program, "assembly_class", None)
    classed = [(part, getattr(part, "class_id", None)) for part in program.parts]
    classed = [(part, class_id) for part, class_id in classed if class_id]
    output = []
    if assembly_id is None:
        output.append(check("assembly-class-unset", "UNVERIFIED",
                            "no assembly class declared, so required slots are not checked", required=False))
        if not classed:
            return output
    ont, problem = ontology_or_error()
    if ont is None:
        # A claim nobody can read against the library is not verified, and it blocks completion.
        if assembly_id is not None:
            output.append(check("assembly-class-unknown", "UNVERIFIED", problem, assembly_class=assembly_id))
        output += [check(f"class-unknown:{part.id}", "UNVERIFIED", problem, class_id=class_id) for part, class_id in classed]
        return output
    categories = {_norm(e["label_verbatim"]): e.get("category") for e in (read or {}).get("elements", [])
                  if e.get("label_verbatim")}
    for part, class_id in classed:
        component = ont.component(class_id)
        if component is None:
            output.append(check(f"class-unknown:{part.id}", "FAIL", f"{class_id!r} is not an ontology component class",
                                class_id=class_id))
        elif component.role != part.role:
            category = categories.get(_norm(part.label or ""))
            allowed = READ_ROLES.get(category, ())
            if part.role in allowed and component.role not in allowed:
                output.append(check(f"class-role:{part.id}", "UNVERIFIED",
                                    f"the drawing reading classifies {part.label!r} as {category}, which sets role "
                                    f"{part.role!r}; class {class_id!r} has role {component.role!r}. The drawing's role "
                                    "stands; a reviewer settles the difference", required=False,
                                    class_id=class_id, class_role=component.role, declared_role=part.role))
            else:
                output.append(check(f"class-role:{part.id}", "FAIL",
                                    f"class {class_id!r} has role {component.role!r}; the part declares {part.role!r}",
                                    class_id=class_id, class_role=component.role, declared_role=part.role))
    if assembly_id is None:
        return output
    if ont.assembly(assembly_id) is None:
        output.append(check("assembly-class-unknown", "FAIL", f"{assembly_id!r} is not an ontology assembly class",
                            assembly_class=assembly_id))
        return output
    for slot in ont.required_slots(assembly_id):
        fillers = [part for part, class_id in classed if class_id in slot.classes]
        ids = [part.id for part in fillers]
        if len(fillers) < slot.min:
            output.append(check(f"slot-missing:{slot.id}", "FAIL",
                                f"required slot {slot.id!r} needs {slot.min} of {', '.join(slot.classes)}; found {len(fillers)}",
                                slot=slot.id, classes=list(slot.classes), min=slot.min, found=ids,
                                section_visibility=slot.section_visibility))
        elif all(getattr(part, "provenance", "drawing") == "inferred" for part in fillers):
            output.append(check(f"slot-inferred:{slot.id}", "UNVERIFIED",
                                f"required slot {slot.id!r} is filled only by inferred parts", required=False,
                                slot=slot.id, parts=ids))
    return output


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


def is_lap(program, relation, meshes, axis):
    """Whether two layers lap across `axis`: the one case where a small share of a face
    is a sound contact. In a section every part runs the same length along the sill, so
    offset ends prove nothing by themselves: both must be layers by role, each must run
    past the other's end, and the overlap must be wide for their thickness."""
    by_id = {p.id: p for p in program.parts}
    source, target = by_id[relation.source], by_id[relation.target]
    a, b = meshes[source.id].bounds(), meshes[target.id].bounds()
    if source.role not in LAP_ROLES or target.role not in LAP_ROLES or not a or not b:
        return False
    env = {p.name: p.value for p in program.params}
    thinner = min(part_thickness(part, meshes[part.id], axis, env) for part in (source, target))
    i, j = [k for k in range(3) if k != axis]
    for along, across in ((i, j), (j, i)):
        past = (a[along] < b[along] - 1e-6 and a[along + 3] < b[along + 3] - 1e-6) or (
                a[along] > b[along] + 1e-6 and a[along + 3] > b[along + 3] + 1e-6)
        narrower = min(a[across + 3] - a[across], b[across + 3] - b[across])
        spanned = min(a[across + 3], b[across + 3]) - max(a[across], b[across])
        width = min(a[along + 3], b[along + 3]) - max(a[along], b[along])
        if past and width >= LAP_WIDTHS * thinner and narrower > 0 and spanned / narrower >= CONTACT_FLOOR:
            return True
    return False


def relation_problem(relation, measured, low, high, declared, ordering, lap):
    """Why a measured relation does not pass, or None."""
    minimum, maximum = measured.get("min_gap", measured.get("gap")), measured.get("max_gap", measured.get("gap"))
    if minimum is None or maximum is None:
        return "the two faces do not face each other anywhere: name the faces that really meet"
    if minimum < low - 1e-6 or maximum > high + 1e-6:
        return f"the faces are {minimum:.4g} to {maximum:.4g} mm apart; the accepted gap is {low:g} to {high:g} mm"
    if measured["contact"] + 1e-6 < CONTACT_FLOOR and not lap:
        return (f"only {measured['contact']:.0%} of the smaller face touches; the verifier requires {CONTACT_FLOOR:.0%} for every "
                "kind, and no declared value lowers it. Fix the geometry, or name the faces that really meet")
    if relation.kind == "covers" and measured["coverage"] + 1e-6 < COVER_FLOOR:
        return (f"covers hides only {measured['coverage']:.1%} of the target's face; the verifier requires {COVER_FLOOR:.0%}, and "
                "no declared value lowers it. A part that rests on, stops at or is sealed to another is supported_by, "
                "terminates_against or sealed_to; only what really hides a face covers it")
    if measured["coverage"] + 1e-6 < declared:
        return f"coverage {measured['coverage']:.0%} is under the {declared:.0%} this relation declares"
    if not ordering and relation.kind != "abuts":
        return f"{relation.target} must be installed before {relation.source}"
    return None


def fixing_depth(program, meshes, relation):
    """(mm of solid a fixing shares with its target along its own length, the mm it
    must beat). Measured on both solids, never on the ends of a span: a part resting on
    a surface shares none of it. The axis is the fixing's own, whatever a relation
    declares, and the two must overlap by more than the contact tolerance every way."""
    source, target = meshes[relation.source], meshes[relation.target]
    a, b = source.bounds(), target.bounds()
    tolerances = [pair_tolerance(program, meshes, relation.source, relation.target, k) for k in range(3)]
    overlaps = [min(a[k + 3], b[k + 3]) - max(a[k], b[k]) for k in range(3)]
    spans = [a[k + 3] - a[k] for k in range(3)]
    axis = spans.index(max(spans))
    if any(overlap <= tolerance for overlap, tolerance in zip(overlaps, tolerances)):
        return 0., tolerances[axis]
    i, j = [k for k in range(3) if k != axis]
    points = lines_through(source, [max(a[k], b[k]) for k in (i, j)], [min(a[k + 3], b[k + 3]) for k in (i, j)], axis)
    depths = [shared_length(solid_spans(first), solid_spans(second))
              for first, second in zip(line_hits(source, axis, points), line_hits(target, axis, points))]
    return max(depths, default=0.), tolerances[axis]


def held_share(a, target):
    """Of lines through a part's box, along the axis that does best, the share that find
    the target's solid beyond both of the part's ends. That is an opening; an envelope
    is not one: a part floating in the crook of a bent plate is inside its box."""
    best = 0.
    for axis in range(3):
        points = probe_points(a, a, axis)
        if not points:
            continue
        spans = [solid_spans(line) for line in line_hits(target, axis, points)]
        held = sum(any(low < a[axis] - 1e-6 for low, _ in line) and any(high > a[axis + 3] + 1e-6 for _, high in line)
                   for line in spans)
        best = max(best, held / len(points))
    return best


def solid_relation(ident, relation, program, meshes, ordering):
    """fixed_to and hosted_in join solids, not faces, so each has its own measure and
    neither can stand in for a contact: a part resting on or beside another fails both."""
    if relation.kind == "fixed_to":
        depth, needed = fixing_depth(program, meshes, relation)
        measured, ok = dict(embedded=depth, contact=min(depth / needed, 1.) if needed else 0.), depth > needed
        problem = (f"{relation.source} runs {depth:.3g} mm inside {relation.target}, and a fixing must pass into what it fixes by "
                   f"more than {needed:g} mm, whatever axis is declared. A part resting on or against another is a contact "
                   "kind (supported_by, bonded_to, terminates_against), not fixed_to")
    else:
        share = held_share(meshes[relation.source].bounds(), meshes[relation.target])
        measured, ok = dict(contact=share), share + 1e-6 >= CONTACT_FLOOR
        problem = (f"{relation.target} has solid on both sides of {relation.source} along {share:.0%} of the lines through it, and "
                   f"hosted_in needs {CONTACT_FLOOR:.0%}: inside an envelope is not inside an opening. A part resting on or "
                   "against another is a contact kind (supported_by, bonded_to, terminates_against), not hosted_in")
    if ok and not ordering:
        ok, problem = False, f"{relation.target} must be installed before {relation.source}"
    return check(ident, "PASS" if ok else "FAIL", None if ok else problem, **measured, order_valid=ordering)


def facing_levels(part, mesh, planes, axis, side):
    """Where along `axis` a part can meet another on `side`: a measured solid's flat
    faces looking that way, or an exact primitive's outer face."""
    if part.shape != "cad":
        bounds = mesh.bounds()
        return [bounds[axis + (3 if side == "max" else 0)]] if bounds else []
    sign = 1 if side == "max" else -1
    levels = sorted({center[axis] for normal, center in planes if normal[axis] * sign > 0.999999})
    if not levels:
        raise ValueError("selected CAD interface is not an independently measured B-Rep plane")
    return levels


def meeting_interface(relation, parts, meshes, cad_planes, axis, settings, tolerance):
    """Measure two parts where they really meet. A bent plate does not rest on its
    lowest point, so a shaped object's interface is not its outermost plane: of the
    flat faces the two turn to each other, it is the pair within the contact tolerance
    with the most contact, or else the nearest pair, whose gap will show. The verifier
    picks the planes: the author names the sides, and the kind plays no part in it."""
    source, target = parts[relation.source], parts[relation.target]
    planes = cad_planes or {}
    below = facing_levels(source, meshes[source.id], planes.get(source.id, []), axis, settings["source_face"])
    above = facing_levels(target, meshes[target.id], planes.get(target.id, []), axis, settings["target_face"])
    pairs = sorted(((abs(a - b), a, b) for a in below for b in above), key=lambda pair: pair[0])
    close = [pair for pair in pairs if pair[0] <= tolerance + 1e-6] or pairs[:1]
    def measured(pair):
        return measure_interface(meshes[source.id], meshes[target.id], axis, settings["source_face"], settings["target_face"],
                                 relation.kind in ("covers", "laps_over"),
                                 source_level=pair[1] if source.shape == "cad" else None,
                                 target_level=pair[2] if target.shape == "cad" else None)
    return max((measured(pair) for pair in close), key=lambda result: result["contact"])


def largest_face(mesh):
    """The area of a part's broadest bounding face: the one a layer is covered on."""
    bounds = mesh.bounds() if mesh else None
    if not bounds:
        return 0.
    spans = sorted(bounds[k + 3] - bounds[k] for k in range(3))
    return spans[1] * spans[2]


def first_draft_layers(program):
    """Parts a first draft declared covered layers, which a later draft cannot rename
    out of their obligation. A role its own class contradicts was never a claim about
    the drawing: seen live, a sealant was covered_layer for one draft by mistake."""
    known, _ = ontology_or_error()
    def agrees(part):
        component = known.component(part["class_id"]) if known and part.get("class_id") else None
        return component is None or component.role == "covered_layer"
    return {part["id"] for part in program.get("parts", []) if part.get("role") == "covered_layer" and agrees(part)}


def spatial_checks(program, meshes, exposed=None, cad_planes=None, covered_before=()):
    """`covered_before`: parts the first draft declared covered layers, which owe the
    same evidence whatever role they carry now."""
    output, covering, anchored = [], {}, set()
    by_id = {p.id: p for p in program.parts}
    for i, relation in enumerate(program.relations):
        ident = f"assembly-{i}-{relation.kind}"
        if relation.kind == "installed_before":
            output.append(check(ident, "PASS" if _dependencies(program, relation.source, relation.target) else "FAIL"))
            continue
        if any(getattr(by_id[k], "provenance", "drawing") == "inferred" for k in (relation.source, relation.target)):
            # Where a placeholder sits is an assumption, so how it meets a part is
            # no evidence either way: never a PASS, and no FAIL against a shape
            # the archetype cannot know. It is measured once a reviewer confirms it.
            output.append(check(ident, "UNVERIFIED", "an inferred part's interface is a placeholder: not measured "
                                "until a reviewer confirms the part", required=False))
            continue
        if relation.kind == "repeated_along":
            # Not a way out of a measurement: it stays open, so it cannot stand in for one.
            output.append(check(ident, "UNVERIFIED", "repeated_along cannot be measured from one modelled instance; give the "
                                "spacing as a printed dimension or a note, and measure how the part meets its host"))
            continue
        ordering = _dependencies(program, relation.target, relation.source)
        if relation.kind not in CONTACT_KINDS:
            built = [k for k in (relation.source, relation.target) if by_id[k].shape != "cad" or k in (cad_planes or {})]
            a, b = meshes[relation.source].bounds(), meshes[relation.target].bounds()
            if len(built) < 2 or a is None or b is None:
                output.append(check(ident, "UNVERIFIED", f"{relation.kind} is measured once both parts are built"))
                continue
            output.append(solid_relation(ident, relation, program, meshes, ordering))
            anchored |= {relation.source, relation.target}
            continue
        settings = relation.constraints
        required_fields = {"axis", "source_face", "target_face", "min_gap", "max_gap"}
        if not required_fields <= settings.keys():
            output.append(check(ident, "UNVERIFIED", "missing explicit interface constraints"))
            continue
        axis = {"x": 0, "y": 1, "z": 2}.get(settings["axis"])
        if axis is None or settings["source_face"] not in ("min", "max") or settings["target_face"] not in ("min", "max"):
            output.append(check(ident, "FAIL", "invalid interface selector"))
            continue
        if settings["source_face"] == settings["target_face"]:
            # Two faces looking the same way are flush, not touching: every end of an
            # extruded section is coplanar, so this would measure nothing at all.
            output.append(check(ident, "FAIL", f"both faces are {settings['source_face']} faces along {settings['axis']}, so they "
                                "look the same way and cannot meet: a contact joins a max face to a min face"))
            continue
        # The declared gap band may be no wider than the pair's contact tolerance.
        tolerance = pair_tolerance(program, meshes, relation.source, relation.target, axis)
        min_gap, max_gap = max(settings["min_gap"], -tolerance), min(settings["max_gap"], tolerance)
        band = dict(contact_tolerance=tolerance, gap_band=[min_gap, max_gap])
        declared = settings.get("min_coverage") or 0
        a, b = meshes[relation.source].bounds(), meshes[relation.target].bounds()
        if any(by_id[k].shape != "box" for k in (relation.source, relation.target)):
            if any(by_id[k].shape not in ("box","wedge","sheet","profile","cad") for k in (relation.source,relation.target)):
                output.append(check(ident,"UNVERIFIED","interface requires supported planar primitives"))
                continue
            try:
                measurement = meeting_interface(relation, by_id, meshes, cad_planes, axis, settings, tolerance)
            except ValueError as error:
                output.append(check(ident,"UNVERIFIED",str(error)))
                continue
        else:
            if a is None or b is None:
                output.append(check(ident, "FAIL", "missing interface geometry"))
                continue
            af = a[axis + (3 if settings["source_face"] == "max" else 0)]
            bf = b[axis + (3 if settings["target_face"] == "max" else 0)]
            direction = 1 if settings["source_face"] == "min" else -1
            overlap, source_area, target_area = 1.0, 1.0, 1.0
            for j in [j for j in range(3) if j != axis]:
                source_area *= max(0, a[j + 3] - a[j])
                target_area *= max(0, b[j + 3] - b[j])
                overlap *= max(0, min(a[j + 3], b[j + 3]) - max(a[j], b[j]))
            base = target_area if relation.kind in ("covers", "laps_over") else source_area
            smaller = min(source_area, target_area)
            measurement = {"gap": (af - bf) * direction, "coverage": overlap / base if base else 0,
                           "contact": overlap / smaller if smaller else 0, "overlap": overlap}
        lap = relation.kind == "laps_over" and is_lap(program, relation, meshes, axis)
        problem = relation_problem(relation, measurement, min_gap, max_gap, declared, ordering, lap)
        result = check(ident, "FAIL" if problem else "PASS", problem, **measurement, order_valid=ordering, **band)
        output.append(result)
        anchored |= {relation.source, relation.target}
        if not problem:
            lying = covering.setdefault((relation.target, axis, settings["target_face"]), {})
            lying[relation.source] = max(lying.get(relation.source, 0), measurement["overlap"])
    # What hides a layer is whatever really touches its face from outside, added up
    # part by part. The kinds named play no part in it, so renaming a relation
    # cannot cover a layer, and neither can a sliver that touches it.
    for part in program.parts:
        if part.role != "covered_layer" and part.id not in covered_before:
            continue
        faces = [sum(lying.values()) for key, lying in covering.items() if key[0] == part.id]
        broadest = largest_face(meshes.get(part.id))
        share = min(max(faces, default=0) / broadest, 1) if broadest else 0
        related = any(r.target == part.id and r.kind in CONTACT_KINDS for r in program.relations)
        if part.id in (exposed or set()):
            output.append(check(f"coverage-{part.id}", "N/A", "reviewer accepted an exposed layer", required=False))
        elif share + 1e-6 >= COVER_FLOOR:
            output.append(check(f"coverage-{part.id}", "PASS", covered_share=share))
        elif related:
            output.append(check(f"coverage-{part.id}", "UNVERIFIED", f"what lies on this layer hides {share:.1%} of its broad "
                                f"face; the verifier requires {COVER_FLOOR:.0%} before it counts as covered", covered_share=share))
        else:
            output.append(check(f"coverage-{part.id}", "UNVERIFIED", "covered layer has no covering relationship"))
    # A relation that clears a FAIL by going away leaves its part measured against
    # nothing: that stays open, so removing a relation is no way past its pass mark.
    physical = [p for p in program.parts if p.role != "zone" and p.shape != "annotation"
                and getattr(p, "provenance", "drawing") != "inferred"]
    if len(physical) > 1:
        for part in physical:
            if part.id not in anchored:
                output.append(check(f"anchored-{part.id}", "UNVERIFIED", f"{part.id} is measured against no other part: "
                                    "give it a relation to what holds it, on the faces that really meet"))
    return output


def sheet_thickness_claim(part, mesh, feature, measured, env):
    """An extent reads a part's whole span, so on a bent sheet it reads the bends: seen
    live, 18.8 mm against a 2 mm membrane. A claim of the sheet's own thickness is measured
    through the sheet instead, across a straight run. Any other extent stands as it is."""
    axis = {"x": 0, "y": 1}.get(feature.selector.get("axis"))
    thickness = evaluate(part.dim("thickness"), env)
    if feature.kind != "extent" or axis is None or abs(measured["expected"] - thickness) > 1e-6:
        return measured
    along = 1 - axis
    points = [[evaluate(point.x or "0", env), evaluate(point.y or "0", env)] for point in part.path]
    runs = [(a, b) for a, b in zip(points, points[1:]) if abs(a[axis] - b[axis]) < 1e-6 and abs(a[along] - b[along]) > 1e-6]
    bounds = mesh.bounds()
    if not runs or not bounds:
        return {**measured, "status": "UNVERIFIED", "actual": None, "reason": "this sheet has no straight run across that axis to measure through"}
    start, end = max(runs, key=lambda run: abs(run[0][along] - run[1][along]))
    spans = solid_spans(line_hits(mesh, axis, [((start[along] + end[along]) / 2, (bounds[2] + bounds[5]) / 2)])[0])
    through = [high - low for low, high in spans if low - 1e-6 <= start[axis] <= high + 1e-6]
    if not through:
        return {**measured, "status": "UNVERIFIED", "actual": None, "reason": "no line through this sheet's straight run met it"}
    return {**measured, "actual": through[0], "reason": None,
            "status": "PASS" if abs(through[0] - measured["expected"]) <= measured["tolerance"] else "FAIL"}


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
        legacy = [check("legacy-format", "UNVERIFIED", "archived v2 version")]
        return {"scene": spec, "checks": legacy,
                "manifest": {"version_sha": version["content_sha"], "archive": version["bindings"]},
                "quality": {"outcome": outcome_tier(legacy, []), "inferred_parts": []}}
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
    verbatim = label_check(read)
    if verbatim:
        checks.append(verbatim)
    for result in results:
        if result.id.startswith("C-cad-"):
            continue  # v2's illustrative-only policy does not describe v3 CAD.
        required = result.id.startswith(("S-", "B-mesh-", "B-built-", "C-trace-")) or result.id == "C-source"
        entry = {**result.model_dump(mode="json"), "required": required}
        if result.id == "C-labels" and not read.get("elements"):
            entry.update(status="N/A", required=False)
        elif result.id == "C-labels" and verbatim["status"] != "PASS":
            entry.update(status="UNVERIFIED", description=result.description.replace("exact drawing labels", "labels as read from the drawing"),
                         evidence="see label-verbatim: these labels are not confirmed from the drawing's text")
        checks.append(entry)
    env = {p.name: p.value for p in program.params}
    resolutions = repo.list(d.reviews, d.reviews.c.version_id == version_id, d.reviews.c.action == "resolve")
    feature_refs = {}
    for part in program.parts:
        if part.shape == "cad":
            binding = version["bindings"].get(part.id)
            if not binding or binding.get("placeholder"):
                failure = (binding or {}).get("placeholder")
                checks.append(check(f"cad-{part.id}", "FAIL", "placeholder: CAD missing or failed" +
                                    (f": {failure}" if failure else "")))
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
                                None if envelope_ok else envelope_problem(actual_bounds, expected_bounds),
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
                if part.shape == "sheet":
                    measured = [sheet_thickness_claim(part, meshes[part.id], feature, result, env)
                                for feature, result in zip(part.features, measured)]
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
        if getattr(part, "provenance", "drawing") == "inferred":
            # An inferred part fills a slot; its sizes are nobody's evidence
            # until a reviewer confirms it, so a measured match is not a PASS.
            measured = [{**m, "status": "UNVERIFIED", "required": False,
                         "reason": "inferred part: dimensions are not verified until a reviewer confirms it"}
                        if m["status"] == "PASS" else m for m in measured]
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
        if len(features) > 1:
            reason += "; claimed by " + ", ".join(f"{part.id}.{result['id']}" for part, result in features)
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
    covered_before = first_draft_layers(ancestors[-1]["program"] if ancestors else {}) & {p.id for p in program.parts}
    checks += spatial_checks(program, meshes, exposed, cad_planes, covered_before)
    for probe in layer_order_probes(program, meshes):
        checks.append(check(probe.pop("id"), probe.pop("status"), probe.pop("reason", None), **probe))
    checks += slot_checks(program, read)
    checks = [{"stage": "B", "description": c["id"], **c} for c in checks]
    # Completeness tier (repository.outcome_tier): stored in the verification's
    # quality dict and shown on the scene; an inferred part never makes it verified.
    inferred = inferred_part_ids(program.parts)
    quality = {"outcome": outcome_tier(checks, inferred), "inferred_parts": inferred}
    scene = export_scene_json(spec, meshes)
    for solid in scene["solids"]:
        if solid["id"] in spec.context.get("placeholders", {}):
            solid["placeholder"] = spec.context["placeholders"][solid["id"]]
    scene["verification"] = checks
    scene["quality"] = quality
    manifest = {"version_sha": version["content_sha"], "read_sha": sha(read), "bindings": version["bindings"],
                "meshes": {key: sha(mesh.positions) for key, mesh in meshes.items()}, "policy": VERIFIER_VERSION,
                "ontology": ontology_version(),
                "review_resolution_ids": sorted(review["id"] for review in resolutions)}
    return {"scene": scene, "checks": checks, "manifest": manifest, "quality": quality}


def stage_verification(repo, version_id, candidate=None):
    result = prepare_verification(repo, version_id, candidate)
    result["manifest_sha"] = sha(result["manifest"])
    result["manifest_ref"] = repo.artifacts.put(canonical(result["manifest"]).encode(), "application/json")
    result["scene_ref"] = repo.artifacts.put(canonical(result["scene"]).encode(), "application/json")
    return result
