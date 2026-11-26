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
| GET    | `/notes/{id}` | Get one saved note (with your accuracy) |
| POST   | `/attempts`   | Save one quiz answer (`note_id`, `question_index`, `chosen`) |
| GET    | `/weakness`   | Subtopics sorted weakest first (optional `?subject=`) |
| POST   | `/notes/{id}/flashcards` | Make flashcards for an older note from its saved text |
| GET    | `/cards/due`  | Flashcards due today or earlier (optional `?subject=`) |
| POST   | `/cards/{id}/review` | Review one card (`quality`: 1 Again, 3 Hard, 4 Good, 5 Easy) |

## Progress

Every quiz answer is saved. The **Progress** page ranks subtopics by a smoothed
error rate, `(wrong + 1) / (attempts + 2)`, so one lucky or unlucky answer doesn't
make a topic look perfect or hopeless. See `explanations.md` for details.

## Flashcards

Each new note comes with 8-10 flashcards. For notes saved earlier, open the note and click
**Generate flashcards**. The **Daily Revision** page (the badge shows how many are due) shows one card
at a time: click or press Space to flip, then rate it Again / Hard / Good / Easy (keys 1-4).
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
- `static/`: frontend (HTML, CSS, JS)
- `main.py`: command-line version
- `tests/`: pytest tests
