"""OpenAI-compatible LLM client for 3D Make.

Uses the ``openai`` SDK only: ``OpenAI(base_url=..., api_key=...)`` and
``client.chat.completions.create(...)``. Credentials come from the environment
(locally a gitignored ``.env``; in production the Modal secret ``setout-llm``
on the web function, never on the Blender worker). This module does not log
or return keys, base URLs, or authorization headers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

import yaml
from openai import OpenAI

from app.make.redact import redact

ROOT = Path(__file__).resolve().parents[2]
PROFILES_PATH = ROOT / "config" / "llm_profiles.yaml"
DEFAULT_MODEL = "muse-spark-1.3"

_TOOLS_OFF: set[str] = set()

SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_blender_script",
        "description": "Submit the complete Blender Python script that builds the detail.",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "The full Blender Python script."},
                "summary": {"type": "string", "description": "One sentence describing the scene."},
            },
            "required": ["script", "summary"],
            "additionalProperties": False,
        },
    },
}

_FENCE_ASK = (
    "Tool calling is unavailable. Reply with the full Blender script in exactly "
    "one fenced python block. Do not include any other code block."
)

_SYSTEM = """You write a Blender Python script that builds one construction detail as named mesh objects.

The harness has already emptied the scene. Do not call bpy.ops.wm.read_factory_settings. Do not save a file and do not export. The harness exports the scene.

Blender's unit is the metre. Author every size in millimetres and convert with the injected mm() helper.

Start the script with PARAMS, a dict of every dimension in millimetres. OVERRIDES is injected by the harness and may be empty. Before building geometry, for each object, if OVERRIDES[name]["dims"] contains a key that is also in PARAMS, use that value. The harness re-applies location (millimetres) and rotation_deg (degrees) after the script, in Blender's Z-up world, even if the script ignores them.

Each part is one named mesh object in a named collection. On every object set dims (a JSON object of the PARAMS keys that size that part), build_order (an int, bottom-up), material (a short name) and component_ref (an empty string unless the catalog gives an id). Give each part a distinct colour.

Helpers injected by the harness:
- box_mm(name, size, location=(0, 0, 0), *, collection, dims, material, build_order, color, metallic=0, roughness=0.5)
  size and location are millimetres. location is the minimum corner. size is (x, y, z). The object origin is the centre of the box.
- extrude_profile(name, points, depth, *, location=(0, 0, 0), collection, dims, material, build_order, color, metallic=0, roughness=0.5)
  points are (x, z) millimetre pairs. The profile is extruded along +Y by depth millimetres.
- add_part(name, obj, *, collection, dims, material, build_order, color, metallic=0, roughness=0.5)

Set SCENE_NAME to a short title.

Imports allowed: bpy, bmesh, math, mathutils, json. No file or network access. No exec, eval, open, or __import__. No bpy.ops.wm.save*.

Component catalog:
{component_catalog}

