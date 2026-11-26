"""SQLite storage for generated notes."""
import json
import sqlite3
from datetime import date, datetime, timezone

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER REFERENCES notes(id),
                front TEXT,
                back TEXT,
                subtopic TEXT,
                subject TEXT,
                ease REAL DEFAULT 2.5,
                interval_days INTEGER DEFAULT 0,
                repetitions INTEGER DEFAULT 0,
                due_date TEXT,       -- "YYYY-MM-DD", so text comparison = date comparison
                last_reviewed TEXT
            )
            """
        )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def subjects():
    """All subject names used by notes, sorted, without case duplicates."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT subject FROM notes WHERE subject != '' GROUP BY subject COLLATE NOCASE ORDER BY subject COLLATE NOCASE"
        ).fetchall()
    return [r["subject"] for r in rows]


def canonical_subject(subject):
    """Reuse an existing spelling if one matches case-insensitively.

    "discrete mathematics" becomes "Discrete Mathematics" if that already exists.
    """
    subject = (subject or "").strip()
    for existing in subjects():
        if existing.casefold() == subject.casefold():
            return existing
    return subject


def rename_subject(old, new):
    """Rename a subject everywhere (notes, attempts, cards), matching old case-insensitively.

    Use it to merge two subjects: rename_subject("discrete", "Discrete Mathematics").
    Returns how many rows changed in each table.
    """
    old, new = old.strip(), new.strip()
    if new.casefold() != old.casefold():  # a pure case fix keeps the new spelling
        new = canonical_subject(new)
    changed = {}
    with connect() as conn:
        for table in ("notes", "attempts", "cards"):
            cur = conn.execute(
                f"UPDATE {table} SET subject = ? WHERE subject = ? COLLATE NOCASE", (new, old)
            )
            changed[table] = cur.rowcount
    return changed


def save_note(data):
    """Save one generated result (and its flashcards, if any). Returns the new row id."""
    created_at = now()
    subject = canonical_subject(data.get("subject", ""))
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes (subject, topic, notes, mcqs, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                subject,
                data.get("topic", ""),
                json.dumps(data.get("notes", []), ensure_ascii=False),
                json.dumps(data.get("mcqs", []), ensure_ascii=False),
                created_at,
            ),
        )
        note_id = cur.lastrowid
        insert_cards(conn, note_id, subject, data.get("flashcards", []))
        return note_id


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


def delete_note(note_id):
    """Delete a note with its quiz attempts and flashcards. Returns False if it didn't exist.

    All three deletes run in one transaction ("with connect()"), so either everything
    is deleted or, if something fails, nothing is.
    """
    with connect() as conn:
        conn.execute("DELETE FROM attempts WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM cards WHERE note_id = ?", (note_id,))
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0


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


def mastery_score(correct, attempts):
    """Smoothed accuracy: (correct + 1) / (attempts + 2). Higher = better.

    With no data it starts at 0.5 ("don't know yet") and moves toward
    the real accuracy as more attempts come in.
    """
    return (correct + 1) / (attempts + 2)


def progress(subject=None):
    """One row per (subject, subtopic), lowest mastery first."""
    sql = """
        SELECT subject, subtopic,
               COUNT(*) AS attempts,
               SUM(correct) AS correct
        FROM attempts
    """
    params = ()
    if subject:
        sql += " WHERE subject = ? COLLATE NOCASE"
        params = (subject,)
    sql += " GROUP BY subject COLLATE NOCASE, subtopic COLLATE NOCASE"

    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    for r in rows:
        r["mastery"] = round(mastery_score(r["correct"], r["attempts"]), 3)

    # Lowest mastery first; with equal mastery, more attempts first.
    rows.sort(key=lambda r: (r["mastery"], -r["attempts"]))
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


# ---------- Flashcards ----------

def today():
    return date.today().isoformat()


def insert_cards(conn, note_id, subject, cards):
    """Insert flashcards for a note. New cards are due today."""
    conn.executemany(
        """INSERT INTO cards (note_id, front, back, subtopic, subject, due_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (note_id, c.get("front", ""), c.get("back", ""), c.get("subtopic", ""), subject, today())
            for c in cards
            if c.get("front") and c.get("back")  # skip broken cards from the model
        ],
    )


def save_cards(note_id, subject, cards):
    with connect() as conn:
        insert_cards(conn, note_id, subject, cards)


def count_cards(note_id):
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM cards WHERE note_id = ?", (note_id,)).fetchone()[0]


def get_card(card_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def due_cards(subject=None, on=None):
    """Cards due on the given day (default today) or earlier, oldest due first."""
    sql = "SELECT * FROM cards WHERE due_date <= ?"
    params = [on or today()]
    if subject:
        sql += " AND subject = ? COLLATE NOCASE"
        params.append(subject)
    sql += " ORDER BY due_date, id"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_card(card_id, ease, interval_days, repetitions, due_date):
    with connect() as conn:
        conn.execute(
            """UPDATE cards
               SET ease = ?, interval_days = ?, repetitions = ?, due_date = ?, last_reviewed = ?
               WHERE id = ?""",
            (ease, interval_days, repetitions, due_date, now(), card_id),
        )
