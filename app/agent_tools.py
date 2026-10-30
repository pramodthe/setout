"""Agent tools prepare outside transactions and commit effects with their result."""
from __future__ import annotations

import copy
import json
import io
import os
import re
from collections import Counter

import sqlalchemy as sa

from . import cad_components, cad_sandbox, close_up, completion, database as d, generate, meta_client, ontology, sam, store
from .model_gateway import ModelGateway, ProviderRejected
from .native_program import kept_bindings, NativeProgram, prepare, expression_unit
from .program import evaluate, expr_names
from .program_edits import apply_edits
from .program_schema import program_schema, from_wire
from .repository import (REPAIR_REFUSALS, BudgetExceeded, Conflict, LostLease, VERIFIER_VERSION, canonical, repair_room,
                         row, sha, uid, version_content)
from .verification_v3 import ontology_or_error, stage_verification


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


STRING = {"type": "string"}
VERSION = {"version_id": STRING}
ISSUE_SCHEMA = obj({"id": STRING, "parts": {"type": "array", "items": STRING}, "reason": STRING,
                    "severity": {"type": "string", "enum": ["error", "warning"]},
                    "views": {"type": "array", "items": STRING}, "needs_human": {"type": "boolean"}})
SHAPE_CHECK_SCHEMA = obj({"close_up_shows_part": {"type": "boolean"},
                          "drawing_features": {"type": "array", "items": obj({"feature": STRING, "built": {"type": "boolean"}})},
                          "built_features": {"type": "array", "items": obj({"feature": STRING, "drawn": {"type": "boolean"}})}})
CRITIC_SCHEMA = obj({"verdict": {"type": "string", "enum": ["pass", "fail", "inconclusive"]},
                     "issues": {"type": "array", "items": ISSUE_SCHEMA},
                     "missing_parts": {"type": "array", "items": obj({"what": STRING, "where": STRING})}})
CAD_SCHEMA = obj({"code": STRING, "envelope_problem": STRING})


def json_object(value, expected_type=dict):
    """Normalize bounded JSON serialization layers without eval or text repair."""
    for _ in range(4):
        if isinstance(value, expected_type):
            return value
        if not isinstance(value, str) or len(value) > 2_000_000:
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            try:
                value = json.loads('"' + value + '"')
            except json.JSONDecodeError:
                break
    raise ValueError("program must be a JSON object or a valid serialized JSON object")


class StagedRepository:
    def __init__(self, base, builds, components=None):
        self.base, self.builds, self.components = base, builds, components or {}

    def get(self, table, ident):
        if table is d.component_builds and ident in self.builds:
            return self.builds[ident]
        if table is d.components and ident in self.components:
            return self.components[ident]
        return self.base.get(table, ident)

    def __getattr__(self, name):
        return getattr(self.base, name)


# How labels work, for every authoring call (losing them was v3's largest gap
# against v2 on the 24-drawing batch).
LABEL_RULES = ("LABELS: a part's label is exactly one READ label_verbatim, copied character for character, "
               "or null; never your own wording, a shortened callout or a label with words added. Every piece "
               "of a split layer carries the same READ label; position words (wall, floor, left, upturn) go in "
               "id, type or note. Give every READ label with category layer, object or zone a part that carries it, "
               "and every entry of the READ's unlabelled list its own part, with label null: never merge a part into "
               "its neighbour, or leave it out for having no label. ")

# The critic may not undo the world model: an inferred part fills a required
# slot the drawing cannot show, and only a reviewer decides its fate.
RETENTION_RULE = ("RETENTION: a part whose provenance is inferred fills a required slot of the assembly class that "
                  "the drawing does not show; it renders ghosted and is never counted as verified. Never propose "
                  "deleting a part that fills a required slot, and 'not in the drawing' is never a reason to delete "
                  "an inferred part. An inferred object is a plain box on purpose, so a box there is not a fidelity "
                  "error. Judge inferred parts only where their placement contradicts what the drawing shows. ")

# Call id prefixes for the work the harness runs by itself (app/agent.py).
AUTO_COMPLETION = "auto-complete-"
AUTO_CAD = "auto-cad-"


def completion_mode():
    """SETOUT_COMPLETION: on (default: the tool applies its own proposal through
    the gate, and runs by itself right after a version is accepted), suggest (the
    tool proposes, the agent applies) or off (the tool is not offered)."""
    mode = os.environ.get("SETOUT_COMPLETION", "on").strip().lower()
    return mode if mode in ("off", "suggest", "on") else "on"


def switched_on(name):
    """A harness step that defaults to on: anything but off, in the environment."""
    return os.environ.get(name, "on").strip().lower() != "off"


def compact_check(check):
    """One check as the model needs it: what is wrong and by how much."""
    return {key: value for key, value in check.items() if value is not None and key != "stage"
            and not (key == "description" and value in (check.get("id"), check.get("reason")))}


def model_checks(checks):
    """What a model reads of a verification: the tally and what is not passing.
    Checks that cannot block completion are named only, unless they list work
    to do (label-coverage). Measured on a live run, they were 62% of the check
    text, repeated every turn. Every check stays in the verification row."""
    open_checks = [c for c in checks if c["status"] not in ("PASS", "N/A")]
    actionable = [c for c in open_checks if c["status"] == "FAIL" or c.get("required") or c.get("missing")]
    return {"checks": [compact_check(c) for c in actionable],
            "informational": [c["id"] for c in open_checks if c not in actionable],
            "check_counts": dict(sorted(Counter(c["status"] for c in checks).items()))}


SAME_REFUSALS = 3  # the same refusal this many times over, with no version, ends the run
REFUSAL_CHARS = 3000  # room for a refusal listing what is wrong; far less than sending the program back


def rejected_write(error):
    """A rejected write as the model reads it. The stored result keeps the whole
    proposal, because patch_program resumes from it when no version exists yet."""
    error, proposal = dict(error), error["proposal"]
    del error["proposal"]
    if error.get("working_version_id"):
        # It came from a program the model has and edits it wrote.
        return {**error, "note": "Nothing was applied: the working version is unchanged. Send the corrected edits."}
    parts, params = proposal.get("parts", []), proposal.get("params", [])
    named = [p for p in parts if re.search(rf"\b{re.escape(p['id'])}\b", error.get("message", ""))]
    named_params = [p for p in params if re.search(rf"\b{re.escape(p['name'])}\b", error.get("message", ""))]
    if not named and not named_params:
        # write_program authors in a nested call, so the model has never seen
        # this program, and the message does not say where the fault is: the
        # whole proposal is the only way to find it.
        return {**error, "proposal": proposal}
    return {**error, "named_parts": named, "named_params": named_params, "part_ids": [p["id"] for p in parts],
            "parameter_names": [p["name"] for p in params],
            "note": "The rejected program is kept. Repair it with patch_program (/parts/@id/...), which applies to it; "
                    "do not call write_program again."}


def refused_again(steps):
    """Whether the last writes all came back with one refusal. It lists every fault, so
    if nothing changed then nothing moved, and with no version more turns only spend:
    seen live, 268k tokens on a program that was never accepted."""
    writes = [s for s in steps if s["kind"] == "tool" and s["tool_name"] in ("write_program", "patch_program")]
    errors = [(s["output"] or {}).get("error") for s in writes[-SAME_REFUSALS:]]
    messages = {e.get("message") if isinstance(e, dict) else None for e in errors}
    return len(errors) == SAME_REFUSALS and len(messages) == 1 and None not in messages


def completion_view(summary):
    """A completion without what its edits or the new version already carry."""
    view = {key: value for key, value in summary.items() if key not in ("parts", "params", "relations")}
    if summary.get("mode") == "on":
        view.pop("edits", None)
    return {**view, "inferred_parts": [p["id"] for p in summary.get("parts", [])]}


