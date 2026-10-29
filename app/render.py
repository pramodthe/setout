"""Headless rendering of the real viewer, pinned to a version and manifest."""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path
import threading

from .repository import sha

_pages = threading.BoundedSemaphore(int(os.environ.get("SETOUT_RENDER_PAGES", "2")))


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


class Renderer:
    def __init__(self, repo, base_url=None):
        self.repo = repo
        self.base_url = (base_url or os.environ.get("SETOUT_RENDER_URL", "http://127.0.0.1:8000")).rstrip("/")

    def render(self, version_id, manifest_sha):
        from playwright.sync_api import sync_playwright
        executable = os.environ.get("SETOUT_CHROMIUM_EXECUTABLE")
        # Use installed Chrome for local development; Linux deployment installs
        # the Playwright-pinned Chromium binary instead.
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not executable and chrome.is_file():
            executable = str(chrome)
        profile = {"width": 1280, "height": 960, "physical": True, "views": ["iso", "front", "top"]}
        output = []
        with _pages, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=executable,
                                                  args=["--use-gl=angle", "--use-angle=swiftshader"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 960}, device_scale_factor=1)
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
                                       "profile_sha": sha(profile), "viewer_sha": viewer_hash(),
                                       "view": view, "tags": tags,
                                       "artifact": self.repo.artifacts.put(data, "image/jpeg")})
            finally:
                browser.close()
        return output
