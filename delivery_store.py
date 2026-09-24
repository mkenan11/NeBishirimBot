"""Claim each update before handlers run. Never blindly replay uncertain side effects."""

import os
import psycopg


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, prepare_threshold=None)


def claim_update(update_id, user_id):
    with connect() as db:
        row = db.execute("""INSERT INTO processed_updates (update_id, user_id, delivery_status)
            VALUES (%s, %s, 'processing') ON CONFLICT (update_id) DO NOTHING
            RETURNING update_id""", (update_id, user_id)).fetchone()
        if row is not None:
            return "claimed"
        # Expired invocations may have already sent Telegram messages. Do not replay them.
        db.execute("""UPDATE processed_updates SET delivery_status = 'uncertain'
            WHERE update_id = %s AND delivery_status = 'processing'
            AND processed_at < NOW() - INTERVAL '5 minutes'""", (update_id,))
        return db.execute("SELECT delivery_status FROM processed_updates WHERE update_id = %s", (update_id,)).fetchone()[0]


def finish_update(update_id, user_id=None, status="completed"):
    if status not in ("completed", "uncertain"):
        raise ValueError("Invalid delivery status")
    with connect() as db:
        db.execute("""UPDATE processed_updates SET delivery_status = %s
            WHERE update_id = %s AND delivery_status = 'processing'""", (status, update_id))


def release_update(update_id):
    """Only allowed before process_update starts: no handler side effects yet."""
    with connect() as db:
        db.execute("DELETE FROM processed_updates WHERE update_id = %s AND delivery_status = 'processing'", (update_id,))
