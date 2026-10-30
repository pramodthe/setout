"""Side-by-side test tiles: correct masks, then each run's predictions, painted with Pillow.

The tiles come from a fixed rule, so every build and every model shows the same ones. Every model
gets the same drawing rule too: its N strongest masks, N being the correct parts on that tile. A
score cut-off would not be fair, because an untrained model's scores are not calibrated on drawings.

A class keeps one colour and fill across images, legend and tables. There are more classes than
the eight hues a palette can keep apart, so identity is hue x fill: the classes that are most often
thin get solid fills, because a hatch does not survive on a part a few pixels wide; the rest get a
hatch. Every mask also has a solid edge, so thin masks read the same whatever their class's fill.
A prediction whose class is not in the dataset, like a pretrained model's COCO label, is outlined.
"""
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from training.eval_seg import segmentation_mask

PANEL_VERSION = 4  # bump when the painting changes, to repaint everything
TILES_PER_KIND = 3
ZOOM_WINDOW, ZOOM_SCALE = 252, 4  # a thin-heavy tile also gets this crop, enlarged back to 1008 px
COCO_NAME = "_annotations.coco.json"
# The dataviz palette's eight hues in their fixed order, light-mode steps: tiles are paper-coloured
# in both colour schemes. Validated with its validate_palette.js.
HUES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
FILLS = ("solid", "hatch45", "hatch135")
HATCH_PERIOD, HATCH_INK = 12, 4
EDGE_ALPHA, FILL_ALPHA, HATCH_ALPHA, GAP_ALPHA = 0.95, 0.34, 0.5, 0.06


@dataclass(frozen=True)
class Style:
    color: str
    fill: str = "solid"

    @property
    def rgb(self):
        return tuple(int(self.color[i:i + 2], 16) for i in (1, 3, 5))


OTHER = Style("#52514e", "outline")  # not one of this dataset's classes: outlined, as such masks pile up


def class_styles(thin_shares):
    """{class name: Style}, most often thin first. Past 24 classes the rest share the neutral style."""
    order = sorted(thin_shares, key=lambda name: (-(thin_shares[name] or 0), name))
    styles = {}
    for i, name in enumerate(order):
        styles[name] = Style(HUES[i % len(HUES)], FILLS[i // len(HUES)]) if i < len(HUES) * len(FILLS) else OTHER
    return styles


def thin_shares(manifest, coco):
    """Class name -> share of thin instances, from the dataset manifest, else counted from the split."""
    per_class = (manifest or {}).get("per_class")
    if per_class:
        return {name: row.get("thin_share") or 0.0 for name, row in per_class.items()}
    names = {c["id"]: c["name"] for c in coco["categories"]}
    total, thin = Counter(), Counter()
    for ann in coco["annotations"]:
        total[names[ann["category_id"]]] += 1
        thin[names[ann["category_id"]]] += bool((ann.get("attributes") or {}).get("thin"))
    return {name: thin[name] / total[name] if total[name] else 0.0 for name in names.values()}


def choose_tiles(coco, per_kind=TILES_PER_KIND):
    """The same tiles every build: the most thin parts, then none or the fewest and the most compact.

    At most one tile per drawing, ties by file name. Returns [(kind, image dict, thin, compact)].
    """
    thin, compact = Counter(), Counter()
    for ann in coco["annotations"]:
        (thin if (ann.get("attributes") or {}).get("thin") else compact)[ann["image_id"]] += 1
    images = sorted(coco["images"], key=lambda i: i["file_name"])
    orders = (("thin", sorted(images, key=lambda i: (-thin[i["id"]], -compact[i["id"]], i["file_name"]))),
              ("compact", sorted(images, key=lambda i: (thin[i["id"]], -compact[i["id"]], i["file_name"]))))
    picks, seen = [], set()
    for kind, order in orders:
        taken = 0
        for img in order:
            drawing = img.get("drawing_id", img["file_name"])
            if taken == per_kind:
                break
            if drawing not in seen:
                seen.add(drawing)
                taken += 1
                picks.append((kind, img, thin[img["id"]], compact[img["id"]]))
    return picks


def _inner(mask):
    """The mask without its one-pixel edge."""
    inner = np.zeros_like(mask)
    inner[1:-1, 1:-1] = (mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1]
                         & mask[1:-1, :-2] & mask[1:-1, 2:])
    return inner


def _alpha(mask, fill, yy, xx):
    inner = _inner(mask)
    alpha = np.zeros(mask.shape, dtype=np.float32)
    alpha[mask] = EDGE_ALPHA
    if fill == "outline":
        alpha[_inner(inner)] = 0.0
    elif fill == "solid":
        alpha[inner] = FILL_ALPHA
    else:
        lines = (xx + yy if fill == "hatch45" else xx - yy) % HATCH_PERIOD < HATCH_INK
        alpha[inner & lines] = HATCH_ALPHA
        alpha[inner & ~lines] = GAP_ALPHA
    return alpha


def paint(base, masks):
    """`base` (h, w, 3) with (mask, Style) pairs laid over it, largest first so thin parts stay on top."""
    out = base.astype(np.float32)
    yy, xx = np.indices(base.shape[:2])
    for mask, style in sorted(masks, key=lambda m: -int(m[0].sum())):
        alpha = _alpha(mask, style.fill, yy, xx)[..., None]
        out = out * (1 - alpha) + np.array(style.rgb, dtype=np.float32) * alpha
    return out.round().astype(np.uint8)


# ---------------------------------------------------------------- panels

@dataclass
class Column:
    key: str  # "gt", or a run id
    title: str
    source: Path | None = None  # the predictions file; None for the correct masks and a run still to come


@dataclass
class TileRow:
    kind: str
    image: dict
    thin: int
    compact: int
    panels: dict = field(default_factory=dict)  # column key -> {"file", "zoom", "masks"}; absent: not painted
    zoom: tuple | None = None  # (x0, y0, x1, y1) of the enlarged crop


@dataclass
class Gallery:
    columns: list
    rows: list
    classes: list  # class names on show, in legend order
    other_classes: bool = False  # a prediction carried a class outside the dataset's list


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))


