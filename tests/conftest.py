"""Keep tests independent of the developer's store."""
import os
import tempfile

_store = tempfile.TemporaryDirectory(prefix="setout-suite-")
os.environ["SETOUT_VAR"] = _store.name
os.environ["SETOUT_ARTIFACT_ROOT"] = _store.name + "/artifacts"
os.environ["SETOUT_WORKER"] = "external"
os.environ["SETOUT_RENDERER"] = "off"  # live Modal calls are opt-in in dedicated tests


def pytest_unconfigure(config):
    _store.cleanup()
