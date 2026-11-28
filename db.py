"""Storage: SQLite locally, Postgres when DATABASE_URL is set.

All SQL is written once with "?" placeholders and portable syntax. The few differences
(placeholders, new row ids, schema) are handled here, in connect() and init_db().
Case-insensitive matching uses LOWER(a) = LOWER(b), which works on both.

Shared demo content: subjects, notes (with MCQs), cards, explanations, why_wrong, ai_cache.
Per-user data (user_id = a guest's cookie id, or OWNER): attempts, card_state, ai_quota.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")  # set = Postgres (production); empty = SQLite file
DB_PATH = "notes.db"
UNCATEGORIZED = "Uncategorized"
OWNER = "owner"  # the admin's own progress (and all progress from before guest users existed)

_pool = None
_pool_url = None


def is_postgres():
    return bool(DATABASE_URL)


class Conn:
    """A connection that takes "?" placeholders on both databases and returns dict-like rows."""

    def __init__(self, raw, pg):
        self.raw, self.pg = raw, pg

    def sql(self, text):
        return text.replace("?", "%s") if self.pg else text

    def execute(self, text, params=()):
        return self.raw.execute(self.sql(text), params)

    def executemany(self, text, rows):
        if not rows:
            return
        if self.pg:
            with self.raw.cursor() as cur:
                cur.executemany(self.sql(text), rows)
        else:
            self.raw.executemany(text, rows)

    def insert(self, text, params=()):
        """Run an INSERT and return the new row's id."""
        if self.pg:
            return self.execute(text + " RETURNING id", params).fetchone()["id"]
        return self.execute(text, params).lastrowid


def pool():
    """One small Postgres connection pool per process (reused across requests on Vercel)."""
    global _pool, _pool_url
    if _pool is None or _pool_url != DATABASE_URL:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        # prepare_threshold=None: no server-side prepared statements, so Neon's pooled
        # (PgBouncer) connection string works too.
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=4, open=True, check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )
        _pool_url = DATABASE_URL
    return _pool


@contextmanager
def connect():
    """One transaction: committed at the end of the "with" block, rolled back on an error."""
    if is_postgres():
        with pool().connection() as raw:
            yield Conn(raw, True)
        return
    raw = sqlite3.connect(DB_PATH)
    raw.row_factory = sqlite3.Row  # rows behave like dicts: row["topic"]
    try:
        with raw:
            yield Conn(raw, False)
    finally:
        raw.close()


# ---------- Schema ----------

SQLITE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT, topic TEXT, notes TEXT, mcqs TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER REFERENCES notes(id),
        question_index INTEGER, subtopic TEXT, subject TEXT, chosen INTEGER,
        correct INTEGER,  -- 1 = true, 0 = false
        created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER REFERENCES notes(id),
        front TEXT, back TEXT, subtopic TEXT, subject TEXT,
        ease REAL DEFAULT 2.5, interval_days INTEGER DEFAULT 0, repetitions INTEGER DEFAULT 0,
        due_date TEXT, last_reviewed TEXT)  -- schedule columns: only read by migrate() now""",
    """CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE COLLATE NOCASE,  -- "math" and "Math" count as the same name
        created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS explanations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT COLLATE NOCASE, subtopic TEXT COLLATE NOCASE,
        data TEXT,  -- JSON: explanation, example, key_points
        created_at TEXT, UNIQUE (subject, subtopic))""",
    """CREATE TABLE IF NOT EXISTS why_wrong (
        note_id INTEGER REFERENCES notes(id), question_index INTEGER, chosen INTEGER,
        text TEXT, created_at TEXT, PRIMARY KEY (note_id, question_index, chosen))""",
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feature TEXT, model TEXT, prompt_tokens INTEGER, output_tokens INTEGER,
        thinking_tokens INTEGER, total_tokens INTEGER, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ai_cache (
        key TEXT PRIMARY KEY,  -- sha256 of feature + input
        feature TEXT, data TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS card_state (
        user_id TEXT, card_id INTEGER, ease REAL, interval_days INTEGER, repetitions INTEGER,
        due_date TEXT, last_reviewed TEXT, PRIMARY KEY (user_id, card_id))""",
    """CREATE TABLE IF NOT EXISTS ai_quota (
        user_id TEXT, day TEXT, calls INTEGER, PRIMARY KEY (user_id, day))""",
]

