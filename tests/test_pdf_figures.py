"""Figures from PDFs: embedded images, rendered vector pages, the rule between them.

Every PDF here is written by hand, in memory. Never commit a PDF or a drawing.
"""
import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pypdfium2
import pytest
from PIL import Image
from pypdf import apply_configuration

from app import database as d, store
from app.versioning import register_figure
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo  # noqa: F401
from test_api_v3 import client  # noqa: F401

MM = 72 / 25.4
A4 = (595, 842)
A3 = (1190.55, 841.89)


class Pdf:
    """The smallest PDF writer that pypdf and PDFium both read."""

    def __init__(self):
        self.objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
        self.kids = []
        self.font = self.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    def add(self, body):
        self.objects.append(body)
        return len(self.objects)

    def stream(self, data, entries=""):
        return self.add(f"<< {entries} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")

    def image(self, size, color=(200, 30, 30)):
        """An embedded JPEG, returned with its bytes to prove extraction is lossless."""
        out = io.BytesIO()
        Image.new("RGB", size, color).save(out, "JPEG")
        ref = self.stream(out.getvalue(), f"/Type /XObject /Subtype /Image /Width {size[0]} /Height {size[1]} "
                                          "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode")
        return ref, out.getvalue()

    def form(self, content, xobjects=None):
        return self.stream(content, f"/Type /XObject /Subtype /Form /BBox [-9999 -9999 9999 9999] "
                                    f"/Resources << /XObject << {self._names(xobjects)} >> >>")

    def page(self, content=b"", size=A4, xobjects=None, entries=""):
        contents = self.stream(content)
        ref = self.add((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] {entries} "
                        f"/Contents {contents} 0 R /Resources << /Font << /F1 {self.font} 0 R >> "
                        f"/XObject << {self._names(xobjects)} >> >> >>").encode())
        self.kids.append(ref)
        return self

    @staticmethod
    def _names(xobjects):
        return " ".join(f"/{name} {ref} 0 R" for name, ref in (xobjects or {}).items())

    def bytes(self):
        kids = " ".join(f"{k} 0 R" for k in self.kids)
        self.objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(self.kids)} >>".encode()
        out, offsets = bytearray(b"%PDF-1.7\n"), []
        for i, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += f"trailer\n<< /Size {len(self.objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


SECTION = b"2 w 100 100 395 300 re S\n100 100 m 495 400 l S\n"


def hatch(lines):
    """Line work: `lines` diagonal strokes, two path segments each."""
    return b"".join(f"{60 + i % 400} 450 m {90 + i % 400} 780 l S\n".encode() for i in range(lines))


def text(lines, per_line=80):
    """Real text objects: `lines` * `per_line` characters."""
    return b"".join(f"BT /F1 8 Tf 40 {800 - 12 * i} Td ({'word ' * (per_line // 5)}) Tj ET\n".encode()
                    for i in range(lines))


def place(name, x, y, width, height):
    return f"q {width} 0 0 {height} {x} {y} cm /{name} Do Q\n".encode()


def store_one(content=b"", **page):
    return store.store_pdf("own.pdf", Pdf().page(content, **page).bytes())


def kinds(doc):
    return [(f["crop_id"], f["kind"]) for f in doc["figures"]]


def png(doc, index=0):
    return Image.open(store.figure_path(doc["id"], doc["figures"][index]["crop_id"]))


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VAR", tmp_path)


# ------------------------------------------------------------ vector pages

def test_vector_page_gives_one_rendered_figure_with_real_ink():
    doc = store_one(SECTION)
    figure = doc["figures"][0]
    assert len(doc["figures"]) == 1 and doc["warnings"] == []
    assert figure == {"crop_id": "p0_rendered", "page": 0, "width": 2480, "height": 3509,
                      "kind": "rendered_page", "dpi": 300.0}
    image = png(doc).convert("L")
    assert image.size == (2480, 3509)
    px = 300 / 72
    assert image.getpixel((round(297.5 * px), round((842 - 250) * px))) < 64  # on the diagonal
    assert image.getpixel((round(100 * px), round((842 - 250) * px))) < 64  # on the box's left edge
    assert image.getpixel((round(200 * px), round((842 - 600) * px))) == 255  # clear paper
    assert image.getpixel((5, 5)) == 255


def test_line_work_inside_a_form_xobject_counts():
    """Exporters often wrap a whole page in one form."""
    pdf = Pdf()
    pdf.page(b"/Fm0 Do\n", xobjects={"Fm0": pdf.form(SECTION)})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_rendered", "rendered_page")]


