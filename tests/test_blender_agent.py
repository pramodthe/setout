"""What a model may run in Blender, and what it is told about the kit. No Blender or network needed."""
import re
from pathlib import Path

import pytest

import goal
from goal.blender_agent import api, check, code_of


@pytest.mark.parametrize("code", ["import os", "from subprocess import run", "open('x')", "eval('1')", "(1).__class__", "import bpy"])
def test_model_code_cannot_reach_past_the_kit(code):
    with pytest.raises(ValueError):
        check(code)


def test_a_scene_over_the_shapes_passes():
    check("from blender_kit import *\nimport math\nstart()\nmaterials()\nbox('a', (0, 0, 0), (1, 1, math.pi), 'concrete', 'g')\nfinish()\n")


def test_the_kit_holds_shapes_and_no_component():
    """The owner's rule: every real part is written by the model."""
    kit = (Path(goal.__file__).parent / "blender_kit.py").read_text()
    made = re.findall(r"^def ([a-z]\w*)\(", kit, re.M)
    assert not {"sliding_door", "cavity_wall", "brick_wall", "tiles", "screw", "floor_finish"} & set(made)
    text = api()
    assert "prism(" in text and "sheet(" in text and "material names:" in text and "sliding_door" not in text


def test_the_scene_is_taken_from_its_code_block():
    assert code_of("Faults:\n- one\n```python\nfrom blender_kit import *\n```\n") == "from blender_kit import *\n"


def test_a_readers_faults_survive_loose_text_and_bad_json():
    from goal.muse_parts import faults_in
    assert faults_in('Here: {"faults": [{"part": "sill", "fault": "floats"}]}') == [{"part": "sill", "fault": "floats"}]
    assert faults_in("no json here") == [] and faults_in('{"faults": [{"part": "x"}]}') == []


def test_the_way_of_working_holds_no_geometry():
    """The owner's rule: this loop may direct the model, and may not model anything itself."""
    source = (Path(goal.__file__).parent / "muse_parts.py").read_text()
    assert not re.search(r"\b(box|prism|sheet|lathe|rod)\(\w", source.replace("box() or prism()", ""))
