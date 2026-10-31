"""3D Make agent loop: one script, then up to three repairs.

Each attempt asks the LLM (tool call, or a fenced python block when tools are
unavailable), rejects a script that fails the AST allowlist, and runs Blender.
A traceback, or a bounding-box warning (a part past 2 m, or thinner than the
harness minimum), is sent back and the model returns a full script. The call
record lands in ``jobs.events`` with profile, model, latency, tokens, tool
versus fence, and attempt. Keys are not stored.
"""
from __future__ import annotations

import ast
import json
import uuid

from app.make import db
from app.make.ast_check import check
from app.make.llm import LLMCallError, complete as default_complete, system_prompt
from app.make.redact import redact
from app.make.worker import run as default_run
from app import store

MAX_ATTEMPTS = 4
REPAIR_TAIL = 4000
_BBOX_MARKS = ("extends beyond", "under 0.05 mm", "no mesh objects")


def generate(text: str, *, project_id: str | None = None, mode: str = "prompt",
             profile: str | None = None, timeout: int = 90, title: str | None = None,
             complete_fn=None, runner=None, mutate_script=None, note=None,
             job_id: str | None = None, record_user: bool = True,
             images: list | None = None, vision: bool | None = None) -> dict:
    """Run one generate job. ``mode`` is ``prompt`` (new project) or ``feedback``."""
    if mode not in ("prompt", "feedback"):
        raise ValueError(f"unknown mode {mode!r}")
    note = note or _note
    complete_fn = complete_fn or default_complete
    runner = runner or default_run
    if mode == "prompt":
        project_id = project_id or uuid.uuid4().hex[:12]
        if db.get_project(project_id) is None:
            db.create_project(title or _title_from(text), project_id)
        version = None
        previous = db.latest_version(project_id)
        parent_n = previous["n"] if previous else None
        number = parent_n + 1 if parent_n else 1
    else:
        if not project_id:
            raise ValueError("feedback needs a project id")
        if db.get_project(project_id) is None:
            raise KeyError(project_id)
        version = db.latest_version(project_id)
        if version is None:
            raise ValueError(f"project {project_id} has no version to revise")
        parent_n = version["n"]
        number = parent_n + 1
    if job_id is None:
        job_id = uuid.uuid4().hex
        db.create_job(job_id, project_id, "generate")
    db.set_job_state(job_id, "running", attempt=0)
    if record_user:
        db.add_message(uuid.uuid4().hex, project_id, "user", redact(text), job_id=job_id)
    if vision is None:
        vision = _vision(profile)
    messages = build_messages(mode, text, version, images=images, vision=vision)
    image_count = _image_count(messages)
    if images or image_count:
        _record(job_id, {"kind": "images", "count": image_count, "vision": bool(vision),
                         "attached": len(images or [])}, 0)
    # Feedback bakes previous overrides into the prompt. The new script is the
    # source of those numbers, so the harness is not asked to apply them again.
    overrides: dict = {}
    last_script = ""
    last_result: dict = {"ok": False, "traceback": "no attempt", "warnings": [], "files": {}}
    summary = ""
    model = ""
    profile_id = profile or ""
    latencies: list[float] = []
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            note(f"attempt {attempt}")
            try:
                completion = complete_fn(messages, profile=profile, attempt=attempt)
            except LLMCallError as exc:
                last_result = {"ok": False, "traceback": str(exc), "warnings": [], "files": {}}
                _record(job_id, {"profile": profile_id, "model": model, "latency_s": None,
                                 "tokens": {}, "via": "", "attempt": attempt, "error": str(exc)}, attempt)
                break
            event = _public_event(completion.get("event") or {}, attempt)
            profile_id = event.get("profile") or profile_id
            model = event.get("model") or model
            if isinstance(event.get("latency_s"), (int, float)):
                latencies.append(float(event["latency_s"]))
            _record(job_id, event, attempt)
            summary = completion.get("summary") or summary
            script = completion.get("script") or ""
            if mutate_script is not None and attempt == 1 and script:
                script = mutate_script(script)
            last_script = script
            if not script:
                last_result = {"ok": False, "traceback": "model returned no script", "warnings": [], "files": {}}
            else:
                faults = check(script)
                if not faults.ok:
                    note("static check failed")
                    last_result = {"ok": False, "traceback": faults.text, "warnings": [], "files": {}}
                else:
                    note(f"building ({event.get('via') or 'script'})")
                    last_result = runner(script, overrides, timeout)
            problem = _problem(last_result)
            _record(job_id, {
                "kind": "blender",
                "attempt": attempt,
                "ok": bool(last_result.get("ok")) and problem is None,
                "warnings": list(last_result.get("warnings") or []),
                "error_tail": redact(problem or "")[-800:],
            }, attempt)
            if problem is None:
                break
            if attempt == MAX_ATTEMPTS:
                break
            messages.append({"role": "assistant", "content": script or ""})
            messages.append({
                "role": "user",
                "content": "Error:\n" + redact(problem)[-REPAIR_TAIL:] + "\nReturn the full corrected script.",
            })
    except Exception as exc:
        last_result = {"ok": False, "traceback": redact(f"{type(exc).__name__}: {exc}"), "warnings": [], "files": {}}
        _record(job_id, {"profile": profile_id, "model": model, "latency_s": None, "tokens": {},
                         "via": "", "attempt": _attempt_of(job_id) or 1, "error": last_result["traceback"]},
                _attempt_of(job_id) or 1)
    problem = _problem(last_result)
    # A build that ran and is inside the bbox limits is the version, even when
    # an earlier attempt needed a repair. The last attempt keeps an ok build
    # that still has a bbox warning rather than discarding the files.
    accept = bool(last_result.get("ok")) and (problem is None or _attempt_of(job_id) >= MAX_ATTEMPTS)
    status = "done" if accept else "failed"
    paths = _write_artifacts(project_id, number, last_result.get("files") or {}) if accept else {}
    tree_text = _tree_text(last_result) if accept else ""
    params = extract_params(last_script) if last_script else {}
    scene_name = _scene_name(tree_text)
    db.insert_version({
        "id": uuid.uuid4().hex,
        "project_id": project_id,
        "n": number,
        "parent_n": parent_n,
        "source": mode if mode == "feedback" else "prompt",
        "script": _scrub_script(last_script),
        "params": json.dumps(params),
        "overrides": json.dumps(overrides),
        "glb_path": paths.get("scene.glb"),
        "tree_json": tree_text or None,
        "preview_path": paths.get("preview.png"),
        "render_path": None,
        "profile": profile_id,
        "model": model,
        "status": status,
        "error": None if status == "done" else redact(problem or last_result.get("traceback") or "")[-REPAIR_TAIL:],
    })
    if scene_name and mode == "prompt" and not title:
        db.touch_project(project_id, title=scene_name, active_version=number)
    else:
        db.touch_project(project_id, active_version=number)
    _record(job_id, {
        "kind": "result",
        "version": number,
        "status": status,
        "error": None if status == "done" else redact(problem or "")[-800:],
    }, _attempt_of(job_id))
    db.set_job_state(job_id, "done" if status == "done" else "failed", attempt=_attempt_of(job_id))
    part_count = _part_count(tree_text)
    assistant = summary or ("Done" if status == "done" else "Failed")
    if status == "done":
        assistant = f"{assistant} — {part_count} parts".strip()
    db.add_message(uuid.uuid4().hex, project_id, "assistant", redact(assistant), job_id=job_id)
    artifact_dir = ""
    if paths:
        artifact_dir = str((store.var_root() / "make" / project_id / str(number)))
    return {
        "project_id": project_id,
        "job_id": job_id,
        "version": number,
        "status": status,
        "attempts": _attempt_of(job_id),
        "latencies": latencies,
        "profile": profile_id,
        "model": model,
        "parts": part_count,
        "warnings": [redact(item) for item in (last_result.get("warnings") or [])],
        "error": None if status == "done" else redact(problem or "")[-800:],
        "artifact_dir": artifact_dir,
    }


