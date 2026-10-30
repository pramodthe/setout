"""The CAD writer and the critic see each part up close, or the whole drawing if unsure.

Our own images and our own words, no network: the model is scripted. What a real
drawing did is in app/close_up.py; here each rule is pinned on its own.
"""
import base64
import io
import json
import sys
import types
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import agent_tools, close_up, database as d
from app.agent_tools import CAD_SCHEMA, CRITIC_SCHEMA, LAYER_RULE, Tools, apply_layer_checks, apply_shape_checks, model_view
from app.model_gateway import ModelGateway
from app.native_program import NativeProgram
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
    for text in (close_up.LOCATE_PROMPT, close_up.CHECK_PROMPT, close_up.ZOOM_PROMPT):
        assert "mm" not in text.lower().split() and "scale" not in text.lower()


# ------------------------------------------------------------------ a closer look

def test_a_closer_look_goes_where_both_calls_looked():
    first = {"a": [100, 100, 200, 200], "b": [300, 300, 400, 400], "c": [500, 500, 600, 600]}
    places = close_up.looked_at(first, _checked(a=("corrected", (150, 150, 400, 300)), b=("cannot_tell",), c=("corrected", (0, 0, 1200, 90))))
    assert places == {"a": [100, 100, 400, 300], "b": [300, 300, 400, 400], "c": [500, 500, 600, 600]}


def test_a_window_reaches_a_part_the_two_calls_placed_beside_it():
    """Our own numbers, the shape of a live miss: a part 8 thousandths wide, boxed 17 to its right."""
    size, placed, part = (2400, 1000), [661, 226, 675, 245], (644, 231, 652, 247)
    piece = close_up.window(size, placed)
    assert piece[0] <= part[0] * 2.4 and piece[2] >= part[2] * 2.4 and piece[1] <= part[1] and piece[3] >= part[3]


def test_a_window_stays_on_the_figure():
    assert close_up.window((1000, 1000), [0, 0, 50, 50])[:2] == [0, 0]
    assert close_up.window((1000, 1000), [950, 950, 1000, 1000])[2:] == [1000, 1000]


def test_a_part_a_fifth_of_the_sheet_keeps_the_box_the_two_calls_gave():
    """Measured: both held a large part, and a closer look boxed a piece of it."""
    assert close_up.window((1000, 1000), [100, 100, 400, 299]) is not None
    assert close_up.window((1000, 1000), [100, 100, 400, 300]) is None


def test_a_window_that_could_not_be_enlarged_gains_nothing():
    assert close_up.window((800, 800), [100, 100, 900, 150]) is not None  # a third of a megapixel
    assert close_up.window((4000, 4000), [100, 100, 900, 150]) is None


def test_a_closer_look_is_enlarged_and_carries_a_grid_of_its_own():
    image = Image.new("RGB", (2000, 1000), "white")
    data = close_up.zoom_image(image, [100, 100, 300, 200])
    with Image.open(io.BytesIO(data)) as shown:
        assert shown.size == (1414, 707)  # about a megapixel
        assert len(shown.convert("RGB").getcolors(1 << 16)) > 1  # the grid, on a blank sheet


def test_a_box_read_on_a_piece_goes_back_onto_the_figure():
    box = close_up.on_figure((1000, 500), [100, 50, 300, 150], {"found": True, "x0": 500, "y0": 0, "x1": 1000, "y1": 500})
    assert box == [200.0, 100.0, 300.0, 200.0]


@pytest.mark.parametrize("answer", [{"found": False, "x0": 1, "y0": 1, "x1": 9, "y1": 9}, {"found": True, "x0": 9, "y0": 1, "x1": 1, "y1": 9}])
def test_a_closer_look_with_no_box_has_none_to_give(answer):
    assert close_up.on_figure((1000, 500), [100, 50, 300, 150], answer) is None


# ------------------------------------------------------------------ the writer

