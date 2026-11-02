"""Keep tests independent of the developer's store."""
import os
import tempfile

_store = tempfile.TemporaryDirectory(prefix="setout-suite-")
os.environ["SETOUT_VAR"] = _store.name
os.environ["SETOUT_ARTIFACT_ROOT"] = _store.name + "/artifacts"
os.environ["SETOUT_WORKER"] = "external"


def pytest_unconfigure(config):
    _store.cleanup()
