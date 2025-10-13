-- PostgreSQL bootstrap script for the Telegram warmup bot
--
-- The script provisions a dedicated role and database, grants all required
-- privileges and creates the baseline schema expected by the application. It
-- is idempotent and can be re-run safely: missing objects will be created and
-- existing ones are left untouched.

-- 1. Ensure application role exists and has the expected password
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'pgbot1010_user') THEN
        CREATE USER pgbot1010_user WITH PASSWORD 'pgbot1010_password';
    ELSE
        ALTER USER pgbot1010_user WITH PASSWORD 'pgbot1010_password';
    END IF;
END
$$;

-- 2. Ensure the target database exists
SELECT 'CREATE DATABASE pgbot1010 OWNER pgbot1010_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pgbot1010')\gexec

-- 3. Grant database-wide privileges to the application role
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_database WHERE datname = 'pgbot1010') THEN
        EXECUTE 'GRANT CONNECT ON DATABASE pgbot1010 TO pgbot1010_user';
    END IF;
END
$$;

\connect pgbot1010

DO $$
BEGIN
    EXECUTE 'GRANT USAGE ON SCHEMA public TO pgbot1010_user';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pgbot1010_user';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pgbot1010_user';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pgbot1010_user';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO pgbot1010_user';
END
$$;

-- 4. Core tables ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    user_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_authenticated BOOLEAN NOT NULL DEFAULT FALSE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session TEXT NOT NULL,
    session_path TEXT,
    phone TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'standard',
    sleep_min INTEGER NOT NULL DEFAULT 10,
    sleep_max INTEGER NOT NULL DEFAULT 30,
    chance INTEGER NOT NULL DEFAULT 50,
    system_prompt TEXT,
    warmup_joined_today INTEGER NOT NULL DEFAULT 0,
    warmup_last_join DATE,
    warmup_last_join_at TIMESTAMPTZ,
    warmup_next_join_at TIMESTAMPTZ,
    warmup_end_at TIMESTAMPTZ,
    last_reaction_at TIMESTAMPTZ,
    channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    warmup_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    regular_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    reaction_emojis JSONB NOT NULL DEFAULT '["❤️", "👍", "🔥", "🎉", "👏"]'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_activity TIMESTAMPTZ,
    comment_count INTEGER NOT NULL DEFAULT 0,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    last_started_at TIMESTAMPTZ,
    last_stopped_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'stopped',
    reaction_chance INTEGER NOT NULL DEFAULT 50,
    reaction_discussion_chance INTEGER NOT NULL DEFAULT 50,
    discussion_reply_prompt TEXT,
    discussion_reply_chance INTEGER NOT NULL DEFAULT 50,
    reaction_sleep_min INTEGER NOT NULL DEFAULT 10,
    reaction_sleep_max INTEGER NOT NULL DEFAULT 30,
    reaction_limit_per_message INTEGER NOT NULL DEFAULT 50,
    reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_comment_at TIMESTAMPTZ,
    channels_last_synced_at TIMESTAMPTZ,
    system_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (mode IN ('warmup', 'standard')),
    UNIQUE (user_id, phone)
);

CREATE TABLE IF NOT EXISTS account_settings (
    id SERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, setting_key)
);

CREATE TABLE IF NOT EXISTS warmup_channels (
    id SERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    joined_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, channel),
    CHECK (status IN ('pending', 'joined', 'error'))
);

CREATE TABLE IF NOT EXISTS warmup_settings (
    id SERIAL PRIMARY KEY,
    channels_per_day INTEGER NOT NULL DEFAULT 15,
    delay_minutes INTEGER NOT NULL DEFAULT 7,
    default_days INTEGER NOT NULL DEFAULT 7,
    join_start_hour INTEGER NOT NULL DEFAULT 1,
    join_start_minute INTEGER NOT NULL DEFAULT 0,
    join_end_hour INTEGER NOT NULL DEFAULT 3,
    join_end_minute INTEGER NOT NULL DEFAULT 0,
    join_limit INTEGER NOT NULL DEFAULT 15,
    window_start TIME NOT NULL DEFAULT TIME '01:00',
    window_end TIME NOT NULL DEFAULT TIME '03:00',
    spans_midnight BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reaction_settings (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    reaction_limit INTEGER NOT NULL DEFAULT 50,
    post_reaction_chance INTEGER NOT NULL DEFAULT 50,
    discussion_reaction_chance INTEGER NOT NULL DEFAULT 50,
    discussion_reply_chance INTEGER NOT NULL DEFAULT 50,
    discussion_prompt TEXT,
    sleep_min INTEGER NOT NULL DEFAULT 10,
    sleep_max INTEGER NOT NULL DEFAULT 30,
    emojis JSONB NOT NULL DEFAULT '["❤️", "👍", "🔥", "🎉", "👏"]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS comment_logs (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT,
    message_id BIGINT,
    comment TEXT,
    status TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS skip_logs (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    post_id BIGINT NOT NULL,
    message TEXT,
    has_media BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, channel, post_id)
);

CREATE TABLE IF NOT EXISTS reaction_logs (
    id SERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    emoji TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    phone TEXT NOT NULL,
    session_data BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, phone)
);

-- 5. Indexes ---------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_account_settings_account_id ON account_settings (account_id);
CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending ON warmup_channels (account_id, status, position);
CREATE INDEX IF NOT EXISTS idx_telegram_sessions_user_phone ON telegram_sessions (user_id, phone);
CREATE INDEX IF NOT EXISTS idx_posts_account_id ON posts (account_id);
CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id ON reaction_logs (account_id);
CREATE INDEX IF NOT EXISTS idx_reaction_logs_channel_message ON reaction_logs (channel, message_id);
CREATE INDEX IF NOT EXISTS idx_comment_logs_created ON comment_logs (created_at);

-- 6. Seed data -------------------------------------------------------------

INSERT INTO warmup_settings (
    id,
    channels_per_day,
    delay_minutes,
    default_days,
    join_start_hour,
    join_start_minute,
    join_end_hour,
    join_end_minute,
    join_limit,
    window_start,
    window_end,
    spans_midnight
) VALUES (
    1, 15, 7, 7, 1, 0, 3, 0, 15, TIME '01:00', TIME '03:00', TRUE
)
ON CONFLICT (id) DO UPDATE SET
    channels_per_day = EXCLUDED.channels_per_day,
    delay_minutes = EXCLUDED.delay_minutes,
    default_days = EXCLUDED.default_days,
    join_start_hour = EXCLUDED.join_start_hour,
    join_start_minute = EXCLUDED.join_start_minute,
    join_end_hour = EXCLUDED.join_end_hour,
    join_end_minute = EXCLUDED.join_end_minute,
    join_limit = EXCLUDED.join_limit,
    window_start = EXCLUDED.window_start,
    window_end = EXCLUDED.window_end,
    spans_midnight = EXCLUDED.spans_midnight,
    updated_at = NOW();

-- Optional bootstrap user entry to ensure foreign keys can be created safely
INSERT INTO users (user_id, is_authenticated)
VALUES (1, TRUE)
ON CONFLICT (user_id) DO NOTHING;

-- Reminder: run pg_dump before modifying existing installations
-- Example: PGPASSWORD=pgbot1010_password pg_dump --format=custom --host=postgres --port=5432 --username=pgbot1010_user pgbot1010 > /backups/pgbot1010_$(date +%Y%m%d_%H%M%S).dump
