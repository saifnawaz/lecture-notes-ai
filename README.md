# Lecture Notes AI

Upload a lecture PDF and get study notes, a 5-question quiz and flashcards, generated with Google Gemini.
Works as a web app (FastAPI) or from the command line.

## Setup

1. Create and activate a virtual environment (Windows):
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
2. Install the packages:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and put your Gemini API key in it:
   ```
   GEMINI_API_KEY=your_key_here
   ```

## Run the web app

```
uvicorn app:app --reload
```

Open http://127.0.0.1:8000 and upload a PDF. Results are saved in `notes.db` (SQLite).

API docs are at http://127.0.0.1:8000/docs.

| Method | Route         | What it does                          |
|--------|---------------|---------------------------------------|
| POST   | `/generate`   | Upload a PDF (`file`, optional `subject`) and get notes + MCQs |
| GET    | `/notes`      | List saved notes                      |
| GET    | `/subjects`   | List subject names (case duplicates merged) |
| GET    | `/notes/{id}` | Get one saved note (with your accuracy) |
| POST   | `/attempts`   | Save one quiz answer (`note_id`, `question_index`, `chosen`) |
| GET    | `/progress`   | Mastery per subtopic, lowest first (optional `?subject=`) |
| POST   | `/notes/{id}/flashcards` | Make flashcards for an older note from its saved text |
| GET    | `/cards/due`  | Flashcards due today or earlier (optional `?subject=`) |
| POST   | `/cards/{id}/review` | Review one card (`quality` 0-5; the page sends 1 = Bhool gaya, 4 = Yaad tha) |

## Progress

Every quiz answer is saved. The **Progress** page shows a mastery bar for each subtopic,
lowest first. Mastery is `(correct + 1) / (attempts + 2)`, so one lucky or unlucky answer
doesn't make a topic look perfect or hopeless. See `explanations.md` for details.

## Subjects

When uploading, pick an existing subject or choose **+ New subject...**. Subjects are matched
ignoring case, so "discrete mathematics" joins "Discrete Mathematics". To merge two different
names, use the rename script (it updates notes, quiz answers and flashcards):

```
python rename_subject.py                                  # list subjects
python rename_subject.py "discrete" "Discrete Mathematics"
```

## Flashcards

Each new note comes with 8-10 flashcards. For notes saved earlier, open the note and click
**Generate flashcards**. The **Daily Revision** page (the badge shows how many are due) shows one card
at a time: click or press Space to flip, then press **Bhool gaya** (forgot, key 1) or
**Yaad tha** (remembered, key 2). Forgotten cards come back at the end of the session.
Cards are scheduled with the SM-2 spaced repetition algorithm (`srs.py`), explained in `explanations.md`.

## Run the tests

```
pytest
```

## Run from the command line

```
python main.py lecture.pdf
python main.py --models
```

## Files

- `study_ai.py`: Gemini logic (prompt, retries, fallback models)
- `app.py`: FastAPI server
- `db.py`: SQLite storage
- `srs.py`: SM-2 spaced repetition math
- `rename_subject.py`: rename or merge a subject
- `static/`: frontend (HTML, CSS, JS)
- `main.py`: command-line version
- `tests/`: pytest tests
