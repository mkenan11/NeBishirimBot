-- Web interfeysi. Anonim web istifadəçiləri mənfi user_id alır; Telegram ID-ləri müsbətdir,
-- ona görə ingredients, favorite_recipes və bot_sessions cədvəlləri dəyişmədən paylaşılır.
CREATE SEQUENCE IF NOT EXISTS web_user_seq;
CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL CHECK (user_id < 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS web_sessions_user_idx ON web_sessions (user_id);
-- Saatlıq AI və sessiya limitləri. bucket: məs. 'user:-5:photo', 'ip:<hash>:session'.
CREATE TABLE IF NOT EXISTS web_rate_limits (
    bucket TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, window_start)
);
