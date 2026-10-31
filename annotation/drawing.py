"""Read one DXF sheet into a single frame: linework, hatches, text and callouts.

Everything comes out in one set of coordinates. That frame is modelspace, unless
modelspace has no linework and a layout does, in which case that layout is the drawing.
Blocks are expanded. A layout is usually a cover sheet, so the only thing taken from one is
a callout: a leader whose tip lands in a viewport that shows some of the drawing, carried
into modelspace with its text. Everything else on a layout stays out. A block drawn as a
texture of tiny pieces is one object, not hundreds of specks.

Layer names decide nothing here. A layer only counts through what CAD itself does with
it: off, frozen and no-plot layers are not on the sheet.

Sizes on a sheet are not measurements. The drawing may be not to scale, so nothing here
reads a dimension from geometry; `Drawing.units_per_paper_mm` is only how large the
sheet is drawn, used for line widths and render size, and says where it came from.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from ezdxf import bbox as dxf_bbox
from ezdxf import path as dxf_path
from ezdxf.addons.drawing import RenderContext
from ezdxf.entities import DXFGraphic
from ezdxf.tools import text as dxf_text

from app import dxf_ingest
from app.dxf_ingest import DIMENSION_TYPES, LEADER_TYPES, TEXT_TYPES

LINEWORK = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")
HATCHES = ("HATCH", "MPOLYGON")
NOT_ON_THE_SHEET = ("VIEWPORT", "POINT", "IMAGE", "OLE2FRAME", "WIPEOUT", "XLINE", "RAY")
MAX_BLOCK_DEPTH = 16
NOMINAL_TEXT_MM = 2.5  # the usual height of drafted text: turns text size into a plot scale
FLATTEN_OF_TEXT = 0.01  # curve error as a share of text height
TEXTURE_PIECES = 30  # a block with this many pieces, each tiny against the whole, is one object
TEXTURE_SHARE = 0.05  # a piece's median size against the whole block's


@dataclass
class Curve:
    """A flattened line, polyline or curve. Closed ones are not repeated at the end."""

    points: np.ndarray  # (n, 2)
    closed: bool
    handle: str  # the top-level entity: the INSERT, for block content
    type: str  # DXF type of the primitive
    linetype: str
    lineweight: float  # plotted mm
    drawn_width: float  # polyline width in drawing units, 0 when it has none
    space: str = dxf_ingest.MODEL
    in_block: bool = False  # came out of an INSERT, whose handle is `handle`


@dataclass
class HatchArea:
    """A HATCH's boundary: rings read together by the even-odd rule."""

    rings: list
    pattern: str  # the verbatim pattern name; "SOLID" for fills
    scale: float
    angle: float
    handle: str
    space: str = dxf_ingest.MODEL

    @property
    def key(self) -> tuple:
        """What makes two hatches the same hatch on a sheet."""
        return (self.pattern.upper(), round(self.scale, 6), round(self.angle, 3))


@dataclass
class TextItem:
    text: str
    handle: str
    box: tuple | None  # x0, y0, x1, y1
    height: float
    space: str = dxf_ingest.MODEL


@dataclass
class Callout:
    """A leader and its text. `tip` is the drawing end; `toward` is the next point
    back along the leader, so the leader's direction at the tip is tip - toward."""

    text: str | None  # verbatim
    tip: tuple
    toward: tuple
    kind: str  # LEADER | MULTILEADER | line+text
    leader: str  # handle of the leader
    note: str | None  # handle of the text entity
    space: str = dxf_ingest.MODEL


