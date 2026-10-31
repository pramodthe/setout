"""One file from DXF to reviewable records: details, regions, proposed names and renders.

`analyse` is the whole blind step on one drawing, and `analyse_sheet` splits a file into
its details first. Both take a DXF document and nothing else, so what they propose can be
scored against synthetic truth without the truth being in reach.

`propose_file` writes one record per detail under an annotation store, as
`<store>/<file id>-d<NN>/` with `sheet.json` and `sheet.png`. A detail is the unit of
review and of the dataset, so from here on a record is a detail.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from app import dxf_ingest, ontology, synthetic

from . import details as splitter
from . import names, regions, sheets
from .drawing import Drawing, read

DPI = 200.0  # how a sheet renders: lineweights and text as plotted at this resolution
MARGIN_PX = 24
MAX_PX = 16384


class AlreadyReviewed(Exception):
    """This file carries a person's decisions: it is not redone unless forced."""


@dataclass
class Analysis:
    """One drawing, or one detail of a file."""

    drawing: Drawing
    result: regions.Result
    proposals: dict
    callouts: list
    detail: splitter.Detail | None = None


@dataclass
class SheetAnalysis:
    drawing: Drawing  # the whole file
    sheet: splitter.Sheet
    analyses: list  # one per detail


def _analyse(drawing, detail=None) -> Analysis:
    result = regions.find_regions(drawing)
    proposals, callouts = names.propose(result.regions, drawing.callouts, ontology.load(), result.tolerance, drawing.texts)
    return Analysis(drawing, result, proposals, callouts, detail)


def thin_width(drawing: Drawing, dpi: float = DPI) -> float:
    """The thin rule's width in drawing units on this sheet."""
    return synthetic.Config().thin_px / render_scale(drawing, dpi)


def analyse(doc) -> Analysis:
    """The sheet as one drawing, however many details it holds."""
    analysis = _analyse(read(doc))
    names.spread([analysis], [], ontology.load(), thin_width(analysis.drawing))
    return analysis


def analyse_sheet(doc) -> SheetAnalysis:
    """The file split into details, each analysed, names shared across them."""
    drawing = read(doc)
    sheet = splitter.split(drawing)
    analyses = []
    for detail in sheet.details:
        part = drawing.subset(detail.curves, detail.hatches, detail.texts, detail.callouts)
        analysis = _analyse(part, detail)
        # a frame drawn in loose lines is only found once the region step has closed it
        detail.framed = detail.framed or any((r.dropped or "").startswith(regions.FRAME) for r in analysis.result.regions)
        analyses.append(analysis)
    names.spread(analyses, sheet.legend, ontology.load(), thin_width(drawing))
    return SheetAnalysis(drawing, sheet, analyses)


def render_scale(drawing: Drawing, dpi: float = DPI) -> float:
    """Pixels per drawing unit: the sheet as plotted at `dpi`, so text and lines come out
    the size they would on paper. It is never fitted to the page: shrinking a sheet to
    fit loses thin parts."""
    return dpi / 25.4 / drawing.units_per_paper_mm


def render_sheet(doc, drawing: Drawing, png=None, dpi: float = DPI, only=None, extra=None):
    """(RenderInfo, image): a drawing, or just the entities in `only`, with layout
    annotation drawn in the same frame."""
    logging.getLogger("fontTools").setLevel(logging.ERROR)  # it complains about most system fonts
    layout = None if drawing.frame == dxf_ingest.MODEL else drawing.frame
    extra = [p for leader in drawing.extra.values() for p in leader] if extra is None else extra
    info = dxf_ingest.render_info(doc, dpi=dpi, px_per_unit=render_scale(drawing, dpi), margin_px=MARGIN_PX,
                                  max_px=MAX_PX, layout=layout, extra=extra, only=only)
    info = dataclasses.replace(info, px_per_mm=dpi / 25.4)
    image = dxf_ingest.render_image(doc, info, layout=layout, extra=extra, only=only)
    if png is not None:
        image.save(png)
    return info, image


def _number(value):
    return round(float(value), 6)


def _ring(ring) -> list:
    return [[_number(x), _number(y)] for x, y in ring]


def region_record(region, proposal, ppu, thin_px) -> dict:
    width_px = region.width * ppu
    return {"id": region.id, "kind": region.kind, "rings": [_ring(r) for r in region.rings],
            "handles": list(region.handles), "hatch": region.hatch, "linetypes": list(region.linetypes),
            "lineweight": region.lineweight, "anchor": [_number(v) for v in region.anchor],
            "area": _number(region.area), "width": _number(region.width), "width_px": round(width_px, 3),
            "thin": bool(width_px < thin_px), "dropped": region.dropped, "rejected": False,
            "proposal": proposal, "seconds": 0.0}


