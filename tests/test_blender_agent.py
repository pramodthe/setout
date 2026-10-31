"""What a model may run in Blender, and what it is told about the kit. No Blender or network needed."""
import pytest

from goal.blender_agent import api, check, code_of


@pytest.mark.parametrize("code", ["import os", "from subprocess import run", "open('x')", "eval('1')", "(1).__class__", "import bpy"])
def test_model_code_cannot_reach_past_the_kit(code):
    with pytest.raises(ValueError):
        check(code)


def test_a_scene_over_the_kit_passes():
    check("from blender_kit import *\nimport math\nstart()\nmaterials()\nbox('a', (0, 0, 0), (1, 1, math.pi), 'concrete', 'g')\nfinish()\n")


def test_the_model_sees_the_kit_but_not_its_code():
    text = api()
    assert "sliding_door(" in text and "cavity_wall(" in text and "material names:" in text and "concrete" in text
    assert "def _add" not in text and "bpy." not in text


def test_the_scene_is_taken_from_its_code_block():
    assert code_of("Faults:\n- one\n```python\nfrom blender_kit import *\n```\n") == "from blender_kit import *\n"
