"""Meta Model API client (Responses endpoint), stdlib only.

One tested path for every model call. Shapes verified against
https://dev.meta.ai/docs/ (structured output, image understanding) 29 Oct 2026.
The API key comes from the environment or the repo's .env; it is never logged.
"""

from __future__ import annotations

import json
import os
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
    return e.get("MODEL_API_MODEL") or "muse-spark-1.3-contributor"


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
