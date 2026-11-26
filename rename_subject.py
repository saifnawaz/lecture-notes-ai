"""Rename (or merge) a subject in notes.db.

Usage:
    python rename_subject.py                          list subjects with note counts
    python rename_subject.py "discrete" "Discrete Mathematics"

The old name is matched case-insensitively. If the new name already exists,
the two subjects become one.
"""
import sys

import db


def main():
    db.init_db()
    if len(sys.argv) == 1:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT subject, COUNT(*) AS n FROM notes GROUP BY subject ORDER BY subject COLLATE NOCASE"
            ).fetchall()
        for r in rows:
            print(f"{r['n']:3}  {r['subject'] or '(no subject)'}")
        return
    if len(sys.argv) != 3 or not sys.argv[2].strip():
        sys.exit(__doc__)

    old, new = sys.argv[1], sys.argv[2]
    changed = db.rename_subject(old, new)
    if not any(changed.values()):
        sys.exit(f'No subject called "{old}" found.')
    print(f'Renamed "{old}" -> "{db.canonical_subject(new)}": '
          + ", ".join(f"{n} {table}" for table, n in changed.items()))


if __name__ == "__main__":
    main()
