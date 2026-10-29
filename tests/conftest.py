"""Keep API tests independent of the developer's migrated library and sources."""
import os
import tempfile

_store = tempfile.TemporaryDirectory(prefix="setout-suite-")
os.environ["SETOUT_VAR"] = _store.name
os.environ["SETOUT_DATABASE_URL"] = "sqlite:///" + _store.name + "/v3.db"
os.environ["SETOUT_ARTIFACT_ROOT"] = _store.name + "/artifacts"
os.environ["SETOUT_WORKER"] = "external"


def pytest_unconfigure(config):
    # Process exit closes cached service handles; TemporaryDirectory also has
    # its own finalizer for interrupted test runs.
    _store.cleanup()
