"""FastAPI web server.

Run with:  uvicorn app:app --reload
"""
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

import db
from study_ai import GenerationError, generate, make_client

MAX_UPLOAD_MB = 20


@asynccontextmanager
async def lifespan(app):
    db.init_db()  # runs once when the server starts
    yield


app = FastAPI(title="Lecture Notes AI", lifespan=lifespan)


@app.post("/generate")
def generate_notes(file: UploadFile = File(...), subject: str = Form("")):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    content = file.file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"PDF is larger than {MAX_UPLOAD_MB} MB.")

    # Gemini's upload needs a file on disk, so write a temporary copy.
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(content)
        tmp.close()
        data = generate(make_client(), tmp.name)
    except GenerationError as e:
        raise HTTPException(502, str(e))
    finally:
        os.unlink(tmp.name)  # always delete the temp file

    if subject.strip():
        data["subject"] = subject.strip()  # user's choice wins over Gemini's guess

    note_id = db.save_note(data)
    return db.get_note(note_id)


@app.get("/notes")
def list_notes():
    return db.list_notes()


@app.get("/notes/{note_id}")
def get_note(note_id: int):
    note = db.get_note(note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    return note


# Serve the frontend (static/index.html at "/"). Must come after the API routes.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
