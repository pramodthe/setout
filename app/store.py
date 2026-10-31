"""Document store: uploaded PDFs, checksums, figures.

A figure is one image of a drawing. A PDF yields it in one of two ways, and
every figure records which (`kind`):

  embedded_image  an image stored in the PDF, extracted losslessly via pypdf +
                  pillow (BSD-3 / HPND). dpi is null: nothing was rendered.
  rendered_page   the whole page rasterised by PDFium via pypdfium2 (Apache-2.0
                  or BSD-3; never an AGPL renderer such as PyMuPDF). CAD exports
                  draw in vector, so they hold no image to extract.

_plan_page decides what each page gets. Blank pages and pages of plain text
report no figure; nothing is faked. All state lives under var/ (SETOUT_VAR
override, gitignored).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path

VAR = Path(os.environ.get("SETOUT_VAR", "var"))
DOC_ID = re.compile(r"^[0-9a-f]{12}$")  # uuid4().hex[:12]
CROP_ID = re.compile(r"^p\d+_[A-Za-z0-9_.-]+$")  # p<page>_<pdf image name> or p<page>_rendered

MIN_FIGURE_PX = 200  # a smaller embedded image is an icon, never a figure

# What makes a page a drawing (_plan_page). Set against the reference manual,
# whose page furniture is 25 path segments under 450+ characters (18 per segment),
# whose smallest real figure covers 8% of its page and whose icons cover 0.6%.
# Unproven on real CAD exports, so meta.json records these measures per page. A
# wrong call must cost a spare figure, not a lost drawing.
CHARS_PER_SEGMENT = 12   # more text per segment than this is a page of text
MIN_IMAGE_SHARE = 0.03   # beside line work, an image placed smaller is a logo
MANY_SEGMENTS = 500      # more than a frame, table or title block
LOST_IMAGE_SHARE = 0.25  # images this large that gave no figure still get rendered

# 300 dpi puts a 0.3 mm gap (a 1.5 mm membrane at 1:5) on 3.5 px, which leaves
# clear pixels between its faces wherever they land on the pixel grid. At 150 dpi
# whether they stay apart depends on where they land.
RENDER_DPI = 300
# Holds A2 at 300 dpi, and A1 at 215 dpi where that gap still shows. Well under
# Pillow's 89 MP decompression-bomb warning, which sam.upright would hit.
MAX_RENDER_PIXELS = 36_000_000
MAX_RENDER_BYTES = 37_000_000  # the model API's 50 MB inline limit, after base64
INK_CONTRAST = 32  # grey levels from the background that count as a mark

_PDFIUM = threading.Lock()  # PDFium is not thread-safe, even across documents


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


def _embedded_images(i: int, page) -> list[tuple[dict, bytes, str]]:
    """The images a page stores, as (figure, bytes, extension). Nothing written."""
    from PIL import Image as PILImage

    try:
        images = list(page.images)
    except Exception:
        images = []
    found = []
    for img in images:
        try:
            payload = img.data
            name = img.name
        except Exception:
            continue
        # skip tiny icons/logos: keep figures >= 200x200 px
        try:
            with PILImage.open(io.BytesIO(payload)) as im:
                w, h = im.size
                fmt = im.format
                if fmt not in ("JPEG", "PNG"):
                    # e.g. JPEG 2000: browsers and the model API can't
                    # read it; re-encode the decoded pixels as PNG
                    buf = io.BytesIO()
                    im.save(buf, "PNG")
                    payload, fmt = buf.getvalue(), "PNG"
        except Exception:
            continue
        if w < MIN_FIGURE_PX or h < MIN_FIGURE_PX:
            continue
        ext = "jpg" if fmt == "JPEG" else "png"
        # Only ids CROP_ID would refuse change: pypdf names inline images "~0~".
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", name.rsplit(".", 1)[0])
        found.append(({"crop_id": f"p{i}_{stem}", "page": i, "width": w, "height": h,
                       "kind": "embedded_image", "dpi": None}, payload, ext))
    return found


def _placed_share(obj, bbox: tuple, area: float) -> float:
    """The share of the page an image object covers. PDFium reports an object
    inside a Form XObject in that form's own space, so undo each form on the way
    out; CAD exporters often wrap whole pages in one."""
    x0, y0, x1, y1 = obj.get_bounds()
    form = obj.container
    while form is not None:
        x0, y0, x1, y1 = form.get_matrix().on_rect(x0, y0, x1, y1)
        form = form.container
    left, bottom, right, top = bbox
    return max(min(x1, right) - max(x0, left), 0) * max(min(y1, top) - max(y0, bottom), 0) / area


def _survey(page) -> dict:
    """What PDFium finds on a page: painted line work, text and placed images."""
    import pypdfium2.raw as pdfium_c

    bbox = page.get_bbox()
    area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1.0)
    segments, covered, placed = 0, 0.0, {}
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH, pdfium_c.FPDF_PAGEOBJ_IMAGE]):
        if obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:  # always painted: clip paths are not objects
            segments += pdfium_c.FPDFPath_CountSegments(obj)
        else:
            share = _placed_share(obj, bbox, area)
            size = obj.get_px_size()
            placed[size] = max(placed.get(size, 0.0), share)  # keyed by size: pypdf's names aren't visible here
            covered += share
    text = page.get_textpage()
    try:
        chars = text.count_chars()
        excerpt = text.get_text_range(0, min(chars, 500)) if chars else ""
    finally:
        text.close()
    return {"segments": segments, "chars": chars, "covered": round(covered, 3),
            "text_excerpt": excerpt, "placed": placed}


def _plan_page(found: list[tuple], view: dict) -> tuple[list[tuple], bool]:
    """Which embedded images stay as figures, and whether to render the page.

    A page has line work when it holds painted vector segments and they are not
    outweighed by text, so a page of prose under a few rules stays figureless.
    A page with no real image (none, or only logos: >= 200 px but under 3% of
    the page as placed) is rendered on that alone, so a title-block logo can't
    stand in for the drawing around it. A page with a real image is rendered
    only for line work too big to be its frame, table or title block, and keeps
    that image as a lossless figure beside the rendering. Logos go once a
    rendering shows them. Any other page keeps exactly its embedded images.

    One more case: images that cover much of the page but gave no figure
    (scans cut into strips, or stored in a form pypdf can't decode) are rendered.
    """
    line_work = view["segments"] > 0 and view["segments"] * CHARS_PER_SEGMENT >= view["chars"]
    real = [item for item in found  # not found on the page: listed in /Resources, never drawn
            if view["placed"].get((item[0]["width"], item[0]["height"]), 0.0) >= MIN_IMAGE_SHARE]
    if line_work and (not real or view["segments"] >= MANY_SEGMENTS):
        return real, True
    return found, not found and view["covered"] >= LOST_IMAGE_SHARE


def _has_ink(gray) -> bool:
    """Marks that stand off the page's own background, which need not be white."""
    levels = gray.histogram()
    background = levels.index(max(levels))
    return any(count for level, count in enumerate(levels)
               if abs(level - background) >= INK_CONTRAST)


def _render_page(page, user_unit: float) -> tuple[bytes, int, int, float] | None:
    """The page as PNG bytes with width, height and true dpi, or None if blank."""
    from PIL import ImageChops

    width, height = page.get_size()
    # PDFium ignores /UserUnit: its page units are user_unit/72 inch, not 1/72.
    scale = min(RENDER_DPI / 72 * user_unit, (MAX_RENDER_PIXELS / max(width * height, 1.0)) ** 0.5)
    while True:
        bitmap = page.render(scale=scale)
        try:
            rgb = bitmap.to_pil()  # a copy: PDFium gives BGR here, which PIL converts
        finally:
            bitmap.close()
        gray = rgb.convert("L")
        if not _has_ink(gray):
            return None
        # Plotted drawings are monochrome, and grey PNGs are half the size.
        mono = ImageChops.difference(rgb, gray.convert("RGB")).getbbox() is None
        buf = io.BytesIO()
        (gray if mono else rgb).save(buf, "PNG")
        if buf.tell() <= MAX_RENDER_BYTES:
            return buf.getvalue(), rgb.width, rgb.height, round(scale * 72 / user_unit, 1)
        scale *= 0.7  # a large raster on the page; lines would suffer as JPEG


def _page_figures(i: int, page, pdf) -> tuple[list[tuple], dict, str | None]:
    """A page's (figure, bytes, extension) entries, notes for meta.json and any
    warning. A PDFium failure must not cost what pypdf can still read."""
    notes = {"text_excerpt": ""}
    if pdf is None:
        return _embedded_images(i, page), notes, None
    found, handle = None, None
    try:
        handle = pdf.get_page(i)
        view = _survey(handle)
        notes = {key: value for key, value in view.items() if key != "placed"}
        # A big vector page with no image drawn on it: skip pypdf's slow, pure
        # Python walk of its content stream. Nothing there can become a figure.
        found = [] if view["segments"] >= MANY_SEGMENTS and not view["placed"] else _embedded_images(i, page)
        keep, render = _plan_page(found, view)
        rendered = _render_page(handle, float(page.user_unit)) if render else None
        # Logos were dropped because a rendering shows them; a blank one doesn't.
        entries = list(found if render and not rendered else keep)
        if rendered:
            png, width, height, dpi = rendered
            entries.append(({"crop_id": f"p{i}_rendered", "page": i, "width": width, "height": height,
                             "kind": "rendered_page", "dpi": dpi}, png, "png"))
        return entries, notes, None
    except Exception as error:  # noqa: BLE001 - reported to the user with the upload
        entries = _embedded_images(i, page) if found is None else found
        return entries, notes, f"page {i + 1} could not be rendered: {str(error)[:200]}"
    finally:
        if handle is not None:
            handle.close()


def page_text(data: bytes, page: int) -> str:
    """A PDF page's text layer, or nothing: a scan or an embedded image has none."""
    import pypdfium2

    with _PDFIUM:
        try:
            pdf = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError:
            return ""
        try:
            handle = pdf.get_page(page)
            layer = handle.get_textpage()
            try:
                return layer.get_text_range()
            finally:
                layer.close()
                handle.close()
        finally:
            pdf.close()


def store_pdf(filename: str, data: bytes) -> dict:
    from pypdf import PdfReader  # local import: clearer error without dep
    import pypdfium2

    sha = hashlib.sha256(data).hexdigest()
    reader = PdfReader(io.BytesIO(data))
    doc_id = uuid.uuid4().hex[:12]
    d = doc_dir(doc_id)
    (d / "original.pdf").write_bytes(data)
    figures, notes, warnings = [], [], []
    with _PDFIUM:
        try:
            pdf = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError as error:
            pdf = None
            warnings.append(f"only embedded images were read; PDFium could not open this file: {error}")
        try:
            for i, page in enumerate(reader.pages):
                entries, page_notes, warning = _page_figures(i, page, pdf)
                for figure, payload, ext in entries:
                    (d / f"{figure['crop_id']}.{ext}").write_bytes(payload)
                    figures.append(figure)
                notes.append(page_notes)
                if warning:
                    warnings.append(warning)
        finally:
            if pdf is not None:
                pdf.close()
    meta = {"pages": [{"index": i,
                       "width_pt": float(reader.pages[i].mediabox.width),
                       "height_pt": float(reader.pages[i].mediabox.height),
                       **notes[i],
                       "figures": [f for f in figures if f["page"] == i]}
                      for i in range(len(reader.pages))]}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    con = db()
    con.execute("INSERT INTO documents(id, filename, sha256, pages, meta) "
                "VALUES(?,?,?,?,?)",
                (doc_id, filename, sha, len(reader.pages),
                 json.dumps({"figures": figures, "warnings": warnings})))
    con.commit()
    con.close()
    return {"id": doc_id, "filename": filename, "sha256": sha,
            "pages": len(reader.pages), "figures": figures, "warnings": warnings}


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
           "pages": row[3], "warnings": []}
    out.update(meta)
    # Uploads from before rendered pages hold only extracted images.
    out["figures"] = [{"kind": "embedded_image", "dpi": None, **f} for f in out.get("figures", [])]
    return out


def figure_path(doc_id: str, crop_id: str) -> Path | None:
    if not DOC_ID.match(doc_id) or not CROP_ID.match(crop_id) or ".." in crop_id:
        return None
    d = var_root() / "docs" / doc_id  # look up only; never create a directory
    for ext in ("jpg", "png"):
        p = d / f"{crop_id}.{ext}"
        if p.exists():
            return p
    return None
