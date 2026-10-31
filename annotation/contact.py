"""A contact sheet: every detail of a file on one page, with its regions.

For checking the split at a glance, and for a person deciding where to start. Goes under the
store with everything else: it shows the drawing, so it is as private as the drawing.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import names, sheets
from .export import to_pixels

CELL = (340, 300)
HEADER = 26
PAD = 8
COLUMNS = 8
KNOWN = (34, 120, 80, 96)  # a region with a proposed class
UNNAMED = (214, 120, 40, 70)  # a kept region with no class yet


def _thumbnail(store, record) -> Image.Image:
    with Image.open(sheets.sheet_dir(store, record["sheet_id"]) / record["render"]["file"]) as image:
        sheet = image.convert("RGBA")
    over = Image.new("RGBA", sheet.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(over)
    for region in record["regions"]:
        if region["dropped"]:
            continue
        colour = UNNAMED if region["proposal"]["class_id"] == names.UNKNOWN else KNOWN
        for ring in to_pixels(region["rings"], record["render"]):
            draw.polygon([tuple(p) for p in ring], fill=colour)
    sheet = Image.alpha_composite(sheet, over)
    sheet.thumbnail((CELL[0] - 2 * PAD, CELL[1] - HEADER - 2 * PAD), Image.Resampling.LANCZOS)
    return sheet.convert("RGB")


def caption(record) -> str:
    live = [r for r in record["regions"] if not r["dropped"]]
    named = sum(1 for r in live if r["proposal"]["class_id"] != names.UNKNOWN)
    detail = record["source"].get("detail", {})
    frame = " framed" if detail.get("framed") else ""
    groups = ""
    if detail.get("groups_inside", 1) > 1:
        groups = " split" if detail.get("split") else f" x{detail['groups_inside']} frame {detail['frame']}"
    return f"d{detail.get('index', 1):02d}{frame}{groups}: {len(live)} regions, {named} named"


def build(store, out, sheet_ids=None) -> Path:
    """Write the page and return its path."""
    ids = sheet_ids if sheet_ids is not None else sheets.sheet_ids(store)
    records = [sheets.read_sheet(store, s) for s in ids]
    columns = min(COLUMNS, max(len(records), 1))
    rows = (len(records) + columns - 1) // columns
    page = Image.new("RGB", (columns * CELL[0], max(rows, 1) * CELL[1]), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=14)
    for k, record in enumerate(records):
        x, y = (k % columns) * CELL[0], (k // columns) * CELL[1]
        draw.rectangle([x, y, x + CELL[0] - 1, y + CELL[1] - 1], outline=(205, 205, 200))
        draw.text((x + PAD, y + 6), caption(record), fill=(20, 20, 20), font=font)
        thumb = _thumbnail(store, record)
        page.paste(thumb, (x + (CELL[0] - thumb.width) // 2, y + HEADER + (CELL[1] - HEADER - thumb.height) // 2))
    out = sheets.require_output(out)
    page.save(out)
    return out
