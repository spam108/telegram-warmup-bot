CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    is_authenticated INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
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
    reactions_enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE (user_id, phone),
    CHECK (mode IN ('warmup', 'standard'))
);

CREATE TABLE IF NOT EXISTS warmup_channels (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
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

CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending
    ON warmup_channels (account_id, status, position);

CREATE TABLE IF NOT EXISTS warmup_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    channels_per_day INTEGER NOT NULL,
    delay_minutes INTEGER NOT NULL,
    join_start_hour INTEGER NOT NULL,
    join_start_minute INTEGER NOT NULL,
    join_end_hour INTEGER NOT NULL,
    join_end_minute INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comment_logs (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT,
    message_id INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    phone_number TEXT NOT NULL,
    session_data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, phone_number)
);

CREATE TABLE IF NOT EXISTS account_settings (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    phone_number TEXT NOT NULL,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
