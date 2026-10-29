"""Immutable, content-addressed bytes shared by API and workers."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from functools import wraps
from pathlib import Path

from . import store


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class ArtifactStore:
    def __init__(self, root=None):
        self._lock = threading.RLock()
        self._volume = None
        if os.environ.get("SETOUT_ARTIFACT_VOLUME"):
            import modal
            self._volume = modal.Volume.from_name(os.environ["SETOUT_ARTIFACT_VOLUME"])
        self.root = Path(root or os.environ.get("SETOUT_ARTIFACT_ROOT") or
                         store.var_root() / "artifacts").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @serialized
    def put(self, data: bytes, media_type="application/octet-stream"):
        digest = hashlib.sha256(data).hexdigest()
        destination = self.root / digest
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            # Hard-link publication is atomic and never replaces existing bytes.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                self.read(digest)
        finally:
            temporary.unlink(missing_ok=True)
        if self._volume:
            self._volume.commit()
        return {"id": digest, "sha256": digest, "storage_key": digest,
                "media_type": media_type, "size_bytes": len(data)}

    @serialized
    def resolve(self, artifact_id):
        if self._volume:
            self._volume.reload()
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise ValueError("invalid artifact ID")
        path = self.root / artifact_id
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("artifact missing")
        return path

    @serialized
    def read(self, artifact_id):
        data = self.resolve(artifact_id).read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact_id:
            raise ValueError("artifact checksum mismatch")
        return data