class Located:
    """A model that places the two objects, then writes their CAD."""
    def __init__(self, check=None, located=None, fail=None, reading=None, missing=(), zoomed=None):
        self.check, self.located, self.fail, self.missing = check, located, fail, list(missing)
        self.zoomed = zoomed or {"found": True, "x0": 400, "y0": 400, "x1": 600, "y1": 600}
        self.reading = reading or (lambda prompt: _shape("match"))
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
        if purpose == "zoom":
            self.calls.append("zoom")
            self.payloads.setdefault("zoom", []).append(payload)
            if self.fail == "zoom":
                return {"output": [], "status": "incomplete", "usage": {"input_tokens": 1, "output_tokens": 1}}
            return response_json(self.zoomed)
        if purpose == "cad":
            self.calls.append("cad")
            self.payloads.setdefault("cad", []).append(payload)
            return response_json({"code": LOCAL, "envelope_problem": ""})
        if purpose == "shape":
            self.calls.append("shape")
            self.payloads.setdefault("shape", []).append(payload)
            return response_json(self.reading(_text(payload)))
        if purpose == "critic":
            self.calls.append("critic")
            self.payloads.setdefault("critic", []).append(payload)
            return response_json({"verdict": "pass", "issues": [], "missing_parts": self.missing})
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
    assert model.calls == ["locate", "check", "zoom", "cad", "cad"]  # the thin tray alone gets a closer look
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


def _layer_reading(part, same, drawn="runs the whole way", built="stops short"):
    return {"part": part, "drawn": drawn, "built": built, "same": same}


def test_a_layer_read_differently_is_an_error_whatever_the_critic_concludes():
    """Seen live: a critic passed a membrane built beside the sheet the drawing lays over it."""
    result = {"verdict": "pass", "issues": [], "layers": [_layer_reading("layer", False), _layer_reading("slab", True)]}
    apply_layer_checks(result, NativeProgram.model_validate(_prepared_raw()))
    assert [issue["id"] for issue in result["issues"]] == ["layout-mismatch:layer"]
    issue = result["issues"][0]
    assert issue["severity"] == "error" and not issue["needs_human"] and issue["parts"] == ["layer"]
    assert "runs the whole way" in issue["reason"] and "stops short" in issue["reason"]


def test_only_a_part_of_the_program_can_raise_a_layer_issue():
    result = {"verdict": "pass", "issues": [], "layers": [_layer_reading("no_such_part", False)]}
    apply_layer_checks(result, NativeProgram.model_validate(_prepared_raw()))
    assert result["issues"] == []


def test_a_critic_with_no_layer_reading_raises_nothing():
    result = {"verdict": "pass", "issues": []}
    apply_layer_checks(result, NativeProgram.model_validate(_prepared_raw()))
    assert result["issues"] == []


def test_the_critic_is_told_to_read_the_drawing_before_the_build(source_repo):
    assert "layers" in CRITIC_SCHEMA["required"] and "before you look at the build" in LAYER_RULE
    assert LAYER_RULE.index("drawn") < LAYER_RULE.index("built")


def test_a_layer_the_critic_read_differently_fails_the_critic_and_is_a_repair(source_repo, close_ups):
    model = Located()
    real = model.__call__
    job, tools, written = _tools(source_repo, model)
    def critic(payload):
        if payload["text"]["format"]["name"] == "critic":
            assert LAYER_RULE in _text(payload)
            return response_json({"verdict": "pass", "issues": [], "missing_parts": [], "layers": [_layer_reading("layer", False)]})
        return real(payload)
    tools.gateway.responder = critic
    version = written["version_id"]
    report = tools.run("v", "build_and_verify", {"version_id": version})
    rendered = tools.run("r", "render", {"version_id": version})
    verdict = tools.run("c", "critic", {"version_id": version, "verification_id": report["verification_id"],
                                        "render_ids": [r["render_id"] for r in rendered["renders"]]})
    assert verdict["verdict"] == "fail" and "layout-mismatch:layer" in tools._repairs(source_repo.run(job["id"]))
    view = model_view("critic", verdict)
    assert "layers" not in view and any(i["id"] == "layout-mismatch:layer" for i in view["issues"])


SMALL = dict(track=(300, 300, 340, 340), bottom_tray=(200, 500, 800, 540))


def _prepared_raw():
    from test_threshold_run import SOURCE, READ
    from app.native_program import prepare
    return prepare(threshold(), SOURCE, "d", READ)[0].model_dump(mode="json")


def _regions(model, repo):
    job, tools, written = _tools(repo, model)
    version = repo.get(d.versions, written["version_id"])
    return tools._part_regions(version, NativeProgram.model_validate(version["program"]))


