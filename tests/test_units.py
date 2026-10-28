from app.units import MM_PER_M, MM_PER_VIEW_UNIT, mm_to_m, mm_to_view


def test_viewer_scale_roundtrip():
    assert mm_to_view(100) == 1.0
    assert mm_to_view(600) * MM_PER_VIEW_UNIT == 600


def test_glb_uses_si_metres():
    assert mm_to_m(1000) == 1.0
    assert mm_to_m(100) == 0.1
    assert MM_PER_M == 1000.0
