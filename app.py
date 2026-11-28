"""FastAPI web server.

Run with:  uvicorn app:app --reload
"""
import contextvars
import hashlib
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import srs
import study_ai
from context import least_covered, related_context
from quiz import pick_questions, plan_practice
from study_ai import (
    GenerationError, explain_topic, extract_text, generate, generate_flashcards, generate_mcqs,
    generate_practice, make_client, read_pdf, shuffle_options, text_for_first_generate, why_wrong,
)

MAX_UPLOAD_MB = 4  # Vercel accepts request bodies up to 4.5 MB
UPLOAD_DIR = Path("uploads")  # saved PDFs (local only), named by their SHA-256 hash
MORE_TEXT_CHARS = 9000  # "Generate more" sends at most this much PDF text (about 3k tokens)
GENERIC_ERROR = "Something went wrong while talking to the AI. Please try again."

# Daily limits on NEW AI answers (saved/cached answers are free and unlimited). The admin has none.
GUEST_DAILY_AI_CALLS = 10
TOTAL_DAILY_AI_CALLS = 200
GUEST_LIMIT_MESSAGE = ("You've used today's 10 new AI answers in this demo. "
                       "Saved explanations and answers still work. Try again tomorrow.")
DEMO_LIMIT_MESSAGE = ("The demo has used all of today's new AI answers. "
                      "Saved explanations and answers still work. Try again tomorrow.")
log = logging.getLogger(__name__)
study_ai.usage_hook = db.log_usage  # every Gemini call's token counts go to the ai_usage table
study_ai.cache_get, study_ai.cache_put = db.cache_get, db.cache_put  # identical requests: no new call


class QuotaExceeded(Exception):
    pass


# Who the current AI action is for: {"user": user_id, "counted": bool}, set by call_ai().
ai_caller = contextvars.ContextVar("ai_caller", default=None)


def spend_ai_call(feature):
    """study_ai.before_call: runs right before a real Gemini request.

    One user action counts once, however many retries or uploads it needs.
    """
    caller = ai_caller.get()
    if caller is None or caller["counted"] or caller["user"] in (None, db.OWNER):
        return
    if db.ai_calls_today() >= TOTAL_DAILY_AI_CALLS:
        raise QuotaExceeded(DEMO_LIMIT_MESSAGE)
    if db.ai_calls_today(caller["user"]) >= GUEST_DAILY_AI_CALLS:
        raise QuotaExceeded(GUEST_LIMIT_MESSAGE)
    db.count_ai_call(caller["user"])
    caller["counted"] = True


study_ai.before_call = spend_ai_call


@asynccontextmanager
async def lifespan(app):
    db.init_db()  # runs once when the server starts
    yield


app = FastAPI(title="Lapse", lifespan=lifespan)  # Vercel's FastAPI preset serves this "app"

# On Vercel (which sets VERCEL=1) the disk is read-only, so Postgres is required. Tables are
# created here, once per cold start, in case the platform doesn't run the lifespan startup.
if os.getenv("VERCEL"):
    if not db.is_postgres():
        raise RuntimeError("DATABASE_URL is not set. On Vercel the disk is read-only, so Postgres is required.")
    db.init_db()


@app.get("/health")
def health():
    """Deploy check: which database is in use, and whether it answers."""
    kind = "postgres" if db.is_postgres() else "sqlite"
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
    except Exception:
        log.exception("health check: database unreachable")
        return JSONResponse(status_code=503, content={"ok": False, "db": kind})
    return {"ok": True, "db": kind}


# ---------- Who is asking: a guest (cookie) or the admin ----------

COOKIE = "lapse_uid"
COOKIE_DAYS = 365


def valid_uuid(value):
    try:
        return str(uuid.UUID(value)) == value
    except (TypeError, ValueError):
        return False


@app.middleware("http")
async def guest_cookie(request: Request, call_next):
    """First visit: give the browser a random guest id in an httpOnly cookie.

    All per-user data (quiz answers, card schedules, AI limits) is stored under this id,
    so a real login later can map an account to a user_id and reuse everything.
    """
    guest_id = request.cookies.get(COOKIE)
    is_new = not valid_uuid(guest_id)
    if is_new:
        guest_id = str(uuid.uuid4())
    request.state.guest_id = guest_id
    response = await call_next(request)
    if is_new:
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, guest_id, max_age=COOKIE_DAYS * 86400,
                            httponly=True, samesite="lax", secure=https)
    return response


