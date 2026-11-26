# Lecture Notes AI

Organise lecture PDFs by subject and get study notes, quizzes and flashcards, generated with Google Gemini.
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

Open http://127.0.0.1:8000. Results are saved in `notes.db` (SQLite) and uploaded PDFs in `uploads/`.

API docs are at http://127.0.0.1:8000/docs.

## How the app is organised

- **Home**: one card per subject (PDFs, cards due today, quiz accuracy) and **+ New subject**.
- **Subject page** with tabs:
  - **Overview**: accuracy, cards due today, your 3 weakest topics with a **Practice** button
  - **PDFs**: the PDFs in this subject and **Upload PDF** (the subject is filled in for you)
  - **Quiz**: 10 questions mixed from all PDFs in the subject; weak topics come up more often
  - **Daily Revision**: this subject's flashcards that are due today
  - **Progress**: an accuracy bar per topic, weakest first
- **PDF page** with tabs **Notes | Quiz | Flashcards**, plus **Generate more MCQs**,
  **Generate more flashcards** (new items are added, repeats skipped) and **Delete PDF**.
- A breadcrumb at the top (Home › Subject › PDF) takes you back up.

Uploading the same PDF twice into one subject asks first. After every quiz and every
Daily Revision session you get a report: score, the questions/cards you got wrong with the
right answers, topics to revise, and **Practice mistakes again** (practice rounds aren't saved).

In Daily Revision, click the card or press Space to flip it, then press **Bhool gaya**
(forgot, key 1) or **Yaad tha** (remembered, key 2). Forgotten cards come back at the end of the
session. Cards are scheduled with the SM-2 spaced repetition algorithm (`srs.py`).
In quizzes, keys 1-4 pick an option and Enter goes to the next question.
See `explanations.md` for how everything works.

| Method | Route         | What it does                          |
|--------|---------------|---------------------------------------|
| GET    | `/subjects`   | Subject cards: PDFs, cards due today, accuracy |
| POST   | `/subjects`   | Create a subject (`name`); returns the existing one if the name is taken |
| GET    | `/subjects/{id}` | Subject overview, including the 3 weakest topics |
| GET    | `/subjects/{id}/quiz` | Mixed MCQs, weak topics more often (`?n=`, `?subtopic=`) |
| POST   | `/generate`   | Upload a PDF (`file`, `subject`, `force`); 409 if it's already in that subject |
| GET    | `/notes`      | List PDFs/notes (optional `?subject=`) |
| GET    | `/notes/{id}` | One note with your accuracy and card count |
| DELETE | `/notes/{id}` | Delete a note with its quiz answers, flashcards and saved PDF |
| POST   | `/notes/{id}/mcqs` | Add new MCQs (from the saved PDF, or the notes text) |
| POST   | `/notes/{id}/flashcards` | Add new flashcards (from the saved PDF, or the notes text) |
| GET    | `/notes/{id}/cards` | All flashcards of one note |
| POST   | `/attempts`   | Save one quiz answer (`note_id`, `question_index`, `chosen`) |
| GET    | `/progress`   | Accuracy per subtopic, weakest first (optional `?subject=`) |
| GET    | `/cards/due`  | Flashcards due today or earlier (optional `?subject=`) |
| POST   | `/cards/{id}/review` | Review one card (`quality` 0-5; the page sends 1 = Bhool gaya, 4 = Yaad tha) |

## Renaming or merging subjects

Subjects are matched ignoring case, so "discrete mathematics" joins "Discrete Mathematics".
To merge two different names, use the rename script (it updates notes, quiz answers and flashcards):

```
python rename_subject.py                                  # list subjects
python rename_subject.py "discrete" "Discrete Mathematics"
```

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
- `quiz.py`: picks subject quiz questions, weak topics more often
- `rename_subject.py`: rename or merge a subject
- `static/`: frontend (HTML, CSS, JS)
- `main.py`: command-line version
- `tests/`: pytest tests