def sheet_record(analysis: Analysis, info, source: dict) -> dict:
    drawing, result = analysis.drawing, analysis.result
    onto = ontology.load()
    thin_px = synthetic.Config().thin_px
    ppu = info.px_per_unit
    return {
        "schema": sheets.SCHEMA,
        "sheet_id": source["sheet_id"],
        "source": {k: v for k, v in source.items() if k != "sheet_id"},
        "ontology_version": onto.version,
        "frame": drawing.frame,
        "text_height": drawing.text_height,
        "units_per_paper_mm": _number(drawing.units_per_paper_mm),
        "scale_source": drawing.scale_source,
        "tolerance": dataclasses.asdict(result.tolerance),
        "gap_bridges": result.bridges,
        "render": {"file": "sheet.png", "width": info.width, "height": info.height,
                   "px_per_unit": info.px_per_unit, "px_per_mm": info.px_per_mm,
                   "extents": list(info.extents), "margin_px": info.margin_px, "capped": info.capped},
        "regions": [region_record(r, analysis.proposals[r.id], ppu, thin_px) for r in result.regions],
        "callouts": analysis.callouts,
        "excluded": result.excluded,
        "skipped": dict(sorted(drawing.skipped.items())),
        "notes": list(drawing.notes),
        "excluded_from_export": None,
    }


def file_id_of(path, digest: str) -> str:
    """Every detail of a file shares this prefix."""
    stem = re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-") or "sheet"
    return f"{stem[:44]}-{digest[:8]}"


def sheet_id_of(path, digest: str, index: int = 1) -> str:
    return f"{file_id_of(path, digest)}-d{index:02d}"


def _existing(store, file_id) -> list[str]:
    return [s for s in sheets.sheet_ids(store) if s.rsplit("-d", 1)[0] == file_id]


def propose_file(path, store, root=None, force=False) -> list[dict]:
    """Read one DXF and write a record per detail under `store`. `root` is the folder it
    was found under: the export reads the project from the path below it. A file a person
    has started is never redone unless `force`, because that would throw their work away."""
    path = Path(path)
    started = time.perf_counter()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    file_id = file_id_of(path, digest)
    earlier = _existing(store, file_id)
    if not force and any(sheets.has_decisions(sheets.read_sheet(store, s)) for s in earlier):
        raise AlreadyReviewed(f"{file_id} has review decisions; pass --force to discard them")
    doc = dxf_ingest.load(path)
    whole = analyse_sheet(doc)
    for sheet_id in earlier:
        shutil.rmtree(sheets.sheet_dir(store, sheet_id))
    relative = None
    if root is not None:
        try:
            relative = str(path.resolve().relative_to(Path(root).resolve()))
        except ValueError:
            relative = None
    reach = splitter.ATTACH * (whole.drawing.text_height or 1.0)
    near = splitter.annotation_near(doc, whole.sheet, reach)
    legend = [dataclasses.asdict(entry) for entry in whole.sheet.legend]
    many = len(whole.analyses) > 1
    records = []
    for analysis in whole.analyses:
        detail = analysis.detail
        sheet_id = sheet_id_of(path, digest, detail.index)
        folder = sheets.sheet_dir(store, sheet_id)
        folder.mkdir(parents=True, exist_ok=True)
        only = splitter.handles_of(whole.drawing, detail) | near.get(detail.index, set()) if many else None
        extra = None if not many else [p for k in detail.callouts
                                       for p in whole.drawing.extra.get(whole.drawing.callouts[k].leader, [])]
        info, _ = render_sheet(doc, whole.drawing, folder / "sheet.png", only=only, extra=extra)
        source = {"sheet_id": sheet_id, "file": path.name, "sha256": digest, "relative_path": relative,
                  "file_id": file_id, "details_in_file": len(whole.analyses),
                  "detail": {"index": detail.index, "box": [_number(v) for v in detail.box], "framed": detail.framed,
                             "groups_inside": detail.groups_inside}}
        record = sheet_record(analysis, info, source)
        record["legend"] = legend
        records.append(record)
    seconds = round(time.perf_counter() - started, 4)
    for record in records:
        record["seconds_to_propose"] = round(seconds / len(records), 4)
        sheets.write_sheet(store, record)
    return records
