-- Database bootstrap script for telegram warmup bot
-- Ensures all required tables and seed data exist on a fresh deployment.

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
    channels_per_day INTEGER NOT NULL,
    delay_minutes INTEGER NOT NULL,
    join_start_hour INTEGER NOT NULL,
    join_start_minute INTEGER NOT NULL,
    join_end_hour INTEGER NOT NULL,
    join_end_minute INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    post_id INTEGER NOT NULL,
    message TEXT,
    has_media BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, channel, post_id)
);

CREATE TABLE IF NOT EXISTS reaction_logs (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
CREATE INDEX IF NOT EXISTS idx_telegram_sessions_user_phone ON telegram_sessions (user_id, phone);
CREATE INDEX IF NOT EXISTS idx_posts_account_id ON posts (account_id);
CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id ON reaction_logs (account_id);
CREATE INDEX IF NOT EXISTS idx_reaction_logs_channel_message ON reaction_logs (channel, message_id);

INSERT INTO users (user_id, is_authenticated)
VALUES (1, TRUE)
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO accounts (
    user_id,
    phone,
    session_path,
    status,
    mode,
    reactions_enabled,
    reaction_emojis,
    reaction_chance,
    reaction_discussion_chance,
    discussion_reply_chance,
    reaction_sleep_min,
    reaction_sleep_max,
    reaction_limit_per_message,
    sleep_min,
    sleep_max,
    chance
)
VALUES (
    1,
    '+10000000000',
    'sessions/1.session',
    'running',
    'standard',
    TRUE,
    '["❤️", "👍"]',
    70,
    80,
    50,
    30,
    90,
    8,
    30,
    90,
    50
)
ON CONFLICT (user_id, phone) DO NOTHING;

INSERT INTO warmup_settings (
    id,
    channels_per_day,
    delay_minutes,
    join_start_hour,
    join_start_minute,
    join_end_hour,
    join_end_minute
) VALUES (
    1,
    5,
    60,
    10,
    0,
    20,
    0
)
ON CONFLICT (id) DO NOTHING;

UPDATE accounts SET 
    status = 'running',
    mode = 'standard',
    reaction_chance = 70,
    reaction_discussion_chance = 80,
    discussion_reply_chance = 50,
    discussion_reply_prompt = 'Ты молодец',
    reaction_sleep_min = 30,
    reaction_sleep_max = 90,
    reaction_emojis = '["❤️", "👍"]',
    reaction_limit_per_message = 8,
    reactions_enabled = TRUE,
    channels = '["@humormetahelp", "@man_about_womens", "@The_Womens_Psychology"]'
WHERE phone = '79639791823';