@dataclass
class Drawing:
    curves: list[Curve] = field(default_factory=list)
    hatches: list[HatchArea] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    callouts: list[Callout] = field(default_factory=list)
    frame: str = dxf_ingest.MODEL  # modelspace, or the layout that holds the drawing
    text_height: float | None = None  # median, drawing units
    units_per_paper_mm: float = 1.0
    scale_source: str = "default"
    extra: dict = field(default_factory=dict)  # {leader handle: layout primitives in this frame}, for the render
    skipped: Counter = field(default_factory=Counter)  # what was left out, and why
    notes: list[str] = field(default_factory=list)

    def subset(self, curves, hatches, texts, callouts) -> Drawing:
        """The same sheet cut down to some of its contents, with the same scale."""
        return Drawing(curves=[self.curves[k] for k in curves], hatches=[self.hatches[k] for k in hatches],
                       texts=[self.texts[k] for k in texts], callouts=[self.callouts[k] for k in callouts],
                       frame=self.frame, text_height=self.text_height, units_per_paper_mm=self.units_per_paper_mm,
                       scale_source=self.scale_source, notes=list(self.notes))

    def geometry_box(self):
        """x0, y0, x1, y1 of the linework and hatches, or None."""
        pts = [c.points for c in self.curves] + [r for h in self.hatches for r in h.rings]
        if not pts:
            return None
        all_points = np.concatenate(pts)
        return tuple(float(v) for v in (*all_points.min(axis=0), *all_points.max(axis=0)))


def _plain_note(entity) -> str | None:
    kind = entity.dxftype()
    if kind in TEXT_TYPES or kind == "MTEXT":
        return entity.plain_text()
    return None


def _flatten(path, distance) -> tuple[np.ndarray, bool]:
    points = np.array([(v.x, v.y) for v in path.flattening(distance)], dtype=float)
    closed = len(points) > 2 and bool(np.allclose(points[0], points[-1], atol=1e-9, rtol=0.0))
    return (points[:-1] if closed else points), closed


def _text_height(entity) -> float:
    kind = entity.dxftype()
    if kind == "MTEXT":
        return float(entity.dxf.char_height)
    return float(entity.dxf.get("height", 0.0))


def _drawn_width(entity) -> float:
    if entity.dxftype() != "LWPOLYLINE":
        return 0.0
    width = float(entity.dxf.get("const_width", 0.0) or 0.0)
    if entity.has_width:
        width = max([width] + [max(s, e) for s, e in entity.get_points("se")])
    return width


