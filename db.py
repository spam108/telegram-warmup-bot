import os
import json
import asyncio
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import asyncpg


_CONN: Optional["AsyncPGConnection"] = None
_POOL: Optional[asyncpg.Pool] = None
_UNSET = object()


class RowMapping(Mapping[str, Any]):
    """Mapping-compatible wrapper for database rows.

    ``aiosqlite.Row`` implements mapping-like behaviour and supports both
    dictionary-style and positional access.  To keep backward compatibility when
    running on PostgreSQL with ``asyncpg`` we emulate the same contract.
    """

    __slots__ = ("_keys", "_values", "_data")

    def __init__(self, keys: Sequence[str], values: Sequence[Any]):
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._data = {key: value for key, value in zip(self._keys, self._values)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):  # type: ignore[override]
        return iter(self._keys)

    def __len__(self) -> int:  # type: ignore[override]
        return len(self._keys)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"RowMapping({self._data!r})"


def _parse_command_tag(status: str) -> int:
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):  # pragma: no cover - defensive
        return -1


class AsyncPGCursor:
    """Async cursor compatible with ``aiosqlite.Cursor``.

    The cursor instance returned by :func:`aiosqlite.Connection.execute` is both
    awaitable and usable as an asynchronous context manager.  ``asyncpg`` does
    not expose an identical API, so this wrapper provides the subset used by the
    project.
    """

    __slots__ = ("_pool", "_query", "_params", "_rows", "_iter", "_is_select", "_rowcount")

    def __init__(self, pool: asyncpg.Pool, query: str, params: Sequence[Any]):
        self._pool = pool
        self._query = query
        self._params = params
        self._rows: List[RowMapping] = []
        self._iter = 0
        self._is_select = _is_select_query(query)
        self._rowcount = -1

    def __await__(self):  # pragma: no cover - exercised indirectly
        return self._run().__await__()

    async def _run(self) -> "AsyncPGCursor":
        async with self._pool.acquire() as connection:
            statement, parameters = _convert_params_for_postgres(
                self._query, self._params
            )
            if self._is_select:
                records = await connection.fetch(statement, *parameters)
                self._rows = [RowMapping(record.keys(), record.values()) for record in records]
                self._rowcount = len(self._rows)
            else:
                status = await connection.execute(statement, *parameters)
                self._rowcount = _parse_command_tag(status)
        return self

    async def __aenter__(self) -> "AsyncPGCursor":  # pragma: no cover - exercised indirectly
        await self._run()
        self._iter = 0
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - indirect
        self._rows.clear()
        self._iter = 0

    async def fetchone(self) -> Optional[RowMapping]:
        if self._iter >= len(self._rows):
            return None
        row = self._rows[self._iter]
        self._iter += 1
        return row

    async def fetchall(self) -> List[RowMapping]:
        return list(self._rows)

    @property
    def rowcount(self) -> int:
        return self._rowcount

    async def close(self) -> None:
        self._rows.clear()
        self._iter = 0


class AsyncPGConnection:
    """Connection facade exposing a subset of ``aiosqlite.Connection``."""

    __slots__ = ("_pool",)

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def execute(self, query: str, params: Sequence[Any] = ()) -> AsyncPGCursor:
        return AsyncPGCursor(self._pool, query, params)

    async def commit(self) -> None:  # pragma: no cover - nothing to do for autocommit
        return None

    async def close(self) -> None:
        await self._pool.close()


def _is_select_query(query: str) -> bool:
    prefix = query.lstrip().split(" ", 1)[0].upper()
    return prefix in {"SELECT", "WITH", "PRAGMA"}


def _convert_params_for_postgres(
    query: str, params: Sequence[Any]
) -> tuple[str, Sequence[Any]]:
    """Translate SQLite-style ``?`` placeholders to ``$`` parameters."""

    placeholder_count = query.count("?")
    if placeholder_count != len(params):
        return query, params

    if placeholder_count == 0:
        return query, params

    parts = query.split("?")
    rebuilt: List[str] = []
    for index, segment in enumerate(parts[:-1]):
        rebuilt.append(segment)
        rebuilt.append(f"${index + 1}")
    rebuilt.append(parts[-1])
    return "".join(rebuilt), params


