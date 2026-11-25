# Explanations

Short notes on each important step of turning the CLI into a web app.

## 1. Ignore the database file
Added `*.db` to `.gitignore`. Saved notes will live in a SQLite file (`notes.db`)
on your computer only. Like `.env` and PDFs, it should never go into git.

## 2. Move Gemini logic into `study_ai.py`
All the Gemini code (prompt, upload, retry + fallback models) now lives in
`study_ai.py`. `main.py` just imports it, so the CLI and the web server share
one copy of the logic. Two changes:
- Instead of `sys.exit()` when everything fails, it raises `GenerationError`.
  A web server must not quit on one failed request; it should send an error back.
- The prompt now also asks for a `subject` (e.g. "Discrete Mathematics"), which we save in the database.

## 3. New packages in `requirements.txt`
- `fastapi`: the web framework that defines our URLs (routes) like `/generate`.
- `uvicorn`: the server program that actually runs the FastAPI app.
- `python-multipart`: lets FastAPI read file uploads from a form.

## 4. SQLite storage in `db.py`
SQLite is a database stored in one file (`notes.db`), built into Python, so there's no server to install.
- `init_db()` creates the `notes` table if it doesn't exist yet.
- `save_note()` inserts a row. Lists (notes, mcqs) are stored as JSON text,
  because a SQLite column holds plain text, not Python lists.
- `list_notes()` / `get_note()` read rows back (and turn the JSON text back into lists).
- The `?` placeholders in SQL keep user data separate from the query (prevents SQL injection).

## 5. FastAPI backend in `app.py`
- `@app.post("/generate")` is a route: when the browser POSTs a PDF to `/generate`, this function runs.
  It checks the file is a PDF (max 20 MB), writes a temporary copy (Gemini needs a file on disk),
  calls `generate()`, deletes the temp file in `finally` (runs even on errors), saves to SQLite and returns the JSON.
- `GET /notes` lists saved notes, `GET /notes/{id}` returns one (404 if missing).
- `HTTPException(400, ...)` sends an error message back to the browser instead of crashing.
- `lifespan` runs `init_db()` once when the server starts.
- The route is a plain `def` (not `async def`), so FastAPI runs the slow Gemini call
  in a background thread and the server stays responsive.
- Try it: run `uvicorn app:app --reload` and open http://127.0.0.1:8000/docs for an auto-generated test page.

## 6. Frontend page (`static/index.html`)
Plain HTML: a sidebar of saved notes, an upload form, and a notes + quiz section
(hidden until a note is opened). `app.mount("/", StaticFiles(...))` in `app.py` makes FastAPI
serve the `static/` folder, so http://127.0.0.1:8000/ shows the page. It's added last so it
doesn't hide the API routes.

## 7. Styling (`static/style.css`)
Colors are defined once as CSS variables (`--accent`, `--correct`, ...) at the top and reused.
The page uses a two-column grid (sidebar + main) that becomes one column on small screens.
Quiz options get `.correct` (green) or `.wrong` (red) classes after you click.

## 8. Frontend logic (`static/app.js`)
- `fetch()` is how the browser talks to our FastAPI routes (`/generate`, `/notes`, `/notes/{id}`).
- Upload: the form's `submit` event builds a `FormData` (file + subject), POSTs it,
  shows "Generating..." and disables the button until the answer arrives.
- Sidebar: `loadNotesList()` fills the list of saved notes; clicking one opens it.
- Quiz: each option is a button. On click, the question locks, the right answer turns green,
  a wrong pick turns red, the explanation appears and the score updates. "Retry quiz" redraws it.
- AI text is inserted with `textContent` (not `innerHTML`), so it is always shown as plain text
  and can never run as HTML or script.

## 9. README
`README.md` explains setup (venv, install, `.env`) and how to run the web app and the CLI.

---

# Phase 2: quiz tracking and weak topics

## 10. `attempts` table and weakness math in `db.py`
- New table `attempts`: one row each time you answer a quiz question
  (which note, which question, its subtopic/subject, what you chose, right or wrong).
  SQLite has no true/false type, so `correct` is stored as 1 or 0.
