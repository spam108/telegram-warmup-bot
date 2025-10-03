import os
import json
import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite


_CONN: Optional[aiosqlite.Connection] = None


class DatabaseNotInitialized(RuntimeError):
    pass


async def _retry_db_operation(func, *args, max_retries: int = 3, delay: int = 1, **kwargs):
    """Retry database operation with exponential backoff for locked database errors."""
    await asyncio.sleep(0.1)

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except sqlite3.OperationalError as exc:  # pragma: no cover - defensive branch
            if "locked" in str(exc).lower() and attempt < max_retries - 1:
                await asyncio.sleep(delay * (2 ** attempt))
                continue
            raise


async def _require_conn() -> aiosqlite.Connection:
    if _CONN is None:
        raise DatabaseNotInitialized("Database connection is not initialized. Call init_db() first.")
    return _CONN


def _deserialize_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return []


def _serialize_list(value: Optional[Iterable[str]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(list(value))


def _require_pool():  # pragma: no cover - backward compatibility stub
    raise RuntimeError("Connection pool is not available when using SQLite backend.")


async def init_db() -> None:
    """Initialise sqlite connection and ensure schema exists."""

    global _CONN

    if _CONN is not None:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    if dsn.startswith("sqlite:///"):
        db_path = dsn[len("sqlite:///") :]
    else:
        db_path = dsn

    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            is_authenticated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            phone TEXT NOT NULL,
            session_path TEXT NOT NULL,
            chance INTEGER,
            system_prompt TEXT,
            sleep_min INTEGER,
            sleep_max INTEGER,
            channels TEXT,
            warmup_channels TEXT,
            status TEXT NOT NULL DEFAULT 'stopped',
            last_started_at TEXT,
            last_stopped_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mode TEXT NOT NULL DEFAULT 'warmup',
            warmup_end_at TEXT DEFAULT (DATETIME('now', '+7 days')),
            warmup_joined_today INTEGER NOT NULL DEFAULT 0,
            warmup_last_join TEXT,
            warmup_last_join_at TEXT,
            warmup_next_join_at TEXT,
            UNIQUE (user_id, phone),
            CHECK (mode IN ('warmup', 'standard'))
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS warmup_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            joined_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (account_id, channel),
            CHECK (status IN ('pending', 'joined', 'error'))
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS warmup_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channels_per_day INTEGER NOT NULL,
            delay_minutes INTEGER NOT NULL,
            join_start_hour INTEGER NOT NULL,
            join_start_minute INTEGER NOT NULL,
            join_end_hour INTEGER NOT NULL,
            join_end_minute INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending
        ON warmup_channels (account_id, status, position)
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comment_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            channel TEXT,
            message_id INTEGER,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await conn.commit()
    _CONN = conn


async def close_db() -> None:
    global _CONN
    if _CONN is not None:
        await _CONN.close()
        _CONN = None


async def ensure_warmup_settings(
    *,
    channels_per_day: int,
    delay_minutes: int,
    join_start_hour: int,
    join_start_minute: int,
    join_end_hour: int,
    join_end_minute: int,
) -> None:
    """Ensure that a single warmup settings row exists in the database."""

    conn = await _require_conn()
    await conn.execute(
        """
        INSERT INTO warmup_settings (
            id,
            channels_per_day,
            delay_minutes,
            join_start_hour,
            join_start_minute,
            join_end_hour,
            join_end_minute
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            channels_per_day,
            delay_minutes,
            join_start_hour,
            join_start_minute,
            join_end_hour,
            join_end_minute,
        ),
    )
    await conn.commit()


async def get_warmup_settings() -> Dict[str, int]:
    """Fetch the warmup settings row."""

    conn = await _require_conn()
    async with conn.execute(
        """
        SELECT channels_per_day,
               delay_minutes,
               join_start_hour,
               join_start_minute,
               join_end_hour,
               join_end_minute
        FROM warmup_settings
        WHERE id = 1
        """
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        raise RuntimeError("Warmup settings are not initialised")

    return {
        "channels_per_day": int(row["channels_per_day"]),
        "delay_minutes": int(row["delay_minutes"]),
        "join_start_hour": int(row["join_start_hour"]),
        "join_start_minute": int(row["join_start_minute"]),
        "join_end_hour": int(row["join_end_hour"]),
        "join_end_minute": int(row["join_end_minute"]),
    }


async def update_warmup_settings(
    *,
    channels_per_day: Optional[int] = None,
    delay_minutes: Optional[int] = None,
    join_start_hour: Optional[int] = None,
    join_start_minute: Optional[int] = None,
    join_end_hour: Optional[int] = None,
    join_end_minute: Optional[int] = None,
) -> None:
    """Update warmup settings with the provided values."""

    updates: List[str] = []
    params: List[Any] = []

    if channels_per_day is not None:
        updates.append("channels_per_day = ?")
        params.append(channels_per_day)
    if delay_minutes is not None:
        updates.append("delay_minutes = ?")
        params.append(delay_minutes)
    if join_start_hour is not None:
        updates.append("join_start_hour = ?")
        params.append(join_start_hour)
    if join_start_minute is not None:
        updates.append("join_start_minute = ?")
        params.append(join_start_minute)
    if join_end_hour is not None:
        updates.append("join_end_hour = ?")
        params.append(join_end_hour)
    if join_end_minute is not None:
        updates.append("join_end_minute = ?")
        params.append(join_end_minute)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(1)

    query = f"""
        UPDATE warmup_settings
        SET {', '.join(updates)}
        WHERE id = ?
    """

    conn = await _require_conn()
    await conn.execute(query, tuple(params))
    await conn.commit()


async def ensure_user(user_id: int) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        INSERT INTO users (user_id)
        VALUES (?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """,
        (user_id,),
    )
    await conn.commit()


async def set_user_authenticated(user_id: int, value: bool) -> None:
    conn = await _require_conn()
    await conn.execute(
        "UPDATE users SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (1 if value else 0, user_id),
    )
    await conn.commit()


async def is_user_authenticated(user_id: int) -> bool:
    conn = await _require_conn()
    async with conn.execute(
        "SELECT is_authenticated FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row["is_authenticated"]) if row else False


async def ensure_account(user_id: int, phone: str, session_path: str) -> Dict[str, Any]:
    conn = await _require_conn()
    await conn.execute(
        """
        INSERT INTO accounts (user_id, phone, session_path)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, phone) DO UPDATE SET
            session_path = excluded.session_path,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, phone, session_path),
    )
    await conn.commit()
    account = await get_account_by_session(user_id, phone)
    if account is None:  # pragma: no cover - defensive branch
        raise RuntimeError("Failed to create or update account")
    return account


async def _fetch_accounts(query: str, params: Iterable[Any]) -> List[Dict[str, Any]]:
    conn = await _require_conn()
    async with conn.execute(query, tuple(params)) as cursor:
        rows = await cursor.fetchall()
    return [_convert_account_row(row) for row in rows]


def _convert_account_row(row: aiosqlite.Row) -> Dict[str, Any]:
    data = dict(row)
    data["channels"] = _deserialize_list(data.get("channels"))
    data["warmup_channels"] = _deserialize_list(data.get("warmup_channels"))
    return data


async def get_accounts_for_user(user_id: int) -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at",
        (user_id,),
    )


async def get_account_by_session(user_id: int, phone: str) -> Optional[Dict[str, Any]]:
    conn = await _require_conn()
    async with conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND phone = ?",
        (user_id, phone),
    ) as cursor:
        row = await cursor.fetchone()
    return _convert_account_row(row) if row else None


async def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    conn = await _require_conn()
    async with conn.execute(
        "SELECT * FROM accounts WHERE id = ?",
        (account_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return _convert_account_row(row) if row else None


async def get_warmup_queue_stats(account_id: int) -> Dict[str, int]:
    conn = await _require_conn()
    async with conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN status = 'joined' THEN 1 ELSE 0 END) AS joined_count,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count
        FROM warmup_channels
        WHERE account_id = ?
        """,
        (account_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return {"pending": 0, "joined": 0, "error": 0}
    return {
        "pending": row["pending_count"] or 0,
        "joined": row["joined_count"] or 0,
        "error": row["error_count"] or 0,
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
    print(
        "DEBUG: update_account_settings called with "
        f"account_id={account_id}, chance={chance}, sleep_min={sleep_min}, "
        f"sleep_max={sleep_max}, system_prompt={system_prompt}"
    )

    updates: List[str] = []
    values: List[Any] = []

    if chance is not None:
        updates.append("chance = ?")
        values.append(chance)
    if system_prompt is not None:
        updates.append("system_prompt = ?")
        values.append(system_prompt)
    if sleep_min is not None:
        updates.append("sleep_min = ?")
        values.append(sleep_min)
    if sleep_max is not None:
        updates.append("sleep_max = ?")
        values.append(sleep_max)
    if channels is not None:
        updates.append("channels = ?")
        values.append(_serialize_list(channels))

    if not updates:
        print("DEBUG: No updates to perform")
        return

    values.append(account_id)
    assignments = ", ".join(updates)
    query = f"""
        UPDATE accounts
        SET {assignments}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """

    conn = await _require_conn()
    await conn.execute(query, tuple(values))
    await conn.commit()
    print(f"DEBUG: SQL update completed successfully for account_id={account_id}")


async def set_account_mode(account_id: int, mode: str, warmup_days: Optional[int] = None) -> None:
    conn = await _require_conn()
    updates: List[str] = ["mode = ?"]
    params: List[Any] = [mode]

    if warmup_days is not None:
        updates.append("warmup_end_at = DATETIME('now', ?)")
        params.append(f"+{warmup_days} days")

    if mode == "warmup":
        updates.extend(
            [
                "warmup_joined_today = 0",
                "warmup_last_join = NULL",
                "warmup_last_join_at = NULL",
                "warmup_next_join_at = NULL",
            ]
        )
    else:
        updates.append("warmup_next_join_at = NULL")

    params.append(account_id)

    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """

    await conn.execute(query, tuple(params))
    await conn.commit()


async def sync_warmup_channels(account_id: int, channels: List[str]) -> None:
    conn = await _require_conn()

    seen: set[str] = set()
    unique_channels = [x for x in channels if not (x in seen or seen.add(x))]

    await conn.execute("DELETE FROM warmup_channels WHERE account_id = ?", (account_id,))

    for idx, channel in enumerate(unique_channels, start=1):
        try:
            await conn.execute(
                """
                INSERT INTO warmup_channels (account_id, channel, position)
                VALUES (?, ?, ?)
                """,
                (account_id, channel, idx),
            )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - defensive branch
            if "unique" not in str(exc).lower():
                raise

    await conn.execute(
        """
        UPDATE accounts
        SET warmup_channels = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (_serialize_list(unique_channels), account_id),
    )

    await conn.commit()


async def get_warmup_pending(
    account_id: int, *, limit: int = 15, reset_if_empty: bool = False
) -> List[Dict[str, Any]]:
    conn = await _require_conn()
    async with conn.execute(
        """
        SELECT *
        FROM warmup_channels
        WHERE account_id = ? AND status = 'pending'
        ORDER BY position
        LIMIT ?
        """,
        (account_id, limit),
    ) as cursor:
        records = await cursor.fetchall()

    if (not records) and reset_if_empty:
        account = await get_account_by_id(account_id)
        if account:
            base_channels = account.get("warmup_channels") or []
            active_channels = set(account.get("channels") or [])
            queue = [chl for chl in base_channels if chl not in active_channels]
            if queue:
                await sync_warmup_channels(account_id, queue)
                async with conn.execute(
                    """
                    SELECT *
                    FROM warmup_channels
                    WHERE account_id = ? AND status = 'pending'
                    ORDER BY position
                    LIMIT ?
                    """,
                    (account_id, limit),
                ) as cursor:
                    records = await cursor.fetchall()

    return [dict(record) for record in records]


async def mark_warmup_channel_joined(account_id: int, channel: str) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        UPDATE warmup_channels
        SET status = 'joined',
            joined_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE account_id = ? AND channel = ?
        """,
        (account_id, channel),
    )
    await conn.commit()


async def record_warmup_channel_error(account_id: int, channel: str, error: str) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        UPDATE warmup_channels
        SET status = 'error',
            error = ?,
            attempts = attempts + 1,
            last_attempt_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE account_id = ? AND channel = ?
        """,
        (error, account_id, channel),
    )
    await conn.commit()


async def reset_warmup_daily_state(account_id: int) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        UPDATE accounts
        SET warmup_joined_today = 0,
            warmup_last_join = DATE('now'),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account_id,),
    )
    await conn.commit()


async def db_update_warmup_schedule(
    account_id: int,
    *,
    next_join: Optional[datetime] = None,
    last_join: Optional[datetime] = None,
) -> None:
    updates: List[str] = []
    params: List[Any] = []

    if next_join is not None:
        updates.append("warmup_next_join_at = ?")
        params.append(next_join.isoformat())

    if last_join is not None:
        iso = last_join.isoformat()
        updates.append("warmup_last_join_at = ?")
        params.append(iso)
        updates.append("warmup_last_join = ?")
        params.append(last_join.date().isoformat())

    if not updates:
        return

    params.append(account_id)
    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """

    conn = await _require_conn()
    await conn.execute(query, tuple(params))
    await conn.commit()


async def increment_warmup_joined(account_id: int) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        UPDATE accounts
        SET warmup_joined_today = warmup_joined_today + 1,
            warmup_last_join = DATE('now'),
            warmup_last_join_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account_id,),
    )
    await conn.commit()


async def get_warmup_stats(account_id: int) -> Optional[Dict[str, Any]]:
    conn = await _require_conn()
    async with conn.execute(
        """
        SELECT warmup_joined_today, warmup_last_join, warmup_end_at, mode
        FROM accounts
        WHERE id = ?
        """,
        (account_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def mark_account_running(account_id: int) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        UPDATE accounts
        SET status = 'running',
            last_started_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account_id,),
    )
    await conn.commit()


async def mark_account_stopped(account_id: int) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        UPDATE accounts
        SET status = 'stopped',
            last_stopped_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account_id,),
    )
    await conn.commit()


async def delete_account(user_id: int, phone: str) -> None:
    conn = await _require_conn()
    await conn.execute(
        "DELETE FROM accounts WHERE user_id = ? AND phone = ?",
        (user_id, phone),
    )
    await conn.commit()


async def get_running_accounts() -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts WHERE status = 'running'",
        (),
    )


async def get_accounts_in_warmup() -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts WHERE mode = 'warmup' AND status = 'running'",
        (),
    )


async def add_comment_log(
    account_id: int,
    *,
    channel: Optional[str],
    message_id: Optional[int],
    status: str,
    error: Optional[str] = None,
) -> None:
    async def _execute_log() -> None:
        conn = await _require_conn()
        await conn.execute(
            """
            INSERT INTO comment_logs (account_id, channel, message_id, status, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_id, channel, message_id, status, error),
        )
        await conn.commit()

    await _retry_db_operation(_execute_log)
