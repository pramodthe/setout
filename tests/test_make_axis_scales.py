"""Dimension overrides resize the axis they originally sized."""
from app.make.harness import axis_scales


def test_height_dim_scales_the_matching_axis():
    scales = axis_scales([2, 400, 100], {"upturn_hgt": 100, "floor_width": 400}, {"upturn_hgt": 160})
    assert scales == [1.0, 1.0, 1.6]


def test_unchanged_dimension_does_not_scale():
    scales = axis_scales([2, 400, 100], {"upturn_hgt": 100}, {"upturn_hgt": 100})
    assert scales == [1.0, 1.0, 1.0]