def is_admin(request: Request):
    """The admin sends ADMIN_PASSCODE in the X-Admin-Passcode header.

    Without ADMIN_PASSCODE: locally (SQLite) you are the admin; in production nobody is.
    """
    passcode = os.getenv("ADMIN_PASSCODE")
    if not passcode:
        return not db.is_postgres()
    given = request.headers.get("x-admin-passcode", "")
    return hmac.compare_digest(given.encode(), passcode.encode())


def require_admin(request: Request):
    """Dependency for upload, generate more, delete, create/rename subject and Regenerate."""
    if not is_admin(request):
        if request.headers.get("x-admin-passcode"):
            time.sleep(1)  # a wrong passcode: slow down guessing
        raise HTTPException(403, "This needs the admin passcode.")


@app.get("/me")
def me(request: Request):
    """What the page should show: admin controls, or the guest view with the demo banner."""
    return {"admin": is_admin(request), "passcode_set": bool(os.getenv("ADMIN_PASSCODE"))}


@app.post("/admin/check", dependencies=[Depends(require_admin)])
def admin_check():
    """The footer's Admin form calls this to test a passcode before saving it."""
    return {"admin": True}


def current_user(request: Request):
    """user_id for per-user data: the admin's own progress is OWNER, everyone else is their guest id."""
    return db.OWNER if is_admin(request) else request.state.guest_id


def saves_pdfs():
    """Locally the PDF is kept in uploads/. In production (Postgres, read-only disk on Vercel)
    nothing is written to files: only the PDF's extracted text is stored, in the database."""
    return not db.is_postgres()


def remove_pdf_if_unused(pdf_path):
    """Delete a saved PDF unless another note still uses it (same file = same hash)."""
    if pdf_path and db.count_notes_with_pdf(pdf_path) == 0:
        Path(pdf_path).unlink(missing_ok=True)


