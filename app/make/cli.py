"""Build one 3D Make version from a prompt, or revise one with feedback.

    python -m app.make.cli "bathroom floor waste junction with a 100mm upturn"
    python -m app.make.cli --project <id> --feedback "make the upturn 150mm and add sealant bead"

Artifacts are written to ``var/make/<project>/<n>/``. The API key is read from
the environment and is not printed.
"""
from __future__ import annotations

import argparse
import sys

from app.make.agent import generate
from app.make.llm import load_dotenv
from app.make.redact import redact


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build a 3D Make detail with Blender")
    parser.add_argument("prompt", nargs="?", help="What to build")
    parser.add_argument("--project", help="Existing project id, required with --feedback")
    parser.add_argument("--feedback", help="Change request. Regenerates the script as the next version")
    parser.add_argument("--profile", default=None, help="Profile id from config/llm_profiles.yaml")
    parser.add_argument("--timeout", type=int, default=90, help="Seconds for one Blender run")
    parser.add_argument("--title", default=None, help="Project title for a new prompt")
    args = parser.parse_args(argv)
    if args.feedback:
        if not args.project:
            parser.error("--feedback needs --project")
        text, mode = args.feedback, "feedback"
    elif args.prompt:
        text, mode = args.prompt, "prompt"
    else:
        parser.error("pass a prompt, or --project and --feedback")
    try:
        result = generate(
            text,
            project_id=args.project,
            mode=mode,
            profile=args.profile,
            timeout=args.timeout,
            title=args.title,
        )
    except (KeyError, ValueError) as exc:
        sys.stderr.write(redact(str(exc)) + "\n")
        return 1
    _print(result)
    return 0 if result["status"] == "done" else 1


def _print(result: dict) -> None:
    latencies = result.get("latencies") or []
    total = round(sum(latencies), 3)
    lines = [
        f"project {result['project_id']}",
        f"version {result['version']}",
        f"status {result['status']}",
        f"attempts {result['attempts']}",
        f"latency_s {total}",
        f"parts {result.get('parts') or 0}",
        f"model {result.get('model') or ''}",
    ]
    if result.get("artifact_dir"):
        lines.append(f"artifacts {result['artifact_dir']}")
    if result.get("warnings"):
        lines.append("warnings " + "; ".join(result["warnings"]))
    if result.get("error"):
        lines.append("error " + result["error"])
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
