"""Headless rendering of the real viewer, pinned to a version and manifest."""
from __future__ import annotations

import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from .repository import sha


def validate_image(data):
    from PIL import Image, ImageStat
    if not data.startswith(b"\xff\xd8\xff") or len(data) < 1000:
        raise ValueError("renderer produced an empty/invalid image")
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if min(image.size) < 100 or max(ImageStat.Stat(image.convert("RGB")).var) < 1:
            raise ValueError("renderer produced a blank or undersized image")


def viewer_hash():
    root = Path(__file__).resolve().parent.parent / "web"
    files = sorted((root / "dist").rglob("*.js")) or sorted((root / "src").rglob("*.ts*"))
    return sha([(p.relative_to(root).as_posix(), p.read_text()) for p in files])


_pool_lock = threading.Lock()
_pool = None
_local = threading.local()
CHROME_ARGS = ["--use-gl=angle", "--use-angle=swiftshader"]


def _executor():
    """A few long-lived render threads, each owning one headless browser.

    Playwright's sync API is bound to the thread that started it, so each
    browser lives in its own thread and is reused across renders instead of
    being launched for every one."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(int(os.environ.get("SETOUT_RENDER_PAGES", "2")),
                                       thread_name_prefix="setout-render")
        return _pool


def _browser():
    browser = getattr(_local, "browser", None)
    if browser is not None and browser.is_connected():
        return browser
    from playwright.sync_api import sync_playwright
    executable = os.environ.get("SETOUT_CHROMIUM_EXECUTABLE")
    # Use installed Chrome for local development; Linux deployment installs
    # the Playwright-pinned Chromium binary instead.
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not executable and chrome.is_file():
        executable = str(chrome)
    if getattr(_local, "playwright", None) is None:
        _local.playwright = sync_playwright().start()
    _local.browser = _local.playwright.chromium.launch(headless=True, executable_path=executable, args=CHROME_ARGS)
    return _local.browser


class Renderer:
    def __init__(self, repo, base_url=None):
        self.repo = repo
        self.base_url = (base_url or os.environ.get("SETOUT_RENDER_URL", "http://127.0.0.1:8000")).rstrip("/")

    def render(self, version_id, manifest_sha):
        return _executor().submit(self._render, version_id, manifest_sha).result(timeout=300)

    def _render(self, version_id, manifest_sha):
        profile = {"width": 1280, "height": 960, "physical": True, "views": ["iso", "front", "top"]}
        output = []
        viewer = viewer_hash()
        browser = _browser()
        page = browser.new_page(viewport={"width": 1280, "height": 960}, device_scale_factor=1)
        try:
            page.goto(f"{self.base_url}/#/render/{version_id}", wait_until="networkidle", timeout=60000)
            page.wait_for_function("window.__ready || window.__renderError", timeout=60000)
            error = page.evaluate("window.__renderError")
            if error:
                raise RuntimeError(error)
            ready = page.evaluate("window.__ready")
            if ready["version_id"] != version_id or ready["manifest_sha"] != manifest_sha:
                raise RuntimeError("renderer loaded a different version or manifest")
            for view in profile["views"]:
                for tags in (False, True):
                    data_url = page.evaluate("([view,tags]) => window.__renderSnapshot(view,tags)", [view, tags])
                    data = base64.b64decode(data_url.split(",", 1)[1], validate=True)
                    validate_image(data)
                    output.append({"version_id": version_id, "manifest_sha": manifest_sha,
                                   "profile_sha": sha(profile), "viewer_sha": viewer,
                                   "view": view, "tags": tags,
                                   "artifact": self.repo.artifacts.put(data, "image/jpeg")})
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001 - a crashed browser is relaunched next time
                _local.browser = None
        return output
