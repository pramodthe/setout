"""Agent tools prepare outside transactions and commit effects with their result."""
from __future__ import annotations

import copy
import json
import io

import sqlalchemy as sa

from . import cad_components, database as d, generate, meta_client, sam
from .model_gateway import ModelGateway
from .native_program import NativeProgram, prepare, expression_unit
from .program import evaluate, expr_names
from .program_edits import apply_edits
from .program_schema import program_schema, from_wire
from .repository import BudgetExceeded, Conflict, LostLease, VERIFIER_VERSION, canonical, row, sha, uid, version_content
from .verification_v3 import stage_verification


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


STRING = {"type": "string"}
VERSION = {"version_id": STRING}
ISSUE_SCHEMA = obj({"id": STRING, "parts": {"type": "array", "items": STRING}, "reason": STRING,
                    "severity": {"type": "string", "enum": ["error", "warning"]},
                    "views": {"type": "array", "items": STRING}, "needs_human": {"type": "boolean"}})
CRITIC_SCHEMA = obj({"verdict": {"type": "string", "enum": ["pass", "fail", "inconclusive"]},
                     "issues": {"type": "array", "items": ISSUE_SCHEMA}})


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


class Tools:
    def __init__(self, repo, fence, gateway=None, renderer=None, cad_builder=None):
        self.repo, self.fence = repo, fence
        self.gateway = gateway or ModelGateway(repo, fence)
        self.renderer = renderer
        self.cad_builder = cad_builder or cad_components.build

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
            "remember": ("Propose a lesson; it cannot supply another drawing's dimensions.",
                         obj({"text": STRING, "scope": {"type": "string", "enum": ["global", "manufacturer", "type", "detail"]},
                              "scope_key": STRING, "tags": {"type": "array", "items": STRING}})),
            "finish": ("Complete only with matching verification and passing critic, or preserve a needs_review draft.",
                       obj({"version_id": {"type": ["string", "null"]},
                            "verification_id": {"type": ["string", "null"]}, "critic_verdict_id": {"type": ["string", "null"]},
                            "status": {"type": "string", "enum": ["done", "needs_review"]},
                            "summary": STRING, "open_issues": {"type": "array", "items": STRING}})),
        }
        return [{"type": "function", "name": name, "description": description, "parameters": schema}
                for name, (description, schema) in signatures.items()]

    def working(self, requested=None):
        run = self.repo.run(self.fence.job_id)
        if requested is not None and requested != run["working_version_id"]:
            raise Conflict("tool must use this run's working version")
        return self.repo.get(d.versions, requested or run["working_version_id"])

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
            raw = self.gateway.json(INSTRUCTIONS + "\nReturn the program object in the supplied schema. "
                "Wire format: param_bindings is an array of {name,expr}; unused selector/constraint fields are null. "
                "Every nonzero physical length, including datums and offsets, must be a named mm parameter; "
                "bare numbers can only be dimensionless coefficients. Keep enough parts to cover all source labels. "
                "Brief: " + args["brief"] + "\nActive context (drawing values still require this READ): " + canonical(recalled) + "\nREAD: " + canonical(read) +
                ("\nExisting program to revise: " + canonical(parent["program"]) if parent else ""),
                [figure["artifact_id"]], program_schema(), "program", call_id)
            proposed = from_wire(raw)
            try:
                return self._prepare(call_id, "write_program", {"program":proposed})
            except ValueError as error:
                return {"tool_error":{"code":"invalid_tool_input","message":str(error)[:1000],
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
            response = self.gateway.json(generate.READ_PROMPT, [figure["artifact_id"]],
                                          generate.READ_SCHEMA, "read", call_id)
            for key, prefix in (("dimensions", "d"), ("elements", "l")):
                for i, item in enumerate(response[key]):
                    item["id"] = f"{prefix}{i}"
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
            program, spec, notes = prepare(proposed, source, detail_id, read["response"],
                                           parent["program"] if parent else None)
            bindings = copy.deepcopy(parent["bindings"]) if parent else {}
            if parent:
                old_parts = {p["id"]: p for p in parent["program"].get("parts",[])}
                old_env = {p["name"]:p["value"] for p in parent["program"].get("params",[])}
                new_env = {p.name:p.value for p in program.params}
                kept = {}
                for part in program.parts:
                    old = old_parts.get(part.id, {})
                    current = part.model_dump(mode="json")
                    contract = ("shape", "type", "role", "cad_brief", "param_bindings", "features")
                    expressions = list(part.param_bindings.values()) + [f.expected_expr for f in part.features]
                    referenced = set().union(*(expr_names(e) for e in expressions)) if expressions else set()
                    if (part.id in bindings and all(current.get(k) == old.get(k) for k in contract)
                            and all(old_env.get(k) == new_env.get(k) for k in referenced)):
                        kept[part.id] = bindings[part.id]
                bindings = kept
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
                raise ValueError("part must use shape=cad")
            env = {p.name: p.value for p in program.params}
            params = {key: evaluate(expression, env) for key, expression in part.param_bindings.items()}
            units = {p.name:p.unit for p in program.params}
            bound_units = {key:expression_unit(expression,units) for key,expression in part.param_bindings.items()}
            if not params:
                raise ValueError("CAD needs explicit named parameter bindings")
            if name == "model_component":
                reference = version
                previous_code = ""
                while reference:
                    previous_binding = reference["bindings"].get(part.id,{})
                    if previous_binding.get("component_id"):
                        previous_code = self.repo.get(d.components,previous_binding["component_id"])["code"]
                        break
                    reference = self.repo.get(d.versions,reference["parent_version_id"]) if reference["parent_version_id"] else None
                dims = {item.name:evaluate(item.expr,env) for item in part.dims}
                offset = {axis:evaluate(part.placement.get(axis,"0"),env) for axis in ("x","y","z")}
                local_bounds = [dims["x0"]-offset["x"],offset["z"]-dims["z1"],dims["y0"]-offset["y"],
                                dims["x1"]-offset["x"],offset["z"]-dims["z0"],dims["y1"]-offset["y"]]
                code = self.gateway.json("Write CadQuery defining build(p) -> cq.Workplane. "
                       "Import only cadquery/math. All physical sizes use p[name], never size literals. "
                       "Model a recognisable manufactured form, not its bounding box. WC/toilet needs an open bowl, pedestal and cistern; basin needs an open bowl/rim; vanity needs a hollow carcass/fronts. "
                       "Axes X width, Y depth, Z up. Default FRONT is negative Y (Setout +z toward the viewer): cabinet doors and the WC pan face negative Y; cistern/back is positive Y. Respect any explicit orientation in the brief. No scaling or fitting. Brief: " + args["brief"] +
                       "\nUse only the provided parameter keys. Keep the code concise. Build curved bowls directly with sized profiles/lofts/shells. "
                       "CadQuery workplane(offset) retains the projected origin; center() shifts are relative and accumulate. Do not apply the same XY center offset again to each loft profile; use explicit absolute origins or zero relative shift on subsequent profiles. "
                       "\nNamed params: " + canonical(params) + "\nUnits by key: " + canonical(bound_units) +
                       "\nRequired LOCAL CadQuery [xmin,ymin,zmin,xmax,ymax,zmax]: " + canonical(local_bounds) +
                       ". These target measurements describe the part's exact declared envelope after undoing rigid placement. "
                       "Reach them through the named parameters and the actual fixture form. Do not clip/intersect with an envelope cage or add slivers just to reach bounds. " +
                       ("\nPrevious revision as repair context (fix rejected form, physical literals and axes; do not fit or preserve a rejected block):\n"+previous_code if previous_code else ""),
                       [], obj({"code": STRING}), "cad", call_id)["code"]
                component = {"id": uid(), "family_key": part.type, "code": code, "code_sha": sha(code),
                             "param_schema": {key: {"unit": "mm" if bound_units[key] == "zero" else bound_units[key]} for key in params},
                             "feature_schema": [f.model_dump(mode="json") for f in part.features], "brief": args["brief"]}
            else:
                component = self.repo.get(d.components, args["component_id"])
                if set(component["param_schema"]) != set(params):
                    raise ValueError("component parameter schema mismatch")
                if any(bound_units[key] not in ("zero",component["param_schema"][key].get("unit")) for key in params):
                    raise ValueError("component binding unit mismatch; preserve the component's declared units")
            self.repo.reserve(self.fence, {"cad_calls": 1})
            self.repo.settle(self.fence, {"cad_calls": 1}, {"cad_calls": 1})
            try:
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
                        "params": params, "build_id":build_id, "successor":successor}
            except (BudgetExceeded, LostLease):
                raise
            except Exception as error:
                reason = str(error)[:500]
                bindings = {**version["bindings"],part.id:{"placeholder":reason,"component_id":component["id"]}}
                successor = self._stage_successor({"program":version["program"], "spec":version["spec"],
                    "bindings":bindings,"figure_id":version["figure_id"],"read_id":version["drawing_read_id"],
                    "expected_parent":version["id"],"detail_id":version["detail_id"]})
                return {"version": version, "component": component, "placeholder":reason, "successor":successor}
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
            with self.repo.resource(self.fence, "renderer"):
                rendered = self.renderer.render(args["version_id"], reports[-1]["manifest_sha"])
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
            result = self.gateway.json("Independently compare the source installation drawing with the assembled renders. "
                     "Check dimension attribution, missing parts, layer placement/order and visible geometry. "
                     "Reject generic blocks standing in for recognisable fixtures, even if all dimension checks pass. A WC must show its pan/open bowl and cistern; a basin must show an open bowl; a vanity needs a cabinet and the basin when the source shows one. A solid white WC box is a fidelity error. "
                     "Return fail for errors; inconclusive if evidence is insufficient. Never approve an empty or unreadable render. "
                     "Version/evidence: " + canonical({"program": version["program"], "checks": report["checks"],
                     "views": [{"id": r["id"], "view": r["view"], "tags": r["tags"]} for r in renders]}),
                     [figure["artifact_id"]] + [r["artifact_id"] for r in renders if not r["tags"] or r["view"] == "iso"],
                     CRITIC_SCHEMA, "critic", call_id)
            if result["verdict"] not in ("pass", "fail", "inconclusive") or not isinstance(result["issues"], list):
                raise ValueError("invalid critic verdict")
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
            found = sam.locate(png, [(p["id"], p["type"], p.get("label")) for p in version["program"]["parts"]], call=call)
            return {"regions": found}
        if name == "finish":
            return {"result": args}
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
                    quality={"scene_artifact_id":data["scene_ref"]["id"]}, created_at=d.now(con)))
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
        if name in ("author_program", "write_program", "patch_program"):
            return {**self._successor(con, data), "gate_notes": data["notes"],
                    **({"program":data["program"]} if name == "author_program" or "brief" in args else {})}
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
                    "placeholder": data.get("placeholder")}
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
                    errors = self.repo.completion_errors(con, result["version_id"], result["verification_id"], result["critic_verdict_id"])
                except (KeyError, TypeError):
                    errors = ["missing_completion_evidence"]
                if errors:
                    return {"error": {"code": "not_ready", "conditions": errors}}
            issues = list(result.get("open_issues", []))
            if result["status"] == "needs_review" and run["working_version_id"]:
                reports = con.execute(sa.select(d.verifications).where(d.verifications.c.version_id == run["working_version_id"])
                                      .order_by(d.verifications.c.created_at.desc())).mappings().first()
                issues += ([c["id"] for c in reports["checks"] if c["status"] == "FAIL" or
                            (c.get("required") and c["status"] != "PASS")] if reports else ["missing_verification"])
            result = {**result, "open_issues": sorted(set(issues)), "version_id": run["working_version_id"], "detail_id": run["detail_id"]}
            self.repo._end(con, self.fence.job_id, "succeeded" if result["status"] == "done" else "needs_review", result)
            return result
        return data["result"]
