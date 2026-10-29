"""Native-tool Muse agent reconstructed from durable model/tool checkpoints."""
from __future__ import annotations

import json

from . import database as d, meta_client
from .agent_tools import Tools
from .model_gateway import ModelGateway
from .repository import TERMINAL, BudgetExceeded, canonical

INSTRUCTIONS = """You build faithful construction installation details from a source drawing.
Use read_drawing first, recall relevant lessons, then write_program with a concise
modelling brief. This tool reliably authors complex programs with structured output.
Use named parameters with honest provenance; never invent a printed value or label.
Define dimensions and datums once and derive coordinates with expressions; avoid
dozens of independent absolute coordinates which disconnect source dimensions.
Use lowercase snake_case parameter names and part IDs.
Pass program as a JSON OBJECT, never a quoted or escaped JSON string. The harness
supplies source, origin, detail_id and optimistic parent identity; do not invent IDs.
Cover the drawing: every READ label with category layer or object gets a part that
carries that exact label (usually 8-30 parts). A part's label is a READ label copied
exactly, or null; pieces of a split layer keep the same label, and position words go
in the id, type or note. Add or repair parts in successor versions. If write_program
returns a rejected proposal, repair that proposal with patch_program; do not start a
new program, and never drop or reword a label to clear an error.
Execute tools rather than returning a program as prose.
LAYERS/SUBSTRATES MUST USE EXACT PRIMITIVES, NEVER CAD. Anything built up in place
or laid as a sheet or board (substrates, slabs, screeds, beds, membranes, coatings,
insulation, boards, finishes) uses box, sheet, wedge or profile. Only discrete
manufactured objects (fixings, brackets, anchors, trims, grates, fixtures,
appliances) use role=object and cad. read_drawing classifies each printed label as
layer, object or other; a part carrying that label must take the matching role.
Shape dimensions are a dims ARRAY of {name,expr}, with expressions in mm:
- box: x0,x1,y0,y1,z0,z1. Thin rectangular layers are boxes too.
- sheet: thickness,z0,z1 and path [{x:expr,y:expr},...].
- wedge: x0,x1,y_bottom,y_top_x0,fall_ratio,z0,z1.
- profile: z0,z1 and a closed section outline path [{x:expr,y:expr},...].
- post: cx,cz,r,y0,y1. rod: cx,cy,r,z0,z1. fillet: x,y,leg_x,leg_y,z0,z1.
- revolve: cx,cz and radius/height path. sweep: r and 3D path.
- cad (objects only): x0,x1,y0,y1,z0,z1 envelope, cad_brief, param_bindings and
  placement {x:expr,y:expr,z:expr}. Define dimensions for every part, including CAD.
Every part has id,type,label,shape,role,dims,path,install_after,note and features.
Use boxes for planar layer interfaces this verifier supports; unsupported curved
interfaces remain UNVERIFIED. Do not replace layers with CAD to evade these checks.
Each READ dimension ID must own one measured feature via source_dimension_id.
Drawing parameters must drive geometry itself, not only expected_expr.
For a relative height, use top = other_top + printed_height in the dims expression.
Share expressions between parts rather than duplicating independent coordinates.
For primitive parts, extent features select an axis x/y/z and expected_expr over
named params. Unsupported measurements remain UNVERIFIED.
For falls and slopes use kind=slope_ratio, unit=ratio, selector={axis:"x"}, and
comparison min for a printed minimum fall. Source-feature expected_expr must
equal the printed parameter value, not an unrelated offset or multiple of it.
For heights ABOVE another part or a slab set-down, use a face_distance feature:
selector {axis:"y",face:"max",other_part:"lower_part",other_face:"max"}
measures this part's top minus the other part's top. Use comparison min/max as
printed. This avoids mistaking overall component height for a relative height.
Objects require CAD, explicit param_bindings and placement {x,y,z} expressions; build(p) uses CadQuery
X width, Y depth, Z up, mapped to Setout x=X,y=Z,z=-Y without scaling.
An object's default front is CadQuery negative Y / Setout positive z. Orient
objects as the drawing shows them: the face a person uses toward the viewer or
room, the back or fixing side against its support.
CAD bindings include width, depth, height and every thickness/radius the code needs.
Every object declares form. form=shaped when the drawing shows form beyond the
envelope (openings, cavities, bowls, flanges, legs, profiles, fins); the measured
solid must not fill its box. form=block only when the manufactured item really
is a rectangular block. Model the recognisable form the drawing shows; a solid
envelope box cannot stand in for a shaped object.
Small manufactured pieces are objects too: role=object and shape=cad, never box
finishes. Include visible supports, legs and fixings, and follow the drawing for
how each object is supported (on legs, wall-hung, fixed through, bedded in).
If a critic rejects a component's form, re-author that revision. Do not reuse the
rejected code or substitute a rectangular hollow shell for a curved form.
Preserve the named dimensions while repairing form.
Declare extent features on x/y/z with expected_expr matching the envelope spans.
The independently measured solid, after placement, must match the native envelope.
Every part declares substrate/covered_layer/finish/object. Define covers/support
relations and install_after paths. For planar box interfaces constraints name
axis, source_face and target_face (min/max), min_gap, max_gap (mm) and min_coverage
(a fraction from 0 to 1: 0.9 means 90%).
Split a layer that turns a corner into separate wall/floor parts so each covering
face can be measured. Renaming/splitting preserves material, source labels and
printed feature ownership. Every covered layer needs a real covering relationship.
Use model_component or reuse_component for each CAD object. Each edit or CAD
binding creates a successor version; follow its returned version_id. Each mutation
already returns its verification_id and checks; inspect those without rebuilding.
Use patch_program with small JSON-pointer edits, not a full program rewrite.
Prefer stable named paths, e.g. /parts/@bracket/placement/z or
/params/@height/value; numeric indices shift after additions/removals.
For revisions, repair the existing program and retain working CAD bindings.
Correct a misplaced CAD object by its rigid placement; do not regenerate a good
solid or change its declared envelope to conceal a measured mismatch.
Render and critic must concern that same latest version and verification.
Pass the render result's critic_args directly to critic; do not guess render IDs
or use only the tagged screenshots.
Inspect tool feedback and images and fix concrete problems with patch_program.
Never remove source evidence to clear checks. Call finish(done) only with the
matching passing verification and critic IDs. Otherwise finish(needs_review)
with a useful summary and unresolved issues. Do not publish or claim compliance.
"""


