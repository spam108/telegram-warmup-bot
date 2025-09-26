import os
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg


_pool: Optional[asyncpg.Pool] = None


class DatabaseNotInitialized(RuntimeError):
    pass


async def _retry_db_operation(func, *args, max_retries=3, delay=1, **kwargs):
    """Retry database operation with exponential backoff"""
    # Небольшая пауза перед операцией для избежания одновременных запросов
    await asyncio.sleep(0.1)
    
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (asyncpg.exceptions.DeadlockDetected, 
                asyncpg.exceptions.LockNotAvailable,
                asyncpg.exceptions.UniqueViolation) as e:
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(delay * (2 ** attempt))
        except Exception as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                await asyncio.sleep(delay * (2 ** attempt))
                continue
            raise e


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise DatabaseNotInitialized("Database pool is not initialized. Call init_db() first.")
    return _pool


async def init_db() -> None:
    """Initialise connection pool and ensure schema is present."""

    global _pool

    if _pool is not None:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=60)

    async with _pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                is_authenticated BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                phone TEXT NOT NULL,
                session_path TEXT NOT NULL,
                chance INTEGER,
                system_prompt TEXT,
                sleep_min INTEGER,
                sleep_max INTEGER,
                channels TEXT[],
                warmup_channels TEXT[],
                status TEXT NOT NULL DEFAULT 'stopped',
                last_started_at TIMESTAMPTZ,
                last_stopped_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                mode TEXT NOT NULL DEFAULT 'warmup',
                warmup_end_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days'),
                warmup_joined_today INTEGER NOT NULL DEFAULT 0,
                warmup_last_join DATE,
                warmup_last_join_at TIMESTAMPTZ,
                warmup_next_join_at TIMESTAMPTZ,
                UNIQUE (user_id, phone)
            );
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'warmup';
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_end_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days');
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_joined_today INTEGER NOT NULL DEFAULT 0;
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_last_join DATE;
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_last_join_at TIMESTAMPTZ;
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_channels TEXT[];
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_next_join_at TIMESTAMPTZ;
            """
        )

        await conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'accounts_mode_check'
                ) THEN
                    ALTER TABLE accounts
                    ADD CONSTRAINT accounts_mode_check CHECK (mode IN ('warmup', 'standard'));
                END IF;
            END $$;
            """
        )

        await conn.execute(
            """
            UPDATE accounts
            SET mode = 'warmup'
            WHERE mode IS NULL;
            """
        )

        await conn.execute(
            """
            UPDATE accounts
            SET warmup_end_at = NOW() + INTERVAL '7 days'
            WHERE warmup_end_at IS NULL;
            """
        )

        await conn.execute(
            """
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
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (account_id, channel)
            );
            """
        )
        
        # Добавляем столбец updated_at если таблица уже существует
        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
            """
        )

        await conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'warmup_channels_status_check'
                ) THEN
                    ALTER TABLE warmup_channels
                    ADD CONSTRAINT warmup_channels_status_check CHECK (status IN ('pending', 'joined', 'error'));
                END IF;
            END $$;
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending
            ON warmup_channels (account_id, status, position);
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comment_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT,
                message_id BIGINT,
                status TEXT NOT NULL,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ensure_user(user_id: int) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO UPDATE
            SET updated_at = NOW()
            """,
            user_id,
        )


async def set_user_authenticated(user_id: int, value: bool) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users SET is_authenticated = $2, updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            value,
        )


async def is_user_authenticated(user_id: int) -> bool:
    pool = _require_pool()
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT is_authenticated FROM users WHERE user_id = $1",
            user_id,
        )
        return bool(record["is_authenticated"]) if record else False