class DatabaseNotInitialized(RuntimeError):
    pass


async def _retry_db_operation(func, *args, max_retries: int = 3, delay: int = 1, **kwargs):
    """Retry database operation with exponential backoff for transient PostgreSQL errors."""

    await asyncio.sleep(0.1)

    transient_errors = (
        asyncpg.exceptions.DeadlockDetectedError,
        asyncpg.exceptions.SerializationError,
        asyncpg.exceptions.CannotConnectNowError,
        asyncpg.exceptions.TooManyConnectionsError,
    )

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except transient_errors:
            if attempt < max_retries - 1:
                await asyncio.sleep(delay * (2 ** attempt))
                continue
            raise


async def _require_conn():
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


def _row_to_dict(row: Mapping[str, Any]) -> Dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.replace(tzinfo=None).isoformat(sep=" ")
        elif isinstance(value, date):
            data[key] = value.isoformat()
    return data


def _sql_date_today() -> str:
    return "DATE(CURRENT_TIMESTAMP)"


def _sql_interval_expression() -> str:
    return "CURRENT_TIMESTAMP + (?::interval)"


def _interval_parameter(days: int) -> str:
    return f"{days} days"


async def init_db() -> None:
    """Initialise database connection and ensure schema exists."""

    global _CONN, _POOL

    if _CONN is not None:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    if not dsn.startswith(("postgresql://", "postgres://", "postgresql+asyncpg://")):
        raise RuntimeError(
            "Unsupported DATABASE_URL. Only PostgreSQL URLs are supported, "
            "for example postgresql://user:pass@host:5432/dbname."
        )

    postgres_dsn = _normalise_postgres_dsn(dsn)
    min_pool = int(os.getenv("POSTGRES_POOL_MIN", "1"))
    max_pool = int(os.getenv("POSTGRES_POOL_MAX", "10"))
    timeout = float(os.getenv("POSTGRES_POOL_TIMEOUT", "10"))
    _POOL = await asyncpg.create_pool(
        dsn=postgres_dsn,
        min_size=min_pool,
        max_size=max_pool,
        timeout=timeout,
    )
    _CONN = AsyncPGConnection(_POOL)
    await _init_postgres_schema(_POOL)


def _normalise_postgres_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    return dsn


async def _init_postgres_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                is_authenticated INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
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
                reaction_emojis TEXT,
                reaction_chance INTEGER,
                reaction_discussion_chance INTEGER,
                discussion_reply_prompt TEXT,
                discussion_reply_chance INTEGER,
                reaction_sleep_min INTEGER,
                reaction_sleep_max INTEGER,
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
            )
            """
        )

        await connection.execute(
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
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (account_id, channel),
                CHECK (status IN ('pending', 'joined', 'error'))
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS warmup_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channels_per_day INTEGER NOT NULL,
                delay_minutes INTEGER NOT NULL,
                join_start_hour INTEGER NOT NULL,
                join_start_minute INTEGER NOT NULL,
                join_end_hour INTEGER NOT NULL,
                join_end_minute INTEGER NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending
            ON warmup_channels (account_id, status, position)
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS comment_logs (
                id SERIAL PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT,
                message_id INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_sessions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                phone_number TEXT NOT NULL,
                session_data BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, phone_number)
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS account_settings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                phone_number TEXT NOT NULL,
                settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            ALTER TABLE accounts
                ADD COLUMN IF NOT EXISTS reaction_emojis TEXT,
                ADD COLUMN IF NOT EXISTS reaction_chance INTEGER,
                ADD COLUMN IF NOT EXISTS reaction_discussion_chance INTEGER,
                ADD COLUMN IF NOT EXISTS discussion_reply_prompt TEXT,
                ADD COLUMN IF NOT EXISTS discussion_reply_chance INTEGER,
                ADD COLUMN IF NOT EXISTS reaction_sleep_min INTEGER,
                ADD COLUMN IF NOT EXISTS reaction_sleep_max INTEGER,
                ADD COLUMN IF NOT EXISTS reaction_limit_per_message INTEGER,
                ADD COLUMN IF NOT EXISTS last_reaction_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS reactions_enabled INTEGER DEFAULT 1
            """
        )

        await connection.execute(
            """
            ALTER TABLE warmup_channels
                ADD COLUMN IF NOT EXISTS error TEXT,
                ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 0,
                ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ
            """
        )


