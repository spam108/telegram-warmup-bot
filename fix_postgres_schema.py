# Этот скрипт дополнит функцию _init_postgres_schema в db.py

import re

# Читаем текущий файл
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Находим функцию _init_postgres_schema и заменяем её
old_function = '''async def _init_postgres_schema() -> None:
    pool = _require_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                is_authenticated BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
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
                mode TEXT DEFAULT 'comment',
                is_running BOOLEAN DEFAULT false,
                last_started_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                warmup_started_at TIMESTAMPTZ,
                warmup_end_at TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '7 days'),
                warmup_joined_today INTEGER NOT NULL DEFAULT 0,
                warmup_last_join DATE,
                warmup_last_join_at TIMESTAMPTZ,
                warmup_next_join_at TIMESTAMPTZ,
                reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (user_id, phone),
                CHECK (mode IN ('warmup', 'standard'))
            )
            """
        )'''

new_function = '''async def _init_postgres_schema() -> None:
    pool = _require_pool()

    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                is_authenticated BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
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
                mode TEXT DEFAULT 'comment',
                is_running BOOLEAN DEFAULT false,
                last_started_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                warmup_started_at TIMESTAMPTZ,
                warmup_end_at TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '7 days'),
                warmup_joined_today INTEGER NOT NULL DEFAULT 0,
                warmup_last_join DATE,
                warmup_last_join_at TIMESTAMPTZ,
                warmup_next_join_at TIMESTAMPTZ,
                reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (user_id, phone),
                CHECK (mode IN ('warmup', 'standard'))
            )
            """
        )

        # Добавляем недостающие таблицы
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS warmup_channels (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel_id TEXT NOT NULL,
                channel_username TEXT,
                joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, channel_id)
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS comment_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT,
                message_id BIGINT,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS warmup_settings (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channels_per_day INTEGER NOT NULL DEFAULT 15,
                delay_minutes INTEGER NOT NULL DEFAULT 7,
                default_days INTEGER NOT NULL DEFAULT 7,
                UNIQUE(account_id)
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS account_settings (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                setting_key TEXT NOT NULL,
                setting_value TEXT,
                UNIQUE(account_id, setting_key)
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_sessions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                phone TEXT NOT NULL,
                session_data BYTEA,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, phone)
            )
            """
        )'''

# Заменяем функцию в содержимом
if old_function in content:
    content = content.replace(old_function, new_function)
    print("Функция _init_postgres_schema успешно обновлена!")
else:
    print("Старая функция не найдена, добавляем новую...")
    # Находим место где заканчивается текущая функция и добавляем новую
    pass

# Записываем обновленный файл
with open('db.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Файл db.py обновлен!")
