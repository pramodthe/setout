"""Unit conventions. Millimetres everywhere in the spec; conversion is explicit."""

MM_PER_VIEW_UNIT = 100.0  # viewer scene units (matches existing viewer K=0.01)
MM_PER_M = 1000.0  # glTF/GLB SI metres


def mm_to_view(mm: float) -> float:
    return mm / MM_PER_VIEW_UNIT


def view_to_mm(u: float) -> float:
    return u * MM_PER_VIEW_UNIT


def mm_to_m(mm: float) -> float:
    return mm / MM_PER_M
