"""Synthetic drawings: templates sample and build, masks sit on the ink, tiles and COCO are valid."""
import dataclasses
import importlib.util
import json
import math
import random
from pathlib import Path

import numpy as np
import pytest
import yaml

from app import ontology
from app import synthetic as syn
from app.program import SHAPE_DIMS

ROOT = Path(__file__).resolve().parents[1]
# Small sheets keep each drawing well under a second.
FAST = syn.Config(geometry_px=(300.0, 520.0), margin_px=(16, 32))


@pytest.fixture(scope="module")
def library():
    return syn.load_templates(), syn.load_styles()


def _cli():
    spec = importlib.util.spec_from_file_location("make_seg_dataset", ROOT / "tools" / "make_seg_dataset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- library data

def test_library_loads_and_every_range_says_it_is_synthetic(library):
    templates, styles = library
    assert {t.id for t in templates} >= {"door_threshold", "slab_edge"}
    assert len(styles) >= 4
    for t in templates:
        ranged = [p for p in t.params if p.expr is None]
        assert ranged and all(syn.BASIS_MARK in p.basis for p in ranged)


def test_a_range_without_its_basis_is_refused(tmp_path):
    data = yaml.safe_load((syn.TEMPLATES / "slab_edge.yaml").read_text())
    data["params"][0].pop("basis")
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="synthetic sampling range"):
        syn.load_template(path)


def test_templates_name_only_ontology_classes_and_primitives(library):
    onto = ontology.load()
    for t in library[0]:
        for part in t.parts:
            assert onto.component(part.class_id) is not None
            assert part.shape in syn.SHAPES  # no CAD objects, no fixtures


def test_engine_code_never_names_a_part(library):
    """Trade-agnostic: classes and part ids live in the YAML, never in the engine."""
    source = (ROOT / "app" / "synthetic.py").read_text() + (ROOT / "tools" / "make_seg_dataset.py").read_text()
    names = set(ontology.load().component_ids()) | {p.id for t in library[0] for p in t.parts}
    for name in names - set(SHAPE_DIMS):  # grammar primitives (box, sheet, fillet...) are not parts
        assert f'"{name}"' not in source and f"'{name}'" not in source, name


# ---------------------------------------------------------------- sampling and building

@pytest.mark.parametrize("seed", range(4))
def test_a_template_samples_builds_and_cuts_every_part_into_a_loop(library, seed):
    for t in library[0]:
        s = syn.sample(t, random.Random(seed))
        program, centrelines = syn.build_program(s, f"t{seed}")
        section = syn.build_section(program)
        assert set(section.parts) == {p.id for p in s.present} and not section.missed
        for part in section.parts.values():
            assert part.loops and not part.open_paths
        assert all(q.provenance.value == "assumption" for q in program.params)
        assert program.origin.startswith("synthetic:") and program.assembly_class == t.assembly_class
        assert set(centrelines) == {p.id for p in s.present if p.shape == "sheet"}
        assert all(evaluate_ok(c, s.env) for c in t.constraints)


def evaluate_ok(expr, env):
    from app.program import evaluate
    return evaluate(expr, env) >= 0


def test_sampled_parts_never_overlap(library):
    for seed in range(6):
        for t in library[0]:
            s = syn.sample(t, random.Random(100 + seed))
            program, centrelines = syn.build_program(s, "x")
            geo = syn.measure(syn.build_section(program), centrelines, 1.0, 1.0)
            assert syn.overlaps(geo, 3.0, 2) == []


# ---------------------------------------------------------------- geometry

def test_width_is_the_thickness_of_a_strip_however_it_folds():
    strip = [np.array([(0, 0), (100, 0), (100, 3), (0, 3)], float)]
    folded = [np.array([(0, 0), (100, 0), (100, 2), (2, 2), (2, 50), (0, 50)], float)]
    disc = [np.array([(10 * math.cos(a), 10 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 48, endpoint=False)])]
    ring = [np.array([(0, 0), (100, 0), (100, 100), (0, 100)], float),
            np.array([(20, 20), (20, 80), (80, 80), (80, 20)], float)]
    assert syn.region_width(strip)[0] == pytest.approx(3.0)
    assert syn.region_width(folded)[0] == pytest.approx(2.0)
    assert syn.region_width(disc)[0] == pytest.approx(20.0, rel=0.01)
    assert syn.region_width(ring)[0] == pytest.approx(20.0)  # holes measure from both sides
    x, y, r = syn.inscribed_circle(folded)
    assert r == pytest.approx(1.17, abs=0.03)  # why the thin rule does not use the inscribed disc


