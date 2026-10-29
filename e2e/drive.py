"""Headless viewer drive: full acceptance pass with screenshots.

Requires: server running (`SETOUT_VAR=var python -m uvicorn app.main:app`),
Playwright + a Chromium executable (uses system Google Chrome), and the
reference PDF at /tmp/conpro_sills.pdf (NOT committed; download from the
Conpro URL in docs/STATUS.md).

Usage:  python e2e/drive.py [base-url]   (default http://localhost:8000)
Screenshots land in /tmp/setout-drive/ (gitignored scratch).
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = Path("/tmp/setout-drive")
OUT.mkdir(exist_ok=True)
shot = lambda n: page.screenshot(path=str(OUT / f"{n}.png"))

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    page = browser.new_page(viewport={"width": 1500, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console-{m.type}: {m.text}")
            if m.type in ("error", "warning") else None)
    page.goto(URL + "/#/workbench", wait_until="networkidle")
    shot("01-loaded")

    # upload
    page.set_input_files("#pdf", "/tmp/conpro_sills.pdf")
    page.click("#upload")
    page.wait_for_selector(".crop", timeout=20000)
    assert "conpro" in page.inner_text("#docline"), page.inner_text("#docline")
    shot("02-crops")

    # select D3 crop
    page.click('.crop[data-crop="p0_Image21"]')
    page.wait_for_selector("#comps .comp", timeout=10000)
    ncomps = page.eval_on_selector_all("#comps .comp", "els => els.length")
    assert ncomps == 17, ncomps
    shot("03-overlay")

    # generate
    page.click("#build")
    page.wait_for_function(
        "() => document.querySelector('#progress').innerText.includes('state: done')",
        timeout=30000)
    page.wait_for_timeout(800)
    verdict = page.inner_text("#verdict")
    print("verdict:", verdict)
    assert verdict.startswith("PASS"), verdict
    shot("04-built")

    # orbit via drags, multi-frame
    box = page.eval_on_selector("#gl canvas",
                                "c => {const r = c.getBoundingClientRect(); "
                                "return {x: r.x + r.width/2, y: r.y + r.height/2}}")
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    for i in range(6):
        page.mouse.move(box["x"] + (i + 1) * 40, box["y"] - (i + 1) * 8)
        page.wait_for_timeout(120)
    page.mouse.up()
    shot("05-orbited")

    # layer toggle: hide screed
    screed = page.eval_on_selector_all(
        "#comps .comp", "els => els.findIndex(e => e.textContent.includes('screed'))")
    page.eval_on_selector_all(
        "#comps .comp input", f"els => {{els[{screed}].click()}}")
    page.wait_for_timeout(400)
    shot("06-layer-off")

    # evidence from list
    page.eval_on_selector_all(
        "#comps .comp", "els => els.find(e => e.textContent.includes('sealant')).click()")
    page.wait_for_timeout(300)
    ev = page.inner_text("#evidence")
    assert "6mm gap with backing rod and sealant" in ev, ev[:300]
    shot("07-evidence-list")

    # evidence from overlay rect (first rect = slab_upper order may vary; click membrane-ish)
    page.locator("#overlay rect").first.dispatch_event("click")
    page.wait_for_timeout(300)
    shot("08-evidence-overlay")

    # evidence from 3D canvas click (center-ish)
    page.mouse.click(box["x"], box["y"])
    page.wait_for_timeout(300)
    shot("09-evidence-3d")

    # explode
    page.eval_on_selector("#explode", "el => {el.value = 220; el.dispatchEvent(new Event('input'))}")
    page.wait_for_timeout(400)
    shot("10-exploded")
    page.eval_on_selector("#explode", "el => {el.value = 0; el.dispatchEvent(new Event('input'))}")

    # section clip
    page.click("#clipOn")
    page.eval_on_selector("#clipZ", "el => {el.value = 300; el.dispatchEvent(new Event('input'))}")
    page.wait_for_timeout(400)
    shot("11-section")
    page.click("#clipOn")

    # install playback, observe mid-run responsiveness
    page.click("#seqPlay")
    page.wait_for_timeout(2500)
    shot("12-playback-mid")
    mid = page.inner_text("#seqLabel")
    page.wait_for_function(
        "() => document.querySelector('#seqPlay').innerText === 'play'",
        timeout=30000)
    print("playback mid-label:", mid)
    assert mid.startswith("step "), mid
    shot("13-playback-done")

    # param edit + rebuild (packer h assumption 8 -> 10)
    page.eval_on_selector_all(
        "#comps .comp", "els => els.find(e => e.textContent.includes('packer')).click()")
    page.wait_for_timeout(300)
    page.fill('input[data-p="h"]', "10")
    page.click("#applyP")
    page.wait_for_function(
        "() => document.querySelector('#progress').innerText.includes('state: done')",
        timeout=30000)
    page.wait_for_timeout(500)
    verdict2 = page.inner_text("#verdict")
    print("verdict after edit:", verdict2)
    shot("14-rebuilt")

    # downloads + verification panel
    for name in ("spec.json", "scene.json", "model.glb", "report.json"):
        with page.expect_download(timeout=10000) as dl:
            page.click(f'#downloads a:has-text("{name}")')
        path = dl.value.path()
        size = Path(path).stat().st_size
        print(f"download {name}: {size} bytes")
        assert size > 100, name
    checks = page.inner_text("#checks")
    assert "FAIL 0" in checks, checks[:200]
    unres = page.inner_text("#unres")
    assert "U-membrane-termination" in unres
    shot("15-final")

    # ---- variant D4: switch, rebuild, inspect ----
    page.click('#variants button[data-detail="conpro-subsill-D4"]')
    page.wait_for_function(
        "() => document.querySelectorAll('#comps .comp').length === 14",
        timeout=10000)
    assert "p0_Image22" in page.get_attribute("#cropimg", "src")
    shot("16-d4-selected")
    page.click("#build")
    page.wait_for_function(
        "() => document.querySelector('#progress').innerText.includes('state: done')",
        timeout=30000)
    page.wait_for_timeout(800)
    verdict4 = page.inner_text("#verdict")
    print("D4 verdict:", verdict4)
    assert verdict4.startswith("PASS"), verdict4
    checks4 = page.inner_text("#checks")
    assert "FAIL 0" in checks4, checks4[:200]
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    for i in range(4):
        page.mouse.move(box["x"] - (i + 1) * 40, box["y"] + (i + 1) * 6)
        page.wait_for_timeout(120)
    page.mouse.up()
    shot("17-d4-built")
    page.eval_on_selector_all(
        "#comps .comp",
        "els => els.find(e => e.textContent.includes('booker_rod')).click()")
    page.wait_for_timeout(300)
    ev4 = page.inner_text("#evidence")
    assert "Stainless steel booker rod" in ev4, ev4[:300]
    shot("18-d4-evidence")

    print("console/page errors:", len(errors))
    for e in errors[:20]:
        print("  ", e[:220])
    browser.close()

print("DRIVE OK")