class _Reader:
    def __init__(self, doc, flatten):
        self.doc = doc
        self.flatten = flatten
        self.out = Drawing()
        self.leader_lines: set[str] = set()  # lines that are leaders: never linework

    # ------------------------------------------------------------ one space

    def walk(self, entities, ctx, space, affine=None, top=None, depth=0):
        for entity in entities:
            if not isinstance(entity, DXFGraphic):
                self.out.skipped["not a drawing entity"] += 1
                continue
            props = ctx.resolve_all(entity)
            kind = entity.dxftype()
            if not props.is_visible:
                self.out.skipped[f"{kind}: not visible"] += 1
                continue
            handle = top or entity.dxf.handle
            if kind == "INSERT":
                self._insert(entity, props, ctx, space, affine, handle, depth)
            elif kind in HATCHES:
                self._hatch(entity, handle, space, affine)
            elif kind in LINEWORK:
                if entity.dxf.handle not in self.leader_lines:
                    self._curve(entity, props, handle, space, affine)
            elif kind in TEXT_TYPES or kind == "MTEXT":
                self._text(entity, handle, space, affine)
            elif kind == "LEADER":
                self._leader(entity, handle, space, affine)
            elif kind in LEADER_TYPES:
                self._mleader(entity, handle, space, affine)
            elif kind in DIMENSION_TYPES or kind == "TOLERANCE":
                self.out.skipped[f"{kind}: annotation"] += 1
            elif kind in NOT_ON_THE_SHEET:
                self.out.skipped[f"{kind}: not linework"] += 1
            else:
                self.out.skipped[f"{kind}: not read"] += 1

    def _insert(self, entity, props, ctx, space, affine, handle, depth):
        if depth >= MAX_BLOCK_DEPTH:
            self.out.skipped["INSERT: nested too deep"] += 1
            return
        ctx.push_state(props)
        try:
            for ref in (entity.multi_insert() if entity.mcount > 1 else [entity]):
                self.walk(ref.virtual_entities(), ctx, space, affine, handle, depth + 1)
                self.walk(ref.attribs, ctx, space, affine, handle, depth + 1)
        except Exception as error:  # a broken block costs its own content, not the sheet
            self.out.skipped[f"INSERT: {type(error).__name__}"] += 1
        finally:
            ctx.pop_state()

    @staticmethod
    def _move(points, affine):
        if affine is None:
            return points
        matrix, offset, _ = affine
        return points @ matrix + offset

    def _curve(self, entity, props, handle, space, affine):
        try:
            path = dxf_path.make_path(entity)
        except Exception:
            self.out.skipped[f"{entity.dxftype()}: no path"] += 1
            return
        if len(path) == 0:
            return
        scale = affine[2] if affine else 1.0
        width = _drawn_width(entity) * scale
        for sub in (path.sub_paths() if path.has_sub_paths else [path]):
            points, closed = _flatten(sub, self.flatten)
            if len(points) < 2:
                continue
            linetype = props.linetype_name.upper()
            if linetype == "STANDARD":  # ezdxf's placeholder for BYBLOCK with nothing to inherit
                linetype = "CONTINUOUS"
            self.out.curves.append(Curve(self._move(points, affine), closed, handle, entity.dxftype(), linetype,
                                         float(props.lineweight), width, space, in_block=handle != entity.dxf.handle))

    def _hatch(self, entity, handle, space, affine):
        rings = []
        for path in dxf_path.from_hatch(entity):
            for sub in (path.sub_paths() if path.has_sub_paths else [path]):
                points, _ = _flatten(sub, self.flatten)
                if len(points) >= 3:
                    rings.append(self._move(points, affine))
        if not rings:
            self.out.skipped["HATCH: no boundary"] += 1
            return
        solid = bool(entity.dxf.get("solid_fill", 0))
        self.out.hatches.append(HatchArea(rings, "SOLID" if solid else str(entity.dxf.get("pattern_name", "")),
                                          float(entity.dxf.get("pattern_scale", 1.0)),
                                          float(entity.dxf.get("pattern_angle", 0.0)), handle, space))

    def _text(self, entity, handle, space, affine):
        text = entity.plain_text()
        box = dxf_ingest._extents(entity)
        scale = affine[2] if affine else 1.0
        if box is not None and affine is not None:
            corners = self._move(np.array([[box[0], box[1]], [box[2], box[3]]]), affine)
            box = (*corners.min(axis=0), *corners.max(axis=0))
        self.out.texts.append(TextItem(text, entity.dxf.handle or handle, box,
                                       _text_height(entity) * scale, space))

    def _leader(self, entity, handle, space, affine):
        vertices = self._move(np.array([(v[0], v[1]) for v in entity.vertices], dtype=float).reshape(-1, 2), affine)
        if len(vertices) < 2:
            self.out.skipped["LEADER: no path"] += 1
            return
        linked = entity.dxf.get("annotation_handle") or None
        note = self.doc.entitydb.get(linked) if linked else None
        self.out.callouts.append(Callout(_plain_note(note) if note else None, tuple(vertices[0]),
                                         tuple(vertices[1]), "LEADER", handle, linked, space))

    def _mleader(self, entity, handle, space, affine):
        text = dxf_text.plain_mtext(entity.get_mtext_content()) if entity.has_mtext_content else None
        attribs = getattr(entity, "block_attribs", None) or []
        if text is None and attribs:
            text = " ".join(a.text for a in attribs if a.text)
        for leader in entity.context.leaders:
            for line in leader.lines:
                points = [(v.x, v.y) for v in line.vertices] + [(leader.last_leader_point.x,
                                                                 leader.last_leader_point.y)]
                points = self._move(np.array(points, dtype=float), affine)
                if len(points) >= 2:
                    self.out.callouts.append(Callout(text, tuple(points[0]), tuple(points[1]), "MULTILEADER",
                                                     handle, None, space))

    # ------------------------------------------------------------ line + text

    def pseudo(self, layout, affine=None, only=None):
        """Plain lines that lead to a text. They are callouts, not linework.

        `pseudo_leaders` says which lines lead to a text. The text itself is picked again
        here: in a stacked column of callouts the nearest centre is often the neighbour's.
        """
        space = dxf_ingest.MODEL if layout is None else layout
        notes = _notes_in(self.doc, layout)
        for line_handle, text_handle in dxf_ingest.pseudo_leaders(self.doc, layout=layout):
            if only is not None and line_handle not in only:
                continue
            line = self.doc.entitydb[line_handle]
            points = (np.array([(line.dxf.start.x, line.dxf.start.y), (line.dxf.end.x, line.dxf.end.y)], dtype=float)
                      if line.dxftype() == "LINE" else np.array(line.get_points("xy"), dtype=float))
            box = dxf_ingest._extents(self.doc.entitydb[text_handle])
            if _gap_to_box(points[0], box) > _gap_to_box(points[-1], box):
                tip, toward, far_end = points[0], points[1], points[-1]
            else:
                tip, toward, far_end = points[-1], points[-2], points[0]
            note = _note_at(far_end, notes)
            self.leader_lines.add(line_handle)
            moved = self._move(np.array([tip, toward]), affine)
            self.out.callouts.append(Callout(note.plain_text(), tuple(moved[0]), tuple(moved[1]), "line+text",
                                             line_handle, note.dxf.handle, space))

    # ------------------------------------------------------------ layouts

    def layouts(self):
        """Callouts on layouts, carried into modelspace. A viewport counts only if it shows
        some of the drawing, and only a leader into one, with its text, comes across."""
        box = self.out.geometry_box()
        views = [v for v in dxf_ingest.viewports(self.doc) if _shows(v, box)]
        self.views = views
        for name in dxf_ingest.layouts(self.doc):
            space = self.doc.paperspace(name)
            local = [v for v in views if v.layout == name]
            content = [e for e in space if isinstance(e, DXFGraphic) and e.dxftype() != "VIEWPORT"]
            if not local:
                if content:
                    self.out.skipped[f"layout {name}: no viewport shows the drawing"] += len(content)
                continue
            pairs = dxf_ingest.pseudo_leaders(self.doc, layout=name)
            view_of_line = {line: self._line_view(self.doc.entitydb[line], local) for line, _ in pairs}
            ctx = RenderContext(self.doc, export_mode=True)
            ctx.set_current_layout(space)
            ctx.current_layout_properties.set_colors("#ffffff")
            brought = 0
            for view in local:
                first = len(self.out.callouts)
                self.pseudo(name, _affine(view), only={h for h, v in view_of_line.items() if v is view})
                for callout in self.out.callouts[first:]:
                    brought += 2
                    self._draw_on_sheet(callout.leader, [callout.leader, callout.note], view)
            for entity in content:
                kind = entity.dxftype()
                view = _entity_view(entity, local) if kind == "LEADER" or kind in LEADER_TYPES else None
                if view is None:
                    continue
                brought += 1
                self.walk([entity], ctx, name, _affine(view))
                linked = entity.dxf.get("annotation_handle") if kind == "LEADER" else None
                self._draw_on_sheet(entity.dxf.handle, [entity.dxf.handle, linked], view)
            if len(content) > brought:
                self.out.skipped[f"layout {name}: not a callout on the drawing"] += len(content) - brought

    def _draw_on_sheet(self, leader, handles, view):
        for handle in handles:
            entity = self.doc.entitydb.get(handle) if handle else None
            if entity is not None:
                self.out.extra.setdefault(leader, []).extend(dxf_ingest.layout_primitives(entity, view))

    @staticmethod
    def _line_view(line, views):
        points = ([line.dxf.start, line.dxf.end] if line.dxftype() == "LINE" else list(line.get_points("xy")))
        return _view_at(points[0], views) or _view_at(points[-1], views)


