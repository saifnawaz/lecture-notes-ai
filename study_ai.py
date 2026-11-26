"""Gemini logic: upload a file and get notes + MCQs back as a dict.

Used by both main.py (CLI) and app.py (web server).
"""
import io
import json
import os
import random
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACKS", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
RETRIES = 3

PROMPT = """You are a study assistant for a university student.
Read the attached study material and return ONLY valid JSON in this exact shape:

{
  "subject": "the course or subject, e.g. Discrete Mathematics",
  "topic": "short title of the material",
  "notes": ["clear bullet point", "..."],
  "mcqs": [
    {
      "question": "question text",
      "options": ["A option", "B option", "C option", "D option"],
      "answer": 0,
      "subtopic": "which subtopic this tests",
      "explanation": "one line on why the answer is correct"
    }
  ],
  "flashcards": [
    {"front": "short question or term", "back": "short answer", "subtopic": "which subtopic"}
  ]
}

Rules:
- 8 to 12 notes, simple English, keep formulas.
- Exactly 5 MCQs. "answer" is the index (0-3) of the correct option.
- Questions must test understanding, not just copying lines.
- 8 to 10 flashcards. Front: one clear question or term. Back: a short answer (1-2 sentences).
  Cover the key definitions, formulas and ideas.
"""

FLASHCARD_PROMPT = """You are a study assistant for a university student.
{source}
Make 8 to 10 NEW flashcards and return ONLY valid JSON in this exact shape:

{{
  "flashcards": [
    {{"front": "short question or term", "back": "short answer", "subtopic": "which subtopic"}}
  ]
}}

Rules:
- Front: one clear question or term. Back: a short answer (1-2 sentences). Keep formulas.
- Cover the key definitions, formulas and ideas.
- Do NOT repeat or rephrase any of these existing flashcards:
{existing}
{notes}"""

MCQ_PROMPT = """You are a study assistant for a university student.
{source}
Make 5 NEW multiple-choice questions and return ONLY valid JSON in this exact shape:

{{
  "mcqs": [
    {{
      "question": "question text",
      "options": ["A option", "B option", "C option", "D option"],
      "answer": 0,
      "subtopic": "which subtopic this tests",
      "explanation": "one line on why the answer is correct"
    }}
  ]
}}

Rules:
- "answer" is the index (0-3) of the correct option.
- Questions must test understanding, not just copying lines.
- Do NOT repeat or rephrase any of these existing questions:
{existing}
{notes}"""


class GenerationError(Exception):
    """Raised when no model could produce a result."""


class BadResponse(ValueError):
    """The model answered, but not in the shape we asked for."""


def unwrap(data):
    """Gemini sometimes wraps the object in a list: [ {...} ]. Take the dict out."""
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return data


def check_list(data, key):
    if not isinstance(data.get(key), list):
        raise BadResponse(f'"{key}" missing or not a list')


def validate_notes(data):
    """Raise BadResponse unless data looks like the main PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    if not isinstance(data.get("topic"), str):
        raise BadResponse('"topic" missing or not text')
    for key in ("notes", "mcqs", "flashcards"):
        check_list(data, key)
    check_mcqs(data["mcqs"])


def validate_mcqs(data):
    """Raise BadResponse unless data looks like MCQ_PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    check_list(data, "mcqs")
    check_mcqs(data["mcqs"])


def check_mcqs(mcqs):
    for q in mcqs:
        ok = (
            isinstance(q, dict)
            and isinstance(q.get("question"), str)
            and isinstance(q.get("options"), list)
            and len(q["options"]) >= 2
            and isinstance(q.get("answer"), int)
            and 0 <= q["answer"] < len(q["options"])
        )
        if not ok:
            raise BadResponse("an MCQ is missing question/options/answer")


def validate_flashcards(data):
    """Raise BadResponse unless data looks like FLASHCARD_PROMPT's JSON."""
    if not isinstance(data, dict):
        raise BadResponse(f"expected an object, got {type(data).__name__}")
    check_list(data, "flashcards")


def shuffle_options(mcqs, rng=random):
    """Shuffle each question's options and move "answer" to the new index.

    Models tend to put the correct answer in the same slot (often A),
    so we mix the options ourselves. Returns new dicts; the input is not changed.
    """
    shuffled = []
    for q in mcqs:
        order = list(range(len(q["options"])))
        rng.shuffle(order)  # order[new_index] = old_index
        shuffled.append({
            **q,
            "options": [q["options"][i] for i in order],
            "answer": order.index(q["answer"]),
        })
    return shuffled


def make_client():
    if not API_KEY:
        raise GenerationError("GEMINI_API_KEY missing. Put it in a .env file.")
    return genai.Client(api_key=API_KEY)


def list_models(client):
    return [
        m.name
        for m in client.models.list()
        if "generateContent" in (m.supported_actions or [])
    ]


