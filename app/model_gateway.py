"""Checkpoint every model dispatch and reconstruct artifact images on replay."""
from __future__ import annotations

import base64
import copy
import json

from . import database as d, meta_client
from .repository import canonical


def output_text(response):
    return "".join(part.get("text", "") for item in response["output"]
                   if item.get("type") == "message" for part in item.get("content", [])
                   if part.get("type") in ("output_text", "text"))


class ModelGateway:
    def __init__(self, repo, fence, responder=None):
        self.repo, self.fence = repo, fence
        self.responder = responder or meta_client.respond_tools

    def call(self, payload, purpose, operation_key, responder=None):
        run = self.repo.run(self.fence.job_id)
        matching = [s for s in self.repo.list(d.steps, d.steps.c.run_id == run["id"], order=d.steps.c.n)
                    if s["kind"] == "model" and s["request"].get("purpose") == purpose
                    and s["request"].get("operation_key") == operation_key]
        if matching and matching[-1]["state"] == "completed":
            return matching[-1]["response"]
        if matching:
            payload = matching[-1]["request"]["payload"]
        image_count = canonical(payload).count('"input_image"')
        # Input tokenization is provider-specific. Reserve a conservative text/
        # image estimate and cap output to the remaining allowance; actual usage
        # is settled atomically and any overrun stops further dispatch.
        input_estimate = max(4096, len(canonical(payload)) // 3) + 8192 * image_count
        remaining = run["budget"]["tokens"] - run["consumed"].get("tokens", 0) - run["reserved"].get("tokens", 0)
        review_tail = 100000 if purpose == "agent" and run["working_version_id"] and run["budget"]["tokens"] >= 120000 else 0
        output_cap = min(payload.get("max_output_tokens", 4096), remaining - input_estimate - review_tail)
        if output_cap < 1024:
            from .repository import BudgetExceeded
            raise BudgetExceeded("tokens budget exhausted")
        payload = {**payload, "max_output_tokens": output_cap}
        reservation = {"model_calls": 1, "tokens": input_estimate + output_cap}
        self.repo.reserve(self.fence, reservation)
        # An uncertain old reservation remains charged after a crash. The
        # repeated dispatch is a new, separately reserved provider request.
        step_id = self.repo.model_request(self.fence, {"purpose": purpose,
                    "operation_key": operation_key, "payload": payload})
        wire = copy.deepcopy(payload)
        for message in wire.get("input", []):
            for part in message.get("content") or []:
                url = part.get("image_url", "")
                if part.get("type") == "input_image" and url.startswith("artifact:"):
                    ident = url.removeprefix("artifact:")
                    ref = self.repo.get(d.artifacts, ident)
                    part["image_url"] = (f"data:{ref['media_type']};base64," +
                                         base64.b64encode(self.repo.artifacts.read(ident)).decode())
        with self.repo.resource(self.fence, "meta"):
            response = (responder or self.responder)(wire)
        self.repo.model_response(self.fence, step_id, response, reservation)
        return response

    def json(self, text, images, schema, purpose, operation_key, model=None):
        content = [{"type": "input_text", "text": text}]
        content += [{"type": "input_image", "image_url": "artifact:" + ident} for ident in images]
        payload = {"model": model or meta_client.default_model(), "max_output_tokens": 32768 if purpose in ("program","cad") else 16384 if purpose == "read" else 8192,
                   "reasoning":{"effort":"low" if purpose == "read" else "medium"},
                   "input": [{"role": "user", "content": content}],
                   "text": {"format": {"type": "json_schema", "name": purpose,
                                        "schema": schema, "strict": True}}}
        response = self.call(payload, purpose, operation_key)
        if response.get("status") == "incomplete":
            raise ValueError(f"{purpose} reached the provider output limit; simplify the request or finish needs_review")
        return json.loads(output_text(response))