def test_rotated_page_renders_upright():
    doc = store_one(b"4 w 20 0 m 20 200 l S\n", size=(400, 200), entries="/Rotate 90")
    image = png(doc).convert("L")
    assert (doc["figures"][0]["width"], doc["figures"][0]["height"]) == image.size == (834, 1667)
    assert image.getpixel((400, round(20 * 300 / 72))) < 64  # the page's left edge is now its top


def test_a_0p3_mm_gap_stays_open_at_300_dpi_wherever_it_lands():
    """A 1.5 mm membrane at 1:5: two 0.13 mm lines 0.3 mm apart on paper."""
    for offset in (0, 0.07, 0.15, 0.2):
        y = 400 + offset
        lines = (f"{0.13 * MM:.4f} w 100 {y:.3f} m 1000 {y:.3f} l S\n"
                 f"100 {y + 0.3 * MM:.3f} m 1000 {y + 0.3 * MM:.3f} l S\n").encode()
        image = png(store_one(lines, size=A3)).convert("L")
        x, mid = image.width // 2, round((841.89 - y) * 300 / 72)
        column = [image.getpixel((x, row)) for row in range(mid - 12, mid + 6)]
        ink = [i for i, level in enumerate(column) if level < 128]
        assert ink and max(column[ink[0]:ink[-1] + 1]) >= 200, column


def test_colour_is_kept_and_monochrome_pages_are_stored_grey():
    assert png(store_one(b"1 0 0 RG 5 w " + SECTION)).mode == "RGB"
    assert png(store_one(SECTION)).mode == "L"


# ------------------------------------------------------------ size limits

def test_a_big_sheet_is_held_to_the_pixel_limit_and_says_so():
    doc = store_one(SECTION, size=(3370, 2384))  # A0: 139 MP at 300 dpi
    figure = doc["figures"][0]
    assert figure["width"] * figure["height"] <= store.MAX_RENDER_PIXELS * 1.001
    assert figure["dpi"] == pytest.approx(152, abs=1)
    assert figure["width"] == pytest.approx(3370 * figure["dpi"] / 72, abs=4)  # the dpi on record is the true one


def test_a_png_over_the_byte_limit_is_rendered_smaller_and_says_so(monkeypatch):
    monkeypatch.setattr(store, "MAX_RENDER_BYTES", 12_000)
    doc = store_one(hatch(900))
    figure = doc["figures"][0]
    assert store.figure_path(doc["id"], "p0_rendered").stat().st_size <= 12_000
    assert figure["dpi"] < 300 and figure["width"] == pytest.approx(595 * figure["dpi"] / 72, abs=2)


def test_user_unit_is_in_the_recorded_dpi():
    """PDFium ignores /UserUnit: 2 means each page unit is 2/72 inch."""
    figure = store_one(SECTION, entries="/UserUnit 2")["figures"][0]
    assert figure["dpi"] == 300 and figure["width"] == pytest.approx(2 * 2480, abs=2)


# ------------------------------------------------------------ embedded images

def test_embedded_image_page_is_unchanged():
    pdf = Pdf()
    ref, jpeg = pdf.image((800, 600))
    pdf.page(place("Im0", 0, 0, 595, 446), xobjects={"Im0": ref})
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert doc["figures"] == [{"crop_id": "p0_Im0", "page": 0, "width": 800, "height": 600,
                               "kind": "embedded_image", "dpi": None}]
    assert store.figure_path(doc["id"], "p0_Im0").read_bytes() == jpeg  # lossless: the PDF's own bytes


def test_a_manual_page_keeps_exactly_its_figures():
    """The reference manual's shape, as measured: 5 boxes of page furniture
    (25 segments), 450 characters, 2 icons, 4 figures of 8-14% of the page."""
    pdf = Pdf()
    xobjects, content = {}, b"".join(f"{20 + i} {20 + i} {500 - i} {780 - i} re S\n".encode() for i in range(5))
    for name, size, box in (("Ic0", (132, 160), (30, 790, 18, 21)), ("Ic1", (154, 136), (60, 790, 22, 20)),
                            ("Fg0", (681, 461), (40, 560, 250, 170)), ("Fg1", (825, 624), (300, 560, 250, 170)),
                            ("Fg2", (781, 615), (40, 330, 250, 170)), ("Fg3", (789, 724), (300, 330, 250, 170))):
        xobjects[name] = pdf.image(size)[0]
        content += place(name, *box)
    pdf.page(content + text(6, 75), size=(540, 780), xobjects=xobjects)
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert kinds(doc) == [(f"p0_Fg{i}", "embedded_image") for i in range(4)]


