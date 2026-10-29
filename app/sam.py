"""SAM 3.1: locate each part of a detail on its source drawing.

One concept prompt per part (its short drawing label, else its type) against
the drawing turned upright (SAM finds nothing in a sideways drawing). The
best box above a confidence floor becomes a SourceRegion(kind="sam_mask")
in fractions of the upright image, which the viewer draws and links to the
3D. Parts SAM cannot find simply have no region: nothing is guessed.
Request/stream shape as verified in ncc/perception/sam_killcheck.py.
"""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import meta_client

BOX = re.compile(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);"
                 r"w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")
MIN_CONFIDENCE = 0.5
SKIP = {"wall", "floor_slab", "slab", "substrate", "screed", "membrane",
        "primer", "adhesive", "tile", "grout", "bedding", "datum"}


def upright(img: bytes, rotation_deg: int) -> bytes:
    """Rotate clockwise by rotation_deg (what the READ pass reports) to PNG."""
    from PIL import Image
    with Image.open(io.BytesIO(img)) as im:
        out = im.rotate(-int(rotation_deg or 0), expand=True) if rotation_deg else im.copy()
        buf = io.BytesIO()
        out.convert("RGB").save(buf, "PNG")
        return buf.getvalue()


def parse_boxes(text: str) -> list[dict]:
    out = []
    for b in BOX.finditer(text):
        _, x1, y1, x2, y2, w, h, c = b.groups()
        W, H = max(int(w), 1), max(int(h), 1)
        out.append({"x0": int(x1) / W, "y0": int(y1) / H, "x1": int(x2) / W,
                    "y1": int(y2) / H, "c": float(c) if c else 0.0})
    return out


# everyday names SAM knows better than trade terms (bathtub 0.91 vs bath 0.66)
SYNONYMS = {"bath": ["bathtub"], "basin": ["washbasin", "sink"], "vanity": ["vanity cabinet"],
            "wc": ["toilet"], "toilet": ["toilet"], "waste": ["floor drain"], "drain": ["floor drain"],
            "grate": ["drain grate"], "screen": ["glass shower screen"], "door": ["door"],
            "tap": ["tap"], "mixer": ["shower mixer"], "tray": ["shower tray"], "window": ["window"]}


def prompts_for(part_type: str, label: str | None) -> list[str]:
    """Up to three short noun phrases SAM can segment: the part's short
    drawing label, its type, and an everyday synonym. Layers it can't see
    (membranes, screeds, slabs…) get only their label, if any."""
    out = [label] if label and len(label.split()) <= 4 else []
    t = part_type.lower().replace("_", " ").strip()
    if t and not any(t == s or t.startswith(s + " ") or t.endswith(" " + s) for s in SKIP):
        out.append(t)
        out += [syn for key, syns in SYNONYMS.items() if key in t.split() for syn in syns]
    return list(dict.fromkeys(out))[:3]


def prompt_for(part_type: str, label: str | None) -> str | None:
    found = prompts_for(part_type, label)
    return found[0] if found else None


def _call(png: bytes, prompt: str) -> list[dict]:
    e = meta_client._env()
    key = e.get("META_API_KEY") or e.get("MODEL_API_KEY")
    base = (e.get("META_API_BASE") or "https://api.meta.ai/v1").rstrip("/")
    payload = {"model": e.get("META_SAM_MODEL") or "sam-3.1", "stream": True,
               "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
               "input": [{"type": "message", "role": "user", "content": [
                   {"type": "input_text", "text": prompt},
                   {"type": "input_image",
                    "image_url": "data:image/png;base64," + base64.b64encode(png).decode()}]}]}
    req = urllib.request.Request(f"{base}/responses", data=json.dumps(payload).encode(),
                                 method="POST", headers={
                                     "Authorization": f"Bearer {key}",
                                     "Content-Type": "application/json",
                                     "Accept": "text/event-stream"})
    text, done = [], False
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            ev = json.loads(data)
            if ev.get("type") == "response.output_text.delta":
                text.append(ev.get("delta") or "")
            elif ev.get("type") == "response.completed":
                done = True
            elif ev.get("type") in ("error", "response.failed"):
                raise RuntimeError(json.dumps(ev)[:300])
    if not done:  # a dropped stream is not "nothing found"
        raise RuntimeError("SAM stream ended early")
    return parse_boxes("".join(text))


def _same(a: dict, b: dict) -> bool:
    """Two boxes cover the same thing (IoU >= 0.7)."""
    w = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    h = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    inter = w * h
    area = lambda r: (r["x1"] - r["x0"]) * (r["y1"] - r["y0"])  # noqa: E731
    return inter > 0 and inter / (area(a) + area(b) - inter) >= 0.7


def locate(png: bytes, parts: list[tuple[str, str, str | None]],
           call=None) -> dict[str, dict]:
    """parts: (id, type, label). Returns {part_id: best box with prompt, c}.

    A box claimed by two parts (a basin tap and a shower mixer both asking
    for "tap") can't be told apart, so it goes to neither, unless exactly one
    of them found it with its own drawing label."""
    call = call or _call
    todo = [(pid, pr, pr == lab) for pid, t, lab in parts for pr in prompts_for(t, lab)]

    def one(item):
        pid, prompt, own = item
        try:
            boxes = [b for b in call(png, prompt) if b["c"] >= MIN_CONFIDENCE]
        except Exception:  # noqa: BLE001 - a failed prompt just finds nothing
            return pid, None
        best = max(boxes, key=lambda b: b["c"], default=None)
        return pid, ({**best, "prompt": prompt, "own": own} if best else None)

    hits: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for pid, box in ex.map(one, todo):
            if box:
                hits.setdefault(pid, []).append(box)
    found = {}
    for pid, hs in hits.items():
        best = max(hs, key=lambda b: b["c"])  # best name wins
        found[pid] = {**best, "own": any(h["own"] and _same(h, best) for h in hs)}
    out = {}
    for pid, box in found.items():
        rivals = [q for q, b in found.items() if q != pid and _same(box, b)]
        if not rivals or (box["own"] and not any(found[q]["own"] for q in rivals)):
            out[pid] = {k: v for k, v in box.items() if k != "own"}
    return out
