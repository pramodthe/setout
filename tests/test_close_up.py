"""The CAD writer and the critic see each part up close, or the whole drawing if unsure.

Our own images and our own words, no network: the model is scripted. What a real
drawing did is in app/close_up.py; here each rule is pinned on its own.
"""
import base64
import io
import json

import pytest
from PIL import Image, ImageDraw

from app import agent_tools, close_up, database as d
from app.agent_tools import CAD_SCHEMA, CRITIC_SCHEMA, Tools, apply_shape_checks, model_view
from app.model_gateway import ModelGateway
from test_agent_v3 import FakeRenderer, response_json, source_repo  # noqa: F401
from test_threshold_run import LOCAL, _builder, _claim, _read_response, threshold

PARTS = ("track", "bottom_tray")


def _image(width, height, colour="white"):
    return Image.new("RGB", (width, height), colour)


def _open(data):
    return Image.open(io.BytesIO(data))


# -------------------------------------------------------------- the crop

def test_a_small_part_is_enlarged_from_the_full_resolution_figure():
    figure = _image(4000, 3000)
    ImageDraw.Draw(figure).rectangle([2000, 1500, 2002, 1540], fill="black")  # 3 px wide: lost in any shrink
    crop = _open(close_up.crop(figure, [495, 495, 505, 520]))
    assert crop.width > 700 and crop.width * crop.height <= close_up.MAX_PIXELS
    dark = sum(crop.convert("L").histogram()[:128])
    assert dark > 3 * 40 * 16  # the 3 px line, kept and enlarged at least four times over


def test_a_crop_stays_under_what_the_model_can_read():
    crop = _open(close_up.crop(_image(8000, 6000), [100, 100, 600, 600]))
    assert crop.width * crop.height <= close_up.MAX_PIXELS * 1.01


def test_a_crop_never_enlarges_past_the_point_of_showing_only_blur():
    crop = _open(close_up.crop(_image(1000, 1000), [500, 500, 510, 510]))
    margin = 25  # the floor, in figure pixels
    assert crop.width <= (10 + 2 * margin) * close_up.MAX_ENLARGE


def test_a_long_thin_box_keeps_room_for_what_the_part_turns_into():
    """Seen on a real drawing: a bent plate was boxed along its flat run, so the
    margin follows the box's long side, and the crop still shows the turn-up."""
    figure = _image(1000, 1000)
    draw = ImageDraw.Draw(figure)
    draw.rectangle([300, 500, 700, 504], fill="black")  # the flat run, which the box holds
    draw.rectangle([696, 410, 700, 500], fill="black")  # the turn-up, 90 above it
    crop = _open(close_up.crop(figure, [300, 498, 700, 506]))
    scale = crop.width / (400 + 2 * 100)
    assert crop.convert("L").getpixel((round((698 - 200) * scale), round((420 - 398) * scale))) < 128


def test_a_box_that_is_most_of_the_figure_is_not_a_close_up():
    assert close_up.crop(_image(1000, 1000), [50, 50, 950, 950]) is None


def test_a_rotated_figure_is_cut_upright():
    figure = _image(100, 200)
    source = io.BytesIO()
    figure.save(source, "PNG")
    assert close_up.upright(source.getvalue(), 90).size == (200, 100)


def test_the_grid_and_the_proof_images_stay_readable_size():
    large = _image(8000, 6000)
    assert _open(close_up.grid_image(large)).size[0] * _open(close_up.grid_image(large)).size[1] <= close_up.MAX_PIXELS * 1.01
    assert _open(close_up.enlarged_image(_image(1000, 1000))).size[0] <= 3000
    assert max(_open(close_up.enlarged_image(large)).size) < 8000


def test_the_check_image_draws_every_box_it_asks_about():
    plain = _open(close_up.grid_image(_image(1000, 1000)))
    drawn = _open(close_up.boxes_image(_image(1000, 1000), {"one": [100, 100, 300, 300], "two": [500, 500, 700, 700]}))
    assert plain.getpixel((200, 100)) != drawn.getpixel((200, 100)) and plain.getpixel((600, 500)) != drawn.getpixel((600, 500))