def test_small_figures_on_a_page_of_text_all_stay():
    pdf = Pdf()
    xobjects = {f"Fg{i}": pdf.image((300, 300))[0] for i in range(3)}
    content = b"".join(place(name, 40 + 150 * i, 100, 30, 30) for i, name in enumerate(xobjects))  # 0.1% each
    pdf.page(content + text(50), xobjects=xobjects)
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [(f"p0_Fg{i}", "embedded_image") for i in range(3)]


def test_an_image_stored_in_a_form_is_found_as_before():
    pdf = Pdf()
    ref, jpeg = pdf.image((400, 400))
    pdf.page(b"q 500 0 0 500 40 40 cm /Fm0 Do Q\n", xobjects={"Fm0": pdf.form(place("Im0", 0, 0, 1, 1), {"Im0": ref})})
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert kinds(doc) == [("p0_Im0", "embedded_image")]
    assert store.figure_path(doc["id"], "p0_Im0").read_bytes() == jpeg


INLINE_IMAGE = b"BI /W 256 /H 256 /BPC 1 /CS /G ID " + b"\xaa" * (32 * 256) + b"\nEI\n"


def test_an_inline_image_gives_a_figure_that_opens():
    """pypdf names it "~0~", which the crop id pattern refuses: it used to be
    listed but could never be opened, so it must not stand as a page's figure."""
    doc = store_one(b"q 400 0 0 400 40 40 cm\n" + INLINE_IMAGE + b"Q\n")
    assert [f["kind"] for f in doc["figures"]] == ["embedded_image"]
    assert store.figure_path(doc["id"], doc["figures"][0]["crop_id"]) is not None


def test_an_inline_logo_is_a_logo():
    doc = store_one(SECTION + b"q 40 0 0 40 500 40 cm\n" + INLINE_IMAGE + b"Q\n")
    assert kinds(doc) == [("p0_rendered", "rendered_page")]


# ------------------------------------------------------------ the rule between them

@pytest.mark.parametrize("logo", [(64, 64), (600, 600)], ids=["icon", "high-resolution"])
def test_a_title_block_logo_never_hides_the_drawing(logo):
    pdf = Pdf()
    pdf.page(SECTION + place("Lg", 500, 40, 40, 40), xobjects={"Lg": pdf.image(logo)[0]})
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert kinds(doc) == [("p0_rendered", "rendered_page")]
    assert png(doc).mode == "RGB"  # the red logo is in the rendering; nothing is lost


def test_a_logo_measured_through_its_form_is_still_a_logo():
    """PDFium reports form contents in the form's space, where it looks page-sized."""
    pdf = Pdf()
    logo = pdf.form(place("Lg", 0, 0, 600, 600), {"Lg": pdf.image((600, 600))[0]})
    pdf.page(SECTION + b"q 0.05 0 0 0.05 500 40 cm /Fm0 Do Q\n", xobjects={"Fm0": logo})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_rendered", "rendered_page")]


def test_a_real_figure_measured_through_its_form_stays():
    """The reverse: small in the form's space, a third of the page once placed."""
    pdf = Pdf()
    figure = pdf.form(place("Fg", 0, 0, 20, 20), {"Fg": pdf.image((600, 600))[0]})
    pdf.page(SECTION + b"q 20 0 0 20 40 400 cm /Fm0 Do Q\n", xobjects={"Fm0": figure})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_Fg", "embedded_image")]


def test_a_drawing_beside_a_real_image_gives_both_and_drops_the_logo():
    pdf = Pdf()
    xobjects = {"Ph": pdf.image((800, 600))[0], "Lg": pdf.image((600, 600))[0]}
    pdf.page(hatch(300) + place("Ph", 40, 40, 300, 225) + place("Lg", 500, 40, 40, 40), xobjects=xobjects)
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_Ph", "embedded_image"), ("p0_rendered", "rendered_page")]


def test_a_scan_in_a_vector_frame_stays_a_single_extraction():
    pdf = Pdf()
    frame = b"".join(f"{20 + i} {20 + i} 555 800 re S\n".encode() for i in range(8))  # 40 segments
    pdf.page(place("Im0", 30, 120, 535, 690) + frame, xobjects={"Im0": pdf.image((1600, 2000))[0]})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_Im0", "embedded_image")]


def test_line_work_over_a_raster_is_rendered_as_well():
    """Hybrid exports paint a raster, then the real lines over it."""
    pdf = Pdf()
    pdf.page(place("Im0", 0, 0, 595, 842) + hatch(300), xobjects={"Im0": pdf.image((1600, 2000))[0]})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_Im0", "embedded_image"), ("p0_rendered", "rendered_page")]


