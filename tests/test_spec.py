import copy

import pytest

from app.seed import door_sill_d3
from app.spec import DetailSpec


def test_seed_validates():
    spec = door_sill_d3()
    assert spec.spec_version == "1.0"
    assert spec.units == "mm"
    assert len(spec.components) >= 10


def test_duplicate_id_rejected():
    spec = door_sill_d3()
    d = spec.model_dump()
    d["components"].append(copy.deepcopy(d["components"][0]))
    with pytest.raises(ValueError, match="duplicate component ids"):
        DetailSpec.model_validate(d)


def test_dangling_install_ref_rejected():
    spec = door_sill_d3()
    d = spec.model_dump()
    d["components"][0]["install_after"] = ["no-such-part"]
    with pytest.raises(ValueError, match="unknown"):
        DetailSpec.model_validate(d)


def test_dangling_relation_rejected():
    spec = door_sill_d3()
    d = spec.model_dump()
    d["relations"].append({"source": "frame", "target": "ghost",
                           "kind": "supported_by"})
    with pytest.raises(ValueError, match="unknown"):
        DetailSpec.model_validate(d)


def test_install_cycle_rejected():
    spec = door_sill_d3()
    d = spec.model_dump()
    by_id = {c["id"]: c for c in d["components"]}
    by_id["frame"]["install_after"].append("sealant")
    by_id["sealant"]["install_after"].append("frame")
    with pytest.raises(ValueError, match="cycle"):
        DetailSpec.model_validate(d)


def test_install_order_respects_deps():
    spec = door_sill_d3()
    order = spec.install_order()
    pos = {cid: i for i, cid in enumerate(order)}
    assert set(order) == {c.id for c in spec.components}
    for c in spec.components:
        for dep in c.install_after:
            assert pos[dep] < pos[c.id]


def test_source_labels_preserved_verbatim():
    spec = door_sill_d3()
    labels = {c.id: c.source_label for c in spec.components}
    assert labels["sealant"] == "6mm gap with backing rod and sealant"
    assert labels["fillet"] == "Fillet or bond breaker"
    assert labels["slab_upper"] is None  # unlabeled stays null, not invented
