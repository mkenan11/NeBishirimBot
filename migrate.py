"""Apply versioned, additive migrations explicitly: python migrate.py."""

import hashlib
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def migrate(db):
    db.execute("SELECT pg_advisory_xact_lock(%s)", (2026092401,))
    db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        name TEXT PRIMARY KEY, checksum TEXT NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    applied = dict(db.execute("SELECT name, checksum FROM schema_migrations").fetchall())
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        content = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.replace("\r\n", "\n").encode()).hexdigest()
        if path.name in applied:
            if applied[path.name] != checksum:
                raise RuntimeError("Applied migration changed: " + path.name)
            continue
        db.execute(content, prepare=False)
        db.execute("INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)", (path.name, checksum))
        print("Applied:", path.name)


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, prepare_threshold=None) as connection:
        migrate(connection)