def test_a_scan_cut_into_strips_is_rendered():
    """No strip passes the 200 px filter, so nothing could be extracted."""
    pdf = Pdf()
    xobjects = {f"S{i}": pdf.image((1000, 100), (120, 120, 120))[0] for i in range(10)}
    content = b"".join(place(name, 0, 84 * i, 595, 84) for i, name in enumerate(xobjects))
    pdf.page(content, xobjects=xobjects)
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_rendered", "rendered_page")]


@pytest.mark.parametrize("line_work", [hatch(300), SECTION], ids=["big", "simple"])
def test_an_image_listed_but_never_drawn_cannot_hide_the_drawing(line_work):
    """Exporters often share one /Resources across pages."""
    pdf = Pdf()
    pdf.page(line_work, xobjects={"Un": pdf.image((800, 600))[0]})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_rendered", "rendered_page")]


def test_a_big_vector_page_skips_pypdfs_slow_walk(monkeypatch):
    """Seconds per CAD sheet in pure Python, and nothing there can become a figure."""
    monkeypatch.setattr(store, "_embedded_images", lambda i, page: pytest.fail("pypdf walked this page"))
    assert kinds(store_one(hatch(300))) == [("p0_rendered", "rendered_page")]


def test_a_simple_drawing_with_a_notes_block_is_still_a_drawing():
    """12 layers (60 segments) under a title block and 600 characters of notes."""
    layers = b"".join(f"100 {200 + 20 * i} 400 20 re S\n".encode() for i in range(12))
    assert kinds(store_one(layers + text(7, 80))) == [("p0_rendered", "rendered_page")]


@pytest.mark.parametrize("content", [b"", b"1 g 0 0 595 842 re f\n", b"0.8 0.8 1 rg 0 0 595 842 re f\n"],
                         ids=["empty", "white-rectangle", "full-page-tint"])
def test_a_blank_page_gives_nothing(content):
    assert store_one(content)["figures"] == []


def test_a_blank_rendering_gives_back_the_images_it_would_have_shown():
    """Line work that turns out invisible must not cost today's figures."""
    pdf = Pdf()
    pdf.page(b"1 g 0 0 595 842 re f\n" + place("Lg", 500, 40, 40, 40), xobjects={"Lg": pdf.image((600, 600), "white")[0]})
    assert kinds(store.store_pdf("own.pdf", pdf.bytes())) == [("p0_Lg", "embedded_image")]


def test_a_page_of_text_gives_nothing():
    rules = b"40 810 m 555 810 l S\n40 40 m 555 40 l S\n"
    assert store_one(text(60) + rules)["figures"] == []


def test_a_mixed_document_keeps_each_page_to_its_own_kind():
    pdf = Pdf()
    ref, _ = pdf.image((400, 300))
    pdf.page(place("Im0", 40, 400, 400, 300), xobjects={"Im0": ref}).page(SECTION).page().page(text(60))
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert doc["pages"] == 4
    assert kinds(doc) == [("p0_Im0", "embedded_image"), ("p1_rendered", "rendered_page")]
    assert [f["page"] for f in doc["figures"]] == [0, 1]


# ------------------------------------------------------------ failure

def test_a_page_too_heavy_for_pypdf_still_uploads():
    """pypdf gives up on very long content streams. Before rendered pages, the
    unguarded text excerpt turned that into a failed upload."""
    with apply_configuration(stream_parsing_work_maximum_count=50):
        doc = store_one(hatch(300) + b"BT /F1 8 Tf 40 40 Td (DETAIL 7) Tj ET\n")
    assert kinds(doc) == [("p0_rendered", "rendered_page")]
    page = json.loads((store.doc_dir(doc["id"]) / "meta.json").read_text())["pages"][0]
    assert "DETAIL 7" in page["text_excerpt"]
    assert page["segments"] == 600 and page["chars"] > 0 and page["covered"] == 0  # why it went this way


def test_a_render_failure_is_reported_and_keeps_the_embedded_figures(monkeypatch):
    def fail(page, user_unit):
        raise pypdfium2.PdfiumError("no")
    monkeypatch.setattr(store, "_render_page", fail)
    pdf = Pdf()
    pdf.page(place("Im0", 40, 40, 300, 225) + hatch(300), xobjects={"Im0": pdf.image((800, 600))[0]})
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert kinds(doc) == [("p0_Im0", "embedded_image")]
    assert doc["warnings"] == ["page 1 could not be rendered: no"]
    assert store.get_document(doc["id"])["warnings"] == doc["warnings"]


