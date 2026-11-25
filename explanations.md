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