def _notes_in(doc, layout) -> list:
    """(entity, box) of every text in a space."""
    space = doc.modelspace() if layout is None else doc.paperspace(layout)
    found = [(e, dxf_ingest._extents(e)) for e in space if e.dxftype() in TEXT_TYPES or e.dxftype() == "MTEXT"]
    return [(entity, box) for entity, box in found if box is not None]


def _note_at(point, notes):
    """The text a leader's end belongs to. Texts it reaches (within most of a text height)
    come first, then the nearest insertion point. Reach alone is not enough: a block of
    notes can overlap a column of callouts, and then the end is inside both."""
    def rank(row):
        entity, box = row
        insert = entity.dxf.insert
        return (_gap_to_box(point, box) > 0.75 * _text_height(entity),
                math.hypot(insert.x - point[0], insert.y - point[1]))
    return min(notes, key=rank)[0]


def _gap_to_box(point, box) -> float:
    if box is None:
        return 0.0
    x0, y0, x1, y1 = box
    return math.hypot(max(x0 - point[0], 0.0, point[0] - x1), max(y0 - point[1], 0.0, point[1] - y1))


def _affine(view):
    """(matrix, offset, scale) carrying paper points into modelspace."""
    m = np.array(view.matrix, dtype=float).reshape(4, 4)
    return m[:2, :2], m[3, :2], 1.0 / view.scale