def test_a_file_pdfium_cannot_open_keeps_its_embedded_figures(monkeypatch):
    def fail(data):
        raise pypdfium2.PdfiumError("no")
    monkeypatch.setattr(pypdfium2, "PdfDocument", fail)
    pdf = Pdf()
    pdf.page(place("Im0", 40, 40, 300, 225), xobjects={"Im0": pdf.image((800, 600))[0]})
    doc = store.store_pdf("own.pdf", pdf.bytes())
    assert kinds(doc) == [("p0_Im0", "embedded_image")]
    assert "PDFium could not open" in doc["warnings"][0]


def test_uploads_may_overlap():
    """Uploads run off the event loop now, and PDFium is not thread-safe."""
    data = Pdf().page(hatch(300)).bytes()
    with ThreadPoolExecutor(4) as pool:
        docs = list(pool.map(lambda _: store.store_pdf("own.pdf", data), range(8)))
    assert len({store.figure_path(doc["id"], "p0_rendered").read_bytes() for doc in docs}) == 1


def test_an_upload_from_before_this_change_reads_as_extracted():
    con = store.db()
    con.execute("INSERT INTO documents(id, filename, sha256, pages, meta) VALUES(?,?,?,?,?)",
                ("0123456789ab", "old.pdf", "x" * 64, 1,
                 json.dumps({"figures": [{"crop_id": "p0_Im0", "page": 0, "width": 800, "height": 600}]})))
    con.commit()
    con.close()
    old = store.get_document("0123456789ab")
    assert old["figures"][0]["kind"] == "embedded_image" and old["figures"][0]["dpi"] is None
    assert old["warnings"] == []


# ------------------------------------------------------------ downstream

def test_a_rendered_figure_takes_the_same_path_as_an_extracted_one(client, source_repo):
    """Upload route, figure route, generate, the real worker and gateway, the detail page."""
    uploaded = client.post("/api/documents", files={"file": ("own.pdf", Pdf().page(SECTION).bytes(), "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    doc = uploaded.json()
    figure = doc["figures"][0]
    assert figure["kind"] == "rendered_page" and figure["dpi"] == 300 and figure["detail_id"].endswith("-p0_rendered")
    assert client.get(f"/api/documents/{doc['id']}").json()["figures"] == [figure]

    image = client.get(f"/api/documents/{doc['id']}/figures/p0_rendered")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"

    job = client.post("/api/generate", json={"doc_id": doc["id"], "crop_id": "p0_rendered"}).json()
    payload = source_repo.get(d.jobs, job["id"])["payload"]
    assert payload["detail_id"] == figure["detail_id"]
    stored = source_repo.get(d.figures, payload["figure_id"])
    assert (stored["crop_key"], stored["page"], stored["width"], stored["height"]) == ("p0_rendered", 0, 2480, 3509)
    assert source_repo.artifacts.read(stored["artifact_id"]) == image.content
    assert source_repo.get(d.artifacts, stored["artifact_id"])["media_type"] == "image/png"

    sent = []
    scripted = ScriptedModel()

    def model(wire):
        sent.extend(part["image_url"] for message in wire["input"] if message.get("role") == "user"
                    for part in message["content"] if part.get("type") == "input_image")
        return scripted(wire)
    Worker(source_repo, responder=model, renderer=FakeRenderer(source_repo)).run_once(only_ids=[job["id"]])
    finished = source_repo.get(d.jobs, job["id"])
    assert finished["state"] == "succeeded", finished

    # What reaches the model is the stored figure, byte for byte, on every call.
    assert sent and sent[0] == "data:image/png;base64," + base64.b64encode(image.content).decode()
    assert sent.count(sent[0]) >= 3

    version = source_repo.get(d.versions, source_repo.run(job["id"])["working_version_id"])
    assert version["figure_id"] == payload["figure_id"] and version["detail_id"] == figure["detail_id"]
    source = version["spec"]["source"]
    assert (source["crop_id"], source["page_index"], source["page_width_pt"]) == ("p0_rendered", 0, 595)
    assert any(c["id"] == "source-artifacts" and c["status"] == "PASS"
               for c in source_repo.list(d.verifications, d.verifications.c.version_id == version["id"])[-1]["checks"])
    drawing = client.get(f"/api/specs/{version['detail_id']}/drawing")
    assert drawing.status_code == 200 and Image.open(io.BytesIO(drawing.content)).size == (2480, 3509)


def test_a_rendered_figure_registers_once_per_upload(source_repo):
    doc = store_one(SECTION)
    first = register_figure(source_repo, doc["id"], "p0_rendered")
    assert register_figure(source_repo, doc["id"], "p0_rendered") == first
    assert source_repo.get(d.figures, first)["crop_key"] == "p0_rendered"
