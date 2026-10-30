"""SAM 3.1: locate each part of a detail on its source drawing.

Short concept prompts per part (its short drawing label, its type) against
the drawing turned upright (SAM finds nothing in a sideways drawing). The
best box above a confidence floor becomes a SourceRegion(kind="sam_mask")
in fractions of the upright image, which the viewer draws and links to the
3D. Parts SAM cannot find simply have no region: nothing is guessed.
Request/stream shape verified 27 Oct 2026.
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
# Roles whose parts SAM can't pick out by kind (thin layers, built-up
# substrates, non-physical zones): they're searched by their drawing label only.
LABEL_ONLY_ROLES = {"substrate", "covered_layer", "finish", "zone"}


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
# Search aids only: extra everyday names for a few common object words. They
# never decide which parts are searched, or anything about a part.
SYNONYMS = {"bath": ["bathtub"], "basin": ["washbasin", "sink"], "vanity": ["vanity cabinet"],
            "wc": ["toilet"], "toilet": ["toilet"], "waste": ["floor drain"], "drain": ["floor drain"],
            "grate": ["drain grate"], "screen": ["glass shower screen"], "door": ["door"],
            "tap": ["tap"], "mixer": ["shower mixer"], "tray": ["shower tray"], "window": ["window"]}


def prompts_for(part_type: str, label: str | None, role: str = "object") -> list[str]:
    """Up to three short noun phrases SAM can segment: the part's short
    drawing label, its type, and an everyday synonym. The part's declared role
    decides, never its name: layers and zones get only their label, if any."""
    out = [label] if label and len(label.split()) <= 4 else []
    t = part_type.lower().replace("_", " ").strip()
    if t and role not in LABEL_ONLY_ROLES:
        out.append(t)
        out += [syn for key, syns in SYNONYMS.items() if key in t.split() for syn in syns]
    return list(dict.fromkeys(out))[:3]


def prompt_for(part_type: str, label: str | None, role: str = "object") -> str | None:
    found = prompts_for(part_type, label, role)
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


def locate(png: bytes, parts: list[tuple],
           call=None) -> dict[str, dict]:
    """parts: (id, type, label[, role]). Returns {part_id: box with prompt, c}.

    Each part ranks what SAM found: hits from its own drawing label first,
    then by confidence. When two parts want the same box, the one that found
    it with its own label keeps it. If neither or both did, nobody can tell
    them apart (a basin tap and a shower mixer both asking for "tap"), so the
    box goes to neither. A part that loses a box falls back to its next one."""
    call = call or _call
    todo = [(part[0], pr, pr == part[2]) for part in parts for pr in prompts_for(*part[1:4])]

    def one(item):
        pid, prompt, own = item
        try:
            boxes = [b for b in call(png, prompt) if b["c"] >= MIN_CONFIDENCE]
        except Exception:  # noqa: BLE001 - a failed prompt just finds nothing
            return pid, None
        best = max(boxes, key=lambda b: b["c"], default=None)
        return pid, ({**best, "prompt": prompt, "own": own} if best else None)

    ranked: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for pid, box in ex.map(one, todo):
            if box:
                ranked.setdefault(pid, []).append(box)
    for hs in ranked.values():
        hs.sort(key=lambda b: (not b["own"], -b["c"]))  # stable: prompt order on ties
    banned: list[dict] = []                               # ambiguous: nobody gets these
    lost: dict[str, list[dict]] = {pid: [] for pid in ranked}  # given up to an owner
    while True:
        pick = {}
        for pid, hs in ranked.items():
            h = next((h for h in hs if not any(_same(h, x) for x in banned + lost[pid])), None)
            if h:
                pick[pid] = h
        clash = next((p for p in pick for q in pick if p != q and _same(pick[p], pick[q])), None)
        if clash is None:
            break
        group = [q for q in pick if _same(pick[q], pick[clash])]
        owners = [q for q in group if pick[q]["own"]]
        if len(owners) == 1:
            for q in group:
                if q != owners[0]:
                    lost[q].append(pick[q])
        else:
            banned.append(pick[clash])
    return {pid: {k: v for k, v in h.items() if k != "own"} for pid, h in pick.items()}