def _stamp(source):
    stat = Path(source).stat()
    return f"{PANEL_VERSION}:{stat.st_size}:{stat.st_mtime_ns}"


def _load_predictions(path, wanted):
    """Predictions of the wanted images, best score first, or None if unreadable."""
    try:
        rows = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    found = defaultdict(list)
    for row in rows:
        if row.get("image_id") in wanted:
            found[row["image_id"]].append(row)
    return {image_id: sorted(items, key=lambda r: -r.get("score", 0)) for image_id, items in found.items()}


def zoom_box(anns, img):
    """A fixed-size window centred on the thin parts of a tile, kept inside it. None without any."""
    h, w = int(img["height"]), int(img["width"])
    points = [np.argwhere(segmentation_mask(a["segmentation"], h, w)) for a in anns
              if (a.get("attributes") or {}).get("thin")]
    points = [p for p in points if p.size]
    if not points or min(h, w) < ZOOM_WINDOW:
        return None
    y, x = np.median(np.concatenate(points), axis=0)
    x0 = int(np.clip(x - ZOOM_WINDOW / 2, 0, w - ZOOM_WINDOW))
    y0 = int(np.clip(y - ZOOM_WINDOW / 2, 0, h - ZOOM_WINDOW))
    return x0, y0, x0 + ZOOM_WINDOW, y0 + ZOOM_WINDOW


def _masks(rows, img, names, styles):
    """(mask, Style) pairs and the class names found in them."""
    h, w = int(img["height"]), int(img["width"])
    masks, seen = [], set()
    for row in rows:
        name = names.get(row["category_id"])
        masks.append((segmentation_mask(row["segmentation"], h, w), styles.get(name, OTHER)))
        seen.add(name)
    return masks, seen


def build_gallery(dataset_dir, columns, styles, out_dir, per_kind=TILES_PER_KIND):
    """Paint what is out of date under out_dir/images and describe all panels. None with no tiles."""
    dataset_dir, out_dir = Path(dataset_dir), Path(out_dir)
    coco_path = dataset_dir / "test" / COCO_NAME
    try:
        coco = json.loads(coco_path.read_text())
    except (OSError, ValueError):
        return None
    picks = choose_tiles(coco, per_kind)
    if not picks:
        return None
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    book_path = images_dir / "panels.json"
    try:
        book = json.loads(book_path.read_text())
    except (OSError, ValueError):
        book = {}
    names = {c["id"]: c["name"] for c in coco["categories"]}
    anns = defaultdict(list)
    for ann in coco["annotations"]:
        anns[ann["image_id"]].append(ann)
    rows = [TileRow(kind, img, thin, compact, zoom=zoom_box(anns[img["id"]], img) if kind == "thin" else None)
            for kind, img, thin, compact in picks]
    gallery = Gallery(columns, rows, [])
    used, classes = set(), set()
    wanted = {img["id"] for _, img, _, _ in picks}
    for column in columns:
        source = coco_path if column.key == "gt" else column.source
        if source is None:
            continue
        stamp = _stamp(source)
        predictions = None
        for row in rows:
            name = f"{Path(row.image['file_name']).stem}__{_safe(column.key)}.png"
            zoomed = name.replace(".png", "__zoom.png") if row.zoom else None
            entry = book.get(name)
            if entry is None or entry.get("stamp") != stamp or not (images_dir / name).exists():
                tile = dataset_dir / "test" / Path(row.image["file_name"]).name
                if not tile.exists():
                    continue
                if column.key == "gt":
                    items = anns[row.image["id"]]
                else:
                    if predictions is None:
                        predictions = _load_predictions(source, wanted)
                    if predictions is None:
                        continue
                    items = predictions.get(row.image["id"], [])[:len(anns[row.image["id"]])]
                masks, seen = _masks(items, row.image, names, styles)
                base = np.array(Image.open(tile).convert("RGB"))
                painted = Image.fromarray(paint(base, masks))
                painted.save(images_dir / name)
                if row.zoom:
                    size = (ZOOM_WINDOW * ZOOM_SCALE, ZOOM_WINDOW * ZOOM_SCALE)
                    painted.crop(row.zoom).resize(size, Image.Resampling.NEAREST).save(images_dir / zoomed)
                scores = [r["score"] for r in items if "score" in r]
                entry = {"stamp": stamp, "masks": len(masks), "scores": [max(scores), min(scores)] if scores else None,
                         "classes": sorted(n for n in seen if n), "other": None in seen}
                book[name] = entry
            used.update(n for n in (name, zoomed) if n)
            row.panels[column.key] = {"file": f"images/{name}", "masks": entry["masks"], "scores": entry["scores"],
                                      "zoom": f"images/{zoomed}" if zoomed else None}
            classes.update(entry["classes"])
            gallery.other_classes = gallery.other_classes or entry["other"]
    book = {name: entry for name, entry in book.items() if name in used}
    book_path.write_text(json.dumps(book, indent=2))
    for stale in images_dir.glob("*.png"):
        if stale.name not in used:
            stale.unlink()
    order = list(styles)
    gallery.classes = sorted(classes, key=lambda n: order.index(n) if n in order else len(order))
    return gallery