async def ensure_account(user_id: int, phone: str, session_path: str) -> Dict[str, Any]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            INSERT INTO accounts (user_id, phone, session_path)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, phone) DO UPDATE
            SET session_path = EXCLUDED.session_path,
                updated_at = NOW()
            RETURNING *
            """,
            user_id,
            phone,
            session_path,
        )
        return dict(record)


async def get_accounts_for_user(user_id: int) -> List[Dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch(
            """
            SELECT * FROM accounts
            WHERE user_id = $1
            ORDER BY created_at
            """,
            user_id,
        )
    return [dict(record) for record in records]


async def get_account_by_session(user_id: int, phone: str) -> Optional[Dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM accounts WHERE user_id = $1 AND phone = $2",
            user_id,
            phone,
        )
    return dict(record) if record else None


async def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM accounts WHERE id = $1",
            account_id,
        )
    return dict(record) if record else None


async def get_warmup_queue_stats(account_id: int) -> Dict[str, int]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
                COUNT(*) FILTER (WHERE status = 'joined') AS joined_count,
                COUNT(*) FILTER (WHERE status = 'error') AS error_count
            FROM warmup_channels
            WHERE account_id = $1
            """,
            account_id,
        )
    if not result:
        return {"pending": 0, "joined": 0, "error": 0}
    return {
        "pending": result["pending_count"] or 0,
        "joined": result["joined_count"] or 0,
        "error": result["error_count"] or 0,
    }


async def update_account_settings(
    account_id: int,
    *,
    chance: Optional[int] = None,
    system_prompt: Optional[str] = None,
    sleep_min: Optional[int] = None,
    sleep_max: Optional[int] = None,
    channels: Optional[List[str]] = None,
) -> None:
    pool = _require_pool()

    updates: List[str] = []
    values: List[Any] = []

    if chance is not None:
        updates.append("chance = $%d" % (len(values) + 1))
        values.append(chance)
    if system_prompt is not None:
        updates.append("system_prompt = $%d" % (len(values) + 1))
        values.append(system_prompt)
    if sleep_min is not None:
        updates.append("sleep_min = $%d" % (len(values) + 1))
        values.append(sleep_min)
    if sleep_max is not None:
        updates.append("sleep_max = $%d" % (len(values) + 1))
        values.append(sleep_max)
    if channels is not None:
        updates.append("channels = $%d" % (len(values) + 1))
        values.append(channels)

    if not updates:
        return

    values.append(account_id)
    assignments = ", ".join(updates)

    query = f"""
        UPDATE accounts
        SET {assignments}, updated_at = NOW()
        WHERE id = ${len(values)}
    """

    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(query, *values)


async def set_account_mode(account_id: int, mode: str, warmup_days: Optional[int] = None) -> None:
    pool = _require_pool()
    updates: List[str] = ["mode = $1"]
    params: List[Any] = [mode]

    if warmup_days is not None:
        updates.append(f"warmup_end_at = NOW() + INTERVAL '{warmup_days} days'")

    if mode == "warmup":
        updates.extend([
            "warmup_joined_today = 0",
            "warmup_last_join = NULL",
            "warmup_last_join_at = NULL",
            "warmup_next_join_at = NULL",
        ])
    else:
        updates.extend([
            "warmup_next_join_at = NULL",
        ])

    params.append(account_id)

    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}, updated_at = NOW()
        WHERE id = ${len(params)}
    """

    async with pool.acquire() as conn:
        await conn.execute(query, *params)


async def sync_warmup_channels(account_id: int, channels: List[str]) -> None:
    pool = _require_pool()
    
    # Удаляем дубликаты из списка каналов
    seen = set()
    unique_channels = [x for x in channels if not (x in seen or seen.add(x))]
    
    async with pool.acquire() as conn:
        # Сначала удаляем все существующие каналы для этого аккаунта
        await conn.execute("DELETE FROM warmup_channels WHERE account_id = $1", account_id)
        
        if unique_channels:
            # Добавляем новые каналы
            for idx, channel in enumerate(unique_channels, start=1):
                try:
                    await conn.execute(
                        """
                        INSERT INTO warmup_channels (account_id, channel, position)
                        VALUES ($1, $2, $3)
                        """,
                        account_id, channel, idx
                    )
                except Exception as e:
                    # Игнорируем ошибки дубликатов
                    if "unique" not in str(e).lower():
                        raise e
        
        # Обновляем поле warmup_channels в таблице accounts
        await conn.execute(
            """
            UPDATE accounts
            SET warmup_channels = $2::text[], updated_at = NOW()
            WHERE id = $1
            """,
            account_id,
            unique_channels,
        )


async def get_warmup_pending(account_id: int, limit: int = 15, reset_if_empty: bool = False) -> List[Dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch(
            """
            SELECT *
            FROM warmup_channels
            WHERE account_id = $1 AND status = 'pending'
            ORDER BY position
            LIMIT $2
            """,
            account_id,
            limit,
        )

    if (not records) and reset_if_empty:
        account = await get_account_by_id(account_id)
        if account:
            base_channels = account.get("warmup_channels") or []
            active_channels = set(account.get("channels") or [])
            queue = [chl for chl in base_channels if chl not in active_channels]
            if queue:
                await sync_warmup_channels(account_id, queue)
                async with pool.acquire() as conn:
                    records = await conn.fetch(
                        """
                        SELECT *
                        FROM warmup_channels
                        WHERE account_id = $1 AND status = 'pending'
                        ORDER BY position
                        LIMIT $2
                        """,
                        account_id,
                        limit,
                    )

    return [dict(record) for record in records]


async def mark_warmup_channel_joined(account_id: int, channel: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE warmup_channels
            SET status = 'joined', joined_at = NOW(), updated_at = NOW()
            WHERE account_id = $1 AND channel = $2
            """,
            account_id,
            channel,
        )