def _view_at(point, views):
    return next((v for v in views if v.contains(point[0], point[1])), None)


def _shows(view, box) -> bool:
    """Whether a viewport's window on modelspace has any of the drawing in it."""
    if box is None:
        return False
    corners = np.array([view.to_model(view.box[k], view.box[m]) for k, m in ((0, 1), (2, 1), (2, 3), (0, 3))])
    low, high = corners.min(axis=0), corners.max(axis=0)
    return bool(low[0] <= box[2] and high[0] >= box[0] and low[1] <= box[3] and high[1] >= box[1])


def _leader_tip(entity):
    vertices = list(entity.vertices)
    return (vertices[0][0], vertices[0][1]) if vertices else (math.inf, math.inf)


def _entity_view(entity, views):
    """The viewport an entity sits in, read at its leader tip or its middle."""
    kind = entity.dxftype()
    if kind in LEADER_TYPES:
        for leader in entity.context.leaders:
            for line in leader.lines:
                if line.vertices:
                    view = _view_at(line.vertices[0], views)
                    if view is not None:
                        return view
        return None
    if kind == "LEADER":
        return _view_at(_leader_tip(entity), views)
    box = dxf_ingest._extents(entity)
    if box is None:
        return None
    return _view_at(((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0), views)


def _quick_text_height(doc) -> float | None:
    heights = []
    for name in [None] + dxf_ingest.layouts(doc):
        space = doc.modelspace() if name is None else doc.paperspace(name)
        scale = 1.0
        if name is not None:
            views = dxf_ingest.viewports(doc, name)
            if not views:
                continue
            scale = 1.0 / views[0].scale
        heights += [_text_height(e) * scale for e in space
                    if e.dxftype() in TEXT_TYPES or e.dxftype() == "MTEXT"]
    heights = [h for h in heights if h > 0]
    return float(np.median(heights)) if heights else None


def _flatten_distance(doc) -> float:
    height = _quick_text_height(doc)
    if height:
        return height * FLATTEN_OF_TEXT
    box = dxf_bbox.extents(doc.modelspace(), fast=True)
    if box.has_data:
        return max(float(box.size.x), float(box.size.y)) / 8000.0 or 0.01
    return 0.01


def _attach_loose_leaders(out: Drawing) -> None:
    """A LEADER with no linked note takes the text nearest its far end, within a text height."""
    for callout in out.callouts:
        if callout.text or not out.texts:
            continue
        near = [(_gap_to_box(callout.toward, t.box), i) for i, t in enumerate(out.texts) if t.space == callout.space]
        if not near:
            continue
        gap, index = min(near)
        text = out.texts[index]
        if gap <= max(text.height, 1e-9):
            callout.text, callout.note = text.text, text.handle


def _collapse_textures(out: Drawing) -> None:
    """A block of many tiny pieces, such as a strip drawn as hundreds of little lines, is one
    object. It comes out as one closed outline round the pieces, along their long axis."""
    groups: dict[str, list] = {}
    for k, curve in enumerate(out.curves):
        if curve.in_block:
            groups.setdefault(curve.handle, []).append(k)
    drop = set()
    fresh = []
    for handle, members in groups.items():
        if len(members) < TEXTURE_PIECES:
            continue
        points = np.concatenate([out.curves[k].points for k in members])
        whole = float(max(np.ptp(points, axis=0)))
        sizes = [float(max(np.ptp(out.curves[k].points, axis=0))) for k in members]
        if whole <= 0 or np.median(sizes) > TEXTURE_SHARE * whole:
            continue
        drop.update(members)
        fresh.append(_outline(points, out.curves[members[0]], handle))
    if drop:
        out.skipped["block pieces read as one object"] += len(drop)
        out.curves = [c for k, c in enumerate(out.curves) if k not in drop] + fresh


def _outline(points, sample, handle) -> Curve:
    """The box round some points along their main axis, as a closed curve."""
    centre = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - centre, full_matrices=False)
    along = (points - centre) @ axes.T
    low, high = along.min(axis=0), along.max(axis=0)
    corners = np.array([(low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1])])
    return Curve(corners @ axes + centre, True, handle, sample.type, sample.linetype, sample.lineweight, 0.0,
                 sample.space, in_block=True)