def build_messages(mode: str, text: str, version: dict | None, images: list | None = None,
                   vision: bool = True) -> list[dict]:
    """System prompt plus the user turn. Feedback includes the script, the tree and baked values."""
    from app.make.llm import build_user_content
    system = {"role": "system", "content": system_prompt()}
    if mode == "feedback" and version:
        script = version.get("script") or ""
        tree = version.get("tree_json") or "{}"
        current = current_values(version.get("params"), version.get("overrides"))
        baked = "Current values in millimetres (overrides already applied; keep these unless the feedback changes them):\n"
        baked += json.dumps(current, sort_keys=True) + "\n\n"
        raw_overrides = _load_object(version.get("overrides"))
        if raw_overrides:
            baked += "Overrides by part, in millimetres and degrees:\n" + json.dumps(raw_overrides, sort_keys=True) + "\n\n"
        body = (
            "Current Blender script:\n```python\n" + script + "\n```\n\n"
            "Current tree.json:\n" + tree + "\n\n"
            + baked
            + "Feedback:\n" + text + "\n\n"
            "Return the full corrected script. Keep existing part names unless the feedback adds or removes a part."
        )
    else:
        body = text
    content = build_user_content(body, images or [], vision=vision) if images else body
    return [system, {"role": "user", "content": content}]