def model_view(name, output):
    """A stored tool result as the model reads it. Only this view is compact:
    the result in the trace stays whole."""
    view = dict(output)
    if "checks" in view:
        view.update(model_checks(view["checks"]))
    if view.get("component_checks"):
        view["component_checks"] = [compact_check(c) for c in view["component_checks"] if c["status"] != "PASS"]
    if isinstance(view.get("error"), dict) and "proposal" in view["error"]:
        view["error"] = rejected_write(view["error"])
    if name == "complete_assembly" and "parts" in view:
        view = completion_view(view)
    if isinstance(view.get("completion"), dict):
        view["completion"] = completion_view(view["completion"])
    unbuilt = [c["id"].removeprefix("cad-") for c in view.get("checks", []) if c["id"].startswith("cad-")
               and (c.get("reason") or "").startswith("placeholder:")]
    if unbuilt:
        view["next_step"] = "Model these objects before any more patches: " + ", ".join(unbuilt)
    if name == "critic":
        wrong = sorted({part for issue in view.get("issues", []) if issue["id"].startswith("shape-mismatch:") for part in issue["parts"]})
        absent = [issue["reason"] for issue in view.get("issues", []) if issue["id"].startswith("missing-part-")]
        steps = ([f"Add a part for each of these with patch_program, and the harness models it: {'; '.join(absent)}."] if absent else []) + (
            ["Call model_component on each of these, with the critic's words in the brief: " + ", ".join(wrong) +
             ". If the form needs more room, widen its envelope first."] if wrong else [])
        if steps:
            view["next_step"] = " ".join(steps)
    return view


def auto_cad_view(output, final):
    """A CAD step the harness ran, for the next turn. The checks of each step would
    repeat, so only the last of a chain carries them."""
    view = model_view("model_component", output)
    if final:
        return view
    return {key: view[key] for key in ("version_id", "component_id", "component_checks", "placeholder", "envelope_problem", "error")
            if view.get(key)}


def slot_brief(assembly_class):
    """The required slots of an assembly class, for the critic and the agent."""
    known = ontology.load()
    if not assembly_class or known.assembly(assembly_class) is None:
        return "No assembly class is set, so no required slots apply. "
    return (f"Assembly class {assembly_class} requires these slots: " + "; ".join(
        f"{s.id} (class {' or '.join(s.classes)}, min {s.min}, {s.section_visibility} in a section)"
        for s in known.required_slots(assembly_class)) + ". ")


# How the CAD writer is told to read what it is shown (app/close_up.py).
CLOSE_UP_HELP = ("The first image is a close-up of this part, cut from the drawing and enlarged. The second is the whole drawing: it shows "
                 "how the part sits among the others, and where it runs on if the close-up cuts it off. ")
WHOLE_DRAWING_HELP = "The source drawing is attached: find this part in it. "
SHAPE_RULES = ("Read the form from the drawing, not from the brief: the brief was written without looking this closely, so "
               "use it for the part's purpose and never for its form. Count every fin, leg, flange, foot, bend, return, opening and hole, and "
               "model each one, on the side and in the order the drawing shows them, not a bounding box or a simplified channel. "
               "A section shows a cut through the part: model exactly that cross-section. Trace its outline point by point as closed polylines "
               "on Workplane('XZ') and extrude them along the depth, so the part runs unchanged along its length, with no end plates or walls "
               "closing it. A gap in the section is a gap in the solid: never bridge pieces with a base plate the drawing does not show, and "
               "pieces that do not touch are separate outlines. A fixing or other round thing cut through its axis is a revolved solid instead: "
               "the axis of revolve() is in the workplane's own two coordinates, so on Workplane('XZ') a vertical axis runs from (0, 0) to "
               "(0, p['height']). "
               "Other parts are modelled on their own: leave out anything that is not this part, even a parameter for it. "
               "The drawing is not to scale: it gives shape and topology, never a size, so take no measurement from it. "
               "Every size is a named parameter or a fraction of one. ")

# How the critic reads the images of each object (close_up.shape_pair).
SHAPE_RULE = ("Compare one object of a construction detail with the drawing it was modelled from. The first image is the whole drawing. In the "
              "second, the left side is a close-up of the drawing with the part outlined, and the right side is the same object as built, seen "
              "in section. The outline only says where the part was found and often cuts it short: follow the part's lines past it, to each of "
              "its ends, using the whole drawing where the close-up stops. Compare form and topology. Neither side is to scale, so never compare "
              "overall sizes. Proportion within one part counts, so a thin plate is not a thick block; but the left side is line work and the "
              "right side is filled in, so a plate drawn as two close lines is the same thing as a solid band of that proportion. A gap or "
              "opening made by another object passing through this part is not a feature of it. Work in three steps. First, from the left side "
              "alone and before you look at the right, list in drawing_features everything the drawing shows of this part: each fin, leg, foot, "
              "bend, turn-up, downturn, drip, return, opening and separate piece, one entry each, saying which end or side it is on. Do not list "
              "what the part is called. Second, tick each one against the right side: built is true only if that feature is there, on that side. "
              "Third, list in built_features every member of the right side the same way: each leg, bar, plate, wall and end, and any slope or "
              "taper, and tick each against the drawing. drawn is true only if this part has that member in the drawing: a bar or plate drawn "
              "there that belongs to another object does not count, and a solid bar joining pieces the drawing leaves apart is not drawn. A close "
              "resemblance is not a match. Set close_up_shows_part false, with no features, if the left side does not show this part. ")
MISSING_RULE = ("List in missing_parts every distinct part the drawing shows that no part of the program stands for, labelled or not: go "
                "over the drawing from one end to the other. A plate, a packer and a fixing are different parts even where they touch, and "
                "a part merged into its neighbour is missing. The evidence lists what the reading found drawn without a label. "
                "Parts beyond the section cut are not missing. ")


def shape_differences(item):
    """What a critic's feature list says differs, in its own words. The tally decides
    and the critic does not: seen live, one wrote "on solid base" of a section with no
    base and still called it a match. None when the close-up gave it nothing to judge."""
    if not item["close_up_shows_part"] or not item["drawing_features"]:
        return None
    absent = [feature["feature"] for feature in item["drawing_features"] if not feature["built"]]
    extras = [feature["feature"] for feature in item["built_features"] if not feature["drawn"]]
    return ([f"the drawing shows {'; '.join(absent)}, and the built part does not"] if absent else []) + (
        [f"the built part has {'; '.join(extras)}, which the drawing does not show"] if extras else [])


def apply_shape_checks(result, shapes):
    """Turn the per-object shape readings into issues the rest of the system reads.
    `shapes`: {part id: a reading, or None if there is none}. A difference is an error,
    so the verdict cannot be pass. An object nobody could compare has not been judged,
    so a pass is held to inconclusive."""
    unjudged = []
    for part, item in shapes.items():
        differences = shape_differences(item) if item else None
        if differences is None:
            unjudged.append(part)
        elif differences:
            result["issues"].append({"id": "shape-mismatch:" + part, "parts": [part], "severity": "error",
                                     "reason": "; ".join(differences), "views": ["shape-pair"], "needs_human": False})
    if unjudged and result["verdict"] == "pass":
        result["verdict"] = "inconclusive"
        result["issues"].append({"id": "shape-unjudged", "parts": unjudged, "severity": "warning", "views": ["shape-pair"],
                                 "reason": "no shape comparison was possible for these objects", "needs_human": True})


def critic_evidence(program):
    """The program as an independent critic reads it: geometry and claims, with none of
    the words its author wrote about the form. Seen live: a critic given the brief read
    the brief back as what the drawing shows, and missed what the brief had missed."""
    def plain(item):
        return {key: value for key, value in item.items() if key not in ("cad_brief", "note")}
    return {**program, "parts": [plain(part) for part in program.get("parts", [])],
            "params": [plain(param) for param in program.get("params", [])]}


