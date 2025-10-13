DO
$$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pgbot1010_user') THEN
        CREATE ROLE pgbot1010_user LOGIN PASSWORD 'pgbot1010_password';
    END IF;
END
$$;

DO
$$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pgbot1010') THEN
        CREATE DATABASE pgbot1010 OWNER pgbot1010_user;
    END IF;
END
$$;

GRANT ALL PRIVILEGES ON DATABASE pgbot1010 TO pgbot1010_user;
\c pgbot1010;

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    is_authenticated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    session_path TEXT NOT NULL,
    chance INTEGER,
    system_prompt TEXT,
    sleep_min INTEGER,
    sleep_max INTEGER,
    reaction_emojis TEXT,
    reaction_chance INTEGER,
    reaction_discussion_chance INTEGER,
    discussion_reply_prompt TEXT,
    discussion_reply_chance INTEGER,
    reaction_sleep_min INTEGER,
    reaction_sleep_max INTEGER,
    reaction_limit_per_message INTEGER,
    last_reaction_at TIMESTAMPTZ,
    channels TEXT,
    warmup_channels TEXT,
    status TEXT NOT NULL DEFAULT 'stopped',
    last_started_at TIMESTAMPTZ,
    last_stopped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    mode TEXT NOT NULL DEFAULT 'warmup',
    warmup_end_at TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '7 days'),
    warmup_joined_today INTEGER NOT NULL DEFAULT 0,
    warmup_last_join DATE,
    warmup_last_join_at TIMESTAMPTZ,
    warmup_next_join_at TIMESTAMPTZ,
    reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (user_id, phone),
    CHECK (mode IN ('warmup', 'standard'))
);

CREATE TABLE IF NOT EXISTS account_settings (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, setting_key)
);

CREATE TABLE IF NOT EXISTS warmup_channels (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    joined_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, channel),
    CHECK (status IN ('pending', 'joined', 'error'))
);

CREATE TABLE IF NOT EXISTS warmup_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    channels_per_day INTEGER NOT NULL DEFAULT 15,
    delay_minutes INTEGER NOT NULL DEFAULT 7,
    default_days INTEGER NOT NULL DEFAULT 7,
    join_start_hour INTEGER NOT NULL DEFAULT 1,
    join_start_minute INTEGER NOT NULL DEFAULT 0,
    join_end_hour INTEGER NOT NULL DEFAULT 3,
    join_end_minute INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO warmup_settings (
    id,
    channels_per_day,
    delay_minutes,
    default_days,
    join_start_hour,
    join_start_minute,
    join_end_hour,
    join_end_minute
) VALUES (1, 15, 7, 7, 1, 0, 3, 0)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS comment_logs (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT,
    message_id BIGINT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    post_id BIGINT NOT NULL,
    message TEXT,
    has_media BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, channel, post_id)
);

CREATE TABLE IF NOT EXISTS reaction_logs (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    emoji TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    phone TEXT NOT NULL,
    session_data BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_account_settings_account_id ON account_settings (account_id);
CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending ON warmup_channels (account_id, status, position);
CREATE INDEX IF NOT EXISTS idx_posts_account_id ON posts (account_id);
CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id ON reaction_logs (account_id);
CREATE INDEX IF NOT EXISTS idx_reaction_logs_channel_message ON reaction_logs (channel, message_id);
CREATE INDEX IF NOT EXISTS idx_telegram_sessions_user_phone ON telegram_sessions (user_id, phone);

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pgbot1010_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO pgbot1010_user;
