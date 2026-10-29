"""
DAY 2: extract numeric rules from a code/manual PDF into CANDIDATE rules.

    python3 extract.py --pdf ../data/manuals/ncc-10.2.pdf
    python3 extract.py --text ../data/part10.2.txt   (downloaded clause text)
    python3 extract.py --file-id file-abc123     (re-run, skip the upload)
    python3 extract.py --pdf ../data/manuals/ncc-10.2.pdf --inline   (no upload)

Model: MODEL_API_MODEL, else META_MODEL, else muse-spark-1.3.

Writes candidates + flags to data/candidates.json. Candidates are NOT rules:
a human confirms every number against the source before anything is merged
into rules/rules.json. This script never touches rules/ - _must_be_under_data
refuses any --out outside data/.

API SHAPE - verified against https://dev.meta.ai/docs/file-handling and
https://dev.meta.ai/docs/structured-output/ on 28 Oct 2026
--------------------------------------------------------------------------
  Upload once with POST /v1/files (purpose=user_data), reference the id in an
  input_file block. PDFs arrive as text + per-page images; <=50 pages fully
  covered, text to 100 pages. text.format json_schema constrains decoding
  so the output is guaranteed to match the schema below (response_format is
  the Chat Completions name for the same thing). --inline sends the
  PDF as a file_data data URL instead (same input_file block).

  Rule ids are assigned at MERGE time, never by the model.
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path

import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent.parent
API_BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
MODEL = (os.environ.get("MODEL_API_MODEL")
         or os.environ.get("META_MODEL")
         or "muse-spark-1.3")

CANDIDATE_FORMAT = {
    "type": "json_schema",
    "name": "rule_candidates",
    "schema": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "applies_to": {"type": "string"},
                            "parameter": {"type": "string"},
                            "operator": {"type": "string",
                                         "enum": [">=", "<="]},
                            "value": {"type": "number"},
                            "unit": {"type": "string"},
                            "text": {"type": "string"},
                            "clause_ref": {"type": "string"},
                            "drives_geometry": {"type": "boolean"},
                        },
                        "required": ["applies_to", "parameter", "operator",
                                     "value", "unit", "text", "clause_ref",
                                     "drives_geometry"],
                        "additionalProperties": False,
                    },
                },
                "flags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["rules", "flags"],
            "additionalProperties": False,
    },
}

PROMPT = """\
Extract every NUMERIC installation requirement from this document section.
One candidate per requirement. Skip anything without a number.

For each candidate:
- applies_to / parameter: snake_case, e.g. shower_hob / membrane_return_onto_floor_mm
- operator: ">=" for minimums/at-least, "<=" for maximums/at-most
- value: the number exactly as stated. unit: exactly as stated.
- text: ONE sentence paraphrase in your own words. Never copy clause wording.
- clause_ref: the clause/figure number as printed, so a human can check your work.
- drives_geometry: true only if the number sizes a physical part; false for
  coverage/extent notes.

Hard rules, no exceptions:
- If a value cannot be confirmed from this document, OMIT the rule and explain
  in flags. Never invent a number, a clause reference, or an id.
- If a unit is not millimetres, put the requirement in flags instead of
  converting it. Never convert silently.
- Emit structured candidates only. Do not repeat source text back.
"""


def _must_be_under_data(out: Path) -> Path:
    out = (Path.cwd() / out).resolve()
    data = (ROOT / "data").resolve()
    if out != data and data not in out.parents:
        sys.exit(f"Refusing --out outside data/: {out}\n"
                 "Extracted text must never land in tracked files.")
    return out


def upload(api_key: str, path: Path) -> str:
    """POST /files once, so re-runs only send the file_id."""
    boundary = uuid.uuid4().hex
    body = b"".join([
        b"--" + boundary.encode() + b'\r\nContent-Disposition: form-data; '
        b'name="purpose"\r\n\r\nuser_data\r\n',
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{path.name}"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"{API_BASE}/files", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())["id"]


def call_extract(api_key: str, prompt: str,
                 media: dict | None) -> tuple[dict, dict | None]:
    """One streamed request. Returns (candidates, raw usage or None)."""
    content = [{"type": "input_text", "text": prompt}]
    if media is not None:
        content.append(media)
    payload = {
        "model": MODEL,
        "input": [{"type": "message", "role": "user", "content": content}],
        "text": {"format": CANDIDATE_FORMAT},
        "stream": True,
    }
    req = urllib.request.Request(
        f"{API_BASE}/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST",
    )
    text, usage, done = [], None, False
    with urllib.request.urlopen(req, timeout=900) as r:
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


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pdf")
    g.add_argument("--text", help="send a local text file inline (stays in data/)")
    g.add_argument("--file-id", help="reuse an upload from a previous run")
    ap.add_argument("--inline", action="store_true",
                    help="send the PDF as file_data instead of uploading")
    ap.add_argument("--out", default="../data/candidates.json")
    args = ap.parse_args()

    api_key = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY")
    if not api_key:
        sys.exit("META_API_KEY is not set.")
    out = _must_be_under_data(Path(args.out))
    print(f"Model : {MODEL}\nURL   : {API_BASE}/responses")

    prompt = PROMPT
    if args.text:
        tpath = Path(args.text)
        if not tpath.exists():
            sys.exit(f"Not found: {tpath}")
        prompt = (PROMPT + "\n\nDOCUMENT TEXT:\n"
                  + tpath.read_text(encoding="utf-8"))
        media, file_id = None, None
        print(f"Media : {tpath.name} ({tpath.stat().st_size/1e6:.1f} MB, "
              "inline text, no upload)")
    elif args.file_id:
        if args.inline:
            sys.exit("--file-id and --inline are mutually exclusive")
        file_id = args.file_id
        print(f"Media : file_id {file_id} (reused, no upload)")
        media = {"type": "input_file", "file_id": file_id}
    else:
        path = Path(args.pdf)
        if not path.exists():
            sys.exit(f"Not found: {path}\n"
                     "Put the Part 10.2 PDF in data/manuals/ first.")
        size_mb = path.stat().st_size / 1e6
        if args.inline:
            print(f"Media : {path.name} ({size_mb:.1f} MB, inline file_data)")
            b64 = base64.b64encode(path.read_bytes()).decode()
            media = {"type": "input_file", "filename": path.name,
                     "file_data": f"data:application/pdf;base64,{b64}"}
            file_id = None
        else:
            print(f"Media : {path.name} ({size_mb:.1f} MB), uploading...")
            try:
                file_id = upload(api_key, path)
            except urllib.error.HTTPError as e:
                sys.exit(f"Upload failed: HTTP {e.code}  "
                         f"{e.read().decode()[:300]}")
            print(f"        file_id {file_id}  (re-run with --file-id {file_id})")
            media = {"type": "input_file", "file_id": file_id}

    try:
        found, usage = call_extract(api_key, prompt, media)
    except urllib.error.HTTPError as e:
        sys.exit(f"Extraction failed: HTTP {e.code}  {e.read().decode()[:300]}")

    found["model"] = MODEL
    if file_id:
        found["file_id"] = file_id
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, indent=2), encoding="utf-8")

    rules, flags = found.get("rules", []), found.get("flags", [])
    print(f"\n  candidates: {len(rules)}   flags: {len(flags)}")
    for f in flags:
        print(f"  FLAG: {f}")
    if usage:
        print(f"  usage: {json.dumps(usage)}")
    print(f"\n  wrote {out}\n  These are CANDIDATES. Confirm every number against "
          "the source before merging anything into rules/rules.json.")


if __name__ == "__main__":
    main()
