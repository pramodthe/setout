"""One detail of a DXF as an image the model can read, wrapped for `POST /api/documents`.

The model's image input stops growing at about 3.2 MP, so a sheet of details is too coarse for
it however finely it renders. One detail is not: it is enlarged to that budget, which is safe
because drawings are mostly not to scale and nothing is measured from pixels. Nothing here
reads a size off the render, and the app is told none: the PDF holds the picture and no more.

The image goes in the PDF as lossless pixels, one image on one page, so `app/store.py` takes
it as an embedded figure and it reaches the model byte for byte. Pillow's own PDF writer would
turn it into a JPEG, which smears thin lines.
"""
from __future__ import annotations

import hashlib
import json
import math
import zlib
from dataclasses import dataclass
from pathlib import Path

from app import dxf_ingest

from . import details as splitter
from . import pipeline, sheets

MAX_PIXELS = 3_200_000  # where the model's image input stops growing
NEED_PX, WANT_PX = 4.0, 8.0  # the thinnest line work, in pixels


class NoSuchDetail(sheets.Refused):
    """The file has no detail of that number."""


@dataclass
class Render:
    image: object  # a greyscale Pillow image
    thinnest: float  # width of the thinnest kept part, drawing units
    thinnest_px: float
    px_per_unit: float

    @property
    def verdict(self) -> str:
        if self.thinnest_px >= WANT_PX:
            return "good"
        return "enough" if self.thinnest_px >= NEED_PX else "too coarse"


def thinnest(analysis) -> float | None:
    """The thinnest part kept in a detail. A single line has no width of its own: its pen is
    set by the plot, and scales with the render, so it says nothing about what stays apart."""
    widths = [r.width for r in analysis.result.regions if not r.dropped and r.kind != "stroke" and r.width > 0]
    return min(widths) if widths else None


def budget_scale(width, height, margin) -> float:
    """Pixels per unit that fill the budget, margins included: the positive root of
    (width s + 2 margin)(height s + 2 margin) = MAX_PIXELS."""
    if width <= 0 or height <= 0:
        raise ValueError("a detail with no extent cannot be scaled")
    b = 2.0 * margin * (width + height)
    return (-b + math.sqrt(b * b - 4.0 * width * height * (4.0 * margin * margin - MAX_PIXELS))) / (2.0 * width * height)


def render_detail(doc, whole, index: int) -> Render:
    """Detail `index`, as numbered by `propose`, filling the budget."""
    analysis = next((a for a in whole.analyses if a.detail.index == index), None)
    if analysis is None:
        raise NoSuchDetail(f"detail {index}: the file has {len(whole.analyses)}")
    drawing, detail = whole.drawing, analysis.detail
    reach = splitter.ATTACH * (drawing.text_height or 1.0)
    only = splitter.handles_of(drawing, detail) | splitter.annotation_near(doc, whole.sheet, reach).get(index, set())
    if [drawing.curves[k].handle for k in detail.curves].count(detail.frame) == 1:
        only.discard(detail.frame)  # a frame only costs pixels, unless it is one piece of a block
    extra = [p for k in detail.callouts for p in drawing.extra.get(drawing.callouts[k].leader, [])]
    layout = None if drawing.frame == dxf_ingest.MODEL else drawing.frame
    x0, y0, x1, y1 = dxf_ingest.drawn_extents(doc, layout=layout, extra=extra, only=only)
    scale = budget_scale(x1 - x0, y1 - y0, pipeline.MARGIN_PX)
    dpi = scale * 25.4 * drawing.units_per_paper_mm  # lines and text grow with the geometry
    info, image = pipeline.render_sheet(doc, drawing, dpi=dpi, only=only, extra=extra)
    width = thinnest(analysis)
    return Render(image, width or 0.0, (width or 0.0) * info.px_per_unit, info.px_per_unit)


def pdf_of(image) -> bytes:
    """One page, one greyscale image, losslessly. One pixel is one point."""
    width, height = image.size
    pixels = zlib.compress(image.tobytes(), 9)
    page = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents 4 0 R "
                "/Resources << /XObject << /Im0 5 0 R >> >> >>").encode(),
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(page), page),
               (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray "
                f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(pixels)} >>\nstream\n").encode()
               + pixels + b"\nendstream"]
    out = bytearray(b"%PDF-1.5\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, table)
    return bytes(out)


def write(path, index: int, out, store=sheets.DEFAULT_STORE) -> dict:
    """`<out>/<file id>-dNN.png`, `.pdf` and `.json`. What the render came to, for a person:
    the JSON stays beside the files, and never reaches the app."""
    path = Path(path)
    out = sheets.require_output(Path(out))
    out.mkdir(parents=True, exist_ok=True)
    doc = dxf_ingest.load(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    marked = sheets.split_frames(store, pipeline.file_id_of(path, digest))
    render = render_detail(doc, pipeline.analyse_sheet(doc, marked), index)
    stem = out / pipeline.sheet_id_of(path, digest, index)
    png, pdf = stem.with_suffix(".png"), stem.with_suffix(".pdf")
    render.image.save(png)
    pdf.write_bytes(pdf_of(render.image))
    result = {"detail": index, "png": str(png), "pdf": str(pdf), "width": render.image.width, "height": render.image.height,
              "megapixels": round(render.image.width * render.image.height / 1e6, 3),
              "thinnest_px": round(render.thinnest_px, 2), "verdict": render.verdict}
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2))
    return result
