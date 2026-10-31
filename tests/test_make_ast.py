"""AST pre-check: imports and calls outside the script contract."""
from app.make.ast_check import check
from app.make.floor_waste_script import SCRIPT


def test_rejects_os_socket_open_and_import():
    cases = {
        "import os\nprint(os.environ)": "os",
        "import socket\nsocket.socket().connect(('127.0.0.1', 9))": "socket",
        "open('/etc/passwd').read()": "open",
        "__import__('os')": "__import__",
        "print(box_mm.__globals__)": "__globals__",
        "import bpy\nbpy.ops.wm.save_mainfile()": "save_mainfile",
    }
    for source, token in cases.items():
        result = check(source)
        assert not result.ok, source
        assert token in result.text


def test_reference_script_is_allowed():
    result = check(SCRIPT)
    assert result.ok, result.text


def test_allows_the_helper_imports():
    source = "import bpy, bmesh, math, mathutils, json\nfrom math import sqrt\nprint(sqrt(4))\n"
    assert check(source).ok
