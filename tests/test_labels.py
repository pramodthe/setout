"""Labels are verbatim only as far as we can check, and a version says how far that is.

Our own labels, with their own typing error. No network.
"""
import json

import pytest

from app import database as d, generate, store
from app.agent_tools import Tools
from app.model_gateway import ModelGateway
from app.verification_v3 import prepare_verification
from test_agent_v3 import response_json, source_repo  # noqa: F401
from test_threshold_run import READ, _claim, threshold

TYPO = "Bead of mastc and backer"
READ_FIXED = "Bead of mastic and backer"


def _read(*labels):
    return {**READ, "elements": [{"label_verbatim": label, "kind": "label", "where": "left", "category": "object"} for label in labels]}


def _reader(read, proof=None):
    seen = []
    def respond(payload):
        purpose = payload["text"]["format"]["name"]
        seen.append(purpose)
        if purpose == "read":
            return response_json(json.loads(json.dumps(read)))
        if purpose == "proof":
            if proof == "incomplete":
                return {"output": [], "status": "incomplete", "usage": {"input_tokens": 1, "output_tokens": 1}}
            return response_json({"labels": proof})
        pytest.fail(f"unexpected {purpose}")
    return respond, seen


def _read_drawing(repo, read, proof=None):
    job, fence = _claim(repo)
    respond, seen = _reader(read, proof)
    tools = Tools(repo, fence, ModelGateway(repo, fence, respond))
    return tools, tools.run("read", "read_drawing", {}), seen


def test_the_read_lists_what_is_drawn_with_no_label():
    """Seen live: an unlabelled bent plate was in one program and not in the next."""
    assert "unlabelled" in generate.READ_SCHEMA["required"] and "unlabelled:" in generate.READ_PROMPT
    from app.agent import INSTRUCTIONS
    from app.agent_tools import LABEL_RULES
    assert "READ's unlabelled list" in INSTRUCTIONS and "READ's unlabelled list" in LABEL_RULES


def test_the_read_is_told_to_keep_spelling_mistakes():
    assert "keep spelling mistakes" in generate.READ_PROMPT and "Never correct" in generate.READ_PROMPT


# ---------------------------------------------------------------- with a text layer

def test_a_label_in_the_text_layer_stands():
    assert generate.labels_in_text(["Bead of mastic"], "NOTES\nBead of\nmastic\nDoor track") == {"Bead of mastic": "Bead of mastic"}


def test_a_label_the_read_tidied_takes_the_text_layers_spelling():
    assert generate.labels_in_text([READ_FIXED], f"Door track {TYPO} 50mm") == {READ_FIXED: TYPO}


def test_a_label_the_read_changed_the_case_of_takes_the_printed_case():
    assert generate.labels_in_text(["door track"], "DOOR TRACK") == {"door track": "DOOR TRACK"}


def test_a_short_word_is_never_guessed_from_a_near_neighbour():
    assert generate.labels_in_text(["Sill"], "Still water") == {"Sill": None}


def test_a_label_that_is_not_in_the_text_layer_is_not_confirmed():
    assert generate.labels_in_text(["Waterproof screed"], "Door track and frame") == {"Waterproof screed": None}


def test_a_read_is_corrected_from_the_text_layer_and_says_so(source_repo, monkeypatch):
    monkeypatch.setattr(store, "page_text", lambda data, page: f"Door track\n{TYPO}\n")
    tools, result, seen = _read_drawing(source_repo, _read("Door track", READ_FIXED))
    labels = [e["label_verbatim"] for e in result["read"]["elements"]]
    assert labels == ["Door track", TYPO] and seen == ["read"]  # no model call needed
    assert result["read"]["label_check"] == {"method": "text_layer", "corrected": [{"read": READ_FIXED, "printed": TYPO}], "unconfirmed": []}


def test_a_page_of_other_text_says_nothing_about_a_figures_labels(source_repo, monkeypatch):
    """A manual page's text layer holds its prose; the figure's labels are pixels."""
    monkeypatch.setattr(store, "page_text", lambda data, page: "This page explains how a floor is laid in general terms.")
    monkeypatch.setenv("SETOUT_LABEL_PROOF", "on")
    tools, result, seen = _read_drawing(source_repo, _read("Door track"), [{"read": "Door track", "printed": "Door track"}])
    assert result["read"]["label_check"]["method"] == "proofread" and "proof" in seen


# ---------------------------------------------------------------- with no text layer

@pytest.fixture
def proofreading(monkeypatch):
    monkeypatch.setattr(store, "page_text", lambda data, page: "")
    monkeypatch.setenv("SETOUT_LABEL_PROOF", "on")


