"""How parts meet, from their geometry alone. The cases are the ways real scenes went wrong while a look at their renders said they were right."""
import numpy as np
import trimesh

from builder import measure


def block(low, high):
    made = trimesh.creation.box(extents=np.subtract(high, low))
    made.apply_translation(np.add(low, high) / 2)
    return made.vertices, made.faces


def meshes(*pairs):
    return [measure.solid(*pair) for pair in pairs]


SLAB = block((0, 0, 0), (100, 100, 20))


def test_a_part_with_nothing_under_it_is_loose():
    found = measure.measure({"slab": SLAB, "wall": block((0, 0, 20), (100, 20, 80)), "grate": block((40, 40, 25), (50, 50, 30))})
    assert found["loose"] == [["grate"]] and found["overlaps"] == []


def test_a_hair_between_layers_is_not_loose():
    assert measure.measure({"slab": SLAB, "tile": block((0, 0, 22), (10, 10, 25))})["loose"] == []


def test_parts_that_pass_through_each_other_are_found():
    found = measure.measure({"slab": SLAB, "kerb": block((0, 0, 10), (100, 20, 50))})
    assert found["overlaps"][0]["a"] == "slab" and found["overlaps"][0]["share"] == 0.25


def test_a_small_graze_is_not_worth_saying():
    assert measure.measure({"slab": block((0, 0, 0), (1000, 1000, 200)), "wall": block((0, 0, 199.5), (1000, 1000, 400))})["overlaps"] == []


def test_a_part_rests_on_another_but_not_under_it():
    wall, slab = meshes(block((0, 0, 20), (100, 20, 80)), SLAB)
    assert measure.holds("on", [wall], [slab]) and not measure.holds("on", [slab], [wall])


def test_layers_can_lie_between():
    brick, bed, sheet, slab = meshes(block((0, 0, 50), (100, 20, 80)), block((0, 0, 35), (100, 20, 50)), block((0, 0, 20), (100, 20, 35)), SLAB)
    assert not measure.holds("on", [brick], [slab], [])
    assert measure.holds("on", [brick], [slab], [bed, sheet])


def test_a_stack_needs_every_layer_to_meet():
    brick, bed, slab = meshes(block((0, 0, 60), (100, 20, 80)), block((0, 0, 20), (100, 20, 40)), SLAB)
    assert not measure.holds("on", [brick], [slab], [bed])


def test_a_part_buried_in_another_doesnt_sit_on_it():
    sheet, leaf = meshes(block((0, 0, 60), (100, 20, 66)), block((0, 0, 0), (100, 20, 80)))
    assert not measure.holds("on", [sheet], [leaf], [])


def test_a_pipe_goes_through_a_slab_cut_in_pieces_around_it():
    pipe, west, east = meshes(block((45, 40, -30), (55, 60, 50)), block((0, 0, 0), (45, 100, 20)), block((55, 0, 0), (100, 100, 20)))
    assert measure.holds("through", [pipe], [west, east])


def test_a_pipe_that_stops_short_does_not_go_through():
    pipe, tile = meshes(block((45, 40, -30), (55, 60, 15)), block((0, 0, 32), (100, 100, 42)))
    assert not measure.holds("through", [pipe], [tile])


def test_a_lap_needs_the_cover_on_the_outside():
    over, under = meshes(block((0, 0, 20), (100, 100, 26)), SLAB)
    assert measure.holds("covers", [over], [under])


def test_two_sheets_sharing_their_space_are_not_a_lap():
    buried, slab = meshes(block((0, 10, 12), (100, 90, 18)), SLAB)
    assert not measure.holds("covers", [buried], [slab])


def test_one_good_sheet_cant_answer_for_a_bad_one():
    good, bad, slab = meshes(block((0, 0, 20), (100, 40, 26)), block((0, 50, 12), (100, 90, 18)), SLAB)
    assert measure.holds("covers", [good], [slab]) and not measure.holds("covers", [good, bad], [slab])


def test_wraps_needs_two_faces():
    cap = trimesh.boolean.union([trimesh.Trimesh(*block((0, 0, 20), (100, 100, 25))), trimesh.Trimesh(*block((-5, 0, 0), (0, 100, 25)))], engine="manifold")
    flat, bent, slab = meshes(block((0, 0, 20), (100, 100, 25)), (cap.vertices, cap.faces), SLAB)
    assert measure.holds("wraps", [bent], [slab]) and not measure.holds("wraps", [flat], [slab])


def test_against_needs_a_side_face():
    wall, side, slab = meshes(block((0, 0, 20), (100, 20, 80)), block((100, 0, 0), (120, 100, 20)), SLAB)
    assert measure.holds("against", [side], [slab]) and not measure.holds("against", [wall], [slab])


def test_apart_holds_for_a_real_gap_and_every_object():
    left, right, joined = meshes(block((0, 0, 0), (10, 100, 50)), block((60, 0, 0), (70, 100, 50)), block((10, 0, 0), (60, 10, 50)))
    assert measure.holds("apart", [left], [right]) and not measure.holds("apart", [left, joined], [right])


def test_left_is_the_largest_y():
    left, right, across = meshes(block((0, 200, 0), (10, 300, 50)), block((0, 0, 0), (10, 100, 50)), block((0, 50, 0), (10, 250, 50)))
    assert measure.holds("left_of", [left], [right]) and not measure.holds("left_of", [right], [left])
    assert measure.holds("left_of", [left], [across]) and measure.holds("left_of", [across], [left])


def test_a_part_that_isnt_in_the_model_cant_be_judged():
    checks = [{"a": ["gone"], "relation": "on", "b": ["slab"]}]
    assert measure.measure({"slab": SLAB}, {"checks": checks})["relations"] == [None]
