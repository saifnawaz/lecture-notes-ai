import pytest

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite database, even if DATABASE_URL is set in .env.

    (tests/test_postgres.py switches to Postgres itself.)
    """
    monkeypatch.setattr(db, "DATABASE_URL", None)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("ADMIN_PASSCODE", raising=False)
    db.init_db()