@app.post("/generate", dependencies=[Depends(require_admin)])
def generate_notes(
    file: UploadFile = File(...),
    subject: str = Form(""),
    force: bool = Form(False),  # True = "yes, generate again" after a duplicate warning
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    # Read the upload ONCE. These same bytes are hashed, saved and sent to Gemini.
    content = file.file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty")
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        size = -(-len(content) * 10 // (1024 * 1024)) / 10  # rounded up: 4.01 MB shows as 4.1
        raise HTTPException(413, f"This PDF is {size:.1f} MB. The limit is {MAX_UPLOAD_MB} MB.")

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

    # Locally, keep the PDF (named by its hash) so we can make more questions from it later.
    pdf_path = None
    if saves_pdfs():
        UPLOAD_DIR.mkdir(exist_ok=True)
        pdf_path = (UPLOAD_DIR / f"{pdf_hash}.pdf").as_posix()
        Path(pdf_path).write_bytes(content)
        saved_size = Path(pdf_path).stat().st_size
        if saved_size != len(content):
            remove_pdf_if_unused(pdf_path)
            log.error("saved %s is %d bytes, upload was %d", pdf_path, saved_size, len(content))
            raise HTTPException(500, "The PDF couldn't be saved. Please try again.")
    info = read_pdf(content)  # once; later calls use info["text"] instead of the PDF
    text = info["text"]
    try:
        # The bytes are sent from memory; the name is only Gemini's display name.
        data = generate(make_client(), pdf_path or Path(file.filename).name, content,
                        text_for_first_generate(info))
    except Exception as e:
        remove_pdf_if_unused(pdf_path)
        if isinstance(e, GenerationError):
            raise HTTPException(502, str(e))
        # Anything unexpected (network, Gemini SDK, ...) still gets a readable message.
        log.exception("generate failed")
        raise HTTPException(502, GENERIC_ERROR)

    if subject:
        data["subject"] = subject  # user's choice wins over Gemini's guess
    data.update(filename=file.filename, pdf_path=pdf_path, pdf_hash=pdf_hash, source_text=text)

    data["mcqs"] = shuffle_options(data["mcqs"])
    note_id = db.save_note(data)
    return public_note(db.get_note(note_id))


def public_note(note):
    """A note for the page: without the (long) extracted PDF text."""
    note.pop("source_text", None)
    return note


def source_text(note):
    """The note's PDF text. Older notes get it extracted from the saved PDF now, once."""
    if note["source_text"] is None and note["pdf_path"] and Path(note["pdf_path"]).exists():
        note["source_text"] = extract_text(Path(note["pdf_path"]).read_bytes())
        db.set_source_text(note["id"], note["source_text"])
    return note["source_text"] or ""


# ---------- Subjects ----------

@app.get("/subjects")
def list_subjects(uid: str = Depends(current_user)):
    """Subject cards for the home page: name, PDFs, this user's cards due today and accuracy."""
    return db.subject_summaries(user_id=uid)


@app.get("/today")
def today_summary(uid: str = Depends(current_user)):
    """Home page "Today" box: cards due, weakest topic overall, score on the last quiz day."""
    subjects = db.subject_summaries(user_id=uid)
    ids = {s["name"].lower(): s["id"] for s in subjects}
    weakest = next(iter(db.progress(user_id=uid)), None)
    if weakest:
        weakest["subject_id"] = ids.get((weakest["subject"] or "").lower())
    return {
        "due": sum(s["due_today"] for s in subjects),
        "weakest": weakest,
        "last_score": db.last_score(uid),
    }


class NewSubject(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.post("/subjects", dependencies=[Depends(require_admin)])
def create_subject(body: NewSubject):
    """Create a subject. If one with the same name (any case) exists, return that one."""
    if not body.name.strip():
        raise HTTPException(400, "Subject name can't be empty.")
    return db.get_subject(db.create_subject(body.name))


@app.patch("/subjects/{subject_id}", dependencies=[Depends(require_admin)])
def rename_subject(subject_id: int, body: NewSubject, uid: str = Depends(current_user)):
    """Rename a subject; renaming to an existing subject's name merges the two."""
    subject = db.get_subject(subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found.")
    if not body.name.strip():
        raise HTTPException(400, "Subject name can't be empty.")
    db.rename_subject(subject["name"], body.name)
    return db.get_subject(db.create_subject(body.name), uid)


@app.get("/subjects/{subject_id}")
def get_subject(subject_id: int, uid: str = Depends(current_user)):
    """Subject overview: the summary plus this user's 3 weakest topics."""
    subject = db.get_subject(subject_id, uid)
    if subject is None:
        raise HTTPException(404, "Subject not found.")
    subject["weakest"] = db.progress(subject["name"], user_id=uid)[:3]
    return subject


@app.get("/subjects/{subject_id}/quiz")
def subject_quiz(subject_id: int, n: int = Query(10, ge=1, le=50), subtopic: str | None = None,
                 uid: str = Depends(current_user)):
    """Mixed MCQs from all PDFs in a subject; weak topics come up more often.

    With ?subtopic=..., only that topic (used by the "Practice" buttons).
    """
    subject = db.get_subject(subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found.")
    questions = db.subject_questions(subject["name"])
    if subtopic:
        questions = [q for q in questions if q["subtopic"].casefold() == subtopic.casefold()]
    mastery = {
        r["subtopic"].casefold(): db.mastery_score(r["correct"], r["attempts"])
        for r in db.progress(subject["name"], user_id=uid)
    }
    return pick_questions(questions, mastery, n)


# ---------- AI tutor ----------

def subject_or_404(subject_id):
    subject = db.get_subject(subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found.")
    return subject


def topic_context(subject_name, topics):
    """Only the note bullets and PDF text pieces about these topics (not the whole subject).

    topics: [(subtopic, [its question stems])]. Returns (bullets, chunks).
    """
    notes = db.subject_notes(subject_name)
    if not any(n["notes"] for n in notes):
        raise HTTPException(400, "This subject has no notes yet. Upload a PDF first.")
    return related_context(notes, topics)


def stems_about(questions, subtopic):
    return [q["question"] for q in questions if q["subtopic"].casefold() == subtopic.casefold()]


@app.post("/subjects/{subject_id}/topics/{subtopic:path}/explain")
def explain(request: Request, subject_id: int, subtopic: str, regenerate: bool = False,
            uid: str = Depends(current_user)):
    """Explain one subtopic from the subject's notes. Saved and shared, so the next time is instant.

    ?regenerate=true (admin only) asks Gemini again and replaces the saved explanation.
    """
    if regenerate:
        require_admin(request)
    subject = subject_or_404(subject_id)
    subtopic = subtopic.strip()
    if not subtopic:
        raise HTTPException(400, "Topic can't be empty.")
    if not regenerate:
        saved = db.get_explanation(subject["name"], subtopic)
        if saved:
            return {"subtopic": subtopic, **saved, "cached": True}
    questions = db.subject_questions(subject["name"])
    bullets, chunks = topic_context(subject["name"], [(subtopic, stems_about(questions, subtopic))])
    data = call_ai("explain", explain_topic, subject["name"], subtopic, bullets, chunks,
                   fresh=regenerate, user=uid)
    db.save_explanation(subject["name"], subtopic, data)
    return {"subtopic": subtopic, **db.get_explanation(subject["name"], subtopic), "cached": False}


def topic_masteries(subject_name, questions, uid):
    """[(subtopic, smoothed mastery)] for every topic that has questions or attempts.

    Topics never answered count as 0.5, like in the quiz picker.
    """
    topics = {q["subtopic"].casefold(): (q["subtopic"], 0.5) for q in questions}
    for r in db.progress(subject_name, user_id=uid):
        topics[r["subtopic"].casefold()] = (r["subtopic"], db.mastery_score(r["correct"], r["attempts"]))
    return list(topics.values())


@app.post("/subjects/{subject_id}/practice")
def practice(subject_id: int, n: int = Query(8, ge=5, le=10), uid: str = Depends(current_user)):
    """Make n NEW MCQs for the weakest topics and return them as a quiz.

    They are saved as normal questions (marked "practice": true) on the note that
    already has the most questions about that topic, so answers count toward progress.
    """
    subject = subject_or_404(subject_id)
    questions = db.subject_questions(subject["name"])
    plan = plan_practice(topic_masteries(subject["name"], questions, uid), n)
    if not plan:
        raise HTTPException(400, "This subject has no questions yet. Upload a PDF first.")
    wanted = {topic.casefold() for topic, _ in plan}
    existing = [q["question"] for q in questions if q["subtopic"].casefold() in wanted]  # stems only
    bullets, chunks = topic_context(subject["name"], [(t, stems_about(questions, t)) for t, _ in plan])
    mcqs = call_ai("practice", generate_practice, subject["name"], plan, bullets, existing, chunks, user=uid)

    # Which note each topic's new questions go to.
    home = {}
    for topic in wanted:
        counts = {}
        for q in questions:
            if q["subtopic"].casefold() == topic:
                counts[q["note_id"]] = counts.get(q["note_id"], 0) + 1
        home[topic] = max(counts, key=counts.get) if counts else questions[-1]["note_id"]
    by_note = {}
    for q in mcqs:
        by_note.setdefault(home[q["subtopic"].casefold()], []).append({**q, "practice": True})

    added = []
    for note_id, new in by_note.items():
        start = db.add_mcqs(note_id, new) - len(new)
        added += [(note_id, start + i) for i in range(len(new))]
    fresh = {(q["note_id"], q["question_index"]): q for q in db.subject_questions(subject["name"])}
    return [fresh[key] for key in added]


class WhyRequest(BaseModel):
    note_id: int
    question_index: int
    chosen: int


@app.post("/why")
def why(body: WhyRequest, uid: str = Depends(current_user)):
    """Why was my answer wrong? Saved per (question, chosen option)."""
    note = db.get_note(body.note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    if not 0 <= body.question_index < len(note["mcqs"]):
        raise HTTPException(400, "Invalid question_index.")
    q = note["mcqs"][body.question_index]
    if not 0 <= body.chosen < len(q["options"]):
        raise HTTPException(400, "Invalid chosen option.")
    if body.chosen == q["answer"]:
        raise HTTPException(400, "That answer is correct.")
    saved = db.get_why(body.note_id, body.question_index, body.chosen)
    if saved:
        return {"why": saved, "cached": True}
    text = call_ai("why wrong", why_wrong, q["question"], q["options"], body.chosen, q["answer"], user=uid)
    db.save_why(body.note_id, body.question_index, body.chosen, text)
    return {"why": text, "cached": False}


@app.get("/usage")
def usage():
    """Gemini tokens per feature: today, total and average per call."""
    return db.usage_summary()


@app.get("/notes")
def list_notes(subject: str | None = None):
    return db.list_notes(subject)


@app.get("/notes/{note_id}")
def get_note(note_id: int, uid: str = Depends(current_user)):
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    note["accuracy"] = db.note_accuracy(note_id, uid)  # None if this user never tried the quiz
    note["card_count"] = db.count_cards(note_id)
    return public_note(note)


@app.delete("/notes/{note_id}", dependencies=[Depends(require_admin)])
def delete_note(note_id: int):
    """Delete a note together with its quiz attempts, flashcards and saved PDF."""
    deleted = db.delete_note(note_id)
    if deleted is None:
        raise HTTPException(404, "Note not found.")
    remove_pdf_if_unused(deleted["pdf_path"])
    return {"deleted": note_id}


def call_ai(what, fn, *args, user=None, **kwargs):
    """Run a Gemini call for this user (None or OWNER = no daily limit).

    Over the limit gives a 429; any other failure a 502, both with a readable message.
    """
    token = ai_caller.set({"user": user, "counted": False})
    try:
        return fn(make_client(), *args, **kwargs)
    except QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except GenerationError as e:
        raise HTTPException(502, str(e))
    except Exception:
        # Anything unexpected (network, Gemini SDK, ...) still gets a readable message.
        log.exception("%s failed", what)
        raise HTTPException(502, GENERIC_ERROR)
    finally:
        ai_caller.reset(token)


def generate_more(note_id, generator, existing_of):
    """Shared code for "Generate more MCQs / flashcards".

    Sends the parts of the PDF's text that existing items cover least (the PDF itself only
    if it has no text, the notes if there's no PDF). The generator drops repeats.
    """
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    existing = existing_of(note)
    text = least_covered(source_text(note), existing, MORE_TEXT_CHARS)
    return note, call_ai(
        "generate more", generator, note["subject"], note["topic"], note["notes"],
        existing=existing, pdf_path=note["pdf_path"], text=text,
    )


@app.post("/notes/{note_id}/flashcards", dependencies=[Depends(require_admin)])
def make_flashcards(note_id: int):
    """Add new flashcards to a note (old ones are kept, duplicates skipped)."""
    note, cards = generate_more(
        note_id, generate_flashcards, lambda n: [c["front"] for c in db.note_cards(note_id)]
    )
    before = db.count_cards(note_id)
    db.save_cards(note_id, note["subject"], cards)
    total = db.count_cards(note_id)
    return {"added": total - before, "card_count": total}


@app.post("/notes/{note_id}/mcqs", dependencies=[Depends(require_admin)])
def make_mcqs(note_id: int):
    """Add new MCQs to a note (old ones are kept, duplicates skipped)."""
    note, mcqs = generate_more(note_id, generate_mcqs, lambda n: [q["question"] for q in n["mcqs"]])
    total = db.add_mcqs(note_id, mcqs)
    return {"added": len(mcqs), "mcq_count": total}


@app.get("/notes/{note_id}/cards")
def list_note_cards(note_id: int, uid: str = Depends(current_user)):
    """All flashcards of one note with this user's schedule (for the PDF page's Flashcards tab)."""
    if db.get_note(note_id) is None:
        raise HTTPException(404, "Note not found.")
    return db.note_cards(note_id, uid)


class Attempt(BaseModel):
    """What the browser sends after answering one question."""
    note_id: int
    question_index: int
    chosen: int


@app.post("/attempts")
def record_attempt(attempt: Attempt, uid: str = Depends(current_user)):
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
        note["subject"], attempt.chosen, correct, user_id=uid,
    )
    return {"id": attempt_id, "correct": correct, "subtopic": subtopic, "subject": note["subject"]}


@app.get("/progress")
def get_progress(subject: str | None = None, uid: str = Depends(current_user)):
    return db.progress(subject, user_id=uid)


# ---------- Flashcards ----------

@app.get("/cards/due")
def get_due_cards(subject: str | None = None, uid: str = Depends(current_user)):
    return db.due_cards(subject, user_id=uid)


class Review(BaseModel):
    """One flashcard review: Again=1, Hard=3, Good=4, Easy=5."""
    quality: int = Field(ge=0, le=5)


@app.post("/cards/{card_id}/review")
def review_card(card_id: int, review: Review, uid: str = Depends(current_user)):
    """Schedule one card for this user only (SM-2)."""
    card = db.get_card(card_id, uid)
    if card is None:
        raise HTTPException(404, "Card not found.")
    result = srs.sm2(review.quality, card["ease"], card["interval_days"], card["repetitions"])
    db.update_card(
        card_id, result["ease"], result["interval_days"], result["repetitions"],
        result["due_date"].isoformat(), user_id=uid,
    )
    return db.get_card(card_id, uid)


# Serve the frontend (static/index.html at "/"). Must come after the API routes.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
