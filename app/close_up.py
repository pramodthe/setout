"""Close-ups of a drawing's parts, for the CAD writer and the critic.

A model sees a drawing shrunk to fit, so a small part is a few pixels of what it
reads. Before a part is modelled it is cut out of the full-resolution figure and
enlarged. Finding it takes two model calls. Measured 30 Oct on a real section,
against boxes marked by hand:

- SAM found nothing on line work: 0 of 17 prompts.
- One call on the bare image put small parts in the wrong place at a stated
  confidence of 0.9, so a model's own confidence decides nothing here.
- A coordinate grid drawn on the image fixed the placing, but a bent plate was boxed
  along its flat run, and a repeat of the call agreed with the mistake.
- A second call that sees the boxes drawn on the figure, told they may be wrong,
  caught all 9 boxes we had moved on purpose and got the bent plate right.

So a box is used when the second call accepts it, or corrects it to the same spot.
A correction that moves it elsewhere means the two calls disagree, and the writer
gets the whole drawing instead: a missing close-up costs little, a wrong one misleads.

The crop shows shape and topology and nothing else. Drawings are mostly not to
scale, so no size is ever read from it: dimensions stay named values.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

MAX_PIXELS = 3_200_000  # the model's input stops growing at about this size
MAX_ENLARGE = 8.0       # past this an enlargement only shows the blur between pixels
GRID_STEP = 50          # grid lines, in thousandths of the image; labels every 100
MARGIN = 0.25           # of the box's longer side on every edge: a long thin box still shows what it turns into
MIN_MARGIN = 0.025      # of the figure's longer side: a tiny part keeps its neighbours
MIN_AGREEMENT = 0.5     # share of the smaller box inside the larger, for a corrected box to count as the same spot
MAX_CROP_SHARE = 0.6    # a crop this much of the figure is the figure: it gains nothing
PROOF_ENLARGE = 3.0     # labels read better larger, and the figure needs no more than this
SHEET_ROWS = 12         # objects on one shape sheet, which stays under MAX_PIXELS
SHEET_CELL = (400, 290)
BOX_COLOURS = [(0, 130, 0), (160, 0, 160), (0, 120, 160), (200, 100, 0), (120, 80, 0), (0, 0, 0), (220, 0, 90), (0, 90, 90), (90, 0, 200)]

_BOX = {name: {"type": "integer"} for name in ("x0", "y0", "x1", "y1")}
LOCATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["parts"], "properties": {"parts": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "found", *_BOX],
                               "properties": {"id": {"type": "string"}, "found": {"type": "boolean"}, **_BOX}}}}}
CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["parts"], "properties": {"parts": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "verdict", *_BOX],
                               "properties": {"id": {"type": "string"}, **_BOX, "verdict": {
                                   "type": "string", "enum": ["holds_whole_part", "corrected", "cannot_tell"]}}}}}}

LOCATE_PROMPT = (
    "Locate each listed part on this construction drawing, so that we can cut a close-up of it. A printed label is joined "
    "to its part by a leader line: follow the leader to where it ends, and box the part it touches, not the words. Box the whole "
    "part from end to end, including every bend, leg and fin; a part drawn in several places gets a box around all of it. "
    "A thin grid is drawn over the image: red numbers along the top are x, blue numbers down the side are y, from 0 to 1000 as "
    "fractions of the image. Read the coordinates off the grid. If a part is not drawn or you cannot tell where it is, set "
    "found=false. Parts: ")
CHECK_PROMPT = (
    "Boxes from an earlier step are drawn over this construction drawing, each tagged with its part id. They may be wrong: check each "
    "one on its own, trusting the drawing and not the box. A box is right only if it holds the WHOLE part, every bend, leg, fin and end, "
    "and the part is really the one described. If a box is right, answer holds_whole_part. If it is on the wrong thing or cuts the part "
    "off, answer corrected and give the right box, read from the grid (red x along the top, blue y down the side, 0 to 1000). If you "
    "cannot tell where the part is, answer cannot_tell. Parts: ")


def upright(data: bytes, rotation_deg: int = 0) -> Image.Image:
    """The figure at its full resolution, turned so its text reads upright."""
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
    return image.rotate(-int(rotation_deg), expand=True) if rotation_deg else image


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _sized(image: Image.Image, scale: float) -> Image.Image:
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image if size == image.size else image.resize(size, Image.LANCZOS)


def _fitted(image: Image.Image) -> Image.Image:
    return _sized(image, min(1.0, (MAX_PIXELS / (image.width * image.height)) ** 0.5))


def _font(image: Image.Image):
    return ImageFont.load_default(size=max(10, max(image.size) // 100))


def _grid(image: Image.Image) -> Image.Image:
    image = _fitted(image).copy()
    draw, font = ImageDraw.Draw(image), _font(image)
    for mark in range(GRID_STEP, 1000, GRID_STEP):
        x, y, major = image.width * mark / 1000, image.height * mark / 1000, mark % 100 == 0
        draw.line([x, 0, x, image.height], fill=(255, 110, 110) if major else (255, 200, 200), width=1)
        draw.line([0, y, image.width, y], fill=(110, 110, 255) if major else (200, 200, 255), width=1)
        if major:
            draw.text((x + 2, 2), str(mark), fill=(200, 0, 0), font=font)
            draw.text((2, y + 2), str(mark), fill=(0, 0, 200), font=font)
    return image


def grid_image(image: Image.Image) -> bytes:
    return _png(_grid(image))


def boxes_image(image: Image.Image, boxes: dict) -> bytes:
    """The grid with each candidate box drawn on it and tagged with its part id."""
    canvas = _grid(image)
    draw, font = ImageDraw.Draw(canvas), _font(canvas)
    for i, (ident, box) in enumerate(boxes.items()):
        colour = BOX_COLOURS[i % len(BOX_COLOURS)]
        corners = [box[0] * canvas.width / 1000, box[1] * canvas.height / 1000, box[2] * canvas.width / 1000, box[3] * canvas.height / 1000]
        draw.rectangle(corners, outline=colour, width=2)
        tag = draw.textbbox((corners[0], corners[1]), ident, font=font)
        lifted = max(tag[3] - tag[1] + 2, 0)
        draw.rectangle([tag[0], tag[1] - lifted, tag[2] + 4, tag[1]], fill=colour)
        draw.text((corners[0] + 2, corners[1] - lifted - 1), ident, fill=(255, 255, 255), font=font)
    return _png(canvas)


def enlarged_image(image: Image.Image) -> bytes:
    """The whole figure as large as the model reads it, for proofreading labels."""
    return _png(_sized(image, min(PROOF_ENLARGE, (MAX_PIXELS / (image.width * image.height)) ** 0.5)))


def _valid(box) -> bool:
    return 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000


def _area(box) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def agreement(a, b) -> float:
    """Share of the smaller box inside the larger: a correction that only extends a
    box along a part agrees with it, and one that moves it elsewhere does not."""
    across = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    down = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return across * down / min(_area(a), _area(b))


def as_box(item: dict) -> list[int]:
    return [item["x0"], item["y0"], item["x1"], item["y1"]]


def first_boxes(located: dict) -> dict:
    """The boxes of the first call that can be drawn at all."""
    found = {item["id"]: as_box(item) for item in located["parts"] if item["found"]}
    return {ident: box for ident, box in found.items() if _valid(box)}


def settle(first: dict, checked: dict) -> dict:
    """{part id: box} for parts both calls put in the same spot; any other part is
    left out, so its writer gets the whole drawing."""
    settled = {}
    for item in checked["parts"]:
        ident, verdict, box = item["id"], item["verdict"], as_box(item)
        before = first.get(ident)
        if before is None:
            continue
        if verdict == "holds_whole_part":
            settled[ident] = before
        elif verdict == "corrected" and _valid(box) and agreement(before, box) >= MIN_AGREEMENT:
            settled[ident] = box
    return settled


def crop(image: Image.Image, box) -> bytes | None:
    """A box of the figure, with a margin, enlarged. None when it would show so much
    of the figure that the close-up gains nothing."""
    width, height = image.size
    x0, y0, x1, y1 = box[0] * width / 1000, box[1] * height / 1000, box[2] * width / 1000, box[3] * height / 1000
    margin = max(MARGIN * max(x1 - x0, y1 - y0), MIN_MARGIN * max(width, height))
    left, top, right, bottom = (round(max(0, x0 - margin)), round(max(0, y0 - margin)),
                                round(min(width, x1 + margin)), round(min(height, y1 + margin)))
    if right <= left or bottom <= top or (right - left) * (bottom - top) > MAX_CROP_SHARE * width * height:
        return None
    region = image.crop((left, top, right, bottom))
    return _png(_sized(region, min(MAX_ENLARGE, (MAX_PIXELS / (region.width * region.height)) ** 0.5)))


def silhouette(positions: list[float], size: tuple[int, int]) -> Image.Image:
    """A built part seen along its run, the way a section shows it, fitted to `size`.
    Shape only: it carries no scale, as the close-up beside it carries none."""
    image = Image.new("RGB", size, "white")
    xs, ys = positions[0::3], positions[1::3]
    if not xs:
        return image
    left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)
    scale = min((size[0] - 16) / max(right - left, 1e-6), (size[1] - 16) / max(top - bottom, 1e-6))
    draw = ImageDraw.Draw(image)
    point = lambda i: (8 + (positions[i] - left) * scale, size[1] - 8 - (positions[i + 1] - bottom) * scale)  # noqa: E731
    for i in range(0, len(positions) - 8, 9):
        draw.polygon([point(i), point(i + 3), point(i + 6)], fill=(40, 40, 40))
    return image


def shape_sheet(rows: list[tuple]) -> bytes:
    """One image for the critic: (part id, close-up PNG or None, built positions).
    Each object's close-up sits beside its section as built, so a form that differs
    shows at a size a whole-assembly render cannot give it."""
    width, height = SHEET_CELL
    title = 24
    columns = 2
    shown = rows[:SHEET_ROWS]
    lines = (len(shown) + 1) // columns
    sheet = Image.new("RGB", (columns * (2 * width + 12), lines * (height + title + 8)), (235, 235, 235))
    draw, font = ImageDraw.Draw(sheet), ImageFont.load_default(size=15)
    for i, (ident, close_up, positions) in enumerate(shown):
        left, top = (i % columns) * (2 * width + 12), (i // columns) * (height + title + 8)
        draw.text((left + 4, top + 4), f"{ident}: drawing | as built", fill=(0, 0, 0), font=font)
        cell = Image.new("RGB", (width, height), "white")
        if close_up:
            with Image.open(io.BytesIO(close_up)) as source:
                drawn = source.convert("RGB")
            fitted = _sized(drawn, min(width / drawn.width, height / drawn.height))
            cell.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
        else:
            ImageDraw.Draw(cell).text((12, 12), "no reliable close-up: use the whole drawing", fill=(120, 0, 0), font=font)
        sheet.paste(cell, (left, top + title))
        sheet.paste(silhouette(positions, (width, height)), (left + width + 2, top + title))
    return _png(sheet)