def test_a_small_part_is_placed_by_its_closer_look_not_by_the_boxes_before(source_repo, close_ups):
    """Seen live: both calls passed a box on the empty leader beside the part."""
    model = Located(located=_located(**SMALL))
    regions = _regions(model, source_repo)
    assert model.calls.count("zoom") == 2 and regions["track"] != [300, 300, 340, 340]
    assert "track" in _text(model.payloads["zoom"][0]) and len(_images(model.payloads["zoom"][0])) == 1
    assert model.payloads["zoom"][0]["reasoning"]["effort"] == "low"  # a box needs no deep thought, and there is one per part


def test_where_the_two_calls_disagree_a_closer_look_can_still_place_the_part(source_repo, close_ups):
    model = Located(located=_located(**SMALL), check=_checked(track=("corrected", (360, 300, 400, 340)), bottom_tray=("cannot_tell",)))
    regions = _regions(model, source_repo)
    assert set(regions) == {"track", "bottom_tray"}  # seen live: no close-up for either


def test_a_part_a_closer_look_cannot_find_gets_no_close_up(source_repo, close_ups):
    model = Located(located=_located(**SMALL), zoomed={"found": False, "x0": 0, "y0": 0, "x1": 1, "y1": 1})
    assert _regions(model, source_repo) == {}


def test_a_failed_closer_look_leaves_what_the_two_calls_settled(source_repo, close_ups):
    model = Located(located=_located(**SMALL), fail="zoom")
    assert _regions(model, source_repo) == {"track": [300, 300, 340, 340], "bottom_tray": [200, 500, 800, 540]}


def test_a_large_part_is_not_looked_at_closer(source_repo, close_ups):
    model = Located(located=_located(track=(100, 100, 600, 600), bottom_tray=(100, 100, 900, 900)))
    regions = _regions(model, source_repo)
    assert "zoom" not in model.calls and regions["track"] == [100, 100, 600, 600]


def test_a_closer_look_is_spent_once_however_often_it_is_asked_for(source_repo, close_ups):
    model = Located(located=_located(**SMALL))
    job, tools, written = _tools(source_repo, model)
    version = source_repo.get(d.versions, written["version_id"])
    program = NativeProgram.model_validate(version["program"])
    assert tools._part_regions(version, program) == tools._part_regions(version, program)
    assert model.calls.count("zoom") == 2


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


def test_a_solid_that_cannot_be_meshed_says_why(monkeypatch):
    """Seen live, as FileNotFoundError: part.stl, which tells a writer nothing. The real
    export script runs here against a stand-in for CadQuery that writes no mesh."""
    from app import cad_components
    class Shape:
        pass
    class Workplane:
        def __init__(self, *args):
            pass
        def box(self, *args, **kwargs):
            return self
        def vals(self):
            return [Shape()]
    def export(shape, target, **options):
        if target.endswith(".step"):
            Path(target).write_bytes(b"step")
    stand_in = types.ModuleType("cadquery")
    stand_in.Workplane, stand_in.Shape, stand_in.exporters = Workplane, Shape, types.SimpleNamespace(export=export)
    monkeypatch.setitem(sys.modules, "cadquery", stand_in)
    scripts = []
    def runner(script):
        scripts.append(script)
        raise cad_components.cad_sandbox.CadCodeError("stop here")
    with pytest.raises(cad_components.cad_sandbox.CadCodeError):
        cad_components.build(LOCAL, {"width": 1, "depth": 1, "height": 1}, [], runner=runner)
    with pytest.raises(ValueError, match="could not be meshed.*axis"):
        exec(scripts[0], {})


