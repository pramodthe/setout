"""Day-1 go/no-go: five detail prompts, scored against a Blender build.

From the repo root, with ``.env`` filled in and not committed:

    python -m eval.make.muse_bpy_eval --target local
    python -m eval.make.muse_bpy_eval --target modal

The Markdown report is written to ``eval/make/out/day1_report.md``. It is
redacted before it is written. Share that file, not the terminal log.

A prompt passes when the script runs, the scene has at least 5 named mesh
objects, at least one collection, and tree.json is valid. A profile is GO
when at least 3 of the 5 prompts pass.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.make.llm import LLMCallError, complete, describe, load_dotenv, load_profiles, system_prompt
from app.make.redact import redact

DEFAULT_OUT = ROOT / "eval" / "make" / "out" / "day1_report.md"
PASS_OBJECTS = 5
GO_AT = 3
MAX_ATTEMPTS = 4  # one write plus three repairs
ERROR_TAIL = 800
REPAIR_TAIL = 4000

PROMPTS = (
    {
        "id": "floor_waste",
        "text": (
            "Bathroom floor waste junction: screed, a membrane with a 100 mm upturn at the wall, "
            "a stainless angle, tile and sealant, on a substrate. Use collections Structure, "
            "Waterproofing and Finishes. Name the meshes Substrate, Screed, Membrane, Upturn, "
            "Stainless Angle, Tile and Sealant."
        ),
    },
    {
        "id": "shower_hob",
        "text": (
            "Shower hob upturn: membrane dressed up and over a hob, with tile and sealant. "
            "Include a substrate, the hob, the membrane, an upturn, tile and sealant as named meshes "
            "in at least two collections."
        ),
    },
    {
        "id": "window_sill",
        "text": (
            "Window sill flashing: a wall, a sill, a flashing tray, an upstand and sealant, "
            "as separate named meshes in at least one collection."
        ),
    },
    {
        "id": "wall_floor",
        "text": (
            "Wall and floor junction with a membrane upturn and a sealant joint. Include substrate, "
            "screed, membrane, upturn and sealant as named meshes in at least one collection."
        ),
    },
    {
        "id": "balcony_threshold",
        "text": (
            "Balcony door threshold with a step down, subsill flashing, membrane and sealant, "
            "plus the interior slab. Five or more named meshes, at least one collection."
        ),
    },
)


def main(argv: list[str] | None = None) -> int:
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Day-1 Muse Spark bpy go/no-go eval")
    parser.add_argument("--target", choices=("local", "modal"), default="local")
    parser.add_argument("--profile", action="append", dest="profiles",
                        help="Profile id to run. Repeat to select more than one. Default: every profile.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    out = Path(args.out)
    try:
        return _run(args.target, args.profiles, out, args.timeout)
    except Exception as exc:
        text = redact(
            "# 3D Make day-1 bpy eval\n\n"
            f"Target: {args.target}\n\n"
            f"The eval stopped: {type(exc).__name__}: {exc}\n"
        )
        _write(out, text)
        sys.stderr.write(redact(traceback.format_exc()))
        print(text)
        return 1


def _run(target: str, profile_ids: list[str] | None, out: Path, timeout: int) -> int:
    load_dotenv()
    _note(f"target {target}; writing {out}")
    data = load_profiles()
    selected = profile_ids or list(data["profiles"])
    sections = []
    runner = None
    for pid in selected:
        info = describe(pid)
        if info["missing"]:
            names = ", ".join(info["missing"])
            _note(f"profile {pid} skipped; missing {names}")
            sections.append({
                "id": pid,
                "label": info["label"],
                "model": info["model"],
                "verdict": "SKIPPED",
                "missing": names,
                "rows": [],
            })
            continue
        if runner is None:
            runner = _runner(target, timeout)
        rows = []
        for prompt in PROMPTS:
            _note(f"profile {pid} prompt {prompt['id']}")
            rows.append(_one_prompt(prompt, info, runner, timeout))
        passed = sum(1 for row in rows if row["pass"])
        sections.append({
            "id": pid,
            "label": info["label"],
            "model": info["model"],
            "verdict": profile_verdict(rows),
            "passed": passed,
            "rows": rows,
        })
    text = redact(render_report(sections, target=target))
    _write(out, text)
    print(text)
    ran = [section for section in sections if section["verdict"] in ("GO", "NO-GO")]
    if not ran:
        return 2
    if any(section["verdict"] == "NO-GO" for section in ran):
        return 1
    return 0


def _runner(target: str, timeout: int):
    if target == "local":
        from app.make.worker import BlenderMissing, blender_version, resolve_blender, run

        binary = resolve_blender()
        if binary is None:
            raise BlenderMissing(
                "Blender was not found. Set BLENDER_BIN or install Blender 4.2 LTS "
                "(pin 4.2.23) and re-run."
            )
        version = blender_version(binary)
        if "4.2" not in version:
            _note(f"warning: {version} is not Blender 4.2 LTS; the harness is written for 4.2")
        def runner(script, overrides, limit):
            return run(script, overrides, timeout=limit, preview=True, blender_bin=binary)
        return runner
    return _modal_runner()


def _modal_runner():
    function = _ensure_modal()

    def runner(script, overrides, limit):
        result = function.remote(script, overrides or {}, int(limit))
        return _normalize(result)

    return runner


def _ensure_modal():
    import modal
    from modal.exception import NotFoundError

    def hydrate():
        import asyncio
        found = modal.Function.from_name("setout-blender", "run")
        asyncio.run(found.hydrate())
        return found

    try:
        return hydrate()
    except NotFoundError:
        _note("setout-blender is not deployed; deploying it (no secrets on this app)")
        subprocess.run(
            [sys.executable, "-m", "modal", "deploy", str(ROOT / "deploy" / "blender_app.py")],
            check=True,
        )
        return hydrate()


def _normalize(result: dict) -> dict:
    files = {}
    for name, payload in (result.get("files") or {}).items():
        if isinstance(payload, str):
            files[name] = base64.b64decode(payload)
        else:
            files[name] = payload
    return {**result, "files": files}


def _one_prompt(prompt: dict, info: dict, runner, timeout: int) -> dict:
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": prompt["text"]},
    ]
    last = {"ok": False, "traceback": "no attempt", "files": {}, "stdout_tail": ""}
    via = ""
    script = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _note(f"  attempt {attempt}")
        try:
            completion = complete(messages, profile=info["id"], attempt=attempt)
        except LLMCallError as exc:
            if _auth_failure(exc):
                raise
            last = {"ok": False, "traceback": str(exc), "files": {}, "stdout_tail": ""}
            script = None
        else:
            via = completion.get("via") or via
            script = completion.get("script")
            if script:
                last = runner(script, {}, timeout)
            else:
                last = {"ok": False, "traceback": "model returned no script", "files": {}, "stdout_tail": ""}
        row = score_build(last)
        row.update({
            "prompt_id": prompt["id"],
            "repairs": attempt - 1,
            "via": via,
            "error_tail": "",
        })
        if row["pass"]:
            return row
        err = redact((last.get("traceback") or last.get("stdout_tail") or "build failed").strip())
        messages.append({"role": "assistant", "content": script or ""})
        messages.append({
            "role": "user",
            "content": "Error:\n" + err[-REPAIR_TAIL:] + "\nReturn the full corrected script.",
        })
    row["pass"] = False
    row["error_tail"] = redact((last.get("traceback") or last.get("stdout_tail") or "").strip())[-ERROR_TAIL:]
    return row


def profile_verdict(rows: list[dict]) -> str:
    passed = sum(1 for row in rows if row.get("pass"))
    return "GO" if passed >= GO_AT else "NO-GO"


def score_build(result: dict) -> dict:
    """Score one harness result. Does not keep file bytes."""
    tree_valid = False
    objects = 0
    collections = 0
    raw = (result.get("files") or {}).get("tree.json")
    if raw:
        try:
            if isinstance(raw, bytes):
                tree = json.loads(raw.decode("utf-8"))
            elif isinstance(raw, str):
                tree = json.loads(raw)
            else:
                tree = raw
            if isinstance(tree, dict) and tree.get("name") and tree.get("type"):
                tree_valid = True
                found = _walk(tree)
                objects = len(found["meshes"])
                collections = max(0, len(found["collections"]) - 1)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
            tree_valid = False
    runs = bool(result.get("ok"))
    passed = runs and objects >= PASS_OBJECTS and collections >= 1 and tree_valid
    return {
        "runs": runs,
        "objects": objects,
        "collections": collections,
        "collections_present": collections >= 1,
        "tree_valid": tree_valid,
        "pass": passed,
    }


def _walk(node: dict) -> dict:
    found = {"meshes": [], "collections": []}
    def visit(item):
        if not isinstance(item, dict):
            return
        if item.get("type") == "mesh" and item.get("name"):
            found["meshes"].append(item["name"])
        if item.get("type") == "collection":
            found["collections"].append(item.get("name"))
        for child in item.get("children") or []:
            visit(child)
    visit(node)
    return found


def render_report(sections: list[dict], *, target: str) -> str:
    """Markdown go/no-go table. Caller redacts the result before writing it."""
    lines = [
        "# 3D Make day-1 bpy eval",
        "",
        f"Target: {target}",
        "",
        "A prompt passes when the script runs, the scene has at least "
        f"{PASS_OBJECTS} named mesh objects, at least one collection, and tree.json is valid. "
        f"A profile is GO when at least {GO_AT} of {len(PROMPTS)} prompts pass.",
        "",
    ]
    for section in sections:
        model = section.get("model") or "(unset)"
        lines.append(f"## {section['label']} (`{section['id']}`)")
        lines.append("")
        lines.append(f"Model: `{model}`")
        lines.append("")
        if section["verdict"] == "SKIPPED":
            lines.append(f"Result: SKIPPED. Not configured; set {section.get('missing') or 'its environment variables'}.")
            lines.append("")
            continue
        lines.append(
            f"Result: {section['verdict']} ({section['passed']}/{len(section['rows'])} prompts passed)"
        )
        lines.append("")
        lines.append("| Prompt | Runs | Named objects | Collections | tree.json | Repairs | Result |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in section["rows"]:
            lines.append(
                "| {prompt} | {runs} | {objects} | {collections} | {tree} | {repairs} | {result} |".format(
                    prompt=_cell(row["prompt_id"]),
                    runs="yes" if row["runs"] else "no",
                    objects=row["objects"],
                    collections="yes" if row["collections_present"] else "no",
                    tree="yes" if row["tree_valid"] else "no",
                    repairs=row["repairs"],
                    result="PASS" if row["pass"] else "FAIL",
                )
            )
        lines.append("")
        tails = [row for row in section["rows"] if row.get("error_tail")]
        if tails:
            lines.append("### Error tails")
            lines.append("")
            for row in tails:
                lines.append(f"#### {section['id']} / {row['prompt_id']}")
                lines.append("")
                lines.append("```")
                lines.append(row["error_tail"].rstrip())
                lines.append("```")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cell(value: str) -> str:
    return str(value).replace("|", "/")


def _auth_failure(exc: LLMCallError) -> bool:
    text = str(exc).lower()
    return "authentication" in text or "permissiondenied" in text or " 401" in text or "(401)" in text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _note(message: str) -> None:
    sys.stderr.write(redact(message) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
