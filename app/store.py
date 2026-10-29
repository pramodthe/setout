"""Document store: uploaded PDFs, checksums, embedded-figure crops.

For this reference PDF the technical drawings are embedded raster figures,
so crops are extracted losslessly via pypdf + pillow (BSD-3 / HPND — no
AGPL renderer). Vector-only pages honestly report zero figures; nothing is
faked. All state lives under var/ (SETOUT_VAR override, gitignored).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

VAR = Path(os.environ.get("SETOUT_VAR", "var"))


def var_root() -> Path:
    VAR.mkdir(parents=True, exist_ok=True)
    return VAR


def db() -> sqlite3.Connection:
    con = sqlite3.connect(var_root() / "setout.db")
    con.execute("""CREATE TABLE IF NOT EXISTS documents(
        id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT, pages INTEGER,
        meta TEXT, created DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS builds(
        id TEXT PRIMARY KEY, detail_id TEXT, doc_id TEXT, state TEXT,
        progress TEXT, spec TEXT, report TEXT,
        created DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    return con


def doc_dir(doc_id: str) -> Path:
    d = var_root() / "docs" / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_dir(job_id: str) -> Path:
    d = var_root() / "builds" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_pdf(filename: str, data: bytes) -> dict:
    from pypdf import PdfReader  # local import: clearer error without dep

    sha = hashlib.sha256(data).hexdigest()
    reader = PdfReader(__import__("io").BytesIO(data))
    doc_id = uuid.uuid4().hex[:12]
    d = doc_dir(doc_id)
    (d / "original.pdf").write_bytes(data)
    figures = []
    for i, page in enumerate(reader.pages):
        try:
            images = list(page.images)
        except Exception:
            images = []
        for img in images:
            try:
                payload = img.data
                name = img.name
            except Exception:
                continue
            # skip tiny icons/logos: keep figures >= 200x200 px
            try:
                from PIL import Image as PILImage
                with PILImage.open(__import__("io").BytesIO(payload)) as im:
                    w, h = im.size
                    fmt = im.format
                    if fmt not in ("JPEG", "PNG"):
                        # e.g. JPEG 2000: browsers and the model API can't
                        # read it; re-encode the decoded pixels as PNG
                        buf = __import__("io").BytesIO()
                        im.save(buf, "PNG")
                        payload, fmt = buf.getvalue(), "PNG"
            except Exception:
                continue
            if w < 200 or h < 200:
                continue
            ext = "jpg" if fmt == "JPEG" else "png"
            stem = name.rsplit(".", 1)[0]
            crop_id = f"p{i}_{stem}"
            (d / f"{crop_id}.{ext}").write_bytes(payload)
            figures.append({"crop_id": crop_id, "page": i, "width": w,
                            "height": h})
    meta = {"pages": [{"index": i,
                       "width_pt": float(reader.pages[i].mediabox.width),
                       "height_pt": float(reader.pages[i].mediabox.height),
                       "text_excerpt": (reader.pages[i].extract_text() or "")[:500],
                       "figures": [f for f in figures if f["page"] == i]}
                      for i in range(len(reader.pages))]}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    con = db()
    con.execute("INSERT INTO documents(id, filename, sha256, pages, meta) "
                "VALUES(?,?,?,?,?)",
                (doc_id, filename, sha, len(reader.pages),
                 json.dumps({"figures": figures})))
    con.commit()
    con.close()
    return {"id": doc_id, "filename": filename, "sha256": sha,
            "pages": len(reader.pages), "figures": figures}


def find_document_by_sha(sha256: str) -> dict | None:
    """Most recent upload of this exact PDF (crops are stored per upload)."""
    con = db()
    row = con.execute("SELECT id FROM documents WHERE sha256=? ORDER BY created DESC",
                      (sha256,)).fetchone()
    con.close()
    return get_document(row[0]) if row else None


def get_document(doc_id: str) -> dict | None:
    con = db()
    row = con.execute("SELECT id, filename, sha256, pages, meta FROM documents "
                      "WHERE id=?", (doc_id,)).fetchone()
    con.close()
    if not row:
        return None
    meta = json.loads(row[4]) if row[4] else {}
    out = {"id": row[0], "filename": row[1], "sha256": row[2],
           "pages": row[3]}
    out.update(meta)
    return out


def figure_path(doc_id: str, crop_id: str) -> Path | None:
    d = doc_dir(doc_id)
    for ext in ("jpg", "png"):
        p = d / f"{crop_id}.{ext}"
        if p.exists():
            return p
    return None
