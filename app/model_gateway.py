"""Checkpoint every model dispatch and reconstruct artifact images on replay."""
from __future__ import annotations

import base64
import copy
import json
import urllib.error

from . import database as d, meta_client
from .repository import BudgetExceeded, canonical, review_tail, tokens_left

IMAGE_TOKENS = 2048  # seen live: about 1.3k for a page-sized figure or a render
TURN_MIN_OUTPUT = 8192  # below this a turn cannot think and still call a tool


class ProviderRejected(RuntimeError):
    """The provider refused the request itself (a 4xx other than 408/429).
    Sending the same request again would fail the same way."""


def output_text(response):
    return "".join(message_texts(response))


def message_texts(response):
    return ["".join(part.get("text", "") for part in item.get("content", [])
                    if part.get("type") in ("output_text", "text"))
            for item in response["output"] if item.get("type") == "message"]


def structured(response):
    """The schema object of a structured response. Muse occasionally narrates
    in extra messages before the object (seen 29 Oct: dozens of "I'll start by
    reading the drawing" lines), so the object is the last message that parses."""
    texts = message_texts(response)
    try:
        return json.loads("".join(texts))
    except json.JSONDecodeError:
        for text in reversed(texts):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
        raise


class ModelGateway:
    def __init__(self, repo, fence, responder=None):
        self.repo, self.fence = repo, fence
        self.responder = responder or meta_client.respond_tools

    def call(self, payload, purpose, operation_key, responder=None):
        run = self.repo.run(self.fence.job_id)
        steps = [s for s in self.repo.list(d.steps, d.steps.c.run_id == run["id"], order=d.steps.c.n)
                 if s["kind"] == "model" and s["request"].get("purpose") == purpose]
        matching = [s for s in steps if s["request"].get("operation_key") == operation_key]
        if matching and matching[-1]["state"] == "completed":
            return matching[-1]["response"]
        if matching:
            payload = matching[-1]["request"]["payload"]
        # Input tokenization is provider-specific. Reserve a conservative text/
        # image estimate and cap output to the remaining allowance; actual usage
        # is settled atomically and any overrun stops further dispatch. A turn's
        # conversation repeats the last turn's, which the provider serves from
        # its cache and the budget does not bill.
        input_estimate = len(canonical(payload)) // 3 + IMAGE_TOKENS * canonical(payload).count('"input_image"')
        if purpose == "agent" and steps and steps[-1]["state"] == "completed":
            input_estimate -= (steps[-1]["tokens"] or {}).get("input_tokens", 0)
        input_estimate = max(4096, input_estimate)
        wanted = payload.get("max_output_tokens", 4096)
        tail = review_tail(run) if purpose == "agent" else 0
        output_cap = min(wanted, tokens_left(run) - input_estimate - tail)
        if output_cap < (min(TURN_MIN_OUTPUT, wanted) if purpose == "agent" else 1024):
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
        try:
            with self.repo.resource(self.fence, "meta"):
                response = (responder or self.responder)(wire)
        except urllib.error.HTTPError as error:
            # The provider answered with an error, so nothing was generated:
            # release the tokens reserved for this call. (A timeout or dropped
            # connection stays charged: its usage is unknown.)
            self.repo.settle(self.fence, reservation, {"model_calls": 1})
            if error.code in (408, 429) or error.code >= 500:
                raise  # transient: the worker retries the job with backoff
            try:
                detail = error.read().decode(errors="replace")[:300]
            except Exception:  # noqa: BLE001 - the status code is enough
                detail = ""
            raise ProviderRejected(f"provider rejected the {purpose} request ({error.code}): {detail}") from error
        self.repo.model_response(self.fence, step_id, response, reservation)
        return response

    def json(self, text, images, schema, purpose, operation_key, model=None):
        content = [{"type": "input_text", "text": text}]
        content += [{"type": "input_image", "image_url": "artifact:" + ident} for ident in images]
        # Reasoning counts as output: seen live, a critic spent all of 8192 thinking.
        payload = {"model": model or meta_client.default_model(), "max_output_tokens": 32768 if purpose in ("program","cad") else 16384 if purpose in ("read","critic") else 8192,
                   "reasoning":{"effort":"low" if purpose == "read" else "medium"},
                   "input": [{"role": "user", "content": content}],
                   "text": {"format": {"type": "json_schema", "name": purpose,
                                        "schema": schema, "strict": True}}}
        response = self.call(payload, purpose, operation_key)
        if response.get("status") == "incomplete":
            # Not "finish": seen live, that advice ended a run with budget left and no review.
            raise ValueError(f"{purpose} reached the provider output limit, so nothing came back; call the tool again")
        return structured(response)