async def record_warmup_channel_error(account_id: int, channel: str, error: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE warmup_channels
            SET status = 'error', error = $3, attempts = attempts + 1, last_attempt_at = NOW(), updated_at = NOW()
            WHERE account_id = $1 AND channel = $2
            """,
            account_id,
            channel,
            error,
        )


async def reset_warmup_daily_state(account_id: int) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE accounts
            SET warmup_joined_today = 0, warmup_last_join = CURRENT_DATE, updated_at = NOW()
            WHERE id = $1
            """,
            account_id,
        )


async def db_update_warmup_schedule(account_id: int, *, next_join: Optional[datetime] = None, last_join: Optional[datetime] = None) -> None:
    pool = _require_pool()
    updates = []
    values: List[Any] = []
    if next_join is not None:
        updates.append(f"warmup_next_join_at = $%d" % (len(values) + 1))
        values.append(next_join)
    if last_join is not None:
        updates.append(f"warmup_last_join_at = $%d" % (len(values) + 1))
        values.append(last_join)
        updates.append(f"warmup_last_join = $%d" % (len(values) + 1))
        values.append(last_join.date())

    if not updates:
        return

    values.append(account_id)
    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}, updated_at = NOW()
        WHERE id = ${len(values)}
    """

    async with pool.acquire() as conn:
        await conn.execute(query, *values)


async def increment_warmup_joined(account_id: int) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE accounts
            SET warmup_joined_today = warmup_joined_today + 1,
                warmup_last_join = CURRENT_DATE,
                warmup_last_join_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            """,
            account_id,
        )


async def get_warmup_stats(account_id: int) -> Optional[Dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            """
            SELECT warmup_joined_today, warmup_last_join, warmup_end_at, mode
            FROM accounts
            WHERE id = $1
            """,
            account_id,
        )
    return dict(record) if record else None


async def mark_account_running(account_id: int) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE accounts
            SET status = 'running', last_started_at = NOW(), updated_at = NOW()
            WHERE id = $1
            """,
            account_id,
        )


async def mark_account_stopped(account_id: int) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE accounts
            SET status = 'stopped', last_stopped_at = NOW(), updated_at = NOW()
            WHERE id = $1
            """,
            account_id,
        )


async def delete_account(user_id: int, phone: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM accounts WHERE user_id = $1 AND phone = $2",
            user_id,
            phone,
        )


async def get_running_accounts() -> List[Dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT * FROM accounts WHERE status = 'running'",
        )
    return [dict(record) for record in records]


async def get_accounts_in_warmup() -> List[Dict[str, Any]]:
    """Get all accounts that are in warmup mode."""
    pool = _require_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT * FROM accounts WHERE mode = 'warmup' AND status = 'running'",
        )
    return [dict(record) for record in records]


async def add_comment_log(
    account_id: int,
    *,
    channel: Optional[str],
    message_id: Optional[int],
    status: str,
    error: Optional[str] = None,
) -> None:
    async def _execute_log():
        pool = _require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO comment_logs (account_id, channel, message_id, status, error)
                VALUES ($1, $2, $3, $4, $5)
                """,
                account_id,
                channel,
                message_id,
                status,
                error,
            )
    
    await _retry_db_operation(_execute_log)



