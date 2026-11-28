# Lapse

Organise lecture PDFs by subject and get study notes, quizzes and flashcards, generated with Google Gemini.
Works as a web app (FastAPI) or from the command line.

## Setup

1. Create and activate a virtual environment (Windows):
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
2. Install the packages (`requirements.txt` is what production needs; the dev file adds the test tools):
   ```
   pip install -r requirements-dev.txt
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
Locally, with no `ADMIN_PASSCODE` in `.env`, you are the admin: upload, edit and delete work right away.

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

In Daily Revision, click the card or press Space to flip it, then press **Forgot**
(key 1) or **Remembered** (key 2). Forgotten cards come back at the end of the
session. Cards are scheduled with the SM-2 spaced repetition algorithm (`srs.py`).
In quizzes, keys 1-4 pick an option and Enter goes to the next question.
See `explanations.md` for how everything works.

## Demo mode: guests and admin

The deployed demo has two kinds of visitors:

- **Guests** get a random id in an httpOnly cookie on their first visit. Notes, MCQs, flashcards and
  explanations are shared demo content; quiz answers, the flashcard schedule (`card_state`), progress,
  session reports and the Today box are per guest, so every visitor starts fresh and sees only their own.
  A banner says "Demo — your progress is saved in this browser only."
- **The admin** clicks **Admin** in the footer and enters `ADMIN_PASSCODE`. It is kept in
  sessionStorage and sent as the `X-Admin-Passcode` header. Upload, generate more, delete, new subject,
  rename subject and Regenerate need it; guests don't see those buttons. The admin's own progress is
  stored as user `owner`. A real login later only has to map an account to a `user_id`.

Guests can use Explain, Why? and Practice. Saved and cached answers are free; new AI answers are
limited to 10 per guest and 200 for the whole demo per day (UTC), then a friendly message is shown.
PDFs can be at most 4 MB.

Routes marked * need the admin passcode when `ADMIN_PASSCODE` is set.

| Method | Route         | What it does                          |
|--------|---------------|---------------------------------------|
| GET    | `/me`         | `{admin, passcode_set}`: what the page should show |
| POST   | `/admin/check` | Test a passcode (403 if wrong) |
| PATCH  | `/subjects/{id}` * | Rename a subject (`name`); an existing name merges the two |
| GET    | `/subjects`   | Subject cards: PDFs, cards due today, accuracy |
| POST   | `/subjects` * | Create a subject (`name`); returns the existing one if the name is taken |
| GET    | `/subjects/{id}` | Subject overview, including the 3 weakest topics |
| GET    | `/subjects/{id}/quiz` | Mixed MCQs, weak topics more often (`?n=`, `?subtopic=`) |
| POST   | `/subjects/{id}/topics/{topic}/explain` | AI explanation, worked example, 3 key points (saved; `?regenerate=true` *) |
| POST   | `/subjects/{id}/practice` | 5-10 new AI MCQs for the weakest topics (`?n=`, default 8) |
| POST   | `/generate` * | Upload a PDF (max 4 MB; `file`, `subject`, `force`); 409 if it's already in that subject |
| GET    | `/notes`      | List PDFs/notes (optional `?subject=`) |
| GET    | `/notes/{id}` | One note with your accuracy and card count |
| DELETE | `/notes/{id}` * | Delete a note with its quiz answers, flashcards and saved PDF |
| POST   | `/notes/{id}/mcqs` * | Add new MCQs (from the saved PDF, or the notes text) |
| POST   | `/notes/{id}/flashcards` * | Add new flashcards (from the saved PDF, or the notes text) |
| GET    | `/notes/{id}/cards` | All flashcards of one note |
| POST   | `/attempts`   | Save one quiz answer (`note_id`, `question_index`, `chosen`) |
| POST   | `/why`        | Why a wrong answer is wrong (`note_id`, `question_index`, `chosen`; saved) |
| GET    | `/progress`   | Accuracy per subtopic, weakest first (optional `?subject=`) |
| GET    | `/usage`      | Gemini tokens per feature: today, total, average per call |
| GET    | `/today`      | Home "Today" box: cards due, weakest topic overall, score on the last quiz day |
| GET    | `/cards/due`  | Flashcards due today or earlier (optional `?subject=`) |
| POST   | `/cards/{id}/review` | Review one card (`quality` 0-5; the page sends 1 = Forgot, 4 = Remembered) |

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

The tests use a temporary SQLite database. `tests/test_postgres.py` is a smoke test that runs only
when `DATABASE_URL` is set; point it at a dev database or a Neon branch, not the live demo:

```
set DATABASE_URL=postgresql://...      (PowerShell: $env:DATABASE_URL="postgresql://...")
pytest tests/test_postgres.py
```

## Deploy (Neon + Vercel)

Locally the app uses SQLite. When `DATABASE_URL` is set it uses Postgres instead, and writes nothing
to disk: uploaded PDFs are read in memory and only their text is stored.

1. **Neon**: create a project at https://neon.tech. On the project dashboard click **Connect** and copy the
   connection string (it ends with `?sslmode=require`). The pooled one (host contains `-pooler`) works.
2. **Copy your local data** (optional) into the empty Neon database:
   ```
   set DATABASE_URL=postgresql://...      (PowerShell: $env:DATABASE_URL="postgresql://...")
   python migrate_to_postgres.py                  # subjects, notes, MCQs, cards, explanations
   python migrate_to_postgres.py --with-progress  # the same, plus your own quiz answers and card schedule
   ```
   Then remove `DATABASE_URL` from that terminal (or close it), so local runs use SQLite again.
3. **Vercel**: push the repo to GitHub, then on https://vercel.com click **Add New... > Project**, import
   the repo and keep the defaults (Framework Preset: Other, no build command). `vercel.json` sends every
   request to `api/index.py`, which serves both the API and `static/`.
4. In the project's **Settings > Environment Variables**, add (for Production):

   | Name | Value |
   |------|-------|
   | `GEMINI_API_KEY` | your Gemini API key |
   | `GEMINI_MODEL` | e.g. `gemini-3.6-flash` |
   | `GEMINI_FALLBACKS` | e.g. `gemini-3.5-flash,gemini-3.5-flash-lite` |
   | `DATABASE_URL` | the Neon connection string |
   | `ADMIN_PASSCODE` | a long random passphrase (without it nobody can upload or edit) |

5. **Deploy** (Deployments > Redeploy after adding the variables). Tables are created on the first request.
6. Open the site, click **Admin** in the footer, enter the passcode and upload a PDF to check everything.

Limits to know: Vercel accepts request bodies up to 4.5 MB (hence the 4 MB PDF limit), and a request may
run for up to 60 seconds (`maxDuration` in `vercel.json`); first-time generation of a long PDF can come close.

## Run from the command line

```
python main.py lecture.pdf
python main.py --models
```

## Files

- `study_ai.py`: Gemini logic (prompt, retries, fallback models)
- `app.py`: FastAPI server
- `db.py`: storage, SQLite locally or Postgres when `DATABASE_URL` is set
- `migrate_to_postgres.py`: copy the local `notes.db` into Postgres
- `api/index.py`, `vercel.json`, `.vercelignore`: Vercel deployment
- `srs.py`: SM-2 spaced repetition math
- `quiz.py`: picks subject quiz questions, weak topics more often; plans AI practice
- `context.py`: picks the notes and PDF text about given topics (keeps prompts small)
- `measure_tokens.py`: runs every AI feature once on a PDF and prints the tokens used
- `rename_subject.py`: rename or merge a subject
- `static/`: frontend (HTML, CSS, JS)
- `main.py`: command-line version
- `tests/`: pytest tests
