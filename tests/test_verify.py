"""Contact checks measure real bearing gaps on both details. The accepted gap
is per pair: CONTACT_TOL_MM or half the thinner part, whichever is less."""

import pytest

from app.geometry import build_assembly
from app.seed import door_sill_d3, door_subsill_d4
from app.spec import (Component, DetailSpec, GeneratorRef, Param, ParamRole,
                      PathPoint, Provenance, SourceDoc)
from app.verify import CONTACT_TOL_MM, _contact_gap, summarize, verify_all, verify_meshes


def test_d3_contacts_bear():
    m = build_assembly(door_sill_d3())
    assert _contact_gap(m, "packer", "membrane") == 0.0
    assert _contact_gap(m, "frame", "packer") == 0.0
    assert _contact_gap(m, "fillet", "membrane") == 1.0
    assert _contact_gap(m, "screed", "slip_sheet") == 0.0


def test_d4_contacts_bear():
    m = build_assembly(door_subsill_d4())
    assert _contact_gap(m, "cfc_packers", "membrane") == 0.0
    assert _contact_gap(m, "washer", "cfc_packers") == 0.0
    assert _contact_gap(m, "nut", "washer") == 0.0
    assert _contact_gap(m, "subsill", "nut") == 0.0
    assert _contact_gap(m, "fillet", "membrane") == 0.0


def test_d4_verdict_pass_no_fail():
    spec = door_subsill_d4()
    results = verify_all(spec, build_assembly(spec), spec.source.sha256)
    rep = summarize(results)
    assert rep["failed"] == []
    by_id = {r.id: r for r in results}
    assert by_id["B-step"].status == "PASS"
    assert by_id["B-step"].actual == 80
    assert by_id["B-gap"].status == "N/A"
    assert by_id["B-fall"].status == "N/A"
    assert by_id["C-source"].status == "PASS"
    # 1.5 mm membrane under the packers: half of it; 12 mm nut under the sub-sill: the flat cap.
    assert by_id["B-contact-cfc_packers"].status == "PASS" and by_id["B-contact-cfc_packers"].tolerance == 0.75
    assert by_id["B-contact-subsill"].tolerance == CONTACT_TOL_MM


def test_d3_verdict_pass_no_fail():
    """D3's fillet base (y = -97.5) sits on the slip sheet's top, which runs
    between it and the membrane (top -98.5), so that is the contact to check."""
    spec = door_sill_d3()
    results = verify_all(spec, build_assembly(spec), spec.source.sha256)
    assert summarize(results)["failed"] == []
    by_id = {r.id: r for r in results}
    assert by_id["B-contact-fillet"].status == "PASS" and by_id["B-contact-fillet"].actual == 0.0
    assert by_id["B-contact-packer"].status == "PASS" and by_id["B-contact-packer"].tolerance == 0.75
    assert by_id["B-contact-screed"].status == "PASS" and by_id["B-contact-screed"].tolerance == 0.5
    assert by_id["B-contact-frame"].status == "PASS" and by_id["B-contact-frame"].tolerance == CONTACT_TOL_MM


def _membrane_on_slab(gap: float) -> DetailSpec:
    """A 1.5 mm sheet `gap` mm above a slab, with the contact declared."""
    def p(name, value, role=ParamRole.PHYSICAL):
        return Param(name=name, value=value, provenance=Provenance.ASSUMPTION, role=role)
    slab = Component(id="slab", type="substrate", representation="solid",
                     generator=GeneratorRef(id="box", version="setout-mesh-v0"),
                     params=[p("x0", 0), p("x1", 600), p("y0", -150), p("y1", 0)])
    membrane = Component(id="membrane", type="membrane", representation="sheet",
                         generator=GeneratorRef(id="sheet_follow", version="setout-mesh-v0"),
                         params=[p("thickness_physical", 1.5), p("thickness_display", 4, ParamRole.DISPLAY)],
                         path=[PathPoint(x=0, y=gap + 0.75), PathPoint(x=600, y=gap + 0.75)],
                         install_after=["slab"])
    return DetailSpec(detail_id="t", title="t", origin="test",
                      source=SourceDoc(filename="own.pdf", sha256="a" * 64, page_index=0, crop_id="c",
                                       page_width_pt=1, page_height_pt=1),
                      context={"contact_checks": [["membrane", "slab", "membrane beds on slab"]]},
                      components=[slab, membrane])


@pytest.mark.parametrize("gap,status", [(0.0, "PASS"), (0.5, "PASS"), (1.8, "FAIL")])
def test_thin_layer_tolerance_is_half_the_thinner_part(gap, status):
    spec = _membrane_on_slab(gap)
    contact = next(r for r in verify_meshes(spec, build_assembly(spec)) if r.id == "B-contact-membrane")
    assert contact.status == status and contact.tolerance == 0.75
    assert contact.actual == pytest.approx(gap)
    assert gap <= CONTACT_TOL_MM  # every case sat inside the old flat band