async def close_db() -> None:
    global _CONN, _POOL
    if _CONN is not None:
        await _CONN.close()
        _CONN = None
    _POOL = None


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


async def upsert_telegram_session(user_id: int, phone_number: str, session_data: bytes) -> None:
    conn = await _require_conn()
    await conn.execute(
        """
        INSERT INTO telegram_sessions (user_id, phone_number, session_data)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, phone_number) DO UPDATE SET
            session_data = excluded.session_data,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, phone_number, session_data),
    )
    await conn.commit()


async def get_telegram_session(user_id: int, phone_number: str) -> Optional[bytes]:
    conn = await _require_conn()
    async with conn.execute(
        "SELECT session_data FROM telegram_sessions WHERE user_id = ? AND phone_number = ?",
        (user_id, phone_number),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    payload = row["session_data"]
    if isinstance(payload, memoryview):  # asyncpg returns memoryview for BYTEA
        return payload.tobytes()
    return bytes(payload) if isinstance(payload, bytearray) else payload


async def delete_telegram_session(user_id: int, phone_number: str) -> None:
    conn = await _require_conn()
    await conn.execute(
        "DELETE FROM telegram_sessions WHERE user_id = ? AND phone_number = ?",
        (user_id, phone_number),
    )
    await conn.commit()


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


def _convert_account_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    data = _row_to_dict(row)
    data["channels"] = _deserialize_list(data.get("channels"))
    data["warmup_channels"] = _deserialize_list(data.get("warmup_channels"))
    data["reaction_emojis"] = _deserialize_list(data.get("reaction_emojis"))
    reactions_enabled = data.get("reactions_enabled")
    if reactions_enabled is not None:
        data["reactions_enabled"] = bool(reactions_enabled)
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
    reaction_chance: Optional[int] = None,
    reaction_discussion_chance: Any = _UNSET,
    discussion_reply_prompt: Any = _UNSET,
    discussion_reply_chance: Any = _UNSET,
    reaction_sleep_min: Optional[int] = None,
    reaction_sleep_max: Optional[int] = None,
    reaction_emojis: Optional[List[str]] = None,
    reaction_limit_per_message: Any = _UNSET,
    last_reaction_at: Any = _UNSET,
    channels: Optional[List[str]] = None,
    reactions_enabled: Any = _UNSET,
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
    if reaction_chance is not None:
        updates.append("reaction_chance = ?")
        values.append(reaction_chance)
    if reaction_discussion_chance is not _UNSET:
        updates.append("reaction_discussion_chance = ?")
        values.append(reaction_discussion_chance)
    if discussion_reply_prompt is not _UNSET:
        updates.append("discussion_reply_prompt = ?")
        values.append(discussion_reply_prompt)
    if discussion_reply_chance is not _UNSET:
        updates.append("discussion_reply_chance = ?")
        values.append(discussion_reply_chance)
    if reaction_sleep_min is not None:
        updates.append("reaction_sleep_min = ?")
        values.append(reaction_sleep_min)
    if reaction_sleep_max is not None:
        updates.append("reaction_sleep_max = ?")
        values.append(reaction_sleep_max)
    if reaction_emojis is not None:
        updates.append("reaction_emojis = ?")
        values.append(_serialize_list(reaction_emojis))
    if reaction_limit_per_message is not _UNSET:
        updates.append("reaction_limit_per_message = ?")
        values.append(reaction_limit_per_message)
    if last_reaction_at is not _UNSET:
        updates.append("last_reaction_at = ?")
        if isinstance(last_reaction_at, datetime):
            values.append(last_reaction_at.isoformat())
        else:
            values.append(last_reaction_at)
    if channels is not None:
        updates.append("channels = ?")
        values.append(_serialize_list(channels))
    if reactions_enabled is not _UNSET:
        updates.append("reactions_enabled = ?")
        if reactions_enabled is None:
            values.append(None)
        else:
            values.append(1 if reactions_enabled else 0)

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


async def bulk_update_reaction_settings(
    user_id: int,
    *,
    reaction_chance: Optional[int] = None,
    reaction_discussion_chance: Any = _UNSET,
    discussion_reply_prompt: Any = _UNSET,
    discussion_reply_chance: Any = _UNSET,
    reaction_sleep_min: Optional[int] = None,
    reaction_sleep_max: Optional[int] = None,
    reaction_emojis: Optional[List[str]] = None,
    reaction_limit_per_message: Any = _UNSET,
    reactions_enabled: Any = _UNSET,
) -> None:
    updates: List[str] = []
    values: List[Any] = []

    if reaction_chance is not None:
        updates.append("reaction_chance = ?")
        values.append(reaction_chance)
    if reaction_discussion_chance is not _UNSET:
        updates.append("reaction_discussion_chance = ?")
        values.append(reaction_discussion_chance)
    if discussion_reply_prompt is not _UNSET:
        updates.append("discussion_reply_prompt = ?")
        values.append(discussion_reply_prompt)
    if discussion_reply_chance is not _UNSET:
        updates.append("discussion_reply_chance = ?")
        values.append(discussion_reply_chance)
    if reaction_sleep_min is not None:
        updates.append("reaction_sleep_min = ?")
        values.append(reaction_sleep_min)
    if reaction_sleep_max is not None:
        updates.append("reaction_sleep_max = ?")
        values.append(reaction_sleep_max)
    if reaction_emojis is not None:
        updates.append("reaction_emojis = ?")
        values.append(_serialize_list(reaction_emojis))
    if reaction_limit_per_message is not _UNSET:
        updates.append("reaction_limit_per_message = ?")
        values.append(reaction_limit_per_message)
    if reactions_enabled is not _UNSET:
        updates.append("reactions_enabled = ?")
        if reactions_enabled is None:
            values.append(None)
        else:
            values.append(1 if reactions_enabled else 0)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")

    conn = await _require_conn()
    values.append(user_id)
    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}
        WHERE user_id = ?
    """

    await conn.execute(query, tuple(values))
    await conn.commit()


async def update_last_reaction_at(account_id: int, timestamp: Optional[datetime]) -> None:
    conn = await _require_conn()
    value = timestamp.isoformat() if timestamp is not None else None
    await conn.execute(
        """
        UPDATE accounts
        SET last_reaction_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (value, account_id),
    )
    await conn.commit()


async def set_account_mode(account_id: int, mode: str, warmup_days: Optional[int] = None) -> None:
    conn = await _require_conn()
    updates: List[str] = ["mode = ?"]
    params: List[Any] = [mode]

    if warmup_days is not None:
        updates.append(f"warmup_end_at = {_sql_interval_expression()}")
        params.append(_interval_parameter(warmup_days))

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
        except asyncpg.exceptions.UniqueViolationError:  # pragma: no cover - defensive branch
            continue

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

    return [_row_to_dict(record) for record in records]


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
    date_expr = _sql_date_today()
    await conn.execute(
        f"""
        UPDATE accounts
        SET warmup_joined_today = 0,
            warmup_last_join = {date_expr},
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
    date_expr = _sql_date_today()
    await conn.execute(
        f"""
        UPDATE accounts
        SET warmup_joined_today = warmup_joined_today + 1,
            warmup_last_join = {date_expr},
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
    return _row_to_dict(row) if row else None


async def mark_account_running(account_id: int) -> None:
    async def _execute_update() -> None:
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

    await _retry_db_operation(_execute_update, max_retries=5)


async def mark_account_stopped(account_id: int) -> None:
    async def _execute_update() -> None:
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

    await _retry_db_operation(_execute_update, max_retries=5)


async def delete_account(user_id: int, phone: str) -> None:
    conn = await _require_conn()
    await conn.execute(
        "DELETE FROM telegram_sessions WHERE user_id = ? AND phone_number = ?",
        (user_id, phone),
    )
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


async def count_reactions_for_message(channel: str, message_id: int) -> int:
    conn = await _require_conn()
    async with conn.execute(
        """
        SELECT COUNT(*) AS reaction_count
        FROM comment_logs
        WHERE status = 'reaction_success' AND channel = ? AND message_id = ?
        """,
        (channel, message_id),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return 0
    return int(row["reaction_count"] or 0)


async def has_successful_comment_log_entry(
    account_id: int,
    channel: Optional[str],
    message_id: Optional[int],
) -> bool:
    """Return True if we previously stored a successful comment for the message."""

    if channel is None or message_id is None:
        return False

    conn = await _require_conn()
    async with conn.execute(
        """
        SELECT 1
        FROM comment_logs
        WHERE account_id = ?
          AND channel = ?
          AND message_id = ?
          AND status = 'success'
        LIMIT 1
        """,
        (account_id, channel, message_id),
    ) as cursor:
        row = await cursor.fetchone()

    return row is not None


async def cleanup_comment_logs(retention_days: int = 2) -> int:
    """Remove comment log entries older than the specified number of days."""

    conn = await _require_conn()
    cursor = await conn.execute(
        "DELETE FROM comment_logs WHERE created_at < CURRENT_TIMESTAMP - ($1 * INTERVAL '1 day')",
        (retention_days,),
    )
    await conn.commit()
    deleted = cursor.rowcount if cursor.rowcount is not None else 0
    await cursor.close()
    return max(deleted, 0)


async def get_global_statistics() -> Dict[str, Any]:
    """Aggregate high-level metrics for accounts, comments and warmup channels."""

    conn = await _require_conn()

    async def _fetch_counts(query: str, params: Iterable[Any] = ()) -> Dict[str, int]:
        async with conn.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}

    accounts_total = 0
    async with conn.execute("SELECT COUNT(*) FROM accounts") as cursor:
        row = await cursor.fetchone()
        if row:
            accounts_total = int(row[0] or 0)

    accounts_by_status = await _fetch_counts(
        "SELECT status, COUNT(*) FROM accounts GROUP BY status"
    )
    accounts_by_mode = await _fetch_counts(
        "SELECT mode, COUNT(*) FROM accounts GROUP BY mode"
    )
    running_by_mode = await _fetch_counts(
        "SELECT mode, COUNT(*) FROM accounts WHERE status = 'running' GROUP BY mode"
    )

    comment_counts = await _fetch_counts(
        """
        SELECT status, COUNT(*)
        FROM comment_logs
        WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '1 day'
        GROUP BY status
        """
    )
    comments_total = sum(comment_counts.values())

    reaction_counts = {
        status[len("reaction_") :]: count
        for status, count in comment_counts.items()
        if status.startswith("reaction_")
    }
    reaction_total = sum(reaction_counts.values())

    warmup_counts = await _fetch_counts(
        "SELECT status, COUNT(*) FROM warmup_channels GROUP BY status"
    )

    warmup_attempts = 0
    async with conn.execute("SELECT COALESCE(SUM(attempts), 0) FROM warmup_channels") as cursor:
        row = await cursor.fetchone()
        if row:
            warmup_attempts = int(row[0] or 0)

    return {
        "accounts": {
            "total": accounts_total,
            "by_status": accounts_by_status,
            "by_mode": accounts_by_mode,
            "running_by_mode": running_by_mode,
        },
        "comments": {
            "total": comments_total,
            "by_status": comment_counts,
            "reactions": reaction_counts,
            "reactions_total": reaction_total,
        },
        "warmup": {
            "by_status": warmup_counts,
            "total_attempts": warmup_attempts,
        },
    }
