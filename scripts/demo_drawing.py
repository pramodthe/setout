"""Draw the demo detail: a shower step-down section, our own work.

    python scripts/demo_drawing.py [out_dir]          # default data/demo/

Made for the demo video and the public demo, so no third-party drawing is
ever on screen. Every printed number is a minimum read from
ncc/rules/rules.json (human-confirmed, cited to NCC 2022 Housing Provisions
Part 10.2); nothing else carries a number, so the AI has to treat it as an
assumption. Not to scale. Writes a PNG and a one-page PDF with the drawing
embedded as an image, which is what the upload screen extracts.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 2600, 1700
INK, GREY, MEMBRANE = (25, 25, 25), (120, 120, 120), (30, 80, 160)


def rule(rule_id: str) -> int:
    rules = json.loads((ROOT / "ncc/rules/rules.json").read_text())["rules"]
    value = next(r["value"] for r in rules if r["id"] == rule_id)
    return int(value) if float(value).is_integer() else value


STEP = rule("NCC-10.2-shower-stepdown")          # shower floor below FFL outside
WALL = rule("NCC-10.2-membrane-wall-height")      # membrane above finished tile


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["Arial Narrow Bold.ttf" if bold else "Arial Narrow.ttf",
             "Arial Bold.ttf" if bold else "Arial.ttf"]
    for n in names:
        p = Path("/System/Library/Fonts/Supplemental") / n
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


def hatch(img: Image.Image, poly: list, fill: int, step: int = 18,
          slope: int = 1, dots: int = 0) -> None:
    """Fill a polygon with a grey tone plus 45° hatch (or stipple)."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    layer = Image.new("RGB", img.size, (fill, fill, fill))
    d = ImageDraw.Draw(layer)
    if step:
        for k in range(-H, W + H, step):
            d.line([(k, 0), (k + slope * H, H)], fill=GREY, width=2)
    rnd = random.Random(7)
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    for _ in range(dots):
        x, y = rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys))
        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=GREY)
    img.paste(layer, (0, 0), mask)
    ImageDraw.Draw(img).polygon(poly, outline=INK, width=3)


def leader(d: ImageDraw.ImageDraw, text: str, at: tuple, to: tuple, f) -> None:
    w = d.textlength(text, font=f)
    d.text(at, text, font=f, fill=INK)
    x0 = at[0] + w + 8 if to[0] >= at[0] + w / 2 else at[0] - 8  # nearer end
    y0 = at[1] + f.size * 0.6
    d.line([(x0, y0), to], fill=INK, width=2)
    d.ellipse([to[0] - 5, to[1] - 5, to[0] + 5, to[1] + 5], fill=INK)


def tick(d: ImageDraw.ImageDraw, x: float, y: float) -> None:
    d.line([(x - 10, y + 10), (x + 10, y - 10)], fill=INK, width=3)


def vtext(img: Image.Image, centre: tuple, text: str, f) -> None:
    w = int(ImageDraw.Draw(img).textlength(text, font=f)) + 8
    t = Image.new("L", (w, f.size + 12), 0)
    ImageDraw.Draw(t).text((4, 4), text, font=f, fill=255)
    t = t.rotate(90, expand=True)
    img.paste(INK, (int(centre[0] - t.width / 2), int(centre[1] - t.height / 2)), t)


