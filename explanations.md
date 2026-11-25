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
