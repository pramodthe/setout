"""DXF ingest for the data engine: resolved entity records, a census, pixel-aligned
renders with per-entity masks, and a COCO exporter.

Reads DXF only; DWG is converted outside this code. Geometry is used for shapes,
adjacency and masks, never for dimensions: NTS drawings make line lengths
meaningless, so a DIMENSION record carries the printed text beside the measured
value. Layer names are metadata, never labels. Nothing here guesses a class:
`to_coco` writes only the labels the caller supplies.

The rasteriser is a small Pillow backend for ezdxf's drawing add-on. The
frontend does the flattening, linetype dashes, hatch patterns, text glyphs and
block expansion; this module only maps drawing units to pixels, so the sheet
render and every entity mask share one transform.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import bbox as dxf_bbox
from ezdxf import units as dxf_units
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.backend import Backend
from ezdxf.addons.drawing.config import (BackgroundPolicy, ColorPolicy, Configuration,
                                         HatchPolicy)
from ezdxf.entities import DXFGraphic
from ezdxf.lldxf import const
from ezdxf.math import BoundingBox2d
from ezdxf.tools import text as dxf_text
from PIL import Image, ImageChops, ImageDraw

TEXT_TYPES = ("TEXT", "ATTRIB", "ATTDEF")
LEADER_TYPES = ("MULTILEADER", "MLEADER")  # one entity, two DXF names
DIMENSION_TYPES = ("DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION")
MAX_PSEUDO_LEADER_VERTICES = 4  # longer polylines are outlines, not leaders

_INHERITED_LINEWEIGHTS = (const.LINEWEIGHT_BYLAYER, const.LINEWEIGHT_BYBLOCK,
                          const.LINEWEIGHT_DEFAULT)
_INHERITED_COLORS = (const.BYLAYER, const.BYBLOCK)


def load(path):
    """Read a DXF file. DWG is not read here: convert it to DXF first."""
    path = Path(path)
    if path.suffix.lower() == ".dwg":
        raise ValueError(f"{path.name}: DWG must be converted to DXF outside this code")
    return ezdxf.readfile(str(path))


# --- entities -----------------------------------------------------------------

@dataclass
class EntityRecord:
    handle: str
    type: str                  # DXF type: LINE, HATCH, MULTILEADER, ...
    layer: str                 # metadata only, never a label
    linetype: str              # resolved, upper-case
    linetype_explicit: bool    # set on the entity rather than inherited
    linetype_source: str       # "entity" | "layer" | "block"
    color: str                 # resolved "#rrggbb"
    aci: int                   # the entity's own colour index (256 BYLAYER, 0 BYBLOCK)
    color_explicit: bool
    lineweight: float          # resolved, mm
    lineweight_explicit: bool
    bbox: tuple | None         # x0, y0, x1, y1 in drawing units
    text: str | None = None    # TEXT / MTEXT / MLEADER content; DIMENSION displayed text
    style: str | None = None   # text style of TEXT / MTEXT
    block: str | None = None   # INSERT block name
    hatch_pattern: str | None = None   # HATCH pattern name; "SOLID" for solid fills
    dim_kind: str | None = None        # DIMENSION: "measured" | "override" | "suppressed"
    dim_measurement: float | None = None   # DIMENSION: raw measured value, drawing units
    dim_lfac: float | None = None          # DIMENSION: linear factor applied to measured text
    annotation: str | None = None          # LEADER: handle of the annotation it points at


def entities(doc) -> list[EntityRecord]:
    """One record per modelspace entity, properties resolved through BYLAYER / BYBLOCK."""
    msp = doc.modelspace()
    ctx = RenderContext(doc)
    ctx.set_current_layout(msp)
    # Colours as on a white sheet, the same as the render: ACI 7 resolves to black.
    ctx.current_layout_properties.set_colors("#ffffff")
    return [_record(entity, ctx) for entity in msp if isinstance(entity, DXFGraphic)]


def _record(entity, ctx) -> EntityRecord:
    props = ctx.resolve_all(entity)
    stored = entity.dxf.linetype.upper()
    source = {"BYLAYER": "layer", "BYBLOCK": "block"}.get(stored, "entity")
    linetype = props.linetype_name
    if source == "block":
        # BYBLOCK outside a block reference draws continuous; ezdxf names the
        # placeholder "STANDARD".
        linetype = "CONTINUOUS"
    record = EntityRecord(
        handle=entity.dxf.handle, type=entity.dxftype(), layer=entity.dxf.layer,
        linetype=linetype, linetype_explicit=source == "entity", linetype_source=source,
        color=props.color[:7], aci=entity.dxf.color,
        color_explicit=entity.dxf.color not in _INHERITED_COLORS,
        lineweight=props.lineweight,
        lineweight_explicit=entity.dxf.lineweight not in _INHERITED_LINEWEIGHTS,
        bbox=_extents(entity))
    kind = record.type
    if kind in TEXT_TYPES or kind == "MTEXT":
        record.text = entity.plain_text()
        record.style = entity.dxf.style
    elif kind in LEADER_TYPES:
        if entity.has_mtext_content:
            record.text = dxf_text.plain_mtext(entity.get_mtext_content())
    elif kind == "LEADER":
        record.annotation = entity.dxf.get("annotation_handle") or None
    elif kind in DIMENSION_TYPES:
        _dimension_fields(entity, record)
    elif kind == "INSERT":
        record.block = entity.dxf.name
    elif kind == "HATCH":
        record.hatch_pattern = "SOLID" if entity.dxf.solid_fill else entity.dxf.pattern_name
    return record


def _dimension_fields(entity, record):
    stored = entity.dxf.get("text", "")
    if stored == " ":
        record.dim_kind = "suppressed"
    elif stored in ("", "<>"):
        record.dim_kind = "measured"
    else:
        record.dim_kind = "override"
    # The geometry block holds what the CAD application actually printed, with
    # any "<>" placeholder already replaced by the measurement.
    block = entity.get_geometry_block()
    printed = [e.plain_text() for e in block if e.dxftype() in ("MTEXT", "TEXT")] if block else []
    printed = [p for p in printed if p]
    if record.dim_kind == "suppressed":
        record.text = ""
    elif printed:
        record.text = " ".join(printed)
    elif record.dim_kind == "override":
        record.text = stored
    try:
        measured = entity.get_measurement()
    except (TypeError, AttributeError):
        measured = None
    if isinstance(measured, (int, float)):
        record.dim_measurement = float(measured)
    record.dim_lfac = float(entity.override().get("dimlfac", 1.0))


def _extents(entity):
    try:
        box = dxf_bbox.extents([entity], fast=True)
    except Exception:  # unsupported or broken entity: no box rather than a guess
        return None
    if not box.has_data:
        return None
    return (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)


# --- leaders drawn as lines ---------------------------------------------------

def pseudo_leaders(doc, tolerance=None) -> list[tuple[str, str]]:
    """(line handle, text handle) pairs for LINE or short open LWPOLYLINE entities
    with exactly one end at a TEXT / MTEXT box.

    A heuristic for drawings whose leaders are plain lines plus text. It links
    handles, never classes. `tolerance` is in drawing units; by default each
    text's own height.
    """
    msp = doc.modelspace()
    texts = []
    for entity in msp:
        kind = entity.dxftype()
        if kind not in TEXT_TYPES and kind != "MTEXT":
            continue
        box = _extents(entity)
        if box is None:
            continue
        height = entity.dxf.char_height if kind == "MTEXT" else entity.dxf.height
        tol = tolerance if tolerance is not None else float(height)
        texts.append((entity.dxf.handle, box, tol))
    pairs = []
    for entity in msp:
        ends = _free_ends(entity)
        if ends is None:
            continue
        hits = [_nearest_text(point, texts) for point in ends]
        touching = [hit for hit in hits if hit is not None]
        if len(touching) == 1:
            pairs.append((entity.dxf.handle, touching[0]))
    return pairs


def _free_ends(entity):
    kind = entity.dxftype()
    if kind == "LINE":
        return (entity.dxf.start, entity.dxf.end)
    if kind == "LWPOLYLINE" and not entity.closed and 2 <= len(entity) <= MAX_PSEUDO_LEADER_VERTICES:
        points = entity.get_points("xy")
        return (points[0], points[-1])
    return None


def _nearest_text(point, texts):
    x, y = point[0], point[1]
    best, best_distance = None, None
    for handle, (x0, y0, x1, y1), tol in texts:
        if x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol:
            distance = math.hypot(x - (x0 + x1) / 2, y - (y0 + y1) / 2)
            if best is None or distance < best_distance:
                best, best_distance = handle, distance
    return best


# --- census -------------------------------------------------------------------

def census(paths) -> dict:
    """Counts over DXF files: entity types, linetypes (explicit vs inherited), hatch
    patterns, leader kinds, dimension text, blocks, text styles, plus per-file totals.
    Files that fail to load are listed under "errors" rather than aborting the census.
    """
    files, errors = {}, {}
    for path in paths:
        try:
            files[str(path)] = census_doc(load(path))
        except Exception as exc:
            errors[str(path)] = f"{type(exc).__name__}: {exc}"
    total = _merge_counts(files.values())
    total["file_count"] = len(files)
    total["files"] = files
    total["errors"] = errors
    return total


def census_doc(doc) -> dict:
    records = entities(doc)
    types = Counter(r.type for r in records)
    linetypes: dict[str, dict] = {}
    for r in records:
        bucket = linetypes.setdefault(r.linetype, {"explicit": 0, "inherited": 0})
        bucket["explicit" if r.linetype_explicit else "inherited"] += 1
    dims = Counter(r.dim_kind for r in records if r.dim_kind)
    fonts = {style.dxf.name.lower(): style.dxf.font for style in doc.styles}
    text_records = [r for r in records if r.style is not None]
    block_names = [block.name for block in doc.blocks]
    return {
        "entities": len(records),
        "entity_types": dict(types),
        "linetypes": linetypes,
        "linetype_explicit": sum(1 for r in records if r.linetype_explicit),
        "linetype_inherited": sum(1 for r in records if not r.linetype_explicit),
        "hatch_patterns": dict(Counter(r.hatch_pattern for r in records if r.hatch_pattern)),
        "leaders": {
            "LEADER": types["LEADER"],
            "MLEADER": sum(types[t] for t in LEADER_TYPES),
            "line_plus_text": len(pseudo_leaders(doc)),
        },
        "dimensions": {
            "total": sum(dims.values()),
            "measured": dims["measured"],
            "override": dims["override"],
            "suppressed": dims["suppressed"],
        },
        "blocks": {
            "definitions": sum(1 for n in block_names if not n.startswith("*")),
            "anonymous": sum(1 for n in block_names if n.startswith("*")),
            "inserts": dict(Counter(r.block for r in records if r.block)),
        },
        "text": {
            "entities": len(text_records),
            "styles": dict(Counter(r.style for r in text_records)),
            "fonts": dict(Counter(fonts.get(r.style.lower(), "") for r in text_records)),
        },
        "layers": {
            "count": len(doc.layers),
            "linetypes": dict(Counter(layer.dxf.linetype.upper() for layer in doc.layers)),
        },
        "units": doc.units,
        "units_name": dxf_units.unit_name(doc.units),
        "dxfversion": doc.dxfversion,
    }


def _merge_counts(dicts) -> dict:
    """Sum numeric leaves across census dicts, recursing into nested dicts.
    Non-numeric leaves (unit names, versions) stay per file."""
    total: dict = {}
    for item in dicts:
        for key, value in item.items():
            if isinstance(value, dict):
                total[key] = _merge_counts([total.get(key, {}), value])
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] = total.get(key, 0) + value
    return total


# --- render -------------------------------------------------------------------

@dataclass
class RenderInfo:
    """The pixel transform shared by the sheet render and every entity mask.

    `dpi` is pixels per inch of a sheet on which one drawing unit is its
    $INSUNITS length (a 1:1 plot). Lineweights are plotted millimetres and use
    `px_per_mm`; geometry uses `px_per_unit`.
    """
    width: int
    height: int
    dpi: float
    px_per_unit: float
    px_per_mm: float
    units: int                 # $INSUNITS code
    units_name: str
    units_assumed: bool        # unitless file rendered as if millimetres
    capped: bool               # scale reduced to respect max_px
    extents: tuple             # x0, y0, x1, y1 of everything drawn, drawing units
    margin_px: int
    min_stroke_px: int
    path: str | None = None

    def to_pixel(self, x, y):
        x0, _, _, y1 = self.extents
        return (self.margin_px + (x - x0) * self.px_per_unit,
                self.margin_px + (y1 - y) * self.px_per_unit)

    def to_drawing(self, px, py):
        x0, _, _, y1 = self.extents
        return (x0 + (px - self.margin_px) / self.px_per_unit,
                y1 - (py - self.margin_px) / self.px_per_unit)

    def as_dict(self) -> dict:
        return asdict(self)


def render(doc, dpi=96.0, out_png=None, *, max_px=8192, margin_px=16, min_stroke_px=1,
           hatch="pattern") -> RenderInfo:
    """Render modelspace black on white to `out_png` (when given) and return the transform.

    `hatch` is "pattern" (as drawn) or "region" (every HATCH as a solid fill).
    """
    info = render_info(doc, dpi, max_px=max_px, margin_px=margin_px,
                       min_stroke_px=min_stroke_px, out_png=out_png)
    image = render_image(doc, info, hatch=hatch)
    if out_png is not None:
        image.save(out_png)
    return info


def render_info(doc, dpi=96.0, *, max_px=8192, margin_px=16, min_stroke_px=1,
                out_png=None) -> RenderInfo:
    units = doc.units
    known = 0 < units < len(dxf_units.METER_FACTOR) and dxf_units.METER_FACTOR[units] is not None
    assumed = not known
    source = dxf_units.MM if assumed else units
    px_per_unit = dpi * dxf_units.conversion_factor(source, dxf_units.IN)
    extents = drawn_extents(doc) or (0.0, 0.0, 0.0, 0.0)
    span = max(extents[2] - extents[0], extents[3] - extents[1])
    capped = span * px_per_unit + 2 * margin_px > max_px
    if capped:
        px_per_unit = (max_px - 2 * margin_px) / span
    width = math.ceil((extents[2] - extents[0]) * px_per_unit) + 2 * margin_px
    height = math.ceil((extents[3] - extents[1]) * px_per_unit) + 2 * margin_px
    return RenderInfo(width=width, height=height, dpi=dpi, px_per_unit=px_per_unit,
                      px_per_mm=dpi / 25.4, units=units, units_name=dxf_units.unit_name(units),
                      units_assumed=assumed, capped=capped, extents=extents,
                      margin_px=margin_px, min_stroke_px=min_stroke_px,
                      path=str(out_png) if out_png is not None else None)


def render_image(doc, info, *, hatch="pattern"):
    """The sheet as an 8-bit greyscale Pillow image, ink 0 on white."""
    image = Image.new("L", (info.width, info.height), 255)
    backend = _PillowBackend(info)
    backend.begin(image, ink=0)
    frontend = _frontend(doc, backend, _config(info, hatch))
    frontend.draw_layout(doc.modelspace(), finalize=True)
    return image


def _config(info, hatch):
    policy = {"pattern": HatchPolicy.NORMAL, "region": HatchPolicy.SHOW_SOLID}[hatch]
    return Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.BLACK,
        hatch_policy=policy,
        # curve error of a quarter pixel; pattern lines closer than a pixel fill solid
        max_flattening_distance=0.25 / info.px_per_unit,
        min_hatch_line_distance=1.0 / info.px_per_unit,
    )


def _frontend(doc, backend, config):
    ctx = RenderContext(doc)
    ctx.set_current_layout(doc.modelspace())
    return Frontend(ctx, backend, config=config)


def drawn_extents(doc):
    """x0, y0, x1, y1 of everything the drawing add-on draws for modelspace, in
    drawing units, or None for an empty drawing.

    Text glyphs, dimension blocks and leaders are included. `ezdxf.bbox` only
    approximates text, so a page sized from it cuts right-attached callouts.
    """
    backend = _ExtentsBackend()
    # pattern lines never leave their boundary, so the outline is enough here
    config = Configuration(hatch_policy=HatchPolicy.SHOW_OUTLINE)
    _frontend(doc, backend, config).draw_layout(doc.modelspace(), finalize=False)
    box = backend.box
    if not box.has_data:
        return None
    return (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)


class _ExtentsBackend(Backend):
    """Collects the extents of every primitive the frontend emits; draws nothing."""

    def __init__(self):
        super().__init__()
        self.box = BoundingBox2d()

    def set_background(self, color):
        pass

    def draw_point(self, pos, properties):
        self.box.extend([pos])

    def draw_line(self, start, end, properties):
        self.box.extend([start, end])

    def draw_path(self, path, properties):
        if len(path):
            self.box.extend(path.extents())  # control vertices bound the curves

    def draw_filled_paths(self, paths, properties):
        for path in paths:
            self.draw_path(path, properties)

    def draw_filled_polygon(self, points, properties):
        if len(points):
            self.box.extend(points.extents())

    def draw_image(self, image_data, properties):
        pass

    def clear(self):
        self.box = BoundingBox2d()


class _PillowBackend(Backend):
    """ezdxf drawing backend that rasterises into a Pillow image with `info`'s transform."""

    def __init__(self, info):
        super().__init__()
        self.info = info
        self.image = None
        self.draw = None
        self.ink = 0

    def begin(self, image, ink):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.ink = ink

    # ezdxf jiggles every hatch pattern origin with the global random generator
    # (ezdxf.render.hatching) so pattern lines never pass exactly through
    # boundary corners. Seeding it per top-level entity, and restoring it after,
    # makes the sheet render and the entity mask draw the same pixels.
    def enter_entity(self, entity, properties):
        super().enter_entity(entity, properties)
        if len(self.entity_stack) == 1:
            self._random_state = random.getstate()
            random.seed(f"dxf_ingest:{entity.dxf.handle}")

    def exit_entity(self, entity):
        super().exit_entity(entity)
        if not self.entity_stack:
            random.setstate(self._random_state)

    def _px(self, v):
        return self.info.to_pixel(v.x, v.y)

    def _stroke(self, properties) -> int:
        width = properties.lineweight * self.config.lineweight_scaling * self.info.px_per_mm
        return max(self.info.min_stroke_px, round(width))

    def set_background(self, color):
        pass  # the image is created filled

    def draw_point(self, pos, properties):
        x, y = self._px(pos)
        r = self._stroke(properties) / 2
        if r <= 0.5:
            self.draw.point((x, y), fill=self.ink)
        else:
            self.draw.ellipse([x - r, y - r, x + r, y + r], fill=self.ink)

    def draw_line(self, start, end, properties):
        self.draw.line([self._px(start), self._px(end)], fill=self.ink,
                       width=self._stroke(properties))

    def draw_path(self, path, properties):
        points = [self._px(v) for v in path.flattening(self.config.max_flattening_distance)]
        if len(points) < 2:
            return
        width = self._stroke(properties)
        self.draw.line(points, fill=self.ink, width=width, joint="curve" if width > 2 else None)

    def draw_filled_paths(self, paths, properties):
        polygons = []
        for path in paths:
            for sub in (path.sub_paths() if path.has_sub_paths else [path]):
                points = [self._px(v) for v in sub.flattening(self.config.max_flattening_distance)]
                if len(points) >= 3:
                    polygons.append(points)
        self._fill_even_odd(polygons)

    def draw_filled_polygon(self, points, properties):
        vertices = [self._px(v) for v in points.vertices()]
        if len(vertices) >= 3:
            self.draw.polygon(vertices, fill=self.ink)

    def _fill_even_odd(self, polygons):
        """Holes by the even-odd rule, as every ezdxf backend does."""
        if not polygons:
            return
        if len(polygons) == 1:
            self.draw.polygon(polygons[0], fill=self.ink)
            return
        xs = [x for polygon in polygons for x, _ in polygon]
        ys = [y for polygon in polygons for _, y in polygon]
        x0 = max(0, math.floor(min(xs)) - 1)
        y0 = max(0, math.floor(min(ys)) - 1)
        x1 = min(self.image.width, math.ceil(max(xs)) + 2)
        y1 = min(self.image.height, math.ceil(max(ys)) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        accumulated = Image.new("1", (x1 - x0, y1 - y0), 0)
        for polygon in polygons:
            layer = Image.new("1", accumulated.size, 0)
            ImageDraw.Draw(layer).polygon([(x - x0, y - y0) for x, y in polygon], fill=1)
            accumulated = ImageChops.logical_xor(accumulated, layer)
        self.image.paste(self.ink, (x0, y0), accumulated)

    def draw_image(self, image_data, properties):
        pass  # raster references are not part of the line drawing

    def clear(self):
        self.image.paste(255 if self.image.mode == "L" else 0, (0, 0, self.image.width, self.image.height))


# --- masks --------------------------------------------------------------------

@dataclass
class Mask:
    """One entity's pixels on the sheet, stored as the crop inside `box` (x, y, w, h)."""
    handle: str
    type: str
    box: tuple
    bitmap: Image.Image   # mode "1", size (w, h)

    def array(self) -> np.ndarray:
        return np.asarray(self.bitmap, dtype=bool)

    @property
    def area(self) -> int:
        return int(np.count_nonzero(self.array()))

    def full(self, width, height) -> np.ndarray:
        """The mask placed on a (height, width) sheet."""
        sheet = np.zeros((height, width), dtype=bool)
        x, y, w, h = self.box
        sheet[y:y + h, x:x + w] = self.array()
        return sheet

    def intersects(self, other) -> bool:
        ax, ay, aw, ah = self.box
        bx, by, bw, bh = other.box
        x0, y0 = max(ax, bx), max(ay, by)
        x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        if x1 <= x0 or y1 <= y0:
            return False
        a = self.array()[y0 - ay:y1 - ay, x0 - ax:x1 - ax]
        b = other.array()[y0 - by:y1 - by, x0 - bx:x1 - bx]
        return bool(np.any(a & b))

    def rle(self, width, height) -> dict:
        """COCO uncompressed RLE over the whole sheet (column-major, zeros first)."""
        flat = self.full(width, height).flatten(order="F")
        if flat.size == 0:
            return {"size": [height, width], "counts": []}
        changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
        runs = np.diff(np.concatenate(([0], changes, [flat.size]))).tolist()
        if flat[0]:
            runs.insert(0, 0)
        return {"size": [height, width], "counts": runs}


def entity_masks(doc, info, handles=None, *, hatch="region") -> dict[str, Mask]:
    """Rasterise each modelspace entity on its own with `info`'s transform, so every
    mask is pixel-aligned with the sheet render. Entities that draw nothing are left out.

    `hatch` is "region" (a HATCH masks the area it fills, holes excluded) or
    "pattern" (only its drawn pattern lines, as on the sheet).
    """
    msp = doc.modelspace()
    backend = _PillowBackend(info)
    frontend = _frontend(doc, backend, _config(info, hatch))
    scratch = Image.new("1", (info.width, info.height), 0)
    wanted = None if handles is None else set(handles)
    masks = {}
    for entity in msp:
        if not isinstance(entity, DXFGraphic):
            continue
        handle = entity.dxf.handle
        if wanted is not None and handle not in wanted:
            continue
        scratch.paste(0, (0, 0, info.width, info.height))
        backend.begin(scratch, ink=1)
        frontend.draw_entities([entity])
        box = scratch.getbbox()
        if box is None:
            continue
        crop = scratch.crop(box)
        crop.load()
        masks[handle] = Mask(handle=handle, type=entity.dxftype(),
                             box=(box[0], box[1], box[2] - box[0], box[3] - box[1]),
                             bitmap=crop)
    return masks


# --- COCO ---------------------------------------------------------------------

def to_coco(images, masks, labels, path, categories=None) -> dict:
    """Write a COCO instance-segmentation file and return it.

    images: COCO image dicts (id, file_name, width, height; extra keys pass through).
    masks: {image id: {entity handle: Mask}} from `entity_masks`.
    labels: {image id: {entity handle: category name}}, supplied by the caller.
    categories: optional category names fixing the id order; otherwise the
    labels' names sorted. A handle without a label is left out: the ingest
    never guesses a class.
    """
    names = list(categories) if categories is not None else sorted(
        {name for per_image in labels.values() for name in per_image.values()})
    category_ids = {name: index + 1 for index, name in enumerate(names)}
    annotations = []
    for image in images:
        image_id = image["id"]
        per_image = labels.get(image_id, {})
        for handle, mask in masks.get(image_id, {}).items():
            name = per_image.get(handle)
            if name is None:
                continue
            if name not in category_ids:
                raise ValueError(f"label {name!r} is not in categories")
            x, y, w, h = mask.box
            annotations.append({
                "id": len(annotations) + 1,
                "image_id": image_id,
                "category_id": category_ids[name],
                "segmentation": mask.rle(image["width"], image["height"]),
                "area": mask.area,
                "bbox": [x, y, w, h],
                "iscrowd": 0,
                "dxf_handle": handle,
                "dxf_type": mask.type,
            })
    dataset = {
        "info": {"source": "setout.dxf_ingest", "labels": "supplied by the caller"},
        "images": list(images),
        "annotations": annotations,
        "categories": [{"id": category_ids[name], "name": name} for name in names],
    }
    Path(path).write_text(json.dumps(dataset))
    return dataset