def draw() -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f, fb, fs = font(30), font(46, bold=True), font(26)

    edge, waste = 1690, 760                       # shower edge, waste centre (px)
    ffl, slab_hi, slab_lo, slab_bot = 900, 960, 1060, 1280

    def screed_top(x: float) -> float:            # falls to the waste from both sides
        if x <= waste:
            return 1005 + (1018 - 1005) * (x - 343) / (waste - 343)
        return 1018 + (995 - 1018) * (x - waste) / (edge - waste)

    bed, tile = 25, 22
    xs = list(range(343, edge + 1, 8)) + [edge]

    # slab, set down at the shower; wall standing on it
    hatch(img, [(200, slab_lo), (1700, slab_lo), (1700, slab_hi), (2450, slab_hi),
                (2450, slab_bot), (200, slab_bot)], 225, step=0, dots=1800)
    hatch(img, [(260, 150), (340, 150), (340, slab_lo), (260, slab_lo)], 235, step=16)
    # screed to falls
    hatch(img, [(343, slab_lo)] + [(x, screed_top(x)) for x in xs] + [(edge, slab_lo)],
          242, step=0, dots=500)
    # shower tile bed and tile, cut around the waste
    for x0, x1 in ((348, waste - 28), (waste + 28, edge - 6)):
        seg = [x for x in xs if x0 <= x <= x1] + [x1]
        top = [(x, screed_top(x) - bed) for x in seg]
        d.polygon([(x, screed_top(x)) for x in seg] + top[::-1], fill=(232, 232, 232), outline=INK)
        d.polygon(top + [(x, y - tile) for x, y in top][::-1], fill="white", outline=INK, width=3)
        for x in seg[::38]:
            d.line([(x, screed_top(x) - bed - tile), (x, screed_top(x) - bed)], fill=INK, width=2)
    # wall adhesive + tile, full height
    floor_at_wall = screed_top(348) - bed - tile
    d.rectangle([348, 150, 355, floor_at_wall], fill=(232, 232, 232), outline=INK)
    d.rectangle([355, 150, 372, floor_at_wall], fill="white", outline=INK, width=3)
    for y in range(int(floor_at_wall) - 120, 150, -120):
        d.line([(355, y), (372, y)], fill=INK, width=2)
    # bathroom bed and tile at FFL
    d.rectangle([1700, ffl + tile + 3, 2450, slab_hi], fill=(232, 232, 232), outline=INK)
    d.rectangle([1700, ffl, 2450, ffl + tile + 3], fill="white", outline=INK, width=3)
    for x in range(1760, 2450, 120):
        d.line([(x, ffl), (x, ffl + tile + 3)], fill=INK, width=2)
    # floor waste: pipe, puddle flange at membrane level, grate at tile level
    d.rectangle([waste - 20, 1018, waste + 20, 1340], fill="white", outline=INK, width=3)
    d.rectangle([waste - 60, 1014, waste + 60, 1022], fill=(90, 90, 90), outline=INK)
    d.rectangle([waste - 18, 1018 - bed - tile + 8, waste + 18, 1014], fill="white", outline=INK, width=2)
    d.rectangle([waste - 26, 1018 - bed - tile, waste + 26, 1018 - bed - tile + 8], fill=(70, 70, 70))
    # waterstop angle at the shower edge
    d.line([(edge - 70, screed_top(edge) - 2), (edge + 6, screed_top(edge) - 2)], fill=(90, 90, 90), width=6)
    d.line([(edge + 6, screed_top(edge) - 2), (edge + 6, ffl + 4)], fill=(90, 90, 90), width=6)
    # membrane: up the wall, across the screed, up the waterstop face
    top_of_membrane = 230
    d.line([(344, top_of_membrane), (344, screed_top(343))]
           + [(x, screed_top(x) - 3) for x in xs if x <= waste - 60]
           + [(waste - 60, 1012), (waste + 60, 1012)]
           + [(x, screed_top(x) - 3) for x in xs if x >= waste + 60]
           + [(edge + 2, screed_top(edge) - 6), (edge + 2, ffl + 10)],
           fill=MEMBRANE, width=7, joint="curve")
    d.line([(330, top_of_membrane), (380, top_of_membrane)], fill=INK, width=2)

    # break lines: wall height, slab ends
    for y in (520,):
        d.line([(230, y + 10), (290, y + 10), (305, y - 12), (320, y + 22), (335, y), (400, y)],
               fill=INK, width=3)
    for x in (200, 2450):
        d.line([(x, slab_hi - 30 if x > 1000 else slab_lo - 30), (x, slab_bot + 30)], fill="white", width=8)
        top = slab_hi if x > 1000 else slab_lo
        d.line([(x, top - 20), (x, (top + slab_bot) / 2 - 20), (x - 18, (top + slab_bot) / 2),
                (x + 18, (top + slab_bot) / 2 + 20), (x, (top + slab_bot) / 2 + 40), (x, slab_bot + 20)],
               fill=INK, width=3)

    # dimension: step-down at the shower edge
    x = 1590
    shower_ffl = screed_top(x) - bed - tile
    d.line([(1570, ffl), (1700, ffl)], fill=INK, width=2)
    d.line([(x, ffl - 20), (x, shower_ffl + 20)], fill=INK, width=2)
    tick(d, x, ffl), tick(d, x, shower_ffl)
    d.text((x - 150, ffl + 2), f"{STEP} MIN.", font=f, fill=INK)
    # dimension: membrane height above finished tile
    d.line([(180, top_of_membrane), (330, top_of_membrane)], fill=INK, width=2)
    d.line([(180, floor_at_wall), (255, floor_at_wall)], fill=INK, width=2)
    d.line([(205, top_of_membrane - 20), (205, floor_at_wall + 20)], fill=INK, width=2)
    tick(d, 205, top_of_membrane), tick(d, 205, floor_at_wall)
    vtext(img, (165, (top_of_membrane + floor_at_wall) / 2),
          f"{WALL} MIN. ABOVE FINISHED TILE", f)

    # FFL marker and room names
    d.polygon([(2290, ffl - 4), (2276, ffl - 28), (2304, ffl - 28)], fill=INK)
    d.text((2316, ffl - 42), "FFL", font=f, fill=INK)
    d.text((880, 760), "SHOWER", font=font(40, bold=True), fill=GREY)
    d.text((1960, 700), "BATHROOM", font=font(40, bold=True), fill=GREY)

    # labels (our own words)
    leader(d, "TOP OF MEMBRANE", (440, 210), (344, top_of_membrane), f)
    leader(d, "WALL TILE ON ADHESIVE", (440, 420), (364, 440), f)
    leader(d, "WATERPROOF MEMBRANE", (440, 640), (344, 700), f)
    leader(d, "WATERPROOF MEMBRANE", (450, 860), (620, screed_top(620) - 3), f)
    leader(d, "TILE BED AND FLOOR TILE", (1080, 860), (1300, screed_top(1300) - bed - 10), f)
    leader(d, "WATERSTOP ANGLE", (1430, 560), (edge + 6, 930), f)
    leader(d, "BATHROOM FLOOR TILE ON BED", (1820, 790), (2060, ffl + 12), f)
    leader(d, "FLOOR WASTE WITH PUDDLE FLANGE", (420, 1400), (waste + 40, 1018), f)
    leader(d, "SCREED LAID TO FALL TO WASTE", (980, 1400), (1200, 1040), f)
    leader(d, "CONCRETE SLAB SET DOWN AT SHOWER", (980, 1470), (1400, 1180), f)

    # title block
    d.rectangle([1700, 1400, 2550, 1650], outline=INK, width=3)
    d.text((1730, 1420), "SHOWER STEP-DOWN — SECTION", font=fb, fill=INK)
    d.text((1730, 1485), "NOT TO SCALE. DIMENSIONS IN MM.", font=fs, fill=INK)
    d.text((1730, 1525), "MINIMUM VALUES: NCC 2022 HOUSING PROVISIONS PART 10.2", font=fs, fill=INK)
    d.text((1730, 1565), "SETOUT DEMONSTRATION DRAWING", font=fs, fill=INK)
    return img


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(out / "shower-stepdown.png")
    img.save(out / "shower-stepdown.pdf", "PDF", resolution=200)
    print(out / "shower-stepdown.pdf")


if __name__ == "__main__":
    main()
