"""
DAY 1 KILL-CHECK: can SAM 3.1 find waterproofing components in a real bathroom?

This answers the single question that decides the project's scope:
does concept segmentation work on the things a waterproofer looks at?

    python sam_killcheck.py --video ../data/videos/bathroom.mp4
    python sam_killcheck.py --video-file-id file-abc123     (re-run, skip the upload)
    python sam_killcheck.py --image ../data/frames/junction.jpg

Set META_API_KEY first (see .env.example). MODEL_API_KEY, the name Meta's docs
use, works too.

API SHAPE - verified against https://dev.meta.ai/docs/sam/ on 27 Oct 2026
-------------------------------------------------------------------------
  POST {META_API_BASE}/responses with model "sam-3.1". There is no separate
  SAM endpoint; it is the OpenAI-style Responses API. One concept per request:
  an input_text part plus an input_image (data URL) or an input_video (file_id
  from POST /files, purpose=user_data).

  The output is NOT JSON detections. It streams as response.output_text.delta
  events whose text is one line per frame:
      <0f>3<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=0.96|><|mask;...|>,7<|box;...
  No matches = the stream completes with no deltas. That is a valid "nothing".

  Video: MP4, H.264/H.265, 50 to 15,000 frames, up to 16 tracked objects.
  Convert a phone .MOV first:
      ffmpeg -i in.MOV -c:v libx264 -an -movflags +faststart out.mp4
  Cost is per prompt: $2.50 per 1k images, $0.20 per 1k video frames.
  A 30 s clip at 30 fps x 9 prompts is ~8,100 frames, ~$1.60.

WHAT A PASS LOOKS LIKE
----------------------
  Tier A prompts (floor waste, pipe penetration, shower hob, shower screen track)
      -> these are discrete objects. SAM should do well. If these fail,
         something is wrong with your setup, not with the idea.
  Tier B prompts (wall floor junction, internal corner)
      -> edges and creases. Moderate confidence is fine; you only need an anchor.
  Tier C prompts (membrane edge, primer coverage)
      -> a colour change on a flat plane. THIS IS THE RISKY ONE.

DECISION RULE
-------------
  Tier A works                -> project proceeds. Anchor overlays on objects.
  Tier A + B work             -> ideal. Anchor on junctions too.
  Only Tier C fails           -> fine. Drop membrane-edge detection from scope.
  Tier A fails                -> stop. Re-check API/key/footage before pivoting.
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import uuid
from pathlib import Path

import urllib.request
import urllib.error

API_BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
MODEL = os.environ.get("META_SAM_MODEL", "sam-3.1")

PROMPTS = [
    ("floor waste",          "A", "Round drain in the shower floor. Discrete object."),
    ("pipe penetration",     "A", "Pipe through the floor or wall. Discrete object."),
    ("shower hob",           "A", "Raised kerb at the shower edge."),
    ("shower screen track",  "A", "Metal track the screen sits in."),
    ("wall floor junction",  "B", "The crease where wall meets floor."),
    ("internal corner",      "B", "Vertical crease where two walls meet."),
    ("tiled wall",           "B", "Large plane. Sanity check."),
    ("membrane edge",        "C", "RISKY: colour change on a flat plane."),
    ("primer coverage",      "C", "RISKY: subtle tone difference."),
]

# A frame marker starts a line or directly follows the previous mask token.
FRAME = re.compile(r"(?:^|(?<=\|>))<(\d+)f>", re.M)
# The box token is matched strictly so a base85 mask payload can't pass for one.
BOX = re.compile(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);"
                 r"w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")


def encode(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def upload(api_key: str, path: Path) -> str:
    """POST /files once, so nine prompts don't each re-send the video."""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\n'
        f"user_data\r\n".encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode(),
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


def call_sam(api_key: str, media: dict, prompt: str) -> tuple[str, int | None]:
    """One concept, one streamed request. Returns (token text, frames billed)."""
    payload = {
        "model": MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt}, media]}],
        "stream": True,
        # Metadata values are strings in the docs. one_bit keeps the masks
        # small; the kill-check only counts detections.
        "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
    }
    req = urllib.request.Request(
        f"{API_BASE}/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST",
    )
    text, billed, done = [], None, False
    with urllib.request.urlopen(req, timeout=300) as r:
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
                usage = (ev.get("response") or {}).get("usage") or {}
                billed = usage.get("video_frames_processed")
            elif kind in ("error", "response.failed"):
                raise RuntimeError(json.dumps(ev)[:300])
    # A dropped stream must not be read as "nothing found".
    if not done:
        raise RuntimeError("stream ended before response.completed")
    return "".join(text), billed