def apply_missing_parts(result):
    """A part the drawing shows and the program lacks is an error, so no pass."""
    for i, item in enumerate(result.pop("missing_parts")):
        result["issues"].append({"id": f"missing-part-{i}", "parts": [], "severity": "error", "views": ["source"],
                                 "reason": f"the drawing shows {item['what']} ({item['where']}), and no part stands for it",
                                 "needs_human": False})


class Tools:
    def __init__(self, repo, fence, gateway=None, renderer=None, cad_builder=None):
        self.repo, self.fence = repo, fence
        self.gateway = gateway or ModelGateway(repo, fence)
        self.renderer = renderer
        self.cad_builder = cad_builder or cad_components.build
        self.stop_reason = None  # set by the agent when it stops a run on a limit

    def definitions(self):
        signatures = {
            "read_drawing": ("Read the job's source image, printed labels and dimensions.", obj({})),
            "recall": ("Recall scoped lessons and immutable CAD components.", obj({"query": STRING})),
            "write_program": ("Author a faithful v3 program with structured output, then gate, compile and verify it. Supply a concise modelling brief and lessons. Returns the exact program for subsequent small patches.",obj({"brief":STRING})),
            "patch_program": ("Apply small pointer edits to the working program (or last rejected proposal). Prefer stable @id/@name references: /params/@height/value or /parts/@wall/dims/@y1/expr. Numeric indices also work. Append with /parts/-.",
                              obj({"edits": {"type":"array", "items": obj({"op":{"type":"string", "enum":["add","replace","remove"]}, "path":STRING, "value":{}}, ["op","path"])}})),
            "model_component": ("Write parametric CadQuery for a CAD part, independently measure it, bind a successor version.",
                                obj({**VERSION, "part_key": STRING, "brief": STRING})),
            "reuse_component": ("Execute an existing immutable component with this version's exact bindings.",
                                obj({**VERSION, "part_key": STRING, "component_id": STRING})),
            "build_and_verify": ("Build the exact version and measure source/assembly constraints.", obj(VERSION)),
            "render": ("Render assembled iso/front/top images from the exact version.", obj(VERSION)),
            "critic": ("Independently compare source and exact-version renders. Pass the latest render result's critic_args, containing all six render IDs (untagged and tagged).",
                       obj({**VERSION, "verification_id": STRING, "render_ids": {"type": "array", "items": STRING}})),
            "locate_on_drawing": ("Locate identifiable source parts with SAM.", obj(VERSION)),
            "complete_assembly": ("Fill the required slots of the version's assembly_class that the drawing leaves empty "
                                  "(parts beyond the section cut) with inferred placeholder parts: ghosted boxes, never a "
                                  "PASS, confirmed by a reviewer. In mode on it applies them as a successor version, and it "
                                  "runs by itself after an accepted version, so call it only to retry. It reports slots it "
                                  "cannot place; in mode suggest it returns patch_program edits for you to apply.",
                                  obj(VERSION)),
            "remember": ("Propose a lesson; it cannot supply another drawing's dimensions.",
                         obj({"text": STRING, "scope": {"type": "string", "enum": ["global", "manufacturer", "type", "detail"]},
                              "scope_key": STRING, "tags": {"type": "array", "items": STRING}})),
            "finish": ("Complete only with matching verification and passing critic, or preserve a needs_review draft.",
                       obj({"version_id": {"type": ["string", "null"]},
                            "verification_id": {"type": ["string", "null"]}, "critic_verdict_id": {"type": ["string", "null"]},
                            "status": {"type": "string", "enum": ["done", "needs_review"]},
                            "summary": STRING, "open_issues": {"type": "array", "items": STRING}})),
        }
        if completion_mode() == "off":
            signatures.pop("complete_assembly")
        return [{"type": "function", "name": name, "description": description, "parameters": schema}
                for name, (description, schema) in signatures.items()]

    def working(self, requested=None):
        run = self.repo.run(self.fence.job_id)
        if requested is not None and requested != run["working_version_id"]:
            raise Conflict("tool must use this run's working version")
        return self.repo.get(d.versions, requested or run["working_version_id"])

    def completion_call(self, run, steps):
        """The call id to run complete_assembly under now, or None. Due in mode
        on when the working version has empty required slots the archetype can
        fill, once per version: a rejection is the model's to repair, and its
        next version gets another try."""
        if completion_mode() != "on" or not run["working_version_id"]:
            return None
        key = AUTO_COMPLETION + run["working_version_id"]
        version = self.repo.get(d.versions, run["working_version_id"])
        if version["format_version"] != "3.0" or any(s["call_id"] == key for s in steps):
            return None
        program = NativeProgram.model_validate(version["program"])
        known, _ = ontology_or_error()
        if known is None or not program.assembly_class or known.assembly(program.assembly_class) is None:
            return None
        read = self.repo.get(d.reads, version["drawing_read_id"])["response"] if version["drawing_read_id"] else None
        try:
            return key if completion.archetype_completion(program, known, read).parts else None
        except ValueError:
            return None  # nothing it can place; the slot-missing checks stay for the model to read

    def cad_call(self, run, steps):
        """The next object the harness models by itself, as (call id, arguments), or
        None. Seen live: the agent spent five turns patching a program before any CAD
        and then a turn per object, and every number it patched moved again when the
        solids came in. So CAD comes first, with no model turn. Each object gets one
        try per contract; a failed try is the agent's to read and repair."""
        if not switched_on("SETOUT_AUTO_CAD") or not run["working_version_id"]:
            return None
        if run["consumed"].get("cad_calls", 0) + run["reserved"].get("cad_calls", 0) >= run["budget"]["cad_calls"]:
            return None
        version = self.repo.get(d.versions, run["working_version_id"])
        if version["format_version"] != "3.0":
            return None
        program = NativeProgram.model_validate(version["program"])
        env = {p.name: p.value for p in program.params}
        done = {s["call_id"] for s in steps if s["kind"] == "tool"}
        for part in program.parts:
            if part.shape != "cad" or not part.param_bindings or version["bindings"].get(part.id, {}).get("build_id"):
                continue
            contract = [part.cad_brief, {name: evaluate(expr, env) for name, expr in part.param_bindings.items()},
                        [evaluate(item.expr, env) for item in part.dims]]
            key = f"{AUTO_CAD}{part.id}-{sha(contract)[:12]}"
            if key not in done:
                return key, {"version_id": version["id"], "part_key": part.id, "brief": part.cad_brief}
        return None

    def _image(self, data):
        """A generated image as an artifact a model request can name."""
        reference = self.repo.artifacts.put(data, "image/png")
        with d.transaction(self.repo.engine) as con:
            self.repo.artifact(con, reference)
        return reference["id"]

    def _reading_image(self, figure, rotation=0):
        """The figure as large as a model can read it, for reading and authoring: on
        a real section this is what let the READ say how an unlabelled plate bends."""
        if not switched_on("SETOUT_CLOSE_UP"):
            return figure["artifact_id"]
        return self._image(close_up.enlarged_image(close_up.upright(self.repo.artifacts.read(figure["artifact_id"]), rotation)))

    def _figure_image(self, version):
        figure = self.repo.get(d.figures, version["figure_id"])
        read = self.repo.get(d.reads, version["drawing_read_id"])["response"] if version["drawing_read_id"] else {}
        return close_up.upright(self.repo.artifacts.read(figure["artifact_id"]), read.get("rotation_deg", 0))

    def _part_regions(self, version, program):
        """{part id: box} for the objects two model calls agree on (app/close_up.py).
        Checkpointed by the parts they were asked about, so CAD and the critic share
        them. Anything that goes wrong here costs a close-up, never the part."""
        parts = [{"id": p.id, "description": p.cad_brief or p.type, "printed_label": p.label}
                 for p in program.parts if p.shape == "cad"]
        if not parts or not switched_on("SETOUT_CLOSE_UP"):
            return {}
        key = sha([version["figure_id"], parts])[:16]
        listing = canonical(parts)
        try:
            image = self._figure_image(version)
            first = close_up.first_boxes(self.gateway.json(close_up.LOCATE_PROMPT + listing, [self._image(close_up.grid_image(image))],
                                                           close_up.LOCATE_SCHEMA, "locate", "locate-" + key))
            if not first:
                return {}
            checked = self.gateway.json(close_up.CHECK_PROMPT + listing, [self._image(close_up.boxes_image(image, first))],
                                        close_up.CHECK_SCHEMA, "locate", "locate-check-" + key)
            return close_up.settle(first, checked)
        except (BudgetExceeded, ProviderRejected, ValueError, KeyError, OSError):
            return {}

    def _close_up(self, version, program, part):
        """The artifact id of an enlarged crop of this part, or None."""
        box = self._part_regions(version, program).get(part.id)
        if box is None:
            return None
        cropped = close_up.crop(self._figure_image(version), box)
        return self._image(cropped) if cropped else None

    def _shape_pairs(self, version, program):
        """{part id: image of its close-up beside its section}, for the critic. Any
        trouble costs the pictures, never the critic."""
        try:
            regions = self._part_regions(version, program)
            image = self._figure_image(version) if regions else None
            pairs = {}
            for part in program.parts:
                binding = version["bindings"].get(part.id, {})
                if part.shape != "cad" or not binding.get("build_id") or len(pairs) >= close_up.MOST_PAIRS:
                    continue
                build = self.repo.get(d.component_builds, binding["build_id"])
                positions = json.loads(self.repo.artifacts.read(build["artifacts"]["mesh"]))["positions"]
                drawn = close_up.crop(image, regions[part.id], mark=True) if part.id in regions else None
                pairs[part.id] = self._image(close_up.shape_pair(part.id, drawn, positions))
            return pairs
        except (ValueError, KeyError, OSError):
            return {}

    def _shape_readings(self, version, program, pairs):
        """{part id: a reading of its form, or None}, one model call an object. Measured
        on a real run: a shared call named a part's extra base 1 time of 3, and a call
        of its own 5 of 5, at low effort. Checkpointed on the build, so a solid is judged
        once however many critics follow; running out of budget leaves it unjudged."""
        figure = self.repo.get(d.figures, version["figure_id"])["artifact_id"]
        readings = {}
        for part, pair in pairs.items():
            others = [p.id for p in program.parts if p.shape == "cad" and p.id != part]
            prompt = SHAPE_RULE + f"The object is {part}. The other objects of this detail are {', '.join(others) or 'none'}: what belongs to one of them is not this part."
            try:
                readings[part] = self.gateway.json(prompt, [figure, pair], SHAPE_CHECK_SCHEMA, "shape", "shape-" + version["bindings"][part]["build_id"])
            except (BudgetExceeded, ProviderRejected, ValueError, KeyError):
                readings[part] = None
        return readings

    def _check_labels(self, read, figure, call_id):
        """Hold the READ's labels against what the drawing says, as far as that can
        be checked, and record how (read["label_check"]). A text layer settles it. A
        drawing with none can only be proofread by a model at a larger size: seen 30
        Oct, the READ fixed a printed typing error 3 times of 3 even when told not to,
        and a proofreading pass kept it 3 times of 3, for about 5k tokens."""
        elements = [e for e in read["elements"] if e.get("label_verbatim")]
        labels = [e["label_verbatim"] for e in elements]
        document = self.repo.get(d.documents, figure["document_id"])
        text = store.page_text(self.repo.artifacts.read(document["artifact_id"]), figure["page"]) if document["artifact_id"] else ""
        found = generate.labels_in_text(labels, text)
        if labels and sum(value is not None for value in found.values()) * 2 >= len(labels):
            printed, method = found, "text_layer"
        elif labels and switched_on("SETOUT_LABEL_PROOF"):
            image = close_up.upright(self.repo.artifacts.read(figure["artifact_id"]), read.get("rotation_deg", 0))
            try:
                proof = self.gateway.json(generate.PROOF_PROMPT + canonical(labels), [self._image(close_up.enlarged_image(image))],
                                          generate.PROOF_SCHEMA, "proof", call_id + "-proof")
            except ValueError:
                proof = {"labels": []}
            printed, method = generate.proofread(labels, proof["labels"]), "proofread"
        else:
            read["label_check"] = {"method": "none", "corrected": [], "unconfirmed": labels}
            return
        corrected = []
        for element in elements:
            words = printed.get(element["label_verbatim"])
            if words and words != element["label_verbatim"]:
                corrected.append({"read": element["label_verbatim"], "printed": words})
                element["label_verbatim"] = words
        read["label_check"] = {"method": method, "corrected": corrected,
                               "unconfirmed": [label for label in labels if printed.get(label) is None]}

    def _cad_code(self, prompt, call_id, images):
        """CadQuery from the model, and anything it says the envelope cannot hold.
        Code the gate refuses gets one more try here with the gate's reason: a small
        call, where going back through the agent costs a large turn and then this
        call again."""
        written = self.gateway.json(prompt, images, CAD_SCHEMA, "cad", call_id)
        try:
            cad_components.check_component(written["code"])
        except cad_sandbox.CadCodeError as error:
            written = self.gateway.json(prompt + "\nThis code was rejected: " + str(error) + "\nRejected code:\n" + written["code"],
                                        images, CAD_SCHEMA, "cad", call_id + "-retry")
        return written["code"], (written.get("envelope_problem") or "").strip()

    def _repairs(self, run):
        """What this run's own evidence says the agent can still fix: the errors its last
        critic named, and on its working version every FAIL and every relation or part a
        measurement could still reach. A covered layer left open is the reviewer's, and an
        issue that needs a human is not the agent's to fix."""
        steps = self.repo.list(d.steps, d.steps.c.run_id == run["id"], d.steps.c.tool_name == "critic", order=d.steps.c.n)
        issues = next((s["output"]["issues"] for s in reversed(steps) if "issues" in (s["output"] or {})), [])
        named = [issue["id"] for issue in issues if issue.get("severity") == "error" and not issue.get("needs_human")]
        reports = self.repo.list(d.verifications, d.verifications.c.version_id == run["working_version_id"], order=d.verifications.c.created_at)
        open_checks = [c["id"] for c in (reports[-1]["checks"] if reports else []) if c["status"] == "FAIL" or (
            c.get("required") and c["status"] != "PASS" and c["id"].startswith(("assembly-", "anchored-")))]
        return sorted(set(named + open_checks))

    def _refused_finishes(self, run):
        """The working version each earlier finish of this run was sent back from."""
        steps = self.repo.list(d.steps, d.steps.c.run_id == run["id"], d.steps.c.tool_name == "finish", order=d.steps.c.n)
        return [(s["output"].get("error") or {}).get("working_version_id") for s in steps
                if ((s["output"] or {}).get("error") or {}).get("code") == "repairs_left"]

    def _stage_successor(self, values, builds=None, components=None):
        ident = uid()
        content = version_content(values["program"], values["spec"], values["bindings"],
                                  values["figure_id"], values["read_id"])
        candidate = {**content, "id":ident, "detail_id":values["detail_id"],
                     "parent_version_id":values["expected_parent"], "content_sha":sha(content)}
        verification = stage_verification(StagedRepository(self.repo, builds or {},components), ident, candidate)
        return {**values, "candidate_id":ident, "verification":verification}

    def run(self, call_id, name, args):
        allowed = {t["name"] for t in self.definitions()} | {"author_program"}  # replay older author checkpoints
        if name not in allowed:
            return self.error(call_id, name, args, "unknown tool; choose one of: " + ", ".join(sorted(allowed)))
        def prepare_effect():
            self.repo.reserve(self.fence, {"tool_calls": 1})
            self.repo.settle(self.fence, {"tool_calls": 1}, {"tool_calls": 1})
            try:
                return self._prepare(call_id, name, args)
            except (BudgetExceeded, LostLease):
                raise
            except (ValueError, KeyError, TypeError) as error:
                # Invalid proposals are durable tool feedback, not lost jobs.
                working_id = self.repo.run(self.fence.job_id)["working_version_id"]
                program = self.repo.get(d.versions,working_id)["program"] if working_id else {}
                return {"tool_error": {"code": "invalid_tool_input", "message": str(error)[:500],
                        "working_version_id":working_id,"part_ids":[p["id"] for p in program.get("parts",[])],
                        "parameter_names":[p["name"] for p in program.get("params",[])]}}
        return self.repo.operation(self.fence, call_id, name, args, prepare_effect,
                                   lambda con, data: self._commit(con, name, args, data))

    def error(self, call_id, name, args, message):
        def prepare_error():
            self.repo.reserve(self.fence, {"tool_calls":1})
            self.repo.settle(self.fence, {"tool_calls":1}, {"tool_calls":1})
            return {"error": {"code":"invalid_tool_input", "message":message,
                              "working_version_id":self.repo.run(self.fence.job_id)["working_version_id"]}}
        return self.repo.operation(self.fence, call_id, name, args, prepare_error, lambda con, value: value)

    def _prepare(self, call_id, name, args):
        job = self.repo.get(d.jobs, self.fence.job_id)
        run = self.repo.run(job["id"])
        if name == "author_program" or (name == "write_program" and "brief" in args):
            parent = self.working() if run["working_version_id"] else None
            if parent and parent["drawing_read_id"]:
                read = self.repo.get(d.reads, parent["drawing_read_id"])["response"]
                figure_id = parent["figure_id"]
            else:
                reads = self.repo.list(d.operations, d.operations.c.run_id == run["id"],
                                       d.operations.c.tool_name == "read_drawing", d.operations.c.state == "completed")
                reads = [operation for operation in reads if "read" in operation["result"]]
                if not reads:
                    raise ValueError("read_drawing must run before authoring")
                read = reads[-1]["result"]["read"]
                figure_id = (parent or {}).get("figure_id") or job["payload"].get("figure_id")
            from .agent import INSTRUCTIONS
            figure = self.repo.get(d.figures, figure_id)
            recalled = self._prepare(call_id+"-context","recall",{"query":read.get("drawing_type", "assembly")})["result"]
            raw = self.gateway.json(INSTRUCTIONS + "\nYOU ARE NOW INSIDE write_program, authoring the program itself. The READ "
                "below is complete and no tools are available here: do not narrate, plan or announce steps. "
                "Return only the program object in the supplied schema. "
                "Wire format: param_bindings is an array of {name,expr}; unused selector/constraint fields are null. "
                "Every nonzero physical length, including datums and offsets, must be a named mm parameter; "
                "bare numbers can only be dimensionless coefficients. " + LABEL_RULES +
                "Brief: " + args["brief"] + "\nActive context (drawing values still require this READ): " + canonical(recalled) + "\nREAD: " + canonical(read) +
                ("\nExisting program to revise: " + canonical(parent["program"]) if parent else ""),
                [self._reading_image(figure, read.get("rotation_deg", 0))], program_schema([e.get("label_verbatim") for e in read.get("elements", [])],
                    ["drawing", "assumption"] + sorted({q["provenance"] for q in (parent or {}).get("program", {}).get("params", [])}
                                                       & {"user_input", "manufacturer_doc"}),
                    ["drawing", "inferred"] + sorted({p.get("provenance") for p in (parent or {}).get("program", {}).get("parts", [])}
                                                     & {"user_input"})),
                "program", call_id)
            proposed = from_wire(raw)
            try:
                return self._prepare(call_id, "write_program", {"program":proposed})
            except ValueError as error:
                return {"tool_error":{"code":"invalid_tool_input","message":str(error)[:REFUSAL_CHARS],
                         "working_version_id":run["working_version_id"],"proposal":proposed}}
        if name == "read_drawing":
            if run["working_version_id"]:
                version = self.working()
                if version["drawing_read_id"]:
                    existing = self.repo.get(d.reads, version["drawing_read_id"])
                    context = self._prepare(call_id+"-context","recall",{"query":existing["response"].get("drawing_type","assembly")})["result"]
                    return {"existing_read":existing,"context":context}
            figure_id = (self.working()["figure_id"] if run["working_version_id"] else None) or job["payload"].get("figure_id")
            figure = self.repo.get(d.figures, figure_id)
            response = self.gateway.json(generate.READ_PROMPT, [self._reading_image(figure)],
                                          generate.READ_SCHEMA, "read", call_id)
            for key, prefix in (("dimensions", "d"), ("elements", "l")):
                for i, item in enumerate(response[key]):
                    item["id"] = f"{prefix}{i}"
            self._check_labels(response, figure, call_id)
            context = self._prepare(call_id+"-context","recall",{"query":response.get("drawing_type","assembly")})["result"]
            return {"figure_id": figure["id"], "read": response,"context":context}
        if name == "recall":
            if job["payload"].get("memory_mode") == "off":
                return {"result": {"memories": [], "components": []}}
            query = args["query"].lower().split()
            scoped = self.repo.list(d.memories, d.memories.c.state == "active", order=d.memories.c.created_at.desc())
            kinds = set(query)
            if run["working_version_id"]:
                kinds.add(self.working()["program"].get("kind", "assembly"))
            reads = self.repo.list(d.operations, d.operations.c.run_id == run["id"],
                                  d.operations.c.tool_name == "read_drawing", d.operations.c.state == "completed")
            for operation in reads:
                if "read" not in operation["result"]:
                    continue
                drawing_type = operation["result"]["read"]["drawing_type"]
                kinds.add(drawing_type)
                kinds.add("room" if drawing_type in ("isometric_room", "plan") else "section")
            lessons = [m for m in scoped if (m["scope"] == "global" or
                       (m["scope"] == "detail" and m["scope_key"] == run["detail_id"]) or
                       (m["scope"] == "type" and m["scope_key"] in kinds) or
                       (m["scope"] == "manufacturer" and m["scope_key"] == job["payload"].get("manufacturer")))]
            lessons.sort(key=lambda m:sum(word in m["text"].lower() for word in query), reverse=True)
            components = self.repo.list(d.components)
            reviews = self.repo.list(d.component_reviews, order=d.component_reviews.c.at)
            states = {review["component_id"]:review["action"] for review in reviews}
            exact = [c for c in components if c["id"] in query]
            if exact:
                components = exact
            components.sort(key=lambda c:(states.get(c["id"]) == "accept",any(word in c["family_key"].lower() for word in query),c["created_at"] or 0),reverse=True)
            return {"result": {"memories": lessons[:10], "components": [
                    {**{k: c[k] for k in ("id", "family_key", "param_schema")},"brief":c["brief"][:600],
                     "review_status":"reviewed" if states.get(c["id"]) == "accept" else "unreviewed"} for c in components[:8]]}}
        if name in ("write_program", "patch_program"):
            expected = args.get("expected_version_id", run["working_version_id"])
            if expected == "null" and run["working_version_id"] is None:
                expected = None
            if expected != run["working_version_id"]:
                raise Conflict("expected version differs from working version")
            parent = self.working(expected) if expected else None
            figure_id = (parent or {}).get("figure_id") or job["payload"].get("figure_id")
            figure = self.repo.get(d.figures, figure_id)
            document = self.repo.get(d.documents, figure["document_id"])
            if parent and parent["drawing_read_id"]:
                read = self.repo.get(d.reads, parent["drawing_read_id"])
            else:
                operations = self.repo.list(d.operations, d.operations.c.run_id == run["id"],
                                           d.operations.c.tool_name == "read_drawing", d.operations.c.state == "completed")
                operations = [operation for operation in operations if "drawing_read_id" in operation["result"]]
                if not operations:
                    raise ValueError("read_drawing must run before authoring")
                read = self.repo.get(d.reads, operations[-1]["result"]["drawing_read_id"])
            source = {"filename": document["filename"], "sha256": document["sha256"],
                      "page_index": figure["page"], "crop_id": figure["crop_key"],
                      "page_width_pt": figure["width"], "page_height_pt": figure["height"],
                      "rotation_deg": read["response"].get("rotation_deg", 0)}
            if document["artifact_id"]:
                from pypdf import PdfReader
                page = PdfReader(io.BytesIO(self.repo.artifacts.read(document["artifact_id"]))).pages[figure["page"]]
                source.update(page_width_pt=float(page.mediabox.width), page_height_pt=float(page.mediabox.height))
            source["coord_note"] = "Setout axes: +x right, +y up, +z toward viewer. Origin and illustrative extents are named program datums."
            detail_id = run["detail_id"] or job["payload"].get("detail_id") or "ai-" + job["id"][:20]
            base = parent["program"] if parent else None
            if name == "patch_program" and "edits" in args:
                if base is None:
                    previous = self.repo.list(d.steps,d.steps.c.run_id == run["id"],order=d.steps.c.n.desc())
                    base = next(((s["output"] or {}).get("error",{}).get("proposal") for s in previous
                                 if (s["output"] or {}).get("error",{}).get("proposal")),None)
                if base is None:
                    raise ValueError("author a program before patching")
                proposed = apply_edits(base,json_object(args["edits"],list))
            else:
                proposed = json_object(args["program"])
            try:
                program, spec, notes = prepare(proposed, source, detail_id, read["response"], parent["program"] if parent else None,
                                               bound={k for k, v in (parent or {"bindings": {}})["bindings"].items() if v.get("build_id")})
            except ValueError as error:
                # Return the rejected proposal: with no working version yet,
                # the next patch applies to it, so fixes accumulate instead of
                # restarting from the first rejected program.
                return {"tool_error": {"code": "invalid_tool_input", "message": str(error)[:REFUSAL_CHARS],
                        "working_version_id": run["working_version_id"], "proposal": proposed}}
            bindings = kept_bindings(parent["program"], copy.deepcopy(parent["bindings"]), program) if parent else {}
            return self._stage_successor({"program": program.model_dump(mode="json"), "spec": spec.model_dump(mode="json"),
                    "bindings": bindings, "figure_id": figure_id, "read_id": read["id"], "notes": notes,
                    "expected_parent": expected, "detail_id": detail_id})
        if name in ("model_component", "reuse_component"):
            version = self.working(args["version_id"])
            program = NativeProgram.model_validate(version["program"])
            part = next((p for p in program.parts if p.id == args["part_key"]),None)
            if part is None:
                raise ValueError("unknown part_key; choose a part ID from the current program")
            if part.shape != "cad":
                raise ValueError("part must use shape=cad" + ("; an inferred placeholder stays a box until a reviewer "
                                 "confirms it" if part.provenance == "inferred" else ""))
            env = {p.name: p.value for p in program.params}
            params = {key: evaluate(expression, env) for key, expression in part.param_bindings.items()}
            units = {p.name:p.unit for p in program.params}
            bound_units = {key:expression_unit(expression,units) for key,expression in part.param_bindings.items()}
            if not params:
                raise ValueError("CAD needs explicit named parameter bindings")
            if name == "model_component":
                reference = version
                previous_code = previous_failure = ""
                while reference:
                    previous_binding = reference["bindings"].get(part.id,{})
                    if previous_binding.get("component_id"):
                        previous_code = self.repo.get(d.components,previous_binding["component_id"])["code"]
                        previous_failure = previous_binding.get("placeholder") or ""
                        break
                    reference = self.repo.get(d.versions,reference["parent_version_id"]) if reference["parent_version_id"] else None
                dims = {item.name:evaluate(item.expr,env) for item in part.dims}
                offset = {axis:evaluate(part.placement.get(axis,"0"),env) for axis in ("x","y","z")}
                local_bounds = [dims["x0"]-offset["x"],offset["z"]-dims["z1"],dims["y0"]-offset["y"],
                                dims["x1"]-offset["x"],offset["z"]-dims["z0"],dims["y1"]-offset["y"]]
                origin = all(abs(value) < 1e-6 for value in local_bounds[:3])
                images = [self.repo.get(d.figures, version["figure_id"])["artifact_id"]]
                crop = self._close_up(version, program, part)
                if crop:
                    images.insert(0, crop)
                code, envelope_problem = self._cad_code("Write CadQuery defining build(p) -> cq.Workplane. "
                       "Import only cadquery/math. All physical sizes use p[name], never size literals. "
                       f"This part is {part.id}, a {part.type}, " + (f"labelled {part.label!r} on the drawing. " if part.label else "not labelled on the drawing. ") +
                       (CLOSE_UP_HELP if crop else WHOLE_DRAWING_HELP) + SHAPE_RULES +
                       "Axes X width, Y depth, Z up. Default FRONT is negative Y (Setout +z toward the viewer): the face a person uses faces negative Y; the back or fixing side is positive Y. Respect any explicit orientation in the brief. No scaling or fitting. Brief: " + args["brief"] +
                       "\nUse only the provided parameter keys. Keep the code concise. Build curved bowls directly with sized profiles/lofts/shells. "
                       "CadQuery workplane(offset) retains the projected origin; center() shifts are relative and accumulate. Do not apply the same XY center offset again to each loft profile; use explicit absolute origins or zero relative shift on subsequent profiles. "
                       "\nNamed params: " + canonical(params) + "\nUnits by key: " + canonical(bound_units) +
                       "\nRequired LOCAL CadQuery [xmin,ymin,zmin,xmax,ymax,zmax]: " + canonical(local_bounds) +
                       ". These target measurements describe the part's exact declared envelope after undoing rigid placement. "
                       "Reach them through the named parameters and the actual fixture form. Do not clip/intersect with an envelope cage or add slivers just to reach bounds. "
                       "Every number in a size or offset is a fraction of p[...], such as 0.5 * p['width']; any other number except 0 is rejected. "
                       "build(p) never positions the part: its rigid placement does, so never translate to a world position. "
                       "Set envelope_problem to an empty string, unless the envelope above cannot hold the form the drawing shows (say the "
                       "part turns up at one end and its envelope is as thin as the plate): then say in one sentence what must change, and "
                       "still write the closest model you can. " +
                       ("The solid's minimum corner is the local origin: start from (0,0,0), for example box(..., centered=False). "
                        "A section that runs along the part: draw it on Workplane('XZ') and extrude(-p['depth']) so it spans Y from 0 to depth. " if origin else "") +
                       (f"\nThe previous attempt failed: {previous_failure}" if previous_failure else "") +
                       ("\nPrevious revision as repair context (fix rejected form, physical literals and axes; do not fit or preserve a rejected block):\n"+previous_code if previous_code else ""),
                       call_id, images)
                component = {"id": uid(), "family_key": part.type, "code": code, "code_sha": sha(code),
                             "param_schema": {key: {"unit": "mm" if bound_units[key] == "zero" else bound_units[key]} for key in params},
                             "feature_schema": [f.model_dump(mode="json") for f in part.features], "brief": args["brief"]}
            else:
                envelope_problem = ""
                component = self.repo.get(d.components, args["component_id"])
                if set(component["param_schema"]) != set(params):
                    raise ValueError("component parameter schema mismatch")
                if any(bound_units[key] not in ("zero",component["param_schema"][key].get("unit")) for key in params):
                    raise ValueError("component binding unit mismatch; preserve the component's declared units")
            try:
                # Code the gate refuses never reaches the sandbox, so it spends no CAD call.
                cad_components.check_component(component["code"])
                self.repo.reserve(self.fence, {"cad_calls": 1})
                self.repo.settle(self.fence, {"cad_calls": 1}, {"cad_calls": 1})
                with self.repo.resource(self.fence, "cad"):
                    result = self.cad_builder(component["code"], params,
                             [f.model_dump(mode="json") for f in part.features], measurement_params=env)
                refs = {key: self.repo.artifacts.put(result[key] if key != "mesh" else canonical(result[key]).encode(),
                        "application/json" if key == "mesh" else "application/octet-stream") for key in ("step", "stl", "mesh")}
                build_id = uid()
                build_row = {"id":build_id, "component_id":component["id"], "params":params,
                             "artifacts":{key:ref["id"] for key,ref in refs.items()},
                             "measured":result["measured"], "checks":result["checks"]}
                bindings = {**version["bindings"], part.id:{"component_id":component["id"],"build_id":build_id}}
                successor = self._stage_successor({"program":version["program"], "spec":version["spec"],
                    "bindings":bindings, "figure_id":version["figure_id"], "read_id":version["drawing_read_id"],
                    "expected_parent":version["id"], "detail_id":version["detail_id"]}, {build_id:build_row}, {component["id"]:component})
                return {"version": version, "component": component, "build": result, "refs": refs,
                        "params": params, "build_id":build_id, "successor":successor, "envelope_problem":envelope_problem}
            except (BudgetExceeded, LostLease):
                raise
            except Exception as error:
                reason = str(error)[:500]
                bindings = {**version["bindings"],part.id:{"placeholder":reason,"component_id":component["id"]}}
                successor = self._stage_successor({"program":version["program"], "spec":version["spec"],
                    "bindings":bindings,"figure_id":version["figure_id"],"read_id":version["drawing_read_id"],
                    "expected_parent":version["id"],"detail_id":version["detail_id"]})
                return {"version": version, "component": component, "placeholder":reason, "successor":successor,
                        "envelope_problem":envelope_problem}
        if name == "build_and_verify":
            self.working(args["version_id"])
            return stage_verification(self.repo, args["version_id"])
        if name == "render":
            self.working(args["version_id"])
            reports = self.repo.list(d.verifications, d.verifications.c.version_id == args["version_id"], order=d.verifications.c.created_at)
            if not reports:
                raise ValueError("build_and_verify is required before render")
            self.repo.reserve(self.fence, {"render_calls": 1})
            self.repo.settle(self.fence, {"render_calls": 1}, {"render_calls": 1})
            if self.renderer is None:
                from .render import Renderer
                self.renderer = Renderer(self.repo)
            try:
                with self.repo.resource(self.fence, "renderer"):
                    rendered = self.renderer.render(args["version_id"], reports[-1]["manifest_sha"])
            except (BudgetExceeded, LostLease):
                raise
            except Exception as error:
                # A broken browser is no reason to lose the run: the model can
                # try again, and a final review ends as needs_review with its draft.
                raise ValueError(f"the renderer failed: {error}") from error
            from .render import validate_image
            for image in rendered:
                validate_image(self.repo.artifacts.read(image["artifact"]["id"]))
            return {"renders":rendered,"verification_id":reports[-1]["id"]}
        if name == "critic":
            version = self.working(args["version_id"])
            report = self.repo.get(d.verifications, args["verification_id"])
            renders = [self.repo.get(d.renders, ident) for ident in args["render_ids"]]
            if (report["version_id"] != version["id"] or any(r["version_id"] != version["id"] or
                    r["manifest_sha"] != report["manifest_sha"] for r in renders) or
                    not {"iso", "front", "top"} <= {r["view"] for r in renders if not r["tags"]}):
                raise ValueError("critic needs matching assembled iso/front/top renders")
            figure = self.repo.get(d.figures, version["figure_id"])
            program = NativeProgram.model_validate(version["program"])
            read = self.repo.get(d.reads, version["drawing_read_id"])["response"] if version["drawing_read_id"] else {}
            pairs = self._shape_pairs(version, program)
            result = self.gateway.json("Independently compare the source installation drawing with the assembled renders. "
                     "Check dimension attribution, missing parts, layer placement/order and visible geometry. "
                     "Reject generic blocks standing in for recognisable objects, even if all dimension checks pass: each object must show the form the source shows (openings, cavities, bowls, flanges, legs, profiles, supports). A solid box standing in for a shaped object is a fidelity error. "
                     + slot_brief(version["program"].get("assembly_class")) + RETENTION_RULE + MISSING_RULE +
                     "Return fail for errors; inconclusive if evidence is insufficient. Never approve an empty or unreadable render. "
                     "Version/evidence: " + canonical({"program": critic_evidence(version["program"]), **model_checks(report["checks"]),
                     "drawn_without_a_label": read.get("unlabelled", []),
                     "views": [{"id": r["id"], "view": r["view"], "tags": r["tags"]} for r in renders]}),
                     [figure["artifact_id"]] + [r["artifact_id"] for r in renders if not r["tags"] or r["view"] == "iso"],
                     CRITIC_SCHEMA, "critic", call_id)
            if result["verdict"] not in ("pass", "fail", "inconclusive") or not isinstance(result["issues"], list):
                raise ValueError("invalid critic verdict")
            apply_shape_checks(result, self._shape_readings(version, program, pairs))
            if "missing_parts" in result:
                apply_missing_parts(result)
            if result["verdict"] == "pass" and any(i.get("severity") == "error" for i in result["issues"]):
                result["verdict"] = "fail"
            return {"critic": result, "manifest_sha": report["manifest_sha"]}
        if name == "remember":
            if args["scope"] == "detail" and args["scope_key"] != run["detail_id"]:
                raise ValueError("detail memory must belong to this run")
            return {"result": args}
        if name == "locate_on_drawing":
            version = self.working(args["version_id"])
            figure = self.repo.get(d.figures, version["figure_id"])
            read = self.repo.get(d.reads, version["drawing_read_id"])["response"]
            png = sam.upright(self.repo.artifacts.read(figure["artifact_id"]), read.get("rotation_deg", 0))
            def call(image, prompt):
                # SAM's streaming parser is retained; the dispatch is still budgeted/traced.
                ref = self.repo.artifacts.put(image, "image/png")
                self.repo.reserve(self.fence, {"model_calls": 1, "tokens": 8192})
                step = self.repo.model_request(self.fence, {"purpose": "sam", "operation_key": call_id + prompt,
                            "payload": {"prompt": prompt, "artifact_id": ref["id"]}})
                with self.repo.resource(self.fence, "meta"):
                    boxes = sam._call(image, prompt)
                self.repo.model_response(self.fence, step, {"output": [], "boxes": boxes},
                                         {"model_calls": 1, "tokens": 8192})
                return boxes
            found = sam.locate(png, [(p["id"], p["type"], p.get("label"), p.get("role", "object"))
                                     for p in version["program"]["parts"]], call=call)
            return {"regions": found}
        if name == "complete_assembly":
            version = self.working(args["version_id"])
            program = NativeProgram.model_validate(version["program"])
            read = self.repo.get(d.reads, version["drawing_read_id"])["response"] if version["drawing_read_id"] else None
            proposal = completion.archetype_completion(program, ontology.load(), read)
            edits = completion.edits(proposal)
            mode = completion_mode()
            summary = {**proposal.as_json(), "edits": edits, "assembly_class": program.assembly_class, "mode": mode,
                       "note": "inferred parts are ghosted placeholders, never a verified PASS; a reviewer confirms them" +
                               ("" if mode == "on" or not edits else ". Apply the edits with patch_program; the gate checks every claim")}
            if mode == "on" and edits:
                # The same path as patch_program, so the gate validates every
                # inferred claim; a rejection comes back with its reason.
                staged = self._prepare(call_id, "patch_program", {"edits": edits})
                return staged if "tool_error" in staged else {**staged, "completion": summary}
            return {"result": summary}
        if name == "finish":
            return {"result": args, "repairs": self._repairs(run) if args["status"] == "needs_review" and run["working_version_id"] else [],
                    "refused": self._refused_finishes(run)}
        raise ValueError("tool not implemented")

    def _successor(self, con, data, bindings=None):
        run = self.repo._run(con, self.fence.job_id)
        result = self.repo.insert_version(con, detail_id=data["detail_id"], expected_parent=data["expected_parent"],
                    program=data["program"], spec=data["spec"], bindings=bindings if bindings is not None else data["bindings"],
                    figure_id=data["figure_id"], read_id=data["read_id"], created_by=run["id"],
                    version_id=data.get("candidate_id"))
        con.execute(d.runs.update().where(d.runs.c.id == run["id"]).values(
                    working_version_id=result["version_id"], detail_id=result["detail_id"]))
        if not result["advanced"]:
            self.repo._end(con, self.fence.job_id, "needs_review", result, {"code": "version_conflict"})
        if "verification" in data:
            result.update(self._verification(con, result["version_id"], data["verification"]))
        return result

    def _verification(self, con, version_id, data):
        ident = uid()
        for key in ("manifest_ref", "scene_ref"):
            self.repo.artifact(con, data[key])
        con.execute(d.verifications.insert().values(id=ident, version_id=version_id,
                    manifest_sha=data["manifest_sha"], manifest_artifact_id=data["manifest_ref"]["id"],
                    verifier_version=VERIFIER_VERSION, checks=data["checks"],
                    quality={**data.get("quality", {}), "scene_artifact_id":data["scene_ref"]["id"]}, created_at=d.now(con)))
        self.repo.note_repeated_failures(con,version_id,data["checks"])
        return {"verification_id":ident, "manifest_sha":data["manifest_sha"], "checks":data["checks"]}

    def _commit(self, con, name, args, data):
        if "tool_error" in data:
            return {"error": data["tool_error"]}
        run = self.repo._run(con, self.fence.job_id)
        if name == "read_drawing":
            if "existing_read" in data:
                return {"drawing_read_id": data["existing_read"]["id"], "read": data["existing_read"]["response"],**data.get("context",{})}
            ident = uid()
            con.execute(d.reads.insert().values(id=ident, figure_id=data["figure_id"], model=meta_client.default_model(),
                        response=data["read"], content_sha=sha(data["read"]), created_at=d.now(con)))
            return {"drawing_read_id": ident, "read": data["read"],**data.get("context",{})}
        if name in ("author_program", "write_program", "patch_program") or (name == "complete_assembly" and "candidate_id" in data):
            return {**self._successor(con, data), "gate_notes": data["notes"],
                    **({"program":data["program"]} if name == "author_program" or "brief" in args else {}),
                    **({"completion": data["completion"]} if "completion" in data else {})}
        if name in ("model_component", "reuse_component"):
            version, component = data["version"], data["component"]
            if name == "model_component":
                con.execute(d.components.insert().values(**component, created_at=d.now(con)))
            bindings = copy.deepcopy(version["bindings"])
            if "placeholder" in data:
                bindings[args["part_key"]] = {"placeholder": data["placeholder"], "component_id": component["id"]}
                checks = []
            else:
                build_id = data["build_id"]
                for ref in data["refs"].values():
                    self.repo.artifact(con, ref)
                built = data["build"]
                con.execute(d.component_builds.insert().values(id=build_id, component_id=component["id"],
                            params=data["params"], input_sha=built["input_sha"], toolchain_sha=built["toolchain_sha"],
                            artifacts={k: r["id"] for k, r in data["refs"].items()}, measured=built["measured"],
                            checks=built["checks"], created_at=d.now(con)))
                bindings[args["part_key"]] = {"component_id": component["id"], "build_id": build_id}
                checks = built["checks"]
            result = self._successor(con, data["successor"])
            return {**result, "component_id": component["id"], "component_checks": checks,
                    "placeholder": data.get("placeholder"), "envelope_problem": data.get("envelope_problem") or None}
        if name == "build_and_verify":
            return self._verification(con, args["version_id"], data)
        if name == "render":
            refs = []
            for render in data["renders"]:
                artifact_id = self.repo.artifact(con, render["artifact"])
                ident = uid()
                values = {key: value for key, value in render.items() if key != "artifact"}
                con.execute(d.renders.insert().values(id=ident, artifact_id=artifact_id, **values, created_at=d.now(con)))
                refs.append({"render_id": ident, "artifact_id": artifact_id, "view": render["view"], "tags": render["tags"]})
            return {"renders": refs, "images": [r["artifact_id"] for r in refs if r["tags"]],
                    "critic_args":{"version_id":args["version_id"],"verification_id":data["verification_id"],
                                   "render_ids":[r["render_id"] for r in refs]}}
        if name == "critic":
            ident = uid()
            con.execute(d.critics.insert().values(id=ident, version_id=args["version_id"],
                        verification_id=args["verification_id"], render_ids=args["render_ids"],
                        manifest_sha=data["manifest_sha"], model=meta_client.default_model(),
                        verdict=data["critic"]["verdict"], issues=data["critic"]["issues"], created_at=d.now(con)))
            return {"critic_verdict_id": ident, **data["critic"]}
        if name == "remember":
            ident = uid()
            con.execute(d.memories.insert().values(id=ident, **data["result"], state="proposed", kind="lesson",
                        source_version_id=run["working_version_id"], source_event_id=None, created_at=d.now(con)))
            return {"memory_id": ident, "state": "proposed"}
        if name == "locate_on_drawing":
            for part_key, region in data["regions"].items():
                con.execute(d.sam_regions.insert().values(id=uid(), version_id=args["version_id"], part_key=part_key, data=region))
            return {"located": len(data["regions"]), "regions": data["regions"]}
        if name == "finish":
            result = data["result"]
            if result["status"] == "done":
                if result["version_id"] != run["working_version_id"]:
                    return {"error": {"code": "version_conflict"}}
                try:
                    # Inferred parts must be named in the open issues: done never hides them.
                    errors = self.repo.completion_errors(con, result["version_id"], result["verification_id"], result["critic_verdict_id"],
                                                         open_issues=result.get("open_issues"))
                except (KeyError, TypeError):
                    errors = ["missing_completion_evidence"]
                if errors:
                    return {"error": {"code": "not_ready", "conditions": errors}}
            repairs = data.get("repairs") or []
            if repairs and not self.stop_reason:
                # A run may not give up on what it can fix while budget remains. Bounded:
                # a finish is sent back only so many times, and only if something changed
                # since the last. Out of budget, it stops and the worker continues it.
                refused = data["refused"]
                out_of_cad = run["consumed"].get("cad_calls", 0) >= run["budget"]["cad_calls"]
                if not repair_room(run) or (out_of_cad and all(item.startswith(("shape-mismatch:", "cad-")) for item in repairs)):
                    self.stop_reason = "repairs_left"
                elif len(refused) < REPAIR_REFUSALS and (not refused or refused[-1] != run["working_version_id"]):
                    return {"error": {"code": "repairs_left", "working_version_id": run["working_version_id"], "repairs": repairs[:16],
                                      "message": "There is budget left and these can still be fixed, so the run is not over: fix them, "
                                                 "render and call critic again, then finish."}}
            issues = list(result.get("open_issues", []))
            if result["status"] == "needs_review" and run["working_version_id"]:
                reports = con.execute(sa.select(d.verifications).where(d.verifications.c.version_id == run["working_version_id"])
                                      .order_by(d.verifications.c.created_at.desc())).mappings().first()
                issues += ([c["id"] for c in reports["checks"] if c["status"] == "FAIL" or
                            (c.get("required") and c["status"] != "PASS")] if reports else ["missing_verification"])
            used_up = run["consumed"].get("cad_calls", 0) >= run["budget"]["cad_calls"]
            result = {**result, "open_issues": sorted(set(issues)), "version_id": run["working_version_id"], "detail_id": run["detail_id"],
                      "repairs": repairs, "stop_reason": self.stop_reason or ("budget_exhausted" if used_up else "finished")}
            self.repo._end(con, self.fence.job_id, "succeeded" if result["status"] == "done" else "needs_review", result)
            return result
        return data["result"]