def test_a_critic_sees_a_part_no_smaller_than_the_figure_shows_it():
    """Seen live: seven objects on one sheet shrank each crop below the figure's own
    resolution, and a thin turn-up could not be seen. Each object now has an image."""
    figure = _image(1000, 1000)
    ImageDraw.Draw(figure).rectangle([300, 500, 700, 503], fill="black")
    drawn = close_up.crop(figure, [290, 490, 710, 510], mark=True)
    pair = _open(close_up.shape_pair("part", drawn, [0, 0, 0, 10, 0, 0, 10, 10, 0]))
    assert pair.width * pair.height <= close_up.MAX_PIXELS / 2  # a megapixel or so: about 1.3k tokens each
    tall = sum(1 for y in range(pair.height) if pair.convert("L").getpixel((close_up.PAIR_SIDE // 2, y)) < 128)
    assert tall >= 4  # the 4 px plate is at least its full height across the part


def test_the_critics_close_up_outlines_the_part_and_the_writers_does_not():
    figure = _image(1000, 1000)
    plain, marked = close_up.crop(figure, [400, 400, 600, 600]), close_up.crop(figure, [400, 400, 600, 600], mark=True)
    colours = {colour for _, colour in _open(marked).convert("RGB").getcolors(maxcolors=100000)}
    assert (220, 0, 90) in colours and {colour for _, colour in _open(plain).convert("RGB").getcolors(maxcolors=100000)} == {(255, 255, 255)}


def test_an_object_with_no_close_up_still_shows_as_built():
    pair = _open(close_up.shape_pair("part", None, [0, 0, 0, 10, 0, 0, 10, 10, 0]))
    assert pair.convert("L").getpixel((close_up.PAIR_SIDE + 40, pair.height - 20)) < 128


def _built(repo, model):
    job, tools, written = _tools(repo, model)
    first = tools.run("m1", "model_component", {"version_id": written["version_id"], "part_key": "track", "brief": "x"})
    second = tools.run("m2", "model_component", {"version_id": first["version_id"], "part_key": "bottom_tray", "brief": "y"})
    rendered = tools.run("r", "render", {"version_id": second["version_id"]})
    return tools, second, rendered


def test_each_object_is_read_in_a_call_of_its_own(source_repo, close_ups):
    """Measured on a real run: a shared call named a part's extra base 1 time of 3 and a
    call of its own 5 of 5, at low effort."""
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    tools.run("c", "critic", rendered["critic_args"])
    assert model.calls.count("shape") == 2 and model.calls.count("locate") == 1  # the critic reuses what CAD found
    reading = model.payloads["shape"][0]
    assert len(_images(reading)) == 2  # the whole drawing, then the object's pair
    assert reading["reasoning"]["effort"] == "low"
    text = _text(reading)
    assert "The object is track." in text and "bottom_tray" in text and "never compare overall sizes" in text
    assert len(_images(model.payloads["critic"][0])) == 5  # the figure, three plain views and the tagged iso


def test_the_reader_is_shown_the_part_outlined(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    tools.run("c", "critic", rendered["critic_args"])
    pair = _open(base64.b64decode(_images(model.payloads["shape"][0])[1]["image_url"].split(",", 1)[1])).convert("RGB")
    assert any(r - g > 60 for _, (r, g, b) in pair.getcolors(maxcolors=1 << 20))  # resizing blends it, so not the exact colour


def test_a_solid_is_read_once_however_many_critics_follow(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    tools.run("c1", "critic", rendered["critic_args"])
    tools.run("c2", "critic", rendered["critic_args"])
    assert model.calls.count("shape") == 2 and model.calls.count("critic") == 2


def test_a_new_solid_is_read_again(source_repo, close_ups):
    model = Located()
    tools, second, rendered = _built(source_repo, model)
    tools.run("c1", "critic", rendered["critic_args"])
    again = tools.run("m3", "model_component", {"version_id": second["version_id"], "part_key": "track", "brief": "again"})
    rendered = tools.run("r2", "render", {"version_id": again["version_id"]})
    tools.run("c2", "critic", rendered["critic_args"])
    assert model.calls.count("shape") == 3


def test_a_reading_that_comes_back_empty_leaves_the_object_unjudged(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    real = model.__call__
    def fails(payload):
        if payload["text"]["format"]["name"] == "shape":
            return {"output": [], "status": "incomplete", "usage": {"input_tokens": 1, "output_tokens": 1}}
        return real(payload)
    tools.gateway.responder = fails
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result["verdict"] == "inconclusive" and result["issues"][-1]["id"] == "shape-unjudged"
    assert result["issues"][-1]["parts"] == ["track", "bottom_tray"]


def _shape(status):
    """A reading of a part, by what its feature list comes to."""
    features = [{"feature": "three fins on a base", "built": status != "mismatch"}]
    return {"close_up_shows_part": status != "unclear", "built_features": [],
            "drawing_features": [] if status == "unclear" else features}


def _critic(verdict="pass"):
    return {"verdict": verdict, "issues": []}


def test_a_shape_mismatch_is_an_issue_about_that_part():
    result = _critic()
    apply_shape_checks(result, {"track": _shape("mismatch"), "tray": _shape("match")})
    issue = result["issues"][0]
    assert issue["id"] == "shape-mismatch:track" and issue["parts"] == ["track"] and issue["severity"] == "error"
    assert "three fins on a base" in issue["reason"] and "does not" in issue["reason"] and len(result["issues"]) == 1


def test_something_the_drawing_does_not_show_is_a_mismatch_even_if_every_feature_is_ticked():
    """Seen live: the critic noted an extra base and still answered match."""
    item = _shape("match")
    item["built_features"] = [{"feature": "a solid base plate across the bottom", "drawn": False},
                              {"feature": "a fin on the left", "drawn": True}]
    result = _critic()
    apply_shape_checks(result, {"track": item})
    issue = result["issues"][0]
    assert issue["id"] == "shape-mismatch:track" and "solid base plate" in issue["reason"] and "fin on the left" not in issue["reason"]


def test_a_reader_cannot_say_match_for_itself():
    """The tally decides: there is no field for it to say so in."""
    assert list(agent_tools.SHAPE_CHECK_SCHEMA["properties"]) == ["close_up_shows_part", "drawing_features", "built_features"]


def test_a_close_up_that_does_not_show_the_part_judges_nothing():
    assert agent_tools.shape_differences(_shape("unclear")) is None
    item = _shape("match")
    item["drawing_features"] = []
    assert agent_tools.shape_differences(item) is None


def test_the_critic_reads_no_word_the_author_wrote_about_the_form(source_repo, close_ups):
    """It must stay independent of the writer, or it reads the brief back."""
    model = Located()
    tools, version, rendered = _built(source_repo, model)
    tools.run("c", "critic", rendered["critic_args"])
    for text in (_text(model.payloads["critic"][0]), _text(model.payloads["shape"][0])):
        assert "extruded door track with fins" not in text and "bent tray under the track" not in text
        assert '"cad_brief"' not in text and '"note"' not in text
    assert '"id":"track"' in _text(model.payloads["critic"][0])  # the critic still reads the program


def test_the_order_of_the_steps_is_the_drawing_first():
    rule = agent_tools.SHAPE_RULE
    assert rule.index("drawing_features") < rule.index("built_features")
    assert "before you look at the right" in rule and "A close resemblance is not a match" in rule


def test_a_shape_mismatch_means_the_critic_cannot_pass(source_repo, close_ups):
    model = Located(reading=lambda prompt: _shape("mismatch" if "The object is track." in prompt else "match"))
    tools, _, rendered = _built(source_repo, model)
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result["verdict"] == "fail" and result["issues"][0]["id"] == "shape-mismatch:track"
    stored = source_repo.get(d.critics, result["critic_verdict_id"])
    assert stored["issues"][0]["parts"] == ["track"]
    view = model_view("critic", result)
    assert "model_component" in view["next_step"] and "track" in view["next_step"]


def test_an_object_that_could_not_be_compared_holds_a_pass_back():
    result = _critic()
    apply_shape_checks(result, {"track": _shape("match"), "tray": _shape("unclear"), "screw": None})
    assert result["verdict"] == "inconclusive"
    assert result["issues"][0]["parts"] == ["tray", "screw"] and result["issues"][0]["needs_human"]


def test_with_no_objects_to_compare_nothing_is_held_back():
    result = _critic()
    apply_shape_checks(result, {})
    assert result["verdict"] == "pass" and result["issues"] == []


def test_a_part_the_program_lacks_means_the_critic_cannot_pass(source_repo, close_ups):
    """Seen live: a critic passed a version with no part for a bent plate it could see."""
    model = Located(missing=[{"what": "a bent plate under the track", "where": "below the track"}])
    tools, _, rendered = _built(source_repo, model)
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result["verdict"] == "fail" and result["issues"][0]["id"] == "missing-part-0"
    assert "a bent plate under the track" in result["issues"][0]["reason"]
    next_step = model_view("critic", result)["next_step"]
    assert "patch_program" in next_step and "a bent plate" in next_step


def test_the_critic_is_asked_to_look_for_what_is_not_there(source_repo, close_ups):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    tools.run("c", "critic", rendered["critic_args"])
    text = _text(model.payloads["critic"][0])
    assert "missing_parts" in text and '"drawn_without_a_label":' in text
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


def test_a_broken_figure_costs_the_pictures_not_the_critic(source_repo, close_ups, monkeypatch):
    model = Located()
    tools, _, rendered = _built(source_repo, model)
    monkeypatch.setattr(close_up, "shape_pair", lambda *args: (_ for _ in ()).throw(ValueError("no")))
    result = tools.run("c", "critic", rendered["critic_args"])
    assert result.get("critic_verdict_id") and "shape" not in model.calls