def test_rasterize_gives_shared_edges_to_one_side_only():
    left = [np.array([(0.3, 0.2), (5.5, 0.2), (5.5, 7.7), (0.3, 7.7)])]
    right = [np.array([(5.5, 0.2), (9.1, 0.2), (9.1, 7.7), (5.5, 7.7)])]
    box = (0, 0, 10, 8)
    a, b = syn.rasterize(left, box), syn.rasterize(right, box)
    assert not np.any(a & b)
    assert np.array_equal(a | b, syn.rasterize([np.array([(0.3, 0.2), (9.1, 0.2), (9.1, 7.7), (0.3, 7.7)])], box))
    assert a.sum() == 6 * 8  # centres x 0.5..5.5 (an edge centre goes left), y 0.5..7.5


def test_min_width_rule_grows_a_subpixel_strip_to_a_pixel():
    sliver = [(0.0, 10.55), (40.0, 10.55), (40.0, 10.95), (0.0, 10.95)]  # 0.4 px, between pixel centres
    assert not syn.rasterize([np.asarray(sliver)], (0, 0, 40, 20)).any()
    grown = syn.offset_ring(sliver, (1.0 - 0.4) / 2 + 0.01)
    mask = syn.rasterize([np.asarray(grown)], (0, 0, 40, 20))
    assert mask.any(axis=0)[1:-1].all()  # every column inside the strip has a pixel


def test_keyhole_keeps_the_hole_out_of_the_mask():
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
    hole = [(5.0, 5.0), (5.0, 15.0), (15.0, 15.0), (15.0, 5.0)]
    polys = syn.keyhole([outer, hole])
    assert len(polys) == 1
    mask = syn.rasterize([np.asarray(polys[0])], (0, 0, 20, 20))
    assert mask.sum() == 400 - 100 and not mask[10, 10]


def test_clip_keeps_the_area_inside_the_tile():
    square = [(-5.0, -5.0), (15.0, -5.0), (15.0, 15.0), (-5.0, 15.0)]
    piece = syn.clip_ring(square, 0, 0, 10, 10)
    assert abs(syn.ring_area(piece)) == pytest.approx(100.0)
    assert all(0 <= x <= 10 and 0 <= y <= 10 for x, y in piece)
    assert syn.clip_ring(square, 20, 20, 30, 30) == []


# ---------------------------------------------------------------- one drawing

@pytest.fixture(scope="module")
def drawing(library):
    return syn.make_drawing(1, 3, *library, FAST)


def test_thin_flag_follows_the_measured_width(drawing, library):
    for inst in drawing.instances:
        assert inst.thin == (inst.min_width_px < FAST.thin_px)
    # widths are geometry: the same cut drawn twice as large is twice as wide
    s = syn.sample(library[0][0], random.Random(5))
    program, centrelines = syn.build_program(s, "x")
    section = syn.build_section(program)
    one, two = syn.measure(section, centrelines, 1.0, 1.0), syn.measure(section, centrelines, 2.0, 2.0)
    for pid in one:
        assert two[pid].width == pytest.approx(2 * one[pid].width, rel=1e-6)


def test_masks_sit_on_the_ink(drawing):
    continuous = [a for a in drawing.record["alignment"].values() if a["linetype"] == "continuous"]
    assert len(continuous) >= 5
    assert min(a["r2"] for a in continuous) >= 0.95
    assert np.mean([a["r1"] for a in continuous]) >= 0.97


def test_the_alignment_check_catches_a_misplaced_mask(drawing):
    moved = [syn.Instance(i.part_id, i.class_id, [r + (7.0, 5.0) for r in i.rings], i.min_width_px, i.thin,
                          i.widened, i.mode, i.linetype) for i in drawing.instances]
    shifted = syn.alignment(drawing.clean, moved)
    assert np.mean([a["r2"] for a in shifted.values()]) < 0.85


def test_rotation_moves_masks_with_the_ink(library):
    d = syn.make_drawing(2, 3, *library, dataclasses.replace(FAST, rotate_p=1.0))
    assert d.record["sheet"]["rotation_deg"] != 0
    continuous = [a for a in d.record["alignment"].values() if a["linetype"] == "continuous"]
    assert min(a["r2"] for a in continuous) >= 0.95


def test_every_sheet_prints_synthetic_and_only_true_sizes(library, tmp_path):
    templates, styles = library
    rng = random.Random(7)
    s = syn.sample(templates[0], rng)
    d = syn.sample_drafting(styles[0], rng, templates[0].class_ids(), FAST)
    d.dims.update(left=True, right=True, overall=True)
    d.su, d.sv = 1.5, 0.8  # not to scale: printed text must stay the true size
    program, centrelines = syn.build_program(s, "x")
    section = syn.build_section(program)
    geo = syn.measure(section, centrelines, d.su, d.sv)
    ppu = syn.choose_scale(geo, d.target_px, FAST)
    looks = syn.part_looks(section, geo, d, ppu, set(centrelines))
    doc = syn.draft(section, geo, looks, centrelines, {}, s, d, d.dpi / 25.4 / ppu, rng, tmp_path / "x.dxf")
    texts = [e.text for e in doc.modelspace().query("MTEXT")]
    assert any("SYNTHETIC SAMPLE" in t for t in texts)
    dims = doc.modelspace().query("DIMENSION")
    assert dims
    for dim in dims:  # the printed value is the model size: measured / stretch along the dimension
        stretch = d.sv if abs(dim.dxf.angle - 90) < 1e-6 else d.su
        assert float(dim.dxf.text) == pytest.approx(dim.get_measurement() / stretch, abs=0.051)