def ask(client, model, contents, validate):
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = unwrap(json.loads(response.text))
    validate(data)  # raises BadResponse if the shape is wrong
    return data


def describe(e):
    """Readable text for a Gemini API error: code, status and the actual message."""
    return f"{e.code} {e.status or ''}: {e.message or e.details}".replace(" :", ":")


def is_file_error(e):
    """True if a ClientError is about the attached file, not the model.

    A 404 can mean "file not found" as well as "model not found", so we look at the message.
    Trying another model won't help with a bad file.
    """
    text = str(e.message or e.details or "").lower()
    return any(word in text for word in ("file", "document", "mime", "pdf", "page"))


def ask_with_fallback(client, contents, validate):
    """Try the main model, then each fallback, with retries.

    A bad answer (broken JSON or wrong shape) is retried.
    A file error stops right away. Raises GenerationError if everything fails.
    """
    for model in [MODEL] + FALLBACKS:
        for attempt in range(1, RETRIES + 1):
            try:
                print(f"Asking {model} (try {attempt})...")
                return ask(client, model, contents, validate)
            except errors.ServerError:
                print("  Server busy.")
                time.sleep(attempt * 3)
            except errors.ClientError as e:
                print(f"  {model} failed: {describe(e)}")
                if is_file_error(e):
                    raise GenerationError(
                        f"Gemini couldn't read the uploaded file ({e.message or e.code}). "
                        "Check that it's a valid, non-empty PDF."
                    ) from e
                break
            except json.JSONDecodeError:
                print("  Bad JSON from model, retrying.")
            except BadResponse as e:
                print(f"  Unexpected answer shape ({e}), retrying.")

    raise GenerationError(
        "The AI couldn't produce usable notes for this file. Please try again in a minute."
    )


def upload_file(client, file_path, content=None):
    """Upload a file to Gemini: the given bytes if we have them, else read file_path."""
    file_path = Path(file_path)
    try:
        if content is None:
            print(f"Uploading {file_path.name}...")
            return client.files.upload(file=file_path)
        print(f"Uploading {file_path.name} ({len(content)} bytes)...")
        return client.files.upload(
            file=io.BytesIO(content),
            config=types.UploadFileConfig(mime_type="application/pdf", display_name=file_path.name),
        )
    except errors.ClientError as e:
        print(f"  Upload failed: {describe(e)}")
        raise GenerationError(f"Couldn't upload the file to Gemini ({e.message or e.code}).") from e


def generate(client, file_path, content=None):
    """Upload the file to Gemini and return the notes/MCQs/flashcards dict.

    content: the file's bytes, if already in memory (the web upload). Otherwise file_path is read.
    """
    uploaded = upload_file(client, file_path, content)
    return ask_with_fallback(client, [uploaded, PROMPT], validate_notes)


def normalize(text):
    """Lowercase, drop punctuation and extra spaces: for spotting duplicates."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).lower()).split())


def only_new(items, existing, key):
    """Keep items whose item[key] isn't already in existing (or earlier in items)."""
    seen = {normalize(e) for e in existing}
    fresh = []
    for item in items:
        text = normalize(item.get(key, "")) if isinstance(item, dict) else ""
        if text and text not in seen:
            seen.add(text)
            fresh.append(item)
    return fresh


def ask_more(client, template, validate, subject, topic, notes, existing, pdf_path):
    """Ask for more items, from the original PDF if we have it, else from the notes text."""
    about = f'"{topic or "untitled"}" ({subject or "unknown subject"})'
    if pdf_path and Path(pdf_path).exists():
        source = f"Use the attached study material on {about}."
        notes_block = ""
        contents = [upload_file(client, pdf_path)]
    else:
        source = f"Use the study notes below on {about}."
        notes_block = "\nNotes:\n" + "\n".join(f"- {n}" for n in notes)
        contents = []
    prompt = template.format(
        source=source,
        existing="\n".join(f"- {e}" for e in existing) or "- (none yet)",
        notes=notes_block,
    )
    return ask_with_fallback(client, contents + [prompt], validate)


def generate_flashcards(client, subject, topic, notes, existing=(), pdf_path=None):
    """Make new flashcards that don't repeat the existing fronts."""
    data = ask_more(client, FLASHCARD_PROMPT, validate_flashcards, subject, topic, notes, existing, pdf_path)
    return only_new(data["flashcards"], existing, "front")


def generate_mcqs(client, subject, topic, notes, existing=(), pdf_path=None):
    """Make new MCQs (options shuffled) that don't repeat the existing questions."""
    data = ask_more(client, MCQ_PROMPT, validate_mcqs, subject, topic, notes, existing, pdf_path)
    return shuffle_options(only_new(data["mcqs"], existing, "question"))