def _plot_scale(doc, out: Drawing, views) -> None:
    if out.frame != dxf_ingest.MODEL:
        out.units_per_paper_mm, out.scale_source = 1.0, f"layout {out.frame} is drawn on the paper"
    elif views:
        main = max(views, key=lambda v: (v.box[2] - v.box[0]) * (v.box[3] - v.box[1]))
        layout = doc.paperspace(main.layout)
        per_unit = 25.4 if layout.dxf_layout.dxf.get("plot_paper_units", 1) == 0 else 1.0
        out.units_per_paper_mm = 1.0 / (main.scale * per_unit)
        out.scale_source = f"viewport {main.handle} on layout {main.layout}"
        if len({round(v.scale, 9) for v in views}) > 1:
            out.notes.append("viewports differ in scale; the largest sets the sheet scale")
    elif out.text_height:
        out.units_per_paper_mm = out.text_height / NOMINAL_TEXT_MM
        out.scale_source = f"median text height taken as {NOMINAL_TEXT_MM} mm"
    else:
        out.scale_source = "no viewport and no text: 1 drawing unit per paper mm"


def read(doc) -> Drawing:
    """Everything on the sheet that the region and name steps need."""
    reader = _Reader(doc, _flatten_distance(doc))
    reader.views = []
    msp = doc.modelspace()
    ctx = RenderContext(doc, export_mode=True)
    ctx.set_current_layout(msp)
    ctx.current_layout_properties.set_colors("#ffffff")
    reader.pseudo(None)
    reader.walk(msp, ctx, dxf_ingest.MODEL)
    out = reader.out
    has_linework = bool(out.curves or out.hatches)
    if has_linework:
        reader.layouts()
    else:
        for name in dxf_ingest.layouts(doc):  # the whole drawing sits on a layout
            space = doc.paperspace(name)
            if any(isinstance(e, DXFGraphic) and e.dxftype() in LINEWORK + HATCHES for e in space):
                out.frame = name
                layout_ctx = RenderContext(doc, export_mode=True)
                layout_ctx.set_current_layout(space)
                layout_ctx.current_layout_properties.set_colors("#ffffff")
                reader.pseudo(name)
                reader.walk(space, layout_ctx, name)
                break
    heights = [t.height for t in out.texts if t.height > 0 and t.text.strip()]
    out.text_height = float(np.median(heights)) if heights else None
    _attach_loose_leaders(out)
    _collapse_textures(out)
    _plot_scale(doc, out, reader.views)
    return out
