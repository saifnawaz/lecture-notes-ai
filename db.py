"""SQLite storage for generated notes."""
import json
import sqlite3
from datetime import date, datetime, timezone

DB_PATH = "notes.db"
UNCATEGORIZED = "Uncategorized"


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE COLLATE NOCASE,  -- "math" and "Math" count as the same name
                created_at TEXT
            )
            """
        )
        migrate(conn)


def migrate(conn):
    """Bring an older notes.db up to date. Safe to run every time."""
    # New note columns for saved PDFs (ALTER TABLE has no "IF NOT EXISTS").
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
    for column in ("filename", "pdf_path", "pdf_hash"):
        if column not in columns:
            conn.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")

    # Every note belongs to a subject now.
    for table in ("notes", "attempts", "cards"):
        conn.execute(
            f"UPDATE {table} SET subject = ? WHERE subject IS NULL OR TRIM(subject) = ''",
            (UNCATEGORIZED,),
        )

    # Register subjects that so far only existed as text on notes.
    conn.execute(
        """INSERT OR IGNORE INTO subjects (name, created_at)
           SELECT subject, MIN(created_at) FROM notes GROUP BY subject COLLATE NOCASE"""
    )
    # Use the registered spelling everywhere ("math" -> "Math"). "name = x" ignores case
    # because the name column is COLLATE NOCASE.
    for table in ("notes", "attempts", "cards"):
        conn.execute(
            f"""UPDATE {table} SET subject = (SELECT name FROM subjects WHERE name = {table}.subject)
                WHERE EXISTS (SELECT 1 FROM subjects WHERE name = {table}.subject)"""
        )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Subjects ----------

def subject_names():
    """All subject names, sorted."""
    with connect() as conn:
        rows = conn.execute("SELECT name FROM subjects ORDER BY name COLLATE NOCASE").fetchall()
    return [r["name"] for r in rows]


def ensure_subject(conn, name):
    """Create the subject if it doesn't exist yet. Returns its id."""
    conn.execute("INSERT OR IGNORE INTO subjects (name, created_at) VALUES (?, ?)", (name, now()))
    return conn.execute("SELECT id FROM subjects WHERE name = ?", (name,)).fetchone()["id"]


def create_subject(name):
    """Create a subject (or find the existing one with that name). Returns its id."""
    name = canonical_subject(name)
    with connect() as conn:
        return ensure_subject(conn, name)


def subject_summaries(subject_id=None):
    """Per subject: number of PDFs, cards due today, quiz attempts/correct and accuracy %."""
    sql = """
        SELECT s.id, s.name,
               (SELECT COUNT(*) FROM notes n WHERE n.subject = s.name COLLATE NOCASE) AS pdf_count,
               (SELECT COUNT(*) FROM cards c WHERE c.subject = s.name COLLATE NOCASE AND c.due_date <= ?) AS due_today,
               (SELECT COUNT(*) FROM attempts a WHERE a.subject = s.name COLLATE NOCASE) AS attempts,
               (SELECT COALESCE(SUM(correct), 0) FROM attempts a WHERE a.subject = s.name COLLATE NOCASE) AS correct
        FROM subjects s
    """
    params = [today()]
    if subject_id is not None:
        sql += " WHERE s.id = ?"
        params.append(subject_id)
    sql += " ORDER BY s.name COLLATE NOCASE"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["accuracy"] = round(100 * r["correct"] / r["attempts"]) if r["attempts"] else None
    return rows


def get_subject(subject_id):
    rows = subject_summaries(subject_id)
    return rows[0] if rows else None


def canonical_subject(subject):
    """Reuse an existing spelling if one matches case-insensitively.

    "discrete mathematics" becomes "Discrete Mathematics" if that already exists.
    """
    subject = (subject or "").strip()
    for existing in subject_names():
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
        # Subjects table: merge into the existing row, or rename the old one.
        target = conn.execute("SELECT id FROM subjects WHERE name = ?", (new,)).fetchone()
        source = conn.execute("SELECT id FROM subjects WHERE name = ?", (old,)).fetchone()
        if source and target and source["id"] != target["id"]:
            conn.execute("DELETE FROM subjects WHERE id = ?", (source["id"],))
        elif source:
            conn.execute("UPDATE subjects SET name = ? WHERE id = ?", (new, source["id"]))
    return changed