def parse_records(text: str) -> list[dict]:
    """One dict per (frame, object) from SAM's token text. Masks are skipped."""
    marks = list(FRAME.finditer(text))
    recs = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        for b in BOX.finditer(text, m.end(), end):
            oid, x1, y1, x2, y2, w, h, c = b.groups()
            recs.append({"frame": int(m.group(1)), "id": int(oid),
                         "box": [int(x1), int(y1), int(x2), int(y2)],
                         "frame_size": [int(w), int(h)],
                         "c": float(c) if c else None})
    return recs


def summarise(recs: list[dict]) -> dict:
    """Distinct objects, frames with any hit, and the best confidence."""
    return {
        "n": len({r["id"] for r in recs}),
        "frames_hit": len({r["frame"] for r in recs}),
        "best_score": max((r["c"] for r in recs if r["c"] is not None),
                          default=None),
    }


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--video")
    g.add_argument("--video-file-id", help="reuse an upload from a previous run")
    g.add_argument("--image")
    ap.add_argument("--out", default="killcheck_results.json")
    args = ap.parse_args()

    api_key = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY")
    if not api_key:
        sys.exit("META_API_KEY is not set. Copy .env.example and fill it in.")

    is_video = not args.image
    print(f"Model : {MODEL}\nURL   : {API_BASE}/responses")

    if args.image:
        path = Path(args.image)
        if not path.exists():
            sys.exit(f"Not found: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        print(f"Media : {path.name} ({path.stat().st_size/1e6:.1f} MB)\n")
        media = {"type": "input_image",
                 "image_url": f"data:{mime};base64,{encode(path)}"}
    else:
        file_id = args.video_file_id
        if not file_id:
            path = Path(args.video)
            if not path.exists():
                sys.exit(f"Not found: {path}\n"
                         "Put a real bathroom clip in data/videos/ first.")
            if path.suffix.lower() != ".mp4":
                print("WARNING: docs list MP4 for uploads. If this is rejected:\n"
                      "  ffmpeg -i in.MOV -c:v libx264 -an -movflags +faststart out.mp4")
            print(f"Media : {path.name} ({path.stat().st_size/1e6:.1f} MB), uploading...")
            try:
                file_id = upload(api_key, path)
            except urllib.error.HTTPError as e:
                sys.exit(f"Upload failed: HTTP {e.code}  {e.read().decode()[:300]}")
            print(f"        file_id {file_id}  (re-run with --video-file-id {file_id})")
        print()
        media = {"type": "input_video", "file_id": file_id}

    rows = []
    for prompt, tier, why in PROMPTS:
        try:
            text, billed = call_sam(api_key, media, prompt)
            recs = parse_records(text)
            if text.strip() and not recs:
                raise RuntimeError(f"output did not parse: {text[:200]!r}")
            s = summarise(recs)
            ok = s["n"] > 0
            best = "-" if s["best_score"] is None else f"{s['best_score']:.2f}"
            cover = (f"  frames={s['frames_hit']}/{billed if billed else '?'}"
                     if is_video else "")
            print(f"  [{tier}] {prompt:<22} {'FOUND' if ok else 'nothing':>8}"
                  f"  n={s['n']:<3} best={best:<4}{cover}")
            rows.append({"prompt": prompt, "tier": tier, "why": why,
                         "found": ok, **s, "frames_billed": billed,
                         "records": recs})
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            print(f"  [{tier}] {prompt:<22} HTTP {e.code}  {body}")
            rows.append({"prompt": prompt, "tier": tier, "error": f"{e.code} {body}"})
        except Exception as e:
            print(f"  [{tier}] {prompt:<22} ERROR  {e}")
            rows.append({"prompt": prompt, "tier": tier, "error": str(e)})

    Path(args.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")

    def hit(t):
        c = [r for r in rows if r["tier"] == t]
        return sum(1 for r in c if r.get("found")), len(c)

    a, at = hit("A"); b, bt = hit("B"); c, ct = hit("C")
    print(f"\n  Tier A (objects)   {a}/{at}")
    print(f"  Tier B (junctions) {b}/{bt}")
    print(f"  Tier C (surfaces)  {c}/{ct}")

    errs = sum(1 for r in rows if "error" in r)
    if errs:
        print(f"\n  {errs} prompt(s) errored. Fix those before trusting the verdict.")

    print("\nVERDICT")
    if a == 0:
        print("  STOP. No discrete objects found. Check API shape, key and footage")
        print("  BEFORE concluding anything about the idea.")
    elif a >= 2 and b >= 1:
        print("  GO. Anchor overlays on objects AND junctions. Full scope.")
    elif a >= 2:
        print("  GO, reduced. Anchor on objects only. Drop junction-anchored overlays.")
    else:
        print("  MARGINAL. Reshoot with better light and steadier framing, retry once.")
    if c == 0:
        print("  Expected: drop membrane-edge detection. It was never load-bearing.")
    print(f"\n  wrote {args.out}")


if __name__ == "__main__":
    main()
