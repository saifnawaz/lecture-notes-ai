"""SQLite storage for generated notes."""
import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "notes.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["topic"]
    return conn


def init_db():
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                topic TEXT,
                notes TEXT,
                mcqs TEXT,
                created_at TEXT
            )
            """
        )


def save_note(data):
    """Save one generated result. Returns the new row id."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes (subject, topic, notes, mcqs, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                data.get("subject", ""),
                data.get("topic", ""),
                json.dumps(data.get("notes", []), ensure_ascii=False),
                json.dumps(data.get("mcqs", []), ensure_ascii=False),
                created_at,
            ),
        )
        return cur.lastrowid


def list_notes():
    """Short list for the sidebar: no notes/mcqs bodies."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, subject, topic, created_at FROM notes ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_note(note_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return None
    note = dict(row)
    note["notes"] = json.loads(note["notes"])
    note["mcqs"] = json.loads(note["mcqs"])
    return note
