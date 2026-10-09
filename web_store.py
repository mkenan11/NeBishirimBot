"""Web sessiyaları və AI limitləri (migrations/003_web_access.sql)."""

import hashlib
import hmac
import os
import random
import secrets

import psycopg

SESSION_TOUCH_SECONDS = 24 * 3600


def _url():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL tapilmadi.")
    return url


def _connect():
    return psycopg.connect(_url(), connect_timeout=10, prepare_threshold=None)


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_web_user():
    """Yeni anonim web istifadəçisi yaradır. Qaytarır: (token, user_id). Token yalnız hash kimi saxlanılır."""
    token = secrets.token_urlsafe(32)
    with _connect() as db:
        user_id = -int(db.execute("SELECT nextval('web_user_seq')").fetchone()[0])
        db.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        db.execute("INSERT INTO web_sessions (token_hash, user_id) VALUES (%s, %s)", (_hash(token), user_id))
    return token, user_id


def resolve_session(token):
    """Token-ə uyğun web user_id qaytarır, yoxdursa None."""
    if not token or len(token) > 128:
        return None
    with _connect() as db:
        row = db.execute(
            f"""UPDATE web_sessions SET last_seen_at = NOW()
                WHERE token_hash = %s
                  AND last_seen_at < NOW() - INTERVAL '{SESSION_TOUCH_SECONDS} seconds'
                RETURNING user_id""",
            (_hash(token),),
        ).fetchone()
        if row is None:
            row = db.execute("SELECT user_id FROM web_sessions WHERE token_hash = %s", (_hash(token),)).fetchone()
    return int(row[0]) if row else None


def delete_web_sessions(user_id):
    with _connect() as db:
        db.execute("DELETE FROM web_sessions WHERE user_id = %s", (int(user_id),))


def hash_ip(ip):
    """IP ünvanı açıq saxlanılmır: server secret-i ilə HMAC."""
    key = os.getenv("INTERNAL_WEB_API_SECRET", "").encode("utf-8")
    return hmac.new(key, (ip or "unknown").encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def hit(bucket, limit, window_seconds=3600):
    """Pəncərədəki sorğu sayını artırır. Limit keçilibsə False qaytarır."""
    with _connect() as db:
        hits = db.execute(
            """INSERT INTO web_rate_limits (bucket, window_start, hits)
               VALUES (%s, to_timestamp((floor(extract(epoch FROM NOW()) / %s) * %s)::float8), 1)
               ON CONFLICT (bucket, window_start) DO UPDATE SET hits = web_rate_limits.hits + 1
               RETURNING hits""",
            (bucket, window_seconds, window_seconds),
        ).fetchone()[0]
        if random.random() < 0.01:
            db.execute("DELETE FROM web_rate_limits WHERE window_start < NOW() - INTERVAL '2 days'")
    return hits <= limit
