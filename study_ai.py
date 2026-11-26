"""Gemini logic: upload a file and get notes + MCQs back as a dict.

Used by both main.py (CLI) and app.py (web server).
"""
import json
import os
import random
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
Below are study notes on "{topic}" ({subject}).
Make 8 to 10 flashcards from them and return ONLY valid JSON in this exact shape:

{{
  "flashcards": [
    {{"front": "short question or term", "back": "short answer", "subtopic": "which subtopic"}}
  ]
}}

Rules:
- Front: one clear question or term. Back: a short answer (1-2 sentences). Keep formulas.
- Cover the key definitions, formulas and ideas.

Notes:
{notes}
"""


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
    for q in data["mcqs"]:
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


def ask_with_fallback(client, contents, validate):
    """Try the main model, then each fallback, with retries.

    A bad answer (broken JSON or wrong shape) is retried.
    Raises GenerationError if everything fails.
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
                print(f"  {model} not usable: {e.code}")
                break
            except json.JSONDecodeError:
                print("  Bad JSON from model, retrying.")
            except BadResponse as e:
                print(f"  Unexpected answer shape ({e}), retrying.")

    raise GenerationError(
        "The AI couldn't produce usable notes for this file. Please try again in a minute."
    )


def generate(client, file_path):
    """Upload file_path to Gemini and return the notes/MCQs/flashcards dict."""
    file_path = Path(file_path)
    print(f"Uploading {file_path.name}...")
    uploaded = client.files.upload(file=file_path)
    return ask_with_fallback(client, [uploaded, PROMPT], validate_notes)


def generate_flashcards(client, subject, topic, notes):
    """Make flashcards from already-saved notes text (no PDF needed)."""
    prompt = FLASHCARD_PROMPT.format(
        subject=subject or "unknown subject",
        topic=topic or "untitled",
        notes="\n".join(f"- {n}" for n in notes),
    )
    return ask_with_fallback(client, [prompt], validate_flashcards)["flashcards"]
