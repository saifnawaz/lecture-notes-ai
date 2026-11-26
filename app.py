"""FastAPI web server.

Run with:  uvicorn app:app --reload
"""
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import srs
from study_ai import GenerationError, generate, generate_flashcards, make_client, shuffle_options

MAX_UPLOAD_MB = 20
UPLOAD_DIR = Path("uploads")  # saved PDFs, named by their SHA-256 hash
GENERIC_ERROR = "Something went wrong while talking to the AI. Please try again."
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    db.init_db()  # runs once when the server starts
    yield


app = FastAPI(title="Lecture Notes AI", lifespan=lifespan)


def remove_pdf_if_unused(pdf_path):
    """Delete a saved PDF unless another note still uses it (same file = same hash)."""
    if pdf_path and db.count_notes_with_pdf(pdf_path) == 0:
        Path(pdf_path).unlink(missing_ok=True)


@app.post("/generate")
def generate_notes(
    file: UploadFile = File(...),
    subject: str = Form(""),
    force: bool = Form(False),  # True = "yes, generate again" after a duplicate warning
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    content = file.file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"PDF is larger than {MAX_UPLOAD_MB} MB.")

    # Same bytes = same hash, so this finds the same PDF even if it was renamed.
    pdf_hash = hashlib.sha256(content).hexdigest()
    subject = db.canonical_subject(subject)
    if subject and not force:
        existing = db.find_note_by_hash(subject, pdf_hash)
        if existing:
            return JSONResponse(status_code=409, content={
                "detail": f'This PDF is already in {subject} as "{existing["topic"]}".',
                "duplicate_of": existing["id"],
            })

    # Keep the PDF (named by its hash) so we can make more questions from it later.
    UPLOAD_DIR.mkdir(exist_ok=True)
    pdf_path = (UPLOAD_DIR / f"{pdf_hash}.pdf").as_posix()
    Path(pdf_path).write_bytes(content)
    try:
        data = generate(make_client(), pdf_path)
    except Exception as e:
        remove_pdf_if_unused(pdf_path)
        if isinstance(e, GenerationError):
            raise HTTPException(502, str(e))
        # Anything unexpected (network, Gemini SDK, ...) still gets a readable message.
        log.exception("generate failed")
        raise HTTPException(502, GENERIC_ERROR)

    if subject:
        data["subject"] = subject  # user's choice wins over Gemini's guess
    data.update(filename=file.filename, pdf_path=pdf_path, pdf_hash=pdf_hash)

    data["mcqs"] = shuffle_options(data["mcqs"])
    note_id = db.save_note(data)
    return db.get_note(note_id)


# ---------- Subjects ----------

@app.get("/subjects")
def list_subjects():
    """Subject cards for the home page: name, PDFs, cards due today, accuracy."""
    return db.subject_summaries()


class NewSubject(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.post("/subjects")
def create_subject(body: NewSubject):
    """Create a subject. If one with the same name (any case) exists, return that one."""
    if not body.name.strip():
        raise HTTPException(400, "Subject name can't be empty.")
    return db.get_subject(db.create_subject(body.name))


@app.get("/subjects/{subject_id}")
def get_subject(subject_id: int):
    """Subject overview: the summary plus the 3 weakest topics."""
    subject = db.get_subject(subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found.")
    subject["weakest"] = db.progress(subject["name"])[:3]
    return subject


@app.get("/notes")
def list_notes(subject: str | None = None):
    return db.list_notes(subject)


@app.get("/notes/{note_id}")
def get_note(note_id: int):
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    note["accuracy"] = db.note_accuracy(note_id)  # None if quiz never tried
    note["card_count"] = db.count_cards(note_id)
    return note


@app.delete("/notes/{note_id}")
def delete_note(note_id: int):
    """Delete a note together with its quiz attempts, flashcards and saved PDF."""
    deleted = db.delete_note(note_id)
    if deleted is None:
        raise HTTPException(404, "Note not found.")
    remove_pdf_if_unused(deleted["pdf_path"])
    return {"deleted": note_id}


@app.post("/notes/{note_id}/flashcards")
def make_flashcards(note_id: int):
    """Create flashcards for an older note from its saved text (no PDF needed)."""
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    if db.count_cards(note_id) > 0:
        raise HTTPException(400, "This note already has flashcards.")
    try:
        cards = generate_flashcards(make_client(), note["subject"], note["topic"], note["notes"])
    except GenerationError as e:
        raise HTTPException(502, str(e))
    except Exception:
        log.exception("generate_flashcards failed")
        raise HTTPException(502, GENERIC_ERROR)
    db.save_cards(note_id, note["subject"], cards)
    return {"card_count": db.count_cards(note_id)}


class Attempt(BaseModel):
    """What the browser sends after answering one question."""
    note_id: int
    question_index: int
    chosen: int


@app.post("/attempts")
def record_attempt(attempt: Attempt):
    note = db.get_note(attempt.note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    if not 0 <= attempt.question_index < len(note["mcqs"]):
        raise HTTPException(400, "Invalid question_index.")
    question = note["mcqs"][attempt.question_index]
    if not 0 <= attempt.chosen < len(question["options"]):
        raise HTTPException(400, "Invalid chosen option.")

    # The server decides subtopic, subject and correctness from the saved note,
    # so the browser can't send wrong values.
    correct = attempt.chosen == question["answer"]
    subtopic = question.get("subtopic") or note["topic"]
    attempt_id = db.save_attempt(
        attempt.note_id, attempt.question_index, subtopic,
        note["subject"], attempt.chosen, correct,
    )
    return {"id": attempt_id, "correct": correct, "subtopic": subtopic, "subject": note["subject"]}


@app.get("/progress")
def get_progress(subject: str | None = None):
    return db.progress(subject)


# ---------- Flashcards ----------

@app.get("/cards/due")
def get_due_cards(subject: str | None = None):
    return db.due_cards(subject)


class Review(BaseModel):
    """One flashcard review: Again=1, Hard=3, Good=4, Easy=5."""
    quality: int = Field(ge=0, le=5)


@app.post("/cards/{card_id}/review")
def review_card(card_id: int, review: Review):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(404, "Card not found.")
    result = srs.sm2(review.quality, card["ease"], card["interval_days"], card["repetitions"])
    db.update_card(
        card_id, result["ease"], result["interval_days"], result["repetitions"],
        result["due_date"].isoformat(),
    )
    return db.get_card(card_id)


# Serve the frontend (static/index.html at "/"). Must come after the API routes.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
