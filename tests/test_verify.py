"""Contact checks measure real bearing gaps on both details."""

from app.geometry import build_assembly
from app.seed import door_sill_d3, door_subsill_d4
from app.verify import _contact_gap, summarize, verify_all


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


def test_d3_still_passes():
    spec = door_sill_d3()
    results = verify_all(spec, build_assembly(spec), spec.source.sha256)
    assert summarize(results)["failed"] == []
