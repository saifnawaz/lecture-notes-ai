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
