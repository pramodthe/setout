"""The judge has to tell good from bad, or the loop means nothing. No reference data needed."""
import numpy as np
import pytest

from goal import judge, section
from goal.families import family_of, spread


def box(low, high):
    (x0, y0, z0), (x1, y1, z1) = low, high
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return v[np.array(faces)]


def wall(courses=6, gap=0.0, family="brick"):
    """A wall of bricks on a slab, with a board up one face: three families and real repeats."""
    pieces = [{"triangles": box((0, 0, -0.2), (1.2, 0.6, 0)), "family": "concrete"},
              {"triangles": box((0, 0.3, 0), (1.2, 0.32, 0.6)), "family": "board"}]
    for row in range(courses):
        for col in range(6):
            pieces.append({"triangles": box((col * 0.2 + gap, 0, row * 0.1), (col * 0.2 + 0.19, 0.28, row * 0.1 + 0.09)), "family": family})
    return pieces


def test_a_thing_against_itself_reads_one():
    built = wall()
    result = judge.score(built, built)
    assert result["iou"] == 1 and result["pieces"] == 1 and result["families"] == 1
    assert result["layers"] == 1 and result["repetition"] == 1 and result["passes"]


def test_moving_turning_or_scaling_it_changes_nothing_that_matters():
    reference = wall()
    moved = judge.posed(reference, np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.0]]), 2.5, np.array([4, -3, 9.0]))
    result = judge.score(moved, reference)
    assert result["iou"] > 0.95 and result["layers"] > 0.95 and result["passes"]


def test_a_prism_of_the_section_fails():
    reference = wall()
    pieces = [{"box": [*p["triangles"].reshape(-1, 3).min(0), *p["triangles"].reshape(-1, 3).max(0)], "tris": [0, 0]} for p in reference]
    stacked, start = [], 0
    for piece, source in zip(pieces, reference):
        piece["tris"] = [start, start + len(source["triangles"])]
        start += len(source["triangles"])
        stacked.append(source["triangles"])
    cuts = section.section(pieces, np.vstack(stacked), 0, 0.51)
    result = judge.score(section.extruded(cuts, 0, 0, 1.2), reference)
    assert not result["passes"] and result["families"] == 0 and result["pieces"] < 0.3


def test_aligning_never_loses_to_the_frame_it_was_given():
    """Seen on real models: a detail nearly matching its own flip was mirrored, and an extrusion
    measured 0.67 in place read 0.38 through the judge."""
    reference = wall(courses=4) + [{"triangles": box((0, 0.5, 0), (0.3, 0.6, 0.4)), "family": "block"}]
    built = reference[:-1] + [{"triangles": box((0.9, 0.5, 0), (1.2, 0.6, 0.4)), "family": "block"}]
    low, high = judge._bounds(reference)
    spacing, shape = judge._grid_for(low, high, judge.GRID)
    target = judge.voxels(reference, low, spacing, shape)[0] > 0
    in_place = judge.overlap(judge.voxels(built, low, spacing, shape)[0] > 0, target)
    assert judge.score(built, reference)["iou"] >= in_place - 0.02


def test_a_mirror_image_can_not_win_on_shape_alone():
    """A wall overlaps its mirror almost perfectly with every material on the wrong side. Seen on
    real models: only 1 to 17% of known voxels held the right family until this."""
    reference = [{"triangles": box((0, 0, 0), (0.2, 1, 1)), "family": "brick"},
                 {"triangles": box((0.2, 0, 0), (0.4, 1, 1)), "family": "insulation"},
                 {"triangles": box((0.4, 0, 0), (0.6, 1, 1)), "family": "block"}]
    result = judge.score(reference, reference)
    assert result["layers"] == 1
    flipped = judge.posed(reference, np.diag([-1.0, 1, 1]))
    assert judge.score(flipped, reference)["layers"] == 1  # any frame is fair: it must find the true fit


def test_a_lump_with_the_right_outline_fails():
    reference = wall()
    lump = [{"triangles": box((0, 0, -0.2), (1.2, 0.6, 0.6)), "family": "concrete"}]
    result = judge.score(lump, reference)
    assert result["pieces"] < 0.1 and result["repetition"] == 0 and not result["passes"]


def test_the_wrong_material_is_not_the_right_layers():
    result = judge.score(wall(family="block"), wall())
    assert result["families"] < 1 and result["layers"] < 0.9 and result["iou"] > 0.9


def test_saying_every_family_does_not_buy_the_score():
    reference = wall()
    padded = reference + [{"triangles": box((0, 0, 0), (0.01, 0.01, 0.01)), "family": f} for f in ("tile", "glass", "timber")]
    assert judge.score(padded, reference)["precision"] < 1


def test_nothing_built_scores_nothing():
    assert judge.score([], wall())["passes"] is False


def test_a_section_cuts_what_the_plane_crosses():
    segments = section.cut_triangles(box((0, 0, 0), (1, 1, 1)), 0, 0.5)
    assert len(segments) and np.allclose(segments[:, :, 0], 0.5, atol=1e-6)


def test_a_nameless_piece_takes_the_family_of_its_shape_or_material():
    piece = lambda name, material, shape: {"path": [name], "materials": {material: 1}, "shape": shape}  # noqa: E731
    pieces = [piece("Brick_Group_2", "salmon", "a"), piece("group_1", "other", "a"), piece("group_2", "salmon", "b"),
              piece("group_3", "nothing", "c")]
    assert spread(pieces) == ["brick", "brick", "brick", "unknown"]


def test_a_material_shared_by_unlike_parts_names_nothing():
    piece = lambda name, material, shape: {"path": [name], "materials": {material: 1}, "shape": shape}  # noqa: E731
    pieces = [piece("Brick_1", "grey", "a"), piece("Block_1", "grey", "b"), piece("group_1", "grey", "c")]
    assert spread(pieces)[2] == "unknown"


@pytest.mark.parametrize("names, materials, family", [
    (["Brick_Group_2"], [], "brick"), (["BED_JOINT_BLOCK_12"], [], "mortar"), (["head_joint"], [], "mortar"),
    (["HEX_NUT-THIN__250-20_UNC_SIMP"], [], "fixing"), (["group_3"], ["thinset_2"], "thinset"),
    (["joint_wire_simple"], [], "reinforcement"), (["group_3"], ["material"], "unknown"), (["Mortar_Net_2"], [], "drainage"),
])
def test_families_read_the_words_a_model_carries(names, materials, family):
    assert family_of(names, materials) == family
