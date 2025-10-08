-- Warmup channels table
CREATE TABLE IF NOT EXISTS warmup_channels (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    channel_username TEXT,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, channel_id)
);

-- Comment logs table
CREATE TABLE IF NOT EXISTS comment_logs (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channel TEXT,
    message_id BIGINT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Warmup settings table
CREATE TABLE IF NOT EXISTS warmup_settings (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    channels_per_day INTEGER NOT NULL DEFAULT 15,
    delay_minutes INTEGER NOT NULL DEFAULT 7,
    default_days INTEGER NOT NULL DEFAULT 7,
    UNIQUE(account_id)
);

-- Account settings table
CREATE TABLE IF NOT EXISTS account_settings (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    UNIQUE(account_id, setting_key)
);

-- Telegram sessions table
CREATE TABLE IF NOT EXISTS telegram_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    phone TEXT NOT NULL,
    session_data BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, phone)
);
