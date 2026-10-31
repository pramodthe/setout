"""Upload an image or one PDF page and store a PNG.

The type is sniffed from the bytes. Poppler rasterizes a PDF page. Nothing
larger than 10 MB is kept.
"""
from __future__ import annotations

import base64
import io
import subprocess
import tempfile
from pathlib import Path

from app import store

MAX_BYTES = 10 * 1024 * 1024


class AttachmentError(ValueError):
    """The upload is the wrong type, too large, or the page could not be rasterized."""


def sniff(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF"):
        return "application/pdf"
    return ""


def store_upload(project_id: str, attachment_id: str, data: bytes, *, page: int = 1) -> dict:
    if page < 1:
        raise AttachmentError("page must be 1 or greater")
    if len(data) > MAX_BYTES:
        raise AttachmentError("file is larger than 10 MB")
    kind = sniff(data)
    if kind == "image/png":
        png = data
    elif kind == "image/jpeg":
        png = _jpeg_to_png(data)
    elif kind == "application/pdf":
        png = _pdf_page(data, page)
    else:
        raise AttachmentError("file must be a PNG, JPEG, or PDF")
    if len(png) > MAX_BYTES:
        raise AttachmentError("rasterized image is larger than 10 MB")
    dest = _path(project_id, attachment_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png)
    return {"id": attachment_id, "bytes": len(png), "page": page if kind == "application/pdf" else None}


def load_image(project_id: str, attachment_id: str) -> dict:
    path = _path(project_id, attachment_id)
    if not path.is_file():
        raise AttachmentError(f"unknown attachment {attachment_id}")
    return {"mime": "image/png", "b64": base64.b64encode(path.read_bytes()).decode("ascii")}


def _path(project_id: str, attachment_id: str) -> Path:
    root = (store.var_root() / "make" / project_id / "attachments").resolve()
    path = (root / f"{attachment_id}.png").resolve()
    if not path.is_relative_to(root):
        raise AttachmentError("invalid attachment id")
    return path


def _jpeg_to_png(data: bytes) -> bytes:
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    image = image.convert("RGBA")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _pdf_page(data: bytes, page: int) -> bytes:
    with tempfile.TemporaryDirectory(prefix="setout-make-pdf-") as tmp:
        folder = Path(tmp)
        pdf = folder / "in.pdf"
        pdf.write_bytes(data)
        stem = folder / "page"
        try:
            proc = subprocess.run(
                ["pdftoppm", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(stem)],
                capture_output=True, timeout=30, check=False,
            )
        except FileNotFoundError as exc:
            raise AttachmentError("poppler (pdftoppm) is not installed") from exc
        png = Path(str(stem) + ".png")
        if proc.returncode != 0 or not png.is_file():
            raise AttachmentError("could not rasterize that PDF page")
        return png.read_bytes()
