"""PDF text extraction, and choosing text or the PDF itself for the first generate."""
import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

import app as app_module
import db
import study_ai


def text_pdf(lines, image=False):
    """A one-page PDF with real text on it (built by hand, so no extra library is needed).

    image=True also draws a tiny embedded image, like a diagram on a slide.
    """
    stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) '" for line in lines) + " ET"
    xobject = ""
    if image:
        stream += " q 50 0 0 50 72 72 cm /Im1 Do Q"
        xobject = " /XObject << /Im1 6 0 R >>"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        f"/Resources << /Font << /F1 5 0 R >>{xobject} >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if image:  # a 1x1 grey pixel
        objects.append("<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                       "/BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def blank_pdf(pages=2):
    """Pages without any text, like a scanned PDF."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(612, 792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


LINES = ["A set is an unordered collection of distinct objects.",
         "The union of A and B contains every element of A or B.",
         "The empty set is a subset of every set."]
# Enough text per page (over 500 characters) for the first generate to send text.
FULL_PAGE = LINES * 4


# ---------- extract_text ----------

def test_extracts_and_compacts_text():
    text = study_ai.extract_text(text_pdf(LINES))
    assert "unordered collection" in text and "empty set" in text
    assert "  " not in text and "\n\n" not in text


def test_scanned_pdf_gives_no_text():
    assert study_ai.extract_text(blank_pdf()) == ""


def test_too_little_text_counts_as_scanned():
    assert study_ai.extract_text(text_pdf(["Page 1"])) == ""


def test_broken_pdf_gives_no_text():
    assert study_ai.extract_text(b"%PDF-1.4 not really a pdf") == ""


def test_long_text_is_cut(monkeypatch):
    monkeypatch.setattr(study_ai, "MAX_TEXT_CHARS", 50)
    assert len(study_ai.extract_text(text_pdf(LINES))) == 50


# ---------- what gets sent to Gemini ----------

class Recorder:
    """Fake ask_with_fallback that remembers what would be sent."""

    def __init__(self, answer):
        self.answer = answer
        self.contents = None

    def __call__(self, client, contents, validate, *args, **kwargs):
        self.contents = contents
        return self.answer


class FakeClient:
    def __init__(self):
        self.files = self
        self.uploads = 0

    def upload(self, file, config=None):
        self.uploads += 1
        return "uploaded-pdf"


GOOD = {"topic": "Sets", "notes": ["n"], "mcqs": [], "flashcards": []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(app_module, "make_client", lambda: fake)
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    with TestClient(app_module.app) as c:
        c.fake = fake
        yield c


def upload(client, content):
    return client.post("/generate", data={"subject": "Math"},
                       files={"file": ("lecture.pdf", content, "application/pdf")})


def test_upload_sends_text_not_pdf_and_stores_it(client, monkeypatch):
    recorder = Recorder(GOOD)
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    r = upload(client, text_pdf(FULL_PAGE))
    assert r.status_code == 200
    assert "source_text" not in r.json()  # not sent to the page
    assert client.fake.uploads == 0
    assert len(recorder.contents) == 1 and "unordered collection" in recorder.contents[0]
    assert "unordered collection" in db.get_note(r.json()["id"])["source_text"]


def test_scanned_upload_sends_the_pdf(client, monkeypatch):
    recorder = Recorder(GOOD)
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    r = upload(client, blank_pdf())
    assert client.fake.uploads == 1 and recorder.contents[0] == "uploaded-pdf"
    assert db.get_note(r.json()["id"])["source_text"] == ""


def test_more_mcqs_use_stored_text(client, monkeypatch):
    monkeypatch.setattr(study_ai, "ask_with_fallback", Recorder(GOOD))
    nid = upload(client, text_pdf(FULL_PAGE)).json()["id"]
    recorder = Recorder({"mcqs": []})
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    client.post(f"/notes/{nid}/mcqs")
    assert client.fake.uploads == 0
    assert "unordered collection" in recorder.contents[-1]


def test_old_note_gets_text_from_saved_pdf_once(client, monkeypatch, tmp_path):
    pdf = tmp_path / "old.pdf"
    pdf.write_bytes(text_pdf(LINES))
    nid = db.save_note({"subject": "Math", "topic": "Sets", "notes": ["n"], "mcqs": [],
                        "pdf_path": str(pdf)})  # saved before text extraction existed
    assert db.get_note(nid)["source_text"] is None
    recorder = Recorder({"flashcards": []})
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    client.post(f"/notes/{nid}/flashcards")
    assert "unordered collection" in recorder.contents[-1] and client.fake.uploads == 0
    assert "unordered collection" in db.get_note(nid)["source_text"]  # stored for next time


def test_page_headers_and_numbers_are_dropped():
    pages = ["Course X\nPage 1 of 3\nalpha", "Course X\n2\nbeta", "Course X\n3 / 3\ngamma"]
    assert study_ai.drop_headers(pages) == ["alpha", "beta", "gamma"]


def test_short_pdfs_keep_repeated_lines():
    assert study_ai.drop_headers(["Course X\nalpha", "Course X\nbeta"]) == ["Course X\nalpha", "Course X\nbeta"]


# ---------- first generate: text or the PDF itself ----------

def test_full_text_pages_without_images_send_text(capsys):
    info = study_ai.read_pdf(text_pdf(FULL_PAGE))
    assert info["chars_per_page"] >= 500 and not info["has_images"]
    assert "unordered collection" in study_ai.text_for_first_generate(info)
    assert "sending extracted text" in capsys.readouterr().out


def test_little_text_per_page_sends_the_pdf(capsys):
    info = study_ai.read_pdf(text_pdf(LINES))  # about 150 characters on the page
    assert 100 <= info["chars_per_page"] < 500
    assert info["text"]  # still stored for later features
    assert study_ai.text_for_first_generate(info) == ""
    assert "sending the PDF, only" in capsys.readouterr().out


def test_embedded_image_sends_the_pdf(capsys):
    info = study_ai.read_pdf(text_pdf(FULL_PAGE, image=True))
    assert info["has_images"] and info["chars_per_page"] >= 500
    assert study_ai.text_for_first_generate(info) == ""
    assert "embedded images" in capsys.readouterr().out


def test_unreadable_pdf_sends_the_pdf():
    assert study_ai.text_for_first_generate(study_ai.read_pdf(b"not a pdf")) == ""


def test_upload_with_image_sends_pdf_but_stores_text(client, monkeypatch):
    recorder = Recorder(GOOD)
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    r = upload(client, text_pdf(FULL_PAGE, image=True))
    assert client.fake.uploads == 1 and recorder.contents[0] == "uploaded-pdf"
    assert "unordered collection" in db.get_note(r.json()["id"])["source_text"]  # later calls use text


def test_upload_with_little_text_sends_pdf_but_stores_text(client, monkeypatch):
    recorder = Recorder(GOOD)
    monkeypatch.setattr(study_ai, "ask_with_fallback", recorder)
    r = upload(client, text_pdf(LINES))
    assert client.fake.uploads == 1
    assert "unordered collection" in db.get_note(r.json()["id"])["source_text"]