POSTGRES_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS subjects (
        id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, created_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS subjects_name_ci ON subjects (LOWER(name))",
    """CREATE TABLE IF NOT EXISTS notes (
        id BIGSERIAL PRIMARY KEY, subject TEXT, topic TEXT, notes TEXT, mcqs TEXT, created_at TEXT,
        filename TEXT, pdf_path TEXT, pdf_hash TEXT, source_text TEXT)""",
    """CREATE TABLE IF NOT EXISTS attempts (
        id BIGSERIAL PRIMARY KEY, user_id TEXT, note_id BIGINT, question_index INTEGER,
        subtopic TEXT, subject TEXT, chosen INTEGER, correct INTEGER, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS attempts_user ON attempts (user_id)",
    """CREATE TABLE IF NOT EXISTS cards (
        id BIGSERIAL PRIMARY KEY, note_id BIGINT, front TEXT, back TEXT, subtopic TEXT, subject TEXT,
        ease DOUBLE PRECISION DEFAULT 2.5, interval_days INTEGER DEFAULT 0, repetitions INTEGER DEFAULT 0,
        due_date TEXT, last_reviewed TEXT)""",
    "CREATE INDEX IF NOT EXISTS cards_note ON cards (note_id)",
    """CREATE TABLE IF NOT EXISTS card_state (
        user_id TEXT, card_id BIGINT, ease DOUBLE PRECISION, interval_days INTEGER, repetitions INTEGER,
        due_date TEXT, last_reviewed TEXT, PRIMARY KEY (user_id, card_id))""",
    """CREATE TABLE IF NOT EXISTS explanations (
        id BIGSERIAL PRIMARY KEY, subject TEXT, subtopic TEXT, data TEXT, created_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS explanations_topic_ci ON explanations (LOWER(subject), LOWER(subtopic))",
    """CREATE TABLE IF NOT EXISTS why_wrong (
        note_id BIGINT, question_index INTEGER, chosen INTEGER, text TEXT, created_at TEXT,
        PRIMARY KEY (note_id, question_index, chosen))""",
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id BIGSERIAL PRIMARY KEY, feature TEXT, model TEXT, prompt_tokens INTEGER, output_tokens INTEGER,
        thinking_tokens INTEGER, total_tokens INTEGER, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ai_cache (
        key TEXT PRIMARY KEY, feature TEXT, data TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ai_quota (
        user_id TEXT, day TEXT, calls INTEGER, PRIMARY KEY (user_id, day))""",
]


def init_db():
    with connect() as conn:
        for statement in POSTGRES_SCHEMA if conn.pg else SQLITE_SCHEMA:
            conn.execute(statement)
        if not conn.pg:
            migrate(conn)


def migrate(conn):
    """Bring an older SQLite notes.db up to date. Safe to run every time.

    (Postgres databases are created with the current schema and filled by migrate_to_postgres.py.)
    """
    # ALTER TABLE has no "IF NOT EXISTS".
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
    # source_text: the PDF's extracted text. NULL = not tried yet, "" = no usable text (scanned PDF).
    for column in ("filename", "pdf_path", "pdf_hash", "source_text"):
        if column not in columns:
            conn.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")
    if "user_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(attempts)")}:
        conn.execute("ALTER TABLE attempts ADD COLUMN user_id TEXT")

    # Progress from before guest users belongs to the owner: answers, and reviewed cards' schedules.
    conn.execute("UPDATE attempts SET user_id = ? WHERE user_id IS NULL", (OWNER,))
    conn.execute(
        """INSERT INTO card_state (user_id, card_id, ease, interval_days, repetitions, due_date, last_reviewed)
           SELECT ?, id, ease, interval_days, repetitions, due_date, last_reviewed
           FROM cards WHERE last_reviewed IS NOT NULL
           ON CONFLICT DO NOTHING""",
        (OWNER,),
    )

    # Every note belongs to a subject.
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
    # Use the registered spelling everywhere ("math" -> "Math").
    for table in ("notes", "attempts", "cards"):
        conn.execute(
            f"""UPDATE {table} SET subject = (SELECT name FROM subjects WHERE name = {table}.subject)
                WHERE EXISTS (SELECT 1 FROM subjects WHERE name = {table}.subject)"""
        )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_day(timestamp):
    """The local calendar day ("YYYY-MM-DD") of a stored UTC timestamp."""
    return datetime.fromisoformat(timestamp).astimezone().date().isoformat()


# ---------- Subjects ----------

def subject_names():
    """All subject names, sorted."""
    with connect() as conn:
        rows = conn.execute("SELECT name FROM subjects ORDER BY LOWER(name)").fetchall()
    return [r["name"] for r in rows]


def ensure_subject(conn, name):
    """Create the subject if it doesn't exist yet. Returns its id."""
    conn.execute("INSERT INTO subjects (name, created_at) VALUES (?, ?) ON CONFLICT DO NOTHING", (name, now()))
    return conn.execute("SELECT id FROM subjects WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()["id"]


def create_subject(name):
    """Create a subject (or find the existing one with that name). Returns its id."""
    name = canonical_subject(name)
    with connect() as conn:
        return ensure_subject(conn, name)


def subject_summaries(subject_id=None, user_id=OWNER):
    """Per subject: number of PDFs, this user's cards due today, quiz attempts/correct and accuracy %."""
    sql = """
        SELECT s.id, s.name,
               (SELECT COUNT(*) FROM notes n WHERE LOWER(n.subject) = LOWER(s.name)) AS pdf_count,
               (SELECT COUNT(*) FROM cards c
                  LEFT JOIN card_state cs ON cs.card_id = c.id AND cs.user_id = ?
                  WHERE LOWER(c.subject) = LOWER(s.name) AND (cs.due_date IS NULL OR cs.due_date <= ?)) AS due_today,
               (SELECT COUNT(*) FROM attempts a
                  WHERE a.user_id = ? AND LOWER(a.subject) = LOWER(s.name)) AS attempts,
               (SELECT COALESCE(SUM(correct), 0) FROM attempts a
                  WHERE a.user_id = ? AND LOWER(a.subject) = LOWER(s.name)) AS correct
        FROM subjects s
    """
    params = [user_id, today(), user_id, user_id]
    if subject_id is not None:
        sql += " WHERE s.id = ?"
        params.append(subject_id)
    sql += " ORDER BY LOWER(s.name)"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["correct"] = int(r["correct"])
        r["accuracy"] = round(100 * r["correct"] / r["attempts"]) if r["attempts"] else None
    return rows


def get_subject(subject_id, user_id=OWNER):
    rows = subject_summaries(subject_id, user_id)
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
    """Rename a subject everywhere (notes, attempts, cards, explanations), matching old case-insensitively.

    Use it to merge two subjects: rename_subject("discrete", "Discrete Mathematics").
    Returns how many rows changed in each table.
    """
    old, new = old.strip(), new.strip()
    if new.casefold() != old.casefold():  # a pure case fix keeps the new spelling
        new = canonical_subject(new)
    changed = {}
    with connect() as conn:
        if new.casefold() != old.casefold():
            # Merging: a topic explained in both subjects keeps the target's explanation.
            conn.execute(
                """DELETE FROM explanations WHERE LOWER(subject) = LOWER(?) AND LOWER(subtopic) IN
                   (SELECT LOWER(subtopic) FROM explanations WHERE LOWER(subject) = LOWER(?))""",
                (old, new),
            )
        for table in ("notes", "attempts", "cards", "explanations"):
            cur = conn.execute(f"UPDATE {table} SET subject = ? WHERE LOWER(subject) = LOWER(?)", (new, old))
            changed[table] = cur.rowcount
        # Subjects table: merge into the existing row, or rename the old one.
        target = conn.execute("SELECT id FROM subjects WHERE LOWER(name) = LOWER(?)", (new,)).fetchone()
        source = conn.execute("SELECT id FROM subjects WHERE LOWER(name) = LOWER(?)", (old,)).fetchone()
        if source and target and source["id"] != target["id"]:
            conn.execute("DELETE FROM subjects WHERE id = ?", (source["id"],))
        elif source:
            conn.execute("UPDATE subjects SET name = ? WHERE id = ?", (new, source["id"]))
    return changed


# ---------- Notes ----------

def save_note(data):
    """Save one generated result (and its flashcards, if any). Returns the new row id."""
    subject = canonical_subject(data.get("subject", "")) or UNCATEGORIZED
    with connect() as conn:
        ensure_subject(conn, subject)
        note_id = conn.insert(
            """INSERT INTO notes (subject, topic, notes, mcqs, created_at, filename, pdf_path, pdf_hash, source_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                subject,
                data.get("topic", ""),
                json.dumps(data.get("notes", []), ensure_ascii=False),
                json.dumps(data.get("mcqs", []), ensure_ascii=False),
                now(),
                data.get("filename"),
                data.get("pdf_path"),
                data.get("pdf_hash"),
                data.get("source_text"),
            ),
        )
        insert_cards(conn, note_id, subject, data.get("flashcards", []))
        return note_id


def list_notes(subject=None):
    """Short list of notes (newest first), without the notes/mcqs bodies."""
    sql = """
        SELECT id, subject, topic, filename, created_at, mcqs,
               (SELECT COUNT(*) FROM cards WHERE cards.note_id = notes.id) AS card_count
        FROM notes
    """
    params = ()
    if subject:
        sql += " WHERE LOWER(subject) = LOWER(?)"
        params = (subject,)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["mcq_count"] = len(json.loads(r.pop("mcqs") or "[]"))
    return rows


def get_note(note_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return None
    note = dict(row)
    note["notes"] = json.loads(note["notes"])
    note["mcqs"] = json.loads(note["mcqs"])
    return note


def subject_notes(subject):
    """All notes of a subject (id, topic, notes list, source_text), oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, topic, notes, source_text FROM notes WHERE LOWER(subject) = LOWER(?) ORDER BY id",
            (subject,),
        ).fetchall()
    return [{**dict(r), "notes": json.loads(r["notes"])} for r in rows]


def subject_questions(subject):
    """Every MCQ from every note in a subject, with where it came from."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, topic, mcqs FROM notes WHERE LOWER(subject) = LOWER(?) ORDER BY id", (subject,)
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


def set_source_text(note_id, text):
    with connect() as conn:
        conn.execute("UPDATE notes SET source_text = ? WHERE id = ?", (text, note_id))


def find_note_by_hash(subject, pdf_hash):
    """A note in this subject made from the same PDF file, or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, topic FROM notes WHERE LOWER(subject) = LOWER(?) AND pdf_hash = ? LIMIT 1",
            (subject, pdf_hash),
        ).fetchone()
    return dict(row) if row else None


def count_notes_with_pdf(pdf_path):
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM notes WHERE pdf_path = ?", (pdf_path,)).fetchone()["n"]


def delete_note(note_id):
    """Delete a note with everyone's quiz attempts and card schedules for it, and its flashcards.

    Returns the deleted note row (so the caller can remove its PDF file), or None.
    All deletes run in one transaction, so either everything is deleted or nothing is.
    """
    with connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM attempts WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM card_state WHERE card_id IN (SELECT id FROM cards WHERE note_id = ?)", (note_id,))
        conn.execute("DELETE FROM cards WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM why_wrong WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return dict(row)


# ---------- Quiz attempts (per user) ----------

def save_attempt(note_id, question_index, subtopic, subject, chosen, correct, user_id=OWNER):
    with connect() as conn:
        return conn.insert(
            """INSERT INTO attempts
               (user_id, note_id, question_index, subtopic, subject, chosen, correct, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, note_id, question_index, subtopic, subject, chosen, int(correct), now()),
        )


def mastery_score(correct, attempts):
    """Smoothed accuracy: (correct + 1) / (attempts + 2). Higher = better.

    With no data it starts at 0.5 ("don't know yet") and moves toward
    the real accuracy as more attempts come in.
    """
    return (correct + 1) / (attempts + 2)


def progress(subject=None, user_id=OWNER):
    """One row per (subject, subtopic) with raw accuracy %, lowest mastery first.

    The smoothed mastery score is only used for sorting; it is not returned.
    """
    sql = """
        SELECT MIN(subject) AS subject, MIN(subtopic) AS subtopic,
               COUNT(*) AS attempts, SUM(correct) AS correct
        FROM attempts WHERE user_id = ?
    """
    params = [user_id]
    if subject:
        sql += " AND LOWER(subject) = LOWER(?)"
        params.append(subject)
    sql += " GROUP BY LOWER(subject), LOWER(subtopic)"

    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["correct"] = int(r["correct"])

    # Lowest mastery first; with equal mastery, more attempts first.
    rows.sort(key=lambda r: (mastery_score(r["correct"], r["attempts"]), -r["attempts"]))

    for r in rows:
        r["accuracy"] = round(100 * r["correct"] / r["attempts"])
    return rows


def last_score(user_id=OWNER):
    """Score on the most recent (local) day with quiz answers, or None if there are none."""
    with connect() as conn:
        rows = conn.execute("SELECT correct, created_at FROM attempts WHERE user_id = ?", (user_id,)).fetchall()
    if not rows:
        return None
    days = [(local_day(r["created_at"]), r["correct"]) for r in rows]
    last = max(day for day, _ in days)
    marks = [correct for day, correct in days if day == last]
    correct = int(sum(marks))
    return {"day": last, "attempts": len(marks), "correct": correct,
            "accuracy": round(100 * correct / len(marks))}


def note_accuracy(note_id, user_id=OWNER):
    """This user's accuracy on one note, or None if they never tried its quiz."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS attempts, SUM(correct) AS correct FROM attempts WHERE note_id = ? AND user_id = ?",
            (note_id, user_id),
        ).fetchone()
    if row["attempts"] == 0:
        return None
    correct = int(row["correct"])
    return {"attempts": row["attempts"], "correct": correct, "accuracy": round(100 * correct / row["attempts"])}


# ---------- Flashcards: shared cards, per-user schedule ----------

def today():
    return date.today().isoformat()


# A card with this user's schedule. A card the user never reviewed is new: due today.
CARD_SELECT = """
    SELECT c.id, c.note_id, c.front, c.back, c.subtopic, c.subject,
           COALESCE(cs.ease, 2.5) AS ease,
           COALESCE(cs.interval_days, 0) AS interval_days,
           COALESCE(cs.repetitions, 0) AS repetitions,
           COALESCE(cs.due_date, ?) AS due_date,
           cs.last_reviewed
    FROM cards c LEFT JOIN card_state cs ON cs.card_id = c.id AND cs.user_id = ?
"""


def insert_cards(conn, note_id, subject, cards):
    """Insert flashcards for a note."""
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


def note_cards(note_id, user_id=OWNER):
    with connect() as conn:
        rows = conn.execute(CARD_SELECT + " WHERE c.note_id = ? ORDER BY c.id", (today(), user_id, note_id)).fetchall()
    return [dict(r) for r in rows]


def count_cards(note_id):
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM cards WHERE note_id = ?", (note_id,)).fetchone()["n"]


def get_card(card_id, user_id=OWNER):
    with connect() as conn:
        row = conn.execute(CARD_SELECT + " WHERE c.id = ?", (today(), user_id, card_id)).fetchone()
    return dict(row) if row else None


def due_cards(subject=None, on=None, user_id=OWNER):
    """This user's cards due on the given day (default today) or earlier, oldest due first."""
    sql = CARD_SELECT + " WHERE COALESCE(cs.due_date, ?) <= ?"
    params = [today(), user_id, today(), on or today()]
    if subject:
        sql += " AND LOWER(c.subject) = LOWER(?)"
        params.append(subject)
    sql += " ORDER BY due_date, c.id"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_card(card_id, ease, interval_days, repetitions, due_date, user_id=OWNER):
    """Save this user's schedule for one card."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO card_state (user_id, card_id, ease, interval_days, repetitions, due_date, last_reviewed)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, card_id) DO UPDATE SET
                 ease = excluded.ease, interval_days = excluded.interval_days,
                 repetitions = excluded.repetitions, due_date = excluded.due_date,
                 last_reviewed = excluded.last_reviewed""",
            (user_id, card_id, ease, interval_days, repetitions, due_date, now()),
        )


# ---------- AI tutor caches (shared) ----------

def get_explanation(subject, subtopic):
    with connect() as conn:
        row = conn.execute(
            "SELECT data, created_at FROM explanations WHERE LOWER(subject) = LOWER(?) AND LOWER(subtopic) = LOWER(?)",
            (subject, subtopic),
        ).fetchone()
    return {**json.loads(row["data"]), "created_at": row["created_at"]} if row else None


def save_explanation(subject, subtopic, data):
    """Save (or replace, after "Regenerate") the explanation of one subtopic."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM explanations WHERE LOWER(subject) = LOWER(?) AND LOWER(subtopic) = LOWER(?)",
            (subject, subtopic),
        )
        conn.execute(
            "INSERT INTO explanations (subject, subtopic, data, created_at) VALUES (?, ?, ?, ?)",
            (subject, subtopic, json.dumps(data, ensure_ascii=False), now()),
        )


def get_why(note_id, question_index, chosen):
    with connect() as conn:
        row = conn.execute(
            "SELECT text FROM why_wrong WHERE note_id = ? AND question_index = ? AND chosen = ?",
            (note_id, question_index, chosen),
        ).fetchone()
    return row["text"] if row else None


def save_why(note_id, question_index, chosen, text):
    with connect() as conn:
        conn.execute(
            """INSERT INTO why_wrong (note_id, question_index, chosen, text, created_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (note_id, question_index, chosen) DO UPDATE SET
                 text = excluded.text, created_at = excluded.created_at""",
            (note_id, question_index, chosen, text, now()),
        )


# ---------- Token usage ----------

def log_usage(feature, model, usage):
    """Save the token counts of one Gemini call. usage: dict from study_ai.read_usage."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO ai_usage
               (feature, model, prompt_tokens, output_tokens, thinking_tokens, total_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (feature, model, usage["prompt_tokens"], usage["output_tokens"],
             usage["thinking_tokens"], usage["total_tokens"], now()),
        )


def usage_summary():
    """Per feature: calls and tokens today and in total, and the average tokens per call.

    Added up in Python so "today" (local time) works the same on SQLite and Postgres.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT feature, prompt_tokens, output_tokens, thinking_tokens, total_tokens, created_at FROM ai_usage"
        ).fetchall()
    day = today()
    features = {}
    for r in rows:
        f = features.setdefault(r["feature"], {
            "feature": r["feature"], "calls_today": 0, "tokens_today": 0, "calls": 0, "tokens": 0,
            "prompt": 0, "output": 0, "thinking": 0,
        })
        f["calls"] += 1
        f["tokens"] += r["total_tokens"] or 0
        f["prompt"] += r["prompt_tokens"] or 0
        f["output"] += r["output_tokens"] or 0
        f["thinking"] += r["thinking_tokens"] or 0
        if local_day(r["created_at"]) == day:
            f["calls_today"] += 1
            f["tokens_today"] += r["total_tokens"] or 0
    result = []
    for f in features.values():
        n = f["calls"]
        result.append({
            "feature": f["feature"], "calls_today": f["calls_today"], "tokens_today": f["tokens_today"],
            "calls": n, "tokens": f["tokens"],
            "avg_prompt": round(f.pop("prompt") / n), "avg_output": round(f.pop("output") / n),
            "avg_thinking": round(f.pop("thinking") / n), "avg_total": round(f["tokens"] / n),
        })
    return sorted(result, key=lambda f: f["tokens"], reverse=True)


# ---------- Answer cache (shared) ----------

def cache_get(key):
    with connect() as conn:
        row = conn.execute("SELECT data FROM ai_cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["data"]) if row else None


def cache_put(key, feature, data):
    with connect() as conn:
        conn.execute(
            """INSERT INTO ai_cache (key, feature, data, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT (key) DO UPDATE SET feature = excluded.feature, data = excluded.data,
                 created_at = excluded.created_at""",
            (key, feature, json.dumps(data, ensure_ascii=False), now()),
        )


# ---------- Daily AI limits (per user) ----------

def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def ai_calls_today(user_id=None):
    """Uncached AI calls today (UTC): by one user, or by everyone when user_id is None."""
    sql, params = "SELECT COALESCE(SUM(calls), 0) AS n FROM ai_quota WHERE day = ?", [utc_day()]
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    with connect() as conn:
        return int(conn.execute(sql, params).fetchone()["n"])


def count_ai_call(user_id):
    with connect() as conn:
        conn.execute(
            """INSERT INTO ai_quota (user_id, day, calls) VALUES (?, ?, 1)
               ON CONFLICT (user_id, day) DO UPDATE SET calls = ai_quota.calls + 1""",
            (user_id, utc_day()),
        )
