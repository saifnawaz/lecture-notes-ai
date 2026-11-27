"""Run every AI feature once on a real PDF and print the Gemini tokens each one used.

Usage:  python measure_tokens.py lecture.pdf

Uses a throwaway database and upload folder in a temp directory, so notes.db and
uploads/ are never touched. This makes real (paid) Gemini calls.
"""
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module
import db

FEATURES = ["generate", "more_mcqs", "flashcards", "explain", "practice", "why_wrong"]


def check(response, what):
    if response.status_code != 200:
        sys.exit(f"{what} failed: {response.status_code} {response.text}")
    return response.json()


def run(pdf):
    with TestClient(app_module.app) as client:
        with open(pdf, "rb") as f:
            note = check(client.post("/generate", data={"subject": "Measure"},
                                     files={"file": (pdf.name, f, "application/pdf")}), "generate")
        nid = note["id"]
        check(client.post(f"/notes/{nid}/mcqs"), "more MCQs")
        check(client.post(f"/notes/{nid}/flashcards"), "flashcards")

        # Answer every question wrong, so there are weak topics and wrong answers to explain.
        mcqs = client.get(f"/notes/{nid}").json()["mcqs"]
        for i, q in enumerate(mcqs):
            wrong = (q["answer"] + 1) % len(q["options"])
            client.post("/attempts", json={"note_id": nid, "question_index": i, "chosen": wrong})

        sid = client.get("/subjects").json()[0]["id"]
        weakest = client.get(f"/subjects/{sid}").json()["weakest"][0]["subtopic"]
        check(client.post(f"/subjects/{sid}/topics/{weakest}/explain"), "explain")
        check(client.post(f"/subjects/{sid}/practice"), "practice")
        wrong = (mcqs[0]["answer"] + 1) % len(mcqs[0]["options"])
        check(client.post("/why", json={"note_id": nid, "question_index": 0, "chosen": wrong}), "why")
        return client.get("/usage").json()


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        sys.exit("Usage: python measure_tokens.py lecture.pdf")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows keeps the db open
        db.DB_PATH = str(Path(tmp) / "measure.db")
        app_module.UPLOAD_DIR = Path(tmp) / "uploads"
        rows = {r["feature"]: r for r in run(Path(sys.argv[1]))}

    print(f"\n{'feature':<11} {'calls':>5} {'avg total':>9} {'prompt':>7} {'output':>7} {'thinking':>8}")
    for name in FEATURES:
        r = rows.get(name)
        if r:
            print(f"{name:<11} {r['calls']:>5} {r['avg_total']:>9.0f} {r['avg_prompt']:>7.0f} "
                  f"{r['avg_output']:>7.0f} {r['avg_thinking']:>8.0f}")


if __name__ == "__main__":
    main()
