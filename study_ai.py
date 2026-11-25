"""Gemini logic: upload a file and get notes + MCQs back as a dict.

Used by both main.py (CLI) and app.py (web server).
"""
import json
import os
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
  ]
}

Rules:
- 8 to 12 notes, simple English, keep formulas.
- Exactly 5 MCQs. "answer" is the index (0-3) of the correct option.
- Questions must test understanding, not just copying lines.
"""


class GenerationError(Exception):
    """Raised when no model could produce a result."""


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


def ask(client, model, uploaded):
    response = client.models.generate_content(
        model=model,
        contents=[uploaded, PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text)


def generate(client, file_path):
    """Upload file_path to Gemini and return the notes/MCQs dict.

    Tries the main model, then each fallback, with retries.
    Raises GenerationError if everything fails.
    """
    file_path = Path(file_path)
    print(f"Uploading {file_path.name}...")
    uploaded = client.files.upload(file=file_path)

    for model in [MODEL] + FALLBACKS:
        for attempt in range(1, RETRIES + 1):
            try:
                print(f"Asking {model} (try {attempt})...")
                return ask(client, model, uploaded)
            except errors.ServerError:
                print("  Server busy.")
                time.sleep(attempt * 3)
            except errors.ClientError as e:
                print(f"  {model} not usable: {e.code}")
                break
            except json.JSONDecodeError:
                print("  Bad JSON from model, retrying.")

    raise GenerationError("All models failed. Try again later.")