# --------------------------------------------------------- who is sure of what

def _located(**boxes):
    return {"parts": [{"id": k, "found": v is not None, **dict(zip(("x0", "y0", "x1", "y1"), v or (0, 0, 1, 1)))} for k, v in boxes.items()]}


def _checked(**answers):
    return {"parts": [{"id": k, "verdict": v[0], **dict(zip(("x0", "y0", "x1", "y1"), v[1] if len(v) > 1 else (0, 0, 1, 1)))}
                      for k, v in answers.items()]}


def test_a_box_both_calls_accept_is_used():
    first = close_up.first_boxes(_located(a=(100, 100, 300, 300)))
    assert close_up.settle(first, _checked(a=("holds_whole_part",))) == {"a": [100, 100, 300, 300]}


def test_a_correction_that_extends_the_same_part_is_used():
    first = close_up.first_boxes(_located(a=(300, 480, 700, 520)))
    corrected = (280, 300, 720, 540)
    assert close_up.settle(first, _checked(a=("corrected", corrected))) == {"a": list(corrected)}


def test_a_correction_that_moves_the_box_is_a_disagreement_so_no_crop_is_sent():
    """Measured on a real drawing: every wrong crop came from a correction like this."""
    first = close_up.first_boxes(_located(a=(100, 100, 200, 200)))
    assert close_up.settle(first, _checked(a=("corrected", (600, 600, 700, 700)))) == {}


def test_a_part_nobody_can_place_gets_no_crop():
    first = close_up.first_boxes(_located(a=(100, 100, 200, 200), b=None))
    settled = close_up.settle(first, _checked(a=("cannot_tell",), b=("holds_whole_part",)))
    assert settled == {}


@pytest.mark.parametrize("box", [(300, 300, 100, 400), (0, 0, 1200, 500), (-5, 0, 100, 100), (200, 200, 200, 300)])
def test_a_box_that_cannot_exist_is_dropped(box):
    assert close_up.first_boxes(_located(a=box)) == {}
    first = close_up.first_boxes(_located(a=(100, 100, 200, 200)))
    assert close_up.settle(first, _checked(a=("corrected", box))) == {}


def test_the_prompts_never_ask_for_a_size():
    """The owner's drawings are mostly NTS: the grid places a part, it measures nothing."""
    for text in (close_up.LOCATE_PROMPT, close_up.CHECK_PROMPT):
        assert "mm" not in text.lower().split() and "scale" not in text.lower()


# ------------------------------------------------------------------ the writer

class Located:
    """A model that places the two objects, then writes their CAD."""
    def __init__(self, check=None, located=None, fail=None):
        self.check, self.located, self.fail = check, located, fail
        self.calls, self.payloads = [], {}

    def __call__(self, payload):
        purpose = payload["text"]["format"]["name"]
        schema = payload["text"]["format"]["schema"]
        if purpose == "read":
            return _read_response(payload)
        if purpose == "locate":
            second = "verdict" in json.dumps(schema)
            name = "check" if second else "locate"
            self.calls.append(name)
            self.payloads.setdefault(name, []).append(payload)
            if self.fail == name:
                return {"output": [], "status": "incomplete", "usage": {"input_tokens": 1, "output_tokens": 1}}
            if second:
                return response_json(self.check or _checked(**{p: ("holds_whole_part",) for p in PARTS}))
            return response_json(self.located or _located(track=(300, 300, 500, 500), bottom_tray=(200, 500, 800, 540)))
        if purpose == "cad":
            self.calls.append("cad")
            self.payloads.setdefault("cad", []).append(payload)
            return response_json({"code": LOCAL, "envelope_problem": ""})
        if purpose == "critic":
            self.calls.append("critic")
            self.payloads.setdefault("critic", []).append(payload)
            return response_json({"verdict": "pass", "issues": [], "shape_checks": []})
        pytest.fail(f"unexpected {purpose}")


@pytest.fixture
def close_ups(monkeypatch):
    monkeypatch.setenv("SETOUT_CLOSE_UP", "on")


