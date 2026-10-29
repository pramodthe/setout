import base64
import json
import struct
import sys
from types import SimpleNamespace

from app.cad_sandbox import modal_runner
from app.cad_components import build


def test_large_scripts_use_stdin_not_command_arguments(monkeypatch):
    received = []
    class Writer:
        def write(self, value):
            received.append(value)
        def write_eof(self):
            self.closed = True
        def drain(self):
            pass
    process = SimpleNamespace(stdin=Writer(), stdout=iter(["RESULT {}\n"]), stderr=iter([]),
                              wait=lambda: None, returncode=0)
    def execute(*args, **kwargs):
        assert args == ("python", "-")
        return process
    sandbox = SimpleNamespace(exec=execute, terminate=lambda: None)
    class Image:
        def apt_install(self, *args):
            return self
        def pip_install(self, *args):
            return self
    module = SimpleNamespace(App=SimpleNamespace(lookup=lambda *a, **k: None),
             Image=SimpleNamespace(debian_slim=lambda **kw: Image()),
             Sandbox=SimpleNamespace(create=lambda **kw: sandbox))
    monkeypatch.setitem(sys.modules, "modal", module)
    script = "x" * 303_540
    assert modal_runner(script) == "RESULT {}\n"
    assert b"".join(received) == script.encode()
    assert all(len(chunk) <= 65536 for chunk in received)


def test_viewer_mesh_is_tessellated_by_trusted_measurement_process():
    triangle = struct.pack("<12fH", 0,0,1, 0,0,0, 1,0,0, 0,1,0, 0)
    trusted_stl = b" " * 80 + struct.pack("<I", 1) + triangle
    def generated(_):
        return "RESULT " + json.dumps({"step":base64.b64encode(b"step fixture").decode(),
                                       "stl":base64.b64encode(b"untrusted fake mesh").decode()})
    def measured(script):
        assert "step fixture" not in script  # Only the encoded bounded artifact is embedded.
        return "RESULT " + json.dumps({"valid":True,"volume":1,"bounds":[0,0,0,1,1,1],"faces":[],
                                        "stl":base64.b64encode(trusted_stl).decode()})
    code = 'import cadquery as cq\ndef build(p):\n return cq.Workplane("XY").box(p["w"],p["h"],p["d"])'
    result = build(code, {"w":1,"h":1,"d":1}, [], runner=generated, measurement_runner=measured)
    assert result["stl"] == trusted_stl
    assert result["mesh"]["positions"] == [0,0,0, 1,0,0, 0,0,-1]