def current_values(params_json, overrides_json) -> dict:
    """PARAMS with override dims written on top. This is what the next script should start from."""
    params = _load_object(params_json)
    overrides = _load_object(overrides_json)
    values = dict(params)
    for spec in overrides.values():
        if isinstance(spec, dict):
            dims = spec.get("dims") or {}
            if isinstance(dims, dict):
                values.update(dims)
    return values


def extract_params(script: str) -> dict:
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PARAMS":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def _problem(result: dict) -> str | None:
    if not result.get("ok"):
        return (result.get("traceback") or result.get("stdout_tail") or "build failed").strip()
    bbox = [item for item in (result.get("warnings") or []) if _is_bbox(item)]
    if bbox:
        return "Bounding box check failed. Parts must stay within 2 m of the origin and must not have zero thickness.\n" + "\n".join(bbox)
    return None


def _is_bbox(warning: str) -> bool:
    text = warning.lower()
    return any(mark in text for mark in _BBOX_MARKS)


def _public_event(event: dict, attempt: int) -> dict:
    """The jobs.events record. Only the fields that describe the call."""
    tokens = event.get("tokens") if isinstance(event.get("tokens"), dict) else {}
    return {
        "profile": event.get("profile") or "",
        "model": event.get("model") or "",
        "latency_s": event.get("latency_s"),
        "tokens": {
            "prompt": tokens.get("prompt"),
            "completion": tokens.get("completion"),
            "total": tokens.get("total"),
        },
        "via": event.get("via") or "",
        "attempt": event.get("attempt") or attempt,
        "tools_attempted": bool(event.get("tools_attempted")),
    }


def _record(job_id: str, event: dict, attempt: int) -> None:
    safe = json.loads(redact(json.dumps(event)))
    db.append_event(job_id, safe, attempt=attempt)


def _attempt_of(job_id: str) -> int:
    job = db.get_job(job_id)
    return int(job["attempt"] or 0) if job else 0


def _write_artifacts(project_id: str, number: int, files: dict) -> dict[str, str]:
    dest = store.var_root() / "make" / project_id / str(number)
    dest.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in ("scene.glb", "tree.json", "preview.png"):
        payload = files.get(name)
        if payload is None:
            continue
        if isinstance(payload, str):
            payload = payload.encode()
        if name == "tree.json":
            payload = redact(payload.decode("utf-8", errors="replace")).encode()
        path = dest / name
        path.write_bytes(payload)
        written[name] = str(path.relative_to(store.var_root()))
    return written


def _tree_text(result: dict) -> str:
    raw = (result.get("files") or {}).get("tree.json")
    if not raw:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return redact(raw)


def _scene_name(tree_text: str) -> str:
    if not tree_text:
        return ""
    try:
        tree = json.loads(tree_text)
    except json.JSONDecodeError:
        return ""
    name = tree.get("name") if isinstance(tree, dict) else ""
    return str(name or "")[:80]