def test_a_drawing_with_no_text_is_proofread_and_the_printed_spelling_kept(source_repo, proofreading):
    """Seen 30 Oct: the READ fixed a typing error 3 times of 3 even when told not to."""
    tools, result, seen = _read_drawing(source_repo, _read("Door track", READ_FIXED),
                                        [{"read": "Door track", "printed": "Door track"}, {"read": READ_FIXED, "printed": TYPO}])
    assert seen == ["read", "proof"] and [e["label_verbatim"] for e in result["read"]["elements"]] == ["Door track", TYPO]
    check = result["read"]["label_check"]
    assert check["method"] == "proofread" and check["corrected"] == [{"read": READ_FIXED, "printed": TYPO}] and check["unconfirmed"] == []


def test_a_proofreader_that_rewrites_a_label_is_not_believed(source_repo, proofreading):
    tools, result, seen = _read_drawing(source_repo, _read("Door track"), [{"read": "Door track", "printed": "Aluminium threshold section"}])
    assert result["read"]["elements"][0]["label_verbatim"] == "Door track"
    assert result["read"]["label_check"]["unconfirmed"] == ["Door track"]


def test_a_label_the_proofreader_skipped_is_not_confirmed(source_repo, proofreading):
    tools, result, seen = _read_drawing(source_repo, _read("Door track"), [])
    assert result["read"]["label_check"]["unconfirmed"] == ["Door track"]


def test_a_proofread_that_comes_back_empty_keeps_the_read_and_says_so(source_repo, proofreading):
    tools, result, seen = _read_drawing(source_repo, _read("Door track"), "incomplete")
    assert result["read"]["elements"][0]["label_verbatim"] == "Door track"
    assert result["read"]["label_check"]["method"] == "proofread" and result["read"]["label_check"]["unconfirmed"] == ["Door track"]


def test_with_proofreading_off_nothing_claims_a_check(source_repo, monkeypatch):
    monkeypatch.setattr(store, "page_text", lambda data, page: "")
    tools, result, seen = _read_drawing(source_repo, _read("Door track"))
    assert seen == ["read"] and result["read"]["label_check"] == {"method": "none", "corrected": [], "unconfirmed": ["Door track"]}


def test_a_page_with_no_text_layer_reads_as_empty():
    from PIL import Image
    import io
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, "PDF")
    assert store.page_text(output.getvalue(), 0).strip() == ""
    assert store.page_text(b"not a pdf", 0) == ""


# ---------------------------------------------------------------- what the version says

def _verified(repo, read, proof):
    tools, result, seen = _read_drawing(repo, read, proof)
    written = tools.run("write", "write_program", {"program": threshold()})
    assert written.get("version_id"), written
    return {c["id"]: c for c in prepare_verification(repo, written["version_id"])["checks"]}


def test_a_version_from_a_raster_drawing_does_not_claim_exact_labels(source_repo, proofreading):
    labels = [{"read": e["label_verbatim"], "printed": e["label_verbatim"]} for e in READ["elements"]]
    checks = _verified(source_repo, READ, labels)
    assert checks["label-verbatim"]["status"] == "UNVERIFIED" and "no text layer" in checks["label-verbatim"]["reason"]
    assert not checks["label-verbatim"]["required"]
    assert checks["C-labels"]["status"] == "UNVERIFIED" and "exact" not in checks["C-labels"]["description"]


def test_a_version_checked_against_text_does(source_repo, monkeypatch):
    monkeypatch.setattr(store, "page_text", lambda data, page: " ".join(e["label_verbatim"] for e in READ["elements"]))
    checks = _verified(source_repo, READ, None)
    assert checks["label-verbatim"]["status"] == "PASS" and checks["C-labels"]["status"] == "PASS"
    assert "exact drawing labels" in checks["C-labels"]["description"]


def test_a_corrected_label_is_what_the_program_must_carry(source_repo, proofreading):
    """The author chooses from the READ's labels, so the tidied spelling is gone."""
    elements = READ["elements"] + [{"label_verbatim": READ_FIXED, "kind": "label", "where": "left", "category": "object"}]
    answers = [{"read": e["label_verbatim"], "printed": TYPO if e["label_verbatim"] == READ_FIXED else e["label_verbatim"]} for e in elements]
    tools, result, seen = _read_drawing(source_repo, {**READ, "elements": elements}, answers)
    raw = threshold()
    next(p for p in raw["parts"] if p["id"] == "bottom_tray")["label"] = READ_FIXED
    written = tools.run("write", "write_program", {"program": raw})
    assert any("not on drawing" in note for note in written["gate_notes"])
    program = source_repo.get(d.versions, written["version_id"])["program"]
    assert next(p for p in program["parts"] if p["id"] == "bottom_tray")["label"] is None
