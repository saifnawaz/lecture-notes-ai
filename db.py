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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER REFERENCES notes(id),
                question_index INTEGER,
                subtopic TEXT,
                subject TEXT,
                chosen INTEGER,
                correct INTEGER,  -- SQLite has no bool type: 1 = true, 0 = false
                created_at TEXT
            )
            """
        )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_note(data):
    """Save one generated result. Returns the new row id."""
    created_at = now()
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


# ---------- Quiz attempts ----------

def save_attempt(note_id, question_index, subtopic, subject, chosen, correct):
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO attempts
               (note_id, question_index, subtopic, subject, chosen, correct, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (note_id, question_index, subtopic, subject, chosen, int(correct), now()),
        )
        return cur.lastrowid


def weakness_score(wrong, attempts):
    """Smoothed error rate: (wrong + 1) / (attempts + 2).

    With no data it starts at 0.5 ("don't know yet") and moves toward
    the real error rate as more attempts come in.
    """
    return (wrong + 1) / (attempts + 2)


def weakness(subject=None):
    """One row per (subject, subtopic), weakest first."""
    sql = """
        SELECT subject, subtopic,
               COUNT(*) AS attempts,
               SUM(correct = 0) AS wrong
        FROM attempts
    """
    params = ()
    if subject:
        sql += " WHERE subject = ?"
        params = (subject,)
    sql += " GROUP BY subject, subtopic"

    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    for r in rows:
        r["accuracy"] = round(100 * (r["attempts"] - r["wrong"]) / r["attempts"])
        r["score"] = round(weakness_score(r["wrong"], r["attempts"]), 3)

    # Weakest (highest score) first; with equal scores, more attempts first.
    rows.sort(key=lambda r: (-r["score"], -r["attempts"]))
    return rows



def note_accuracy(note_id):
    """Accuracy on one note, or None if it has no attempts yet."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS attempts, SUM(correct) AS correct FROM attempts WHERE note_id = ?",
            (note_id,),
        ).fetchone()
    if row["attempts"] == 0:
        return None
    return {
        "attempts": row["attempts"],
        "correct": row["correct"],
        "accuracy": round(100 * row["correct"] / row["attempts"]),
    }