def _part_count(tree_text: str) -> int:
    if not tree_text:
        return 0
    try:
        tree = json.loads(tree_text)
    except json.JSONDecodeError:
        return 0
    count = 0

    def visit(node):
        nonlocal count
        if isinstance(node, dict):
            if node.get("type") == "mesh":
                count += 1
            for child in node.get("children") or []:
                visit(child)

    visit(tree)
    return count


def _load_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _scrub_script(script: str) -> str:
    from app.make.redact import secret_values
    cleaned = script
    for secret in secret_values():
        cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def _title_from(text: str) -> str:
    line = " ".join(text.split())
    return (line[:80] or "Untitled").strip()


def _note(message: str) -> None:
    import sys
    sys.stderr.write(redact(message) + "\n")


def _vision(profile: str | None) -> bool:
    try:
        from app.make.llm import describe
        return bool(describe(profile)["caps"]["vision"])
    except Exception:
        return True


def _image_count(messages: list) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for part in content if isinstance(part, dict) and part.get("type") == "image_url")
    return count


def rebuild(job_id: str, project_id: str, version_n: int, overrides: dict, *,
            runner=None, timeout: int = 90) -> dict:
    """Re-run a saved script with overrides. No model call. Writes version n+1."""
    runner = runner or default_run
    version = db.get_version(project_id, version_n)
    if version is None:
        raise KeyError(version_n)
    script = version.get("script") or ""
    latest = db.latest_version(project_id)
    number = (latest["n"] if latest else version_n) + 1
    db.set_job_state(job_id, "running", attempt=1)
    result = runner(script, overrides, timeout)
    problem = _problem(result)
    accept = bool(result.get("ok")) and problem is None
    status = "done" if accept else "failed"
    paths = _write_artifacts(project_id, number, result.get("files") or {}) if accept else {}
    tree_text = _tree_text(result) if accept else ""
    db.insert_version({
        "id": uuid.uuid4().hex,
        "project_id": project_id,
        "n": number,
        "parent_n": version_n,
        "source": "override",
        "script": _scrub_script(script),
        "params": version.get("params") or "{}",
        "overrides": json.dumps(overrides),
        "glb_path": paths.get("scene.glb"),
        "tree_json": tree_text or None,
        "preview_path": paths.get("preview.png"),
        "render_path": None,
        "profile": version.get("profile") or "",
        "model": version.get("model") or "",
        "status": status,
        "error": None if status == "done" else redact(problem or "")[-REPAIR_TAIL:],
    })
    if status == "done":
        db.touch_project(project_id, active_version=number)
    _record(job_id, {
        "kind": "result",
        "version": number,
        "status": status,
        "error": None if status == "done" else redact(problem or "")[-800:],
    }, 1)
    db.set_job_state(job_id, "done" if status == "done" else "failed", attempt=1)
    return {"project_id": project_id, "job_id": job_id, "version": number, "status": status}


def render_scene(job_id: str, project_id: str, version_n: int, quality: str, *,
                 render_fn=None, timeout: int = 540) -> dict:
    """Render the saved script of one version and store render.png on that row."""
    from app.make.worker import render as default_render
    render_fn = render_fn or default_render
    version = db.get_version(project_id, version_n)
    if version is None:
        raise KeyError(version_n)
    db.set_job_state(job_id, "running", attempt=1)
    overrides = _load_object(version.get("overrides"))
    result = render_fn(version.get("script") or "", overrides, quality, timeout=timeout)
    png = (result.get("files") or {}).get("render.png")
    status = "done" if result.get("ok") and png else "failed"
    path = None
    if status == "done":
        dest = store.var_root() / "make" / project_id / str(version_n)
        dest.mkdir(parents=True, exist_ok=True)
        file = dest / "render.png"
        file.write_bytes(png if isinstance(png, bytes) else png.encode())
        path = str(file.relative_to(store.var_root()))
        db.set_render_path(project_id, version_n, path)
    error = None if status == "done" else redact(result.get("traceback") or "render failed")[-800:]
    _record(job_id, {"kind": "result", "version": version_n, "status": status, "error": error}, 1)
    db.set_job_state(job_id, "done" if status == "done" else "failed", attempt=1)
    return {"project_id": project_id, "job_id": job_id, "version": version_n, "status": status, "render_path": path}
