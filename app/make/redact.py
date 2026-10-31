"""Scrub credentials from text that might be printed or written to a report.

The redacted string is what a log, a job event, or the day-1 report may keep.
Known secret values are removed first, then URL query strings and common key
shapes. The replacement is the fixed token ``[redacted]``.
"""
from __future__ import annotations

import os
import re

REDACTED = "[redacted]"

# Names whose values are credentials or endpoint URLs. The values are scrubbed;
# these names are not themselves secret.
_VALUE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION", "BASE_URL")

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_AUTH_HEADER = re.compile(r"(?i)\bauthorization\s*[:=]\s*\S+")
_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_ASSIGNED = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|"
    r"llm_api_key|alt_llm_api_key|meta_api_key|openai_api_key|"
    r"modal_token_id|modal_token_secret|modal_token)\s*[=:]\s*\S+"
)
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#]*")


def secret_values(extra: list[str] | None = None) -> list[str]:
    """Credential-shaped environment values, longest first. Never logs them."""
    found: list[str] = []
    for name, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        upper = name.upper()
        if any(marker in upper for marker in _VALUE_MARKERS):
            found.append(value)
    for value in extra or []:
        if value and len(value) >= 8:
            found.append(value)
    unique = [value for value in dict.fromkeys(found) if value not in REDACTED]
    unique.sort(key=len, reverse=True)
    return unique


def redact(text: str, extra: list[str] | None = None) -> str:
    """Return ``text`` with secrets, auth headers and URL query strings removed."""
    if not text:
        return ""
    cleaned = text
    # Drop query strings before value replacement so a base URL that is a prefix
    # of a longer URL cannot leave ``?api_key=`` behind.
    cleaned = _URL_QUERY.sub(r"\1", cleaned)
    for secret in secret_values(extra):
        cleaned = cleaned.replace(secret, REDACTED)
    cleaned = _BEARER.sub(REDACTED, cleaned)
    cleaned = _AUTH_HEADER.sub(REDACTED, cleaned)
    cleaned = _SK.sub(REDACTED, cleaned)
    cleaned = _ASSIGNED.sub(REDACTED, cleaned)
    return cleaned