def save_note(data):
    """Save one generated result (and its flashcards, if any). Returns the new row id."""
    created_at = now()
    subject = canonical_subject(data.get("subject", "")) or UNCATEGORIZED
    with connect() as conn:
        ensure_subject(conn, subject)
        cur = conn.execute(
            """INSERT INTO notes (subject, topic, notes, mcqs, created_at, filename, pdf_path, pdf_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                subject,
                data.get("topic", ""),
                json.dumps(data.get("notes", []), ensure_ascii=False),
                json.dumps(data.get("mcqs", []), ensure_ascii=False),
                created_at,
                data.get("filename"),
                data.get("pdf_path"),
                data.get("pdf_hash"),
            ),
        )
        note_id = cur.lastrowid
        insert_cards(conn, note_id, subject, data.get("flashcards", []))
        return note_id


def list_notes(subject=None):
    """Short list of notes (newest first), without the notes/mcqs bodies."""
    sql = """
        SELECT id, subject, topic, filename, created_at,
               json_array_length(mcqs) AS mcq_count,
               (SELECT COUNT(*) FROM cards WHERE cards.note_id = notes.id) AS card_count
        FROM notes
    """
    params = ()
    if subject:
        sql += " WHERE subject = ? COLLATE NOCASE"
        params = (subject,)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
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


def subject_questions(subject):
    """Every MCQ from every note in a subject, with where it came from."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, topic, mcqs FROM notes WHERE subject = ? COLLATE NOCASE ORDER BY id", (subject,)
        ).fetchall()
    questions = []
    for row in rows:
        for i, q in enumerate(json.loads(row["mcqs"])):
            questions.append({
                **q,
                "note_id": row["id"],
                "question_index": i,
                "note_topic": row["topic"],
                "subtopic": q.get("subtopic") or row["topic"],  # same rule as attempts
            })
    return questions


def add_mcqs(note_id, new_mcqs):
    """Append MCQs to a note. Old questions keep their index, so saved attempts stay valid."""
    with connect() as conn:
        row = conn.execute("SELECT mcqs FROM notes WHERE id = ?", (note_id,)).fetchone()
        mcqs = json.loads(row["mcqs"]) + list(new_mcqs)
        conn.execute(
            "UPDATE notes SET mcqs = ? WHERE id = ?",
            (json.dumps(mcqs, ensure_ascii=False), note_id),
        )
        return len(mcqs)


def find_note_by_hash(subject, pdf_hash):
    """A note in this subject made from the same PDF file, or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, topic FROM notes WHERE subject = ? COLLATE NOCASE AND pdf_hash = ? LIMIT 1",
            (subject, pdf_hash),
        ).fetchone()
    return dict(row) if row else None


def count_notes_with_pdf(pdf_path):
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM notes WHERE pdf_path = ?", (pdf_path,)).fetchone()[0]


def delete_note(note_id):
    """Delete a note with its quiz attempts and flashcards.

    Returns the deleted note row (so the caller can remove its PDF file), or None.
    All deletes run in one transaction ("with connect()"), so either everything
    is deleted or, if something fails, nothing is.
    """
    with connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM attempts WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM cards WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return dict(row)


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
    """One row per (subject, subtopic) with raw accuracy %, lowest mastery first.

    The smoothed mastery score is only used for sorting; it is not returned.
    """
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

    # Lowest mastery first; with equal mastery, more attempts first.
    rows.sort(key=lambda r: (mastery_score(r["correct"], r["attempts"]), -r["attempts"]))

    for r in rows:
        r["accuracy"] = round(100 * r["correct"] / r["attempts"])
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


def note_cards(note_id):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM cards WHERE note_id = ? ORDER BY id", (note_id,)).fetchall()
    return [dict(r) for r in rows]


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
