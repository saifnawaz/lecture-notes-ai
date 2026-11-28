"""Copy the local SQLite database into Postgres (DATABASE_URL), e.g. to fill the Neon demo.

    python migrate_to_postgres.py                  # shared content only
    python migrate_to_postgres.py --with-progress  # also your own quiz answers and card schedule
    python migrate_to_postgres.py --sqlite other.db

Copies subjects, notes (with MCQs and the PDFs' extracted text), flashcards, explanations,
"why was I wrong" answers and the AI answer cache. With --with-progress also your attempts and
card schedule; they stay under the owner id, so you see them after entering the admin passcode.
PDF files are not copied: production keeps only their text. Token usage history stays local.

Runs in one transaction and stops if the Postgres database already has notes.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

import db
from study_ai import extract_text

# table: columns to copy (ids included, so links between tables stay the same)
SHARED = {
    "subjects": ["id", "name", "created_at"],
    "notes": ["id", "subject", "topic", "notes", "mcqs", "created_at", "filename", "pdf_hash", "source_text"],
    "cards": ["id", "note_id", "front", "back", "subtopic", "subject", "due_date"],
    "explanations": ["subject", "subtopic", "data", "created_at"],
    "why_wrong": ["note_id", "question_index", "chosen", "text", "created_at"],
    "ai_cache": ["key", "feature", "data", "created_at"],
}
PROGRESS = {
    "attempts": ["id", "user_id", "note_id", "question_index", "subtopic", "subject", "chosen", "correct", "created_at"],
    "card_state": ["user_id", "card_id", "ease", "interval_days", "repetitions", "due_date", "last_reviewed"],
}
SERIAL_TABLES = ["subjects", "notes", "cards", "explanations", "attempts"]


def read_sqlite(path, tables):
    """Rows of each table from the local database (brought up to date first, like the app does)."""
    db.DATABASE_URL, db.DB_PATH = None, str(path)
    db.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return {t: [dict(r) for r in conn.execute(f"SELECT {', '.join(cols)} FROM {t}")]
                for t, cols in tables.items()}
    finally:
        conn.close()


def fill_missing_text(notes):
    """Notes made before text extraction only have the PDF file: extract its text now."""
    pdfs = {}
    with sqlite3.connect(db.DB_PATH) as conn:
        for note_id, pdf_path in conn.execute("SELECT id, pdf_path FROM notes WHERE source_text IS NULL"):
            pdfs[note_id] = pdf_path
    for note in notes:
        path = pdfs.get(note["id"])
        if note["source_text"] is None and path and Path(path).exists():
            note["source_text"] = extract_text(Path(path).read_bytes())
            print(f"  extracted text from {path}: {len(note['source_text'])} characters")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", default="notes.db", help="local database (default: notes.db)")
    parser.add_argument("--with-progress", action="store_true", help="also copy your attempts and card schedule")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL to the Postgres connection string first (see README, Deploy).")
    if not Path(args.sqlite).exists():
        sys.exit(f"{args.sqlite} not found.")

    tables = {**SHARED, **(PROGRESS if args.with_progress else {})}
    rows = read_sqlite(args.sqlite, tables)
    fill_missing_text(rows["notes"])

    db.DATABASE_URL = url
    db.init_db()
    with db.connect() as conn:
        if conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"]:
            sys.exit("Postgres already has notes. Use an empty database (or a new Neon branch).")
        for table, cols in tables.items():
            marks = ", ".join("?" for _ in cols)
            conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})",
                             [tuple(r[c] for c in cols) for r in rows[table]])
            print(f"{table}: {len(rows[table])} rows")
        # Explicit ids were inserted, so move each id counter past the highest one.
        for table in SERIAL_TABLES:
            conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                         f"COALESCE(MAX(id), 0) + 1, false) FROM {table}")
    print("Done." + ("" if args.with_progress else " (Your own progress was not copied: add --with-progress.)"))


if __name__ == "__main__":
    main()