class Agent:
    def __init__(self, repo, fence, responder=None, renderer=None, cad_builder=None):
        self.repo, self.fence = repo, fence
        self.gateway = ModelGateway(repo, fence, responder)
        self.tools = Tools(repo, fence, self.gateway, renderer, cad_builder)

    def final_review(self, run):
        """Spend the reserved tail on seeing the draft before the run stops."""
        version_id = run["working_version_id"]
        reports = self.repo.list(d.verifications,d.verifications.c.version_id == version_id,order=d.verifications.c.created_at)
        if not reports:
            self.tools.run("final-verify","build_and_verify",{"version_id":version_id})
            reports = self.repo.list(d.verifications,d.verifications.c.version_id == version_id,order=d.verifications.c.created_at)
        if not reports:
            # Verification itself failed: keep the draft for review.
            self.tools.run("final-unverified","finish",{"version_id":version_id,"verification_id":None,
                "critic_verdict_id":None,"status":"needs_review","summary":"The draft could not be verified.",
                "open_issues":["verification failed; see the trace"]})
            return
        report = reports[-1]
        rendered = self.tools.run("final-render","render",{"version_id":version_id})
        verdict = None
        if rendered.get("renders"):
            verdict = self.tools.run("final-critic","critic",{"version_id":version_id,
                "verification_id":report["id"],"render_ids":[r["render_id"] for r in rendered["renders"]]})
        ready = bool(verdict and verdict.get("verdict") == "pass") and not any(
            c["status"] == "FAIL" or (c.get("required") and c["status"] != "PASS") for c in report["checks"])
        args = {"version_id":version_id,"verification_id":report["id"],
                "critic_verdict_id":verdict.get("critic_verdict_id") if verdict else None,
                "status":"done" if ready else "needs_review",
                "summary":"Final review completed within the run budget.",
                "open_issues":[] if ready else ["The draft requires correction or human review; see verification and critic evidence."]}
        result = self.tools.run("final-finish","finish",args)
        if result.get("error"):
            self.tools.run("final-needs-review","finish",{**args,"status":"needs_review",
                "open_issues":["Completion evidence did not satisfy the exact-version guard."]})

    def run(self):
        job = self.repo.get(d.jobs, self.fence.job_id)
        run = self.repo.run(job["id"])
        figure_id = job["payload"].get("figure_id")
        if not figure_id and run["working_version_id"]:
            figure_id = self.repo.get(d.versions, run["working_version_id"])["figure_id"]
        figure = self.repo.get(d.figures, figure_id)
        initial = [{"role": "user", "content": [
            {"type": "input_text", "text": "Generate/revise this installation detail. Request: " + canonical(job["payload"])},
            {"type": "input_image", "image_url": "artifact:" + figure["artifact_id"]}]}]
        if run["base_version_id"]:
            base = self.repo.get(d.versions, run["base_version_id"])
            initial[0]["content"].append({"type":"input_text", "text":"Base version program: " +
                canonical(base["program"] or base["spec"]) + "\nExisting component bindings: " + canonical(base["bindings"]) +
                ("\nThis is an archived v2 detail. Use write_program to create a native v3 successor; replace all fixture objects with parametric CAD." if base["format_version"] == "legacy-v2" else "")})
            if base["format_version"] == "3.0":
                evidence = self.tools.run("initial-verification","build_and_verify",{"version_id":base["id"]})
                initial[0]["content"].append({"type":"input_text","text":"Current verification: "+canonical({
                    **{k:v for k,v in evidence.items() if k != "checks"},
                    "checks":[c for c in evidence.get("checks",[]) if c["status"] != "PASS"]})})
                prior_critics = self.repo.list(d.critics,d.critics.c.version_id == base["id"],
                    d.critics.c.created_at <= job["created_at"],order=d.critics.c.created_at)
                if prior_critics:
                    rejected = {part:base["bindings"][part]["component_id"]
                        for issue in prior_critics[-1]["issues"] if any(word in issue["id"].lower() for word in ("fidelity","form"))
                        for part in issue.get("parts",[]) if base["bindings"].get(part,{}).get("component_id")}
                    initial[0]["content"].append({"type":"input_text","text":"Prior independent critic: "+canonical({
                        "verdict":prior_critics[-1]["verdict"],"issues":prior_critics[-1]["issues"],"rejected_component_revisions":rejected})})
        seen, stalled = -1, 0
        while self.repo.get(d.jobs, job["id"])["state"] not in TERMINAL:
            run = self.repo.run(job["id"])
            steps = self.repo.list(d.steps, d.steps.c.run_id == run["id"], order=d.steps.c.n)
            # Every pass records a step; replayed results never spend budget,
            # so a replay that keeps finding nothing new must stop, not spin.
            stalled = stalled + 1 if len(steps) == seen else 0
            if stalled > 3:
                raise RuntimeError("agent run made no progress; its trace could not be replayed")
            seen = len(steps)
            conversation = list(initial)
            completed_calls = {s["call_id"]: s for s in steps if s["kind"] == "tool"}
            pending = []
            agent_turns = [s for s in steps if s["kind"] == "model" and s["request"].get("purpose") == "agent"
                           and s["state"] == "completed"]
            for turn in agent_turns:
                # Keep full responses in the trace; replay only public messages
                # and tool calls. Native image/tool continuation supports this.
                conversation += [item for item in turn["response"]["output"] if item.get("type") != "reasoning"]
                for call in turn["response"]["output"]:
                    if call.get("type") != "function_call":
                        continue
                    saved = completed_calls.get(call["call_id"])
                    if saved:
                        output = dict(saved["output"])
                        if "checks" in output:
                            output["passed_check_ids"] = [c["id"] for c in output["checks"] if c["status"] == "PASS"]
                            output["checks"] = [c for c in output["checks"] if c["status"] != "PASS"]
                        conversation.append({"type": "function_call_output", "call_id": call["call_id"],
                                             "output": canonical(output)})
                        # Historical renders stay in the trace. Only the latest
                        # matching render is useful to this candidate's loop.
                        images = output.get("images", []) if not any(
                            s["kind"] == "tool" and s["n"] > saved["n"] and s["tool_name"] == "render"
                            for s in steps) else []
                        if images:
                            conversation.append({"role": "user", "content": [
                                {"type": "input_image", "image_url": "artifact:" + ident} for ident in images]})
                    else:
                        pending.append(call)
            if pending:
                # Preserve the response order; never dispatch calls from an
                # uncheckpointed response or execute the same effect twice.
                call = pending[0]
                try:
                    arguments = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (ValueError, TypeError):
                    self.tools.error(call["call_id"], call["name"], call.get("arguments"), "Tool arguments must be a valid JSON object")
                    continue
                self.tools.run(call["call_id"], call["name"], arguments)
                continue
            remaining = run["budget"]["tokens"]-run["consumed"].get("tokens",0)-run["reserved"].get("tokens",0)
            if run["working_version_id"] and run["budget"]["tokens"] >= 120000 and remaining < 100000:
                self.final_review(run)
                return
            if agent_turns and not any(o.get("type") == "function_call" for o in agent_turns[-1]["response"]["output"]):
                response = agent_turns[-1]["response"]
                reason = "provider_output_limit" if response.get("status") == "incomplete" else "model stopped without finish"
                self.repo.end(self.fence, "needs_review", {"version_id": run["working_version_id"],
                              "detail_id": run["detail_id"], "open_issues": [reason]})
                return
            try:
                self.gateway.call({"model": meta_client.default_model(), "instructions": INSTRUCTIONS,
                               "reasoning": {"effort":"medium" if job["kind"] == "revise_detail" else "low"},
                               "max_output_tokens": 32768, "tools": self.tools.definitions(), "input": conversation},
                              "agent", f"turn-{len(agent_turns)}")
            except BudgetExceeded:
                if run["working_version_id"] and remaining >= 100000:
                    self.final_review(run)
                    return
                raise
