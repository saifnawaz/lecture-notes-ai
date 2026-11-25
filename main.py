import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
OUTPUT_DIR = Path("output")

PROMPT = """You are a study assistant for a university student.
Read the attached study material and return ONLY valid JSON in this exact shape:

{
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


def list_models(client):
    for m in client.models.list():
        if "generateContent" in (m.supported_actions or []):
            print(m.name)


def generate(client, file_path):
    print(f"Uploading {file_path.name}...")
    uploaded = client.files.upload(file=file_path)

    print(f"Asking {MODEL}...")
    response = client.models.generate_content(
        model=MODEL,
        contents=[uploaded, PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text)


def save(data, file_path):
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{file_path.stem}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved to {out}")


def show_notes(data):
    print(f"\n=== {data['topic']} ===\n")
    for note in data["notes"]:
        print(f"- {note}")


def quiz(data):
    letters = "ABCD"
    score = 0
    print("\n=== Quiz ===")
    for i, q in enumerate(data["mcqs"], 1):
        print(f"\nQ{i}. {q['question']}")
        for letter, option in zip(letters, q["options"]):
            print(f"  {letter}) {option}")

        choice = input("Your answer: ").strip().upper()
        correct = letters[q["answer"]]
        if choice == correct:
            score += 1
            print("Correct!")
        else:
            print(f"Wrong. Answer: {correct}")
        print(f"  -> {q['explanation']}")

    print(f"\nScore: {score}/{len(data['mcqs'])}")


def main():
    if not API_KEY:
        sys.exit("GEMINI_API_KEY missing. Put it in a .env file.")

    if len(sys.argv) < 2:
        sys.exit("Usage: python main.py <file.pdf|image>   or   python main.py --models")

    client = genai.Client(api_key=API_KEY)

    if sys.argv[1] == "--models":
        list_models(client)
        return

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        sys.exit(f"File not found: {file_path}")

    data = generate(client, file_path)
    save(data, file_path)
    show_notes(data)
    quiz(data)


if __name__ == "__main__":
    main()
