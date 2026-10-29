"""Meta Model API client (Responses endpoint, streamed), stdlib only.

Lifted from the request/stream code in ncc/extractor/extract.py and
ncc/benchmark/run.py so the app has one tested path. Shapes verified against
https://dev.meta.ai/docs/ (structured output, image understanding) 29 Oct 2026.
The API key comes from the environment or the repo's .env; it is never logged.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith(("META_", "MODEL_API_"))})
    return env


def configured() -> bool:
    e = _env()
    return bool(e.get("META_API_KEY") or e.get("MODEL_API_KEY"))


def default_model() -> str:
    e = _env()
    return e.get("MODEL_API_MODEL") or e.get("META_MODEL") or "muse-spark-1.3"


def respond_tools(payload: dict, timeout: int = 300) -> dict:
    """One non-streaming native Responses turn; caller checkpoints and retries.

    Return every output item, including reasoning/function-call identifiers.
    Never retry invisibly: the persistent worker accounts for each dispatch.
    """
    e = _env()
    key = e.get("META_API_KEY") or e.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("AI not configured: set META_API_KEY")
    base = (e.get("META_API_BASE") or "https://api.meta.ai/v1").rstrip("/")
    body = {**payload, "stream": False}
    req = urllib.request.Request(f"{base}/responses", data=json.dumps(body, allow_nan=False).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result.get("output"), list):
        raise RuntimeError("provider response has no output array")
    return result


def respond_json(content: list[dict], schema: dict, name: str,
                 model: str | None = None, timeout: int = 300,
                 instructions: str | None = None) -> tuple[dict, dict | None]:
    """One structured (json_schema, strict) streamed request -> (object, usage)."""
    e = _env()
    key = e.get("META_API_KEY") or e.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("META_API_KEY is not set (.env)")
    base = (e.get("META_API_BASE") or "https://api.meta.ai/v1").rstrip("/")
    payload: dict = {
        "model": model or default_model(),
        "input": [{"type": "message", "role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": name,
                            "schema": schema, "strict": True}},
        "stream": True,
    }
    if instructions:
        payload["instructions"] = instructions
    last: Exception | None = None
    for attempt in range(2):
        try:
            return _stream(base, key, payload, timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = err
            if isinstance(err, urllib.error.HTTPError) and err.code < 500:
                body = err.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"Meta API {err.code}: {body}") from err
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Meta API unreachable: {last}")


def _stream(base: str, key: str, payload: dict, timeout: int
            ) -> tuple[dict, dict | None]:
    req = urllib.request.Request(
        f"{base}/responses", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"})
    text: list[str] = []
    usage, done = None, False
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            ev = json.loads(data)
            kind = ev.get("type")
            if kind == "response.output_text.delta":
                text.append(ev.get("delta") or "")
            elif kind == "response.completed":
                done = True
                usage = (ev.get("response") or {}).get("usage")
            elif kind in ("error", "response.failed"):
                raise RuntimeError(json.dumps(ev)[:300])
    if not done:
        raise RuntimeError("stream ended before response.completed")
    return json.loads("".join(text)), usage