def _tools(repo, model):
    job, fence = _claim(repo)
    tools = Tools(repo, fence, ModelGateway(repo, fence, model), cad_builder=_builder(), renderer=FakeRenderer(repo))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": threshold()})
    assert written.get("version_id"), written
    return job, tools, written


def _images(payload):
    return [part for part in payload["input"][0]["content"] if part["type"] == "input_image"]


def _text(payload):
    return payload["input"][0]["content"][0]["text"]


def test_the_writer_is_given_the_close_up_then_the_whole_drawing(source_repo, close_ups):
    model = Located()
    job, tools, written = _tools(source_repo, model)
    tools.run("m", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "the track"})
    payload = model.payloads["cad"][0]
    assert len(_images(payload)) == 2
    text = _text(payload)
    assert "The first image is a close-up" in text and "The second is the whole drawing" in text
    assert "track, a door_sill, labelled 'Door bottom track' on the drawing" in text
    assert "never a size" in text and "take no measurement" in text  # shape and topology only


def test_the_two_locating_calls_happen_once_for_all_the_parts(source_repo, close_ups):
    model = Located()
    job, tools, written = _tools(source_repo, model)
    first = tools.run("m1", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    tools.run("m2", "model_component", {"version_id": first["version_id"], "part_key": "bottom_tray", "brief": "y"})
    assert model.calls == ["locate", "check", "cad", "cad"]
    listing = _text(model.payloads["locate"][0])
    assert "Door bottom track" in listing and "bottom_tray" in listing
    assert len(_images(model.payloads["check"][0])) == 1  # the boxes drawn on the figure


def test_when_the_calls_disagree_the_writer_gets_the_whole_drawing_and_is_told_to_find_the_part(source_repo, close_ups):
    model = Located(check=_checked(track=("corrected", (800, 800, 900, 900)), bottom_tray=("holds_whole_part",)))
    job, tools, written = _tools(source_repo, model)
    tools.run("m", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    payload = model.payloads["cad"][0]
    assert len(_images(payload)) == 1 and "find this part in it" in _text(payload) and "close-up" not in _text(payload)


@pytest.mark.parametrize("step", ["locate", "check"])
def test_a_locating_call_that_comes_back_empty_costs_the_close_up_not_the_part(source_repo, close_ups, step):
    model = Located(fail=step)
    job, tools, written = _tools(source_repo, model)
    result = tools.run("m", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    assert result.get("component_id") and len(_images(model.payloads["cad"][0])) == 1


def test_when_nothing_is_found_the_check_call_is_not_spent(source_repo, close_ups):
    model = Located(located=_located(track=None, bottom_tray=None))
    job, tools, written = _tools(source_repo, model)
    tools.run("m", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    assert model.calls == ["locate", "cad"]


def test_with_close_ups_off_nothing_is_located(source_repo):
    model = Located()
    job, tools, written = _tools(source_repo, model)
    tools.run("m", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    assert model.calls == ["cad"] and len(_images(model.payloads["cad"][0])) == 1


def test_a_writer_can_say_the_envelope_cannot_hold_the_form(source_repo, close_ups):
    """Seen live: a bent plate had a flat envelope, so no writer could have built it."""
    assert "envelope_problem" in CAD_SCHEMA["required"]
    model = Located()
    real = model.__call__
    job, tools, written = _tools(source_repo, model)
    def bent(payload):
        if payload["text"]["format"]["name"] == "cad":
            return response_json({"code": LOCAL, "envelope_problem": "The plate turns up at one end: raise y1."})
        return real(payload)
    tools.gateway.responder = bent
    result = tools.run("m", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    assert result["envelope_problem"] == "The plate turns up at one end: raise y1."
    assert model_view("model_component", result)["envelope_problem"].startswith("The plate")


# ------------------------------------------------------------------ the critic

def test_a_silhouette_shows_the_section_as_built():
    positions = [0, 0, 0, 100, 0, 0, 100, 10, 0,  0, 0, 0, 100, 10, 0, 0, 10, 0,      # a foot
                 90, 0, 0, 100, 0, 0, 100, 100, 0,  90, 0, 0, 100, 100, 0, 90, 100, 0]  # and an upstand at one end
    image = close_up.silhouette(positions, (200, 200))
    assert image.getpixel((100, 180))[0] < 128      # the foot, along the bottom
    assert image.getpixel((180, 40))[0] < 128       # the upstand, at the right
    assert image.getpixel((40, 40))[0] > 128        # and the corner a block would fill is empty


def test_the_writer_is_told_the_drawing_outranks_the_brief_and_a_section_is_a_section():
    """Seen live: a brief saying tray got end plates and a base no section shows."""
    rules = agent_tools.SHAPE_RULES
    assert "not from the brief" in rules and "no end plates" in rules and "Workplane('XZ')" in rules
    assert "never bridge pieces" in rules and "revolved solid" in rules


def test_a_revolve_is_told_which_coordinates_its_axis_is_in():
    """Seen live: on an XZ plane an axis ending at (0, 0, h) points out of the profile,
    and two screws in a row came back as a degenerate solid."""
    assert "workplane's own two coordinates" in agent_tools.SHAPE_RULES


def test_a_solid_that_cannot_be_meshed_says_why():
    """Seen live, as FileNotFoundError: part.stl, which tells a writer nothing."""
    from app import cad_components
    scripts = []
    def runner(script):
        scripts.append(script)
        raise cad_components.cad_sandbox.CadCodeError("stop here")
    with pytest.raises(cad_components.cad_sandbox.CadCodeError):
        cad_components.build(LOCAL, {"width": 1, "depth": 1, "height": 1}, [], runner=runner)
    assert "could not be meshed" in scripts[0] and "axis" in scripts[0]


def test_the_shape_sheet_stays_under_what_the_model_can_read():
    crop = close_up.crop(_image(1000, 1000), [100, 100, 300, 300])
    rows = [(f"part_{i}", crop if i % 2 else None, [0, 0, 0, 10, 0, 0, 10, 10, 0]) for i in range(30)]
    sheet = _open(close_up.shape_sheet(rows))
    assert sheet.width * sheet.height <= close_up.MAX_PIXELS


def _built(repo, model):
    job, tools, written = _tools(repo, model)
    first = tools.run("m1", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    second = tools.run("m2", "model_component", {"version_id": first["version_id"], "part_key": "bottom_tray", "brief": "y"})
    rendered = tools.run("r", "render", {"version_id": second["version_id"]})
    return tools, second, rendered


def test_the_critic_is_shown_each_object_beside_its_close_up(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    tools.run("c", "critic", rendered["critic_args"])
    payload = model.payloads["critic"][0]
    assert len(_images(payload)) == 6  # the figure, the sheet, the three plain views and the tagged iso
    text = _text(payload)
    assert "shape sheet" in text and "never compare sizes" in text
    assert model.calls.count("locate") == 1  # the critic reuses what CAD found


def _shape_result(*checks):
    return {"verdict": "pass", "issues": [], "shape_checks": list(checks)}


def _shape(part, status):
    return {"part": part, "status": status, "drawing_shows": "three fins on a base", "model_shows": "a hollow channel"}


def test_a_shape_mismatch_is_an_issue_about_that_part():
    result = _shape_result(_shape("track", "mismatch"), _shape("tray", "match"))
    apply_shape_checks(result, ["track", "tray"])
    issue = result["issues"][0]
    assert issue["id"] == "shape-mismatch:track" and issue["parts"] == ["track"] and issue["severity"] == "error"
    assert "three fins" in issue["reason"] and "hollow channel" in issue["reason"]
    assert "shape_checks" not in result


def test_a_shape_mismatch_means_the_critic_cannot_pass(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    real = model.__call__
    def mismatched(payload):
        if payload["text"]["format"]["name"] == "critic":
            return response_json(_shape_result(_shape("track", "mismatch"), _shape("bottom_tray", "match")))
        return real(payload)
    tools.gateway.responder = mismatched
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result["verdict"] == "fail" and result["issues"][0]["id"] == "shape-mismatch:track"
    stored = source_repo.get(d.critics, result["critic_verdict_id"])
    assert stored["issues"][0]["parts"] == ["track"]
    view = model_view("critic", result)
    assert "model_component" in view["next_step"] and "track" in view["next_step"]


def test_an_object_the_critic_could_not_judge_holds_a_pass_back():
    result = _shape_result(_shape("track", "match"), _shape("tray", "unclear"))
    apply_shape_checks(result, ["track", "tray", "screw"])
    assert result["verdict"] == "inconclusive"
    assert result["issues"][0]["parts"] == ["tray", "screw"] and result["issues"][0]["needs_human"]


def test_a_critic_that_was_shown_objects_and_said_nothing_about_them_has_not_passed_them():
    result = {"verdict": "pass", "issues": [], "shape_checks": []}
    apply_shape_checks(result, ["track"])
    assert result["verdict"] == "inconclusive" and result["issues"][0]["parts"] == ["track"]


def test_a_part_the_program_lacks_means_the_critic_cannot_pass(source_repo, close_ups):
    """Seen live: a critic passed a version with no part for a bent plate it could see."""
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    real = model.__call__
    def lacking(payload):
        if payload["text"]["format"]["name"] == "critic":
            return response_json({"verdict": "pass", "issues": [], "shape_checks": [_shape("track", "match"), _shape("bottom_tray", "match")],
                                  "missing_parts": [{"what": "a bent plate under the track", "where": "below the track"}]})
        return real(payload)
    tools.gateway.responder = lacking
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result["verdict"] == "fail" and result["issues"][0]["id"] == "missing-part-0"
    assert "a bent plate under the track" in result["issues"][0]["reason"]
    next_step = model_view("critic", result)["next_step"]
    assert "patch_program" in next_step and "a bent plate" in next_step


def test_the_critic_is_asked_to_count_and_to_look_for_what_is_not_there(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    tools.run("c", "critic", rendered["critic_args"])
    text = _text(model.payloads["critic"][0])
    assert "a close resemblance is not a match" in text and "missing_parts" in text
    assert '"drawn_without_a_label":' in text
    assert "missing_parts" in CRITIC_SCHEMA["required"]


def test_the_read_and_the_author_see_the_figure_as_large_as_it_can_be_read(source_repo, close_ups):
    """Seen on a real section: only the enlarged figure told how a plate bends."""
    sizes = {}
    def respond(payload):
        purpose = payload["text"]["format"]["name"]
        if purpose in ("read", "program"):
            part = next(p for p in payload["input"][0]["content"] if p["type"] == "input_image")
            sizes[purpose] = _open(base64.b64decode(part["image_url"].split(",", 1)[1])).size
        if purpose == "read":
            return _read_response(payload)
        return response_json({})
    job, fence = _claim(source_repo)
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, respond))
    tools.run("read", "read_drawing", {})
    tools.run("write", "write_program", {"brief": "a brief"})
    figure = source_repo.get(d.figures, "figure")
    assert sizes["read"] == sizes["program"] == (figure["width"] * 3, figure["height"] * 3)


def test_a_critic_with_no_sheet_is_not_held_to_one():
    result = {"verdict": "pass", "issues": [], "shape_checks": []}
    apply_shape_checks(result, [])
    assert result["verdict"] == "pass"


def test_the_critic_schema_asks_for_a_reading_per_part():
    assert "shape_checks" in CRITIC_SCHEMA["required"]
    assert agent_tools.SHAPE_CHECK_SCHEMA["properties"]["status"]["enum"] == ["match", "mismatch", "unclear"]


def test_a_broken_figure_costs_the_sheet_not_the_critic(source_repo, close_ups, monkeypatch):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    monkeypatch.setattr(close_up, "shape_sheet", lambda rows: (_ for _ in ()).throw(ValueError("no")))
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result.get("critic_verdict_id") and "Leave shape_checks empty" in _text(model.payloads["critic"][0])