Use the dimensions the user asked for. Do not invent a product name, an approval, or a clause reference. The geometry is illustrative of the detail, not a verdict on a built installation.
"""


class LLMConfigError(RuntimeError):
    """A profile is missing an environment variable. The message names the variable only."""


class LLMCallError(RuntimeError):
    """The provider rejected the call for a reason other than missing tool support."""


def load_dotenv(path: Path | None = None) -> None:
    """Load ``.env`` into the environment without overriding variables already set.

    Does not print the file. Missing file is a no-op.
    """
    file = path or (ROOT / ".env")
    if not file.is_file():
        return
    for raw in file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_profiles(path: Path | None = None) -> dict:
    file = path or PROFILES_PATH
    data = yaml.safe_load(file.read_text())
    if not isinstance(data, dict) or "profiles" not in data:
        raise LLMConfigError(f"profiles file is missing a profiles map: {file.name}")
    return data


def clear_tool_cache() -> None:
    """Forget which profiles rejected tool calling. Tests use this."""
    _TOOLS_OFF.clear()
    try:
        from app.make.db import delete_settings_prefix
        delete_settings_prefix("make.tools.")
    except Exception:
        return


def system_prompt(component_catalog: str = "") -> str:
    catalog = component_catalog.strip() or "(none)"
    return _SYSTEM.replace("{component_catalog}", catalog)


def describe(profile: str | None = None) -> dict:
    """Public description of one profile. No key, no base URL, no headers."""
    load_dotenv()
    data = load_profiles()
    default_id = str(data.get("default") or "muse")
    pid = profile or default_id
    profiles = data["profiles"]
    if pid not in profiles:
        raise LLMConfigError(f"unknown profile {pid!r}")
    spec = profiles[pid]
    model = _model_for(pid, default_id, spec)
    base_name = str(spec.get("base_url_env") or "")
    key_name = str(spec.get("api_key_env") or "")
    ready = bool(os.environ.get(base_name, "").strip() and os.environ.get(key_name, "").strip() and model)
    tools = _tools_mode(pid, spec)
    return {
        "id": pid,
        "label": str(spec.get("label") or pid),
        "model": model,
        "ready": ready,
        "caps": {"vision": bool(spec.get("vision")), "tools": tools},
        "missing": _missing(pid, default_id, spec, model),
    }


def list_models() -> list[dict]:
    """``[{id, label, model, ready, caps}]`` for every profile. Booleans and names only."""
    data = load_profiles()
    listed = []
    for pid in data["profiles"]:
        info = describe(pid)
        info.pop("missing", None)
        listed.append(info)
    return listed


def get_client(profile: str | None = None) -> tuple[OpenAI, str, dict]:
    """Return ``(client, model, caps)``. ``caps`` is the public description.

    The client holds the key. Callers must not log, repr, or serialise the client.
    """
    info = describe(profile)
    if info["missing"]:
        names = ", ".join(info["missing"])
        raise LLMConfigError(f"profile {info['id']!r} is not configured; set {names}")
    data = load_profiles()
    spec = data["profiles"][info["id"]]
    base_url = os.environ.get(str(spec["base_url_env"]), "").strip()
    api_key = os.environ.get(str(spec["api_key_env"]), "").strip()
    client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=120.0)
    public = {key: value for key, value in info.items() if key != "missing"}
    return client, info["model"], public


def build_user_content(text: str, images: list[dict] | None = None, *, vision: bool = True):
    """Chat content for one user turn. Images are base64 data URLs when vision is on."""
    images = images or []
    if not images:
        return text
    if not vision:
        return text + "\n\n(image omitted: this profile has no vision)"
    parts: list[dict] = [{"type": "text", "text": text}]
    for image in images:
        mime = image.get("mime") or "image/png"
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{image['b64']}"},
        })
    return parts


# A fence line is ``` optionally followed by a language tag. Pairing is done in
# code so a closing fence is not read as the next opening fence.
_FENCE_LINE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_-]*)[ \t]*$")


def extract_fenced_python(text: str) -> str | None:
    """The last fenced python block, or None when there is no fence.

    An untagged fence counts. A fence tagged with another language does not.
    """
    if not text:
        return None
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        opened = _FENCE_LINE.match(lines[index])
        if opened is None:
            index += 1
            continue
        lang = opened.group(1).lower()
        index += 1
        body: list[str] = []
        while index < len(lines) and _FENCE_LINE.match(lines[index]) is None:
            body.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        if lang in ("", "python", "py"):
            script = "\n".join(body).strip()
            if script:
                blocks.append(script)
    if not blocks:
        return None
    return blocks[-1]


def complete(messages: list[dict], profile: str | None = None, *,
             client: OpenAI | None = None, attempt: int = 1) -> dict:
    """Ask for one Blender script. Falls back to a fenced python block.

    The returned dict has script, summary, via (``tool`` or ``fence``), profile,
    model and event. It has no key and no base URL. ``event`` is the jobs.events
    record: profile, model, latency, tokens, tools-vs-fence, attempt.
    """
    info = describe(profile)
    own_client = client is None
    if own_client:
        client, model, _caps = get_client(info["id"])
    else:
        model = info["model"] or DEFAULT_MODEL
    started = time.perf_counter()
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    via = "tool"
    script: str | None = None
    summary = ""
    use_tools = info["caps"]["tools"] is not False
    tools_attempted = False
    try:
        if use_tools:
            tools_attempted = True
            try:
                response = _create(client, model, messages, tools=True)
                _add_usage(tokens, response)
                script, summary = _from_tool(response)
            except Exception as exc:
                if not _tools_unsupported(exc):
                    raise LLMCallError(redact(f"{type(exc).__name__}: {exc}")) from None
                _remember_fence(info["id"])
                script = None
            if script is None:
                _remember_fence(info["id"])
                via = "fence"
                response = _create(client, model, _with_fence_ask(messages), tools=False)
                _add_usage(tokens, response)
                script = extract_fenced_python(_message_text(response))
                summary = summary or ""
        else:
            via = "fence"
            response = _create(client, model, _with_fence_ask(messages), tools=False)
            _add_usage(tokens, response)
            script = extract_fenced_python(_message_text(response))
    except LLMCallError:
        raise
    except Exception as exc:
        raise LLMCallError(redact(f"{type(exc).__name__}: {exc}")) from None
    latency = round(time.perf_counter() - started, 3)
    event = {
        "profile": info["id"],
        "model": model,
        "latency_s": latency,
        "tokens": tokens,
        "via": via,
        "attempt": attempt,
        "tools_attempted": tools_attempted,
    }
    return {
        "script": script,
        "summary": summary,
        "via": via,
        "profile": info["id"],
        "model": model,
        "event": event,
    }


def _create(client, model: str, messages: list[dict], *, tools: bool):
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = [SUBMIT_TOOL]
        kwargs["tool_choice"] = {"type": "function", "function": {"name": "submit_blender_script"}}
    return client.chat.completions.create(**kwargs)


def _with_fence_ask(messages: list[dict]) -> list[dict]:
    return [*messages, {"role": "user", "content": _FENCE_ASK}]


def _tools_unsupported(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (400, 404, 405, 422):
        return True
    text = str(exc).lower()
    if "tool" in text and any(word in text for word in ("not support", "unsupported", "unknown", "invalid")):
        return True
    return False


def _from_tool(response) -> tuple[str | None, str]:
    message = response.choices[0].message
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        return None, ""
    function = calls[0].function
    name = function["name"] if isinstance(function, dict) else function.name
    if name != "submit_blender_script":
        return None, ""
    raw = function["arguments"] if isinstance(function, dict) else function.arguments
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, json.JSONDecodeError):
        return None, ""
    script = payload.get("script")
    if not isinstance(script, str) or not script.strip():
        return None, ""
    summary = payload.get("summary") or ""
    return script, summary if isinstance(summary, str) else ""


def _message_text(response) -> str:
    message = response.choices[0].message
    content = getattr(message, "content", None)
    return content or ""


def _add_usage(tokens: dict, response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    for src, dest in (("prompt_tokens", "prompt"), ("completion_tokens", "completion"), ("total_tokens", "total")):
        value = getattr(usage, src, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(src)
        if isinstance(value, int):
            tokens[dest] += value


def _model_for(pid: str, default_id: str, spec: dict) -> str:
    if pid == default_id:
        override = os.environ.get("LLM_MODEL", "").strip()
        if override:
            return override
    if spec.get("model_env"):
        return os.environ.get(str(spec["model_env"]), "").strip()
    return str(spec.get("model") or "").strip()


def _tools_mode(pid: str, spec: dict):
    if _fence_is_default(pid):
        return False
    mode = spec.get("tools", "auto")
    if mode is False or mode == "false":
        return False
    return mode if mode is not True else "auto"


def _fence_is_default(pid: str) -> bool:
    """True after this profile has already missed tool calling, including across processes."""
    if pid in _TOOLS_OFF:
        return True
    try:
        from app.make.db import get_setting
        if get_setting(f"make.tools.{pid}") == "fence":
            _TOOLS_OFF.add(pid)
            return True
    except Exception:
        return False
    return False


def _remember_fence(pid: str) -> None:
    _TOOLS_OFF.add(pid)
    try:
        from app.make.db import put_setting
        put_setting(f"make.tools.{pid}", "fence")
    except Exception:
        return


def _missing(pid: str, default_id: str, spec: dict, model: str) -> list[str]:
    missing: list[str] = []
    for key in ("base_url_env", "api_key_env"):
        name = str(spec.get(key) or "")
        if not name or not os.environ.get(name, "").strip():
            if name:
                missing.append(name)
    if not model:
        if pid == default_id:
            missing.append("LLM_MODEL")
        elif spec.get("model_env"):
            missing.append(str(spec["model_env"]))
        else:
            missing.append("model")
    return missing