# ---------------------------------------------------------------- tiles

def test_tiles_cover_the_sheet_and_keep_instances_inside(drawing):
    tiles = syn.tiles(drawing, FAST, random.Random(0))
    assert tiles
    W, H = drawing.image.size
    xs = syn.tile_origins(W, FAST.tile, FAST.stride)
    ys = syn.tile_origins(H, FAST.tile, FAST.stride)
    assert xs[0] == 0 and xs[-1] + FAST.tile == max(W, FAST.tile)
    assert ys[0] == 0 and ys[-1] + FAST.tile == max(H, FAST.tile)
    for tile in tiles:
        assert tile.image.size == (FAST.tile, FAST.tile)
        for ann in tile.annotations:
            coords = [c for poly in ann["segmentation"] for c in poly]
            assert all(0 <= c <= FAST.tile for c in coords)
            x, y, w, h = ann["bbox"]
            assert x >= 0 and y >= 0 and x + w <= FAST.tile + 1e-6 and y + h <= FAST.tile + 1e-6
            assert ann["area"] >= FAST.min_remnant_px2
            assert set(ann["attributes"]) >= {"thin", "min_width_px", "part_id"}


# ---------------------------------------------------------------- dataset and CLI

def test_coco_is_valid_split_by_drawing_and_deterministic(tmp_path):
    build = _cli().build
    a = build(tmp_path / "a" / "ds", drawings=3, seed=9, cfg=FAST, log=lambda *_: None)
    build(tmp_path / "b" / "ds", drawings=3, seed=9, cfg=FAST, log=lambda *_: None)
    seen_files, seen_drawings = set(), {}
    for split in ("train", "valid", "test"):
        da, db = (json.loads((tmp_path / r / "ds" / split / "_annotations.coco.json").read_text()) for r in "ab")
        assert da == db
        for name in sorted(p.name for p in (tmp_path / "a" / "ds" / split).glob("*.png")):
            assert (tmp_path / "a" / "ds" / split / name).read_bytes() == (tmp_path / "b" / "ds" / split / name).read_bytes()
        cats = {c["id"] for c in da["categories"]}
        assert min(cats) == 1 and all(c["supercategory"] == "component" for c in da["categories"])
        images = {i["id"]: i for i in da["images"]}
        assert len(images) == len(da["images"])
        assert len({x["id"] for x in da["annotations"]}) == len(da["annotations"])
        for image in da["images"]:
            assert image["file_name"] not in seen_files and (tmp_path / "a" / "ds" / split / image["file_name"]).exists()
            seen_files.add(image["file_name"])
            assert seen_drawings.setdefault(image["drawing_id"], split) == split  # never two splits
        for ann in da["annotations"]:
            image = images[ann["image_id"]]
            assert ann["category_id"] in cats and ann["iscrowd"] == 0 and ann["area"] > 0
            x, y, w, h = ann["bbox"]
            assert 0 <= x and 0 <= y and x + w <= image["width"] + 1e-6 and y + h <= image["height"] + 1e-6
            assert all(len(poly) >= 6 and len(poly) % 2 == 0 for poly in ann["segmentation"])
    for name in ("manifest.json", "drawings.jsonl"):  # only run.json (timings) may differ
        assert (tmp_path / "a" / "ds" / name).read_bytes() == (tmp_path / "b" / "ds" / name).read_bytes()
    assert a["splits"]["train"]["drawings"] + a["splits"]["valid"]["drawings"] + a["splits"]["test"]["drawings"] == 3
    assert a["synthetic"] is True and a["source"] == "synthetic" and a["thin_rule"]["threshold_px"] == 6.0


def test_splits_are_fixed_by_the_seed():
    split_of = _cli().split_of
    first, again = split_of(100, 4), split_of(100, 4)
    assert first == again
    assert sorted(list(first.values()).count(s) for s in ("train", "valid", "test")) == [10, 10, 80]


def test_cli_refuses_paths_outside_data(tmp_path):
    cli = _cli()
    for target in (ROOT / "app" / "seg-x", ROOT, ROOT / "data", ROOT / "tests" / "seg-x"):
        with pytest.raises(SystemExit) as stop:
            cli.main(["--out", str(target), "--drawings", "1", "--seed", "1"])
        assert "Refusing" in str(stop.value)
    assert not (ROOT / "app" / "seg-x").exists() and not (ROOT / "tests" / "seg-x").exists()
    assert cli.allowed_output(ROOT / "data" / "datasets" / "seg-x")
    assert cli.allowed_output(tmp_path / "elsewhere")  # outside the repository, as tools/dxf_census.py allows