- `weakness()` groups attempts by subject + subtopic with SQL `GROUP BY`, counts attempts
  and wrong answers, then adds accuracy % and a weakness score. Sorted weakest first.
- `note_accuracy()` gives your accuracy on one note (or `None` if you never tried its quiz).
- `init_db()` uses `CREATE TABLE IF NOT EXISTS`, so your existing `notes.db` just gets the new table.

### Why the score is `(wrong + 1) / (attempts + 2)`
The simple error rate is `wrong / attempts`. With very few answers it gives extreme results:
- 1 question, got it right → 0% wrong → "you've mastered this topic". Too confident!
- 1 question, got it wrong → 100% wrong → "this is your worst topic ever". Also too harsh.

Adding +1 to wrong and +2 to attempts is like pretending you already answered
**two extra questions: one right and one wrong**. So every topic starts at 50% ("we don't know yet"):

| Answers           | Plain error rate | Smoothed score       |
|-------------------|------------------|----------------------|
| 1 right           | 0%               | (0+1)/(1+2) = 33%    |
| 1 wrong           | 100%             | (1+1)/(1+2) = 67%    |
| 10 right          | 0%               | (0+1)/(10+2) = 8%    |
| 10 wrong          | 100%             | (10+1)/(10+2) = 92%  |

With few answers the score stays near the middle. With many answers the two
pretend answers hardly matter and the score gets close to your real error rate.
So a topic only shows up as "very weak" or "very strong" once there is real evidence.
(This trick is called *Laplace smoothing*.)

## 11. API routes: `POST /attempts`, `GET /weakness`
- `Attempt` is a Pydantic model: it describes the JSON the browser must send
  (`note_id`, `question_index`, `chosen`). FastAPI checks the types automatically and returns 422 if they're wrong.
- The server looks up the saved note to fill in subtopic, subject and whether the answer was correct.
  We never trust the browser to say "I was right": it could be buggy or edited.
- `GET /weakness?subject=Math` filters by subject; without `?subject=` you get all subjects.
- `GET /notes/{id}` now also returns `accuracy` (or `null` if you haven't tried that quiz).

## 12. Tests (`tests/test_attempts.py`)
Run them with `pytest` (from the project folder, venv active).
- A *fixture* is setup code pytest runs before a test. `client` points `db.DB_PATH` to a
  temporary file, so tests never touch your real `notes.db`, and gives a `TestClient`
  that calls the API without starting a real server.
- Tests cover the score math (0 attempts → 0.5, 1 right → 1/3, ...), right/wrong answers,
  bad input (404/400), weakest-first ordering, the subject filter and per-note accuracy.
- `pytest.approx` compares decimals safely (1/3 can't be stored exactly).
- `pytest.ini` tells pytest where the tests are and lets them import `app`/`db`.

## 13. Frontend saves every answer
When you click an option, `saveAttempt()` sends `{note_id, question_index, chosen}` to
`POST /attempts` as JSON. It runs in the background (`async`), so the quiz doesn't wait for it.
If saving fails, the quiz still works; the error only shows in the browser console (F12).

## 14. "Weak topics" page
- New sidebar button opens a section listing every subtopic, weakest first (the server already sorts).
- Each row: subtopic name + subject, a bar whose width is your accuracy %, and "accuracy · attempts".
- Bar color comes from the weakness score: red if > 0.5, yellow if 0.3–0.5, green if < 0.3.
  Thanks to smoothing, a topic answered right only once is yellow, not green; it turns green after a few right answers.
- The subject dropdown is filled from your attempts; changing it calls `/weakness?subject=...`.
- `showView()` is a small helper that shows one section and hides the other two.

## 15. Accuracy on each note page
`GET /notes/{id}` returns `accuracy`, and the note page shows it as a small badge,
e.g. "Your accuracy on this note: 60% (3/5 answers)". It's hidden if you never took that quiz.
After each answer is saved, the page re-fetches the note so the badge updates live.
It counts every answer, including retries.

## 16. README
Added the new routes, a "Weak topics" section and how to run the tests (`pytest`).
