import os
import json
import asyncio
import importlib
import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import asyncpg as asyncpg_type

_asyncpg_spec = importlib.util.find_spec("asyncpg")

if _asyncpg_spec is not None:
    asyncpg = importlib.import_module("asyncpg")
else:  # pragma: no cover - executed only when asyncpg is not installed
    asyncpg = None  # type: ignore[assignment]

if TYPE_CHECKING:
    AsyncpgPool = asyncpg_type.pool.Pool  # pragma: no cover - typing only
else:
    AsyncpgPool = Any


class _AsyncpgUniqueViolationError(Exception):
    """Placeholder error used when asyncpg is unavailable."""


if _asyncpg_spec is not None:
    AsyncpgUniqueViolationError = asyncpg.UniqueViolationError  # type: ignore[attr-defined]
else:  # pragma: no cover - executed only when asyncpg is not installed
    AsyncpgUniqueViolationError = _AsyncpgUniqueViolationError


_CONN: Optional[aiosqlite.Connection] = None
_POOL: Optional[AsyncpgPool] = None
_BACKEND: Optional[str] = None
_SQLITE_LOCK: asyncio.Lock = asyncio.Lock()
_UNSET = object()


def _is_sqlite() -> bool:
    return _BACKEND == "sqlite"


def _is_postgres() -> bool:
    return _BACKEND == "postgres"


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
    if not _is_sqlite() or _CONN is None:
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


def _require_pool() -> AsyncpgPool:
    if not _is_postgres() or _POOL is None:
        raise DatabaseNotInitialized("PostgreSQL connection pool is not initialised. Call init_db() first.")
    return _POOL


def _as_tuple(params: Iterable[Any]) -> Tuple[Any, ...]:
    if isinstance(params, tuple):
        return params
    if isinstance(params, list):
        return tuple(params)
    return tuple(params)


def _convert_placeholders(query: str) -> str:
    if not _is_postgres():
        return query

    result: List[str] = []
    param_index = 1
    in_single = False
    in_double = False
    i = 0
    length = len(query)

    while i < length:
        ch = query[i]
        if ch == "'" and not in_double:
            result.append(ch)
            if in_single and i + 1 < length and query[i + 1] == "'":
                result.append("'")
                i += 1
            else:
                in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif ch == "?" and not in_single and not in_double:
            result.append(f"${param_index}")
            param_index += 1
        else:
            result.append(ch)
        i += 1

    return "".join(result)


def _prepare_query(query: str, params: Sequence[Any]) -> Tuple[str, Tuple[Any, ...]]:
    return _convert_placeholders(query), tuple(params)


async def _execute(query: str, params: Sequence[Any] = ()) -> None:
    params_tuple = _as_tuple(params)

    if _is_sqlite():
        conn = await _require_conn()
        async with _SQLITE_LOCK:
            await conn.execute(query, params_tuple)
        return

    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    async with pool.acquire() as connection:
        await connection.execute(prepared_query, *prepared_params)


async def _execute_rowcount(query: str, params: Sequence[Any] = ()) -> int:
    params_tuple = _as_tuple(params)

    if _is_sqlite():
        conn = await _require_conn()
        async with _SQLITE_LOCK:
            cursor = await conn.execute(query, params_tuple)
            rowcount = cursor.rowcount if cursor.rowcount is not None else 0
            await cursor.close()
        return int(rowcount)

    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    async with pool.acquire() as connection:
        result = await connection.execute(prepared_query, *prepared_params)
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):  # pragma: no cover - defensive branch
        return 0


async def _fetchone(query: str, params: Sequence[Any] = ()):
    params_tuple = _as_tuple(params)

    if _is_sqlite():
        conn = await _require_conn()
        async with _SQLITE_LOCK:
            async with conn.execute(query, params_tuple) as cursor:
                return await cursor.fetchone()

    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    async with pool.acquire() as connection:
        return await connection.fetchrow(prepared_query, *prepared_params)


async def _fetchall(query: str, params: Sequence[Any] = ()):
    params_tuple = _as_tuple(params)

    if _is_sqlite():
        conn = await _require_conn()
        async with _SQLITE_LOCK:
            async with conn.execute(query, params_tuple) as cursor:
                return await cursor.fetchall()

    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    async with pool.acquire() as connection:
        return await connection.fetch(prepared_query, *prepared_params)


async def _commit() -> None:
    if _is_sqlite():
        conn = await _require_conn()
        async with _SQLITE_LOCK:
            await conn.commit()


async def init_db() -> None:
    """Initialise database connection and ensure schema exists."""

    global _CONN, _POOL, _BACKEND

    if _BACKEND is not None:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    if dsn.startswith(("postgres://", "postgresql://")):
        if asyncpg is None:  # pragma: no cover - requires asyncpg installed
            raise RuntimeError(
                "asyncpg is required for PostgreSQL connections. Install the 'asyncpg' package to use a PostgreSQL DSN."
            )
        _BACKEND = "postgres"
        _POOL = await asyncpg.create_pool(dsn)
        await _init_postgres_schema()
        return

    if dsn.startswith("sqlite:///"):
        db_path = dsn[len("sqlite:///") :]
    else:
        db_path = dsn

    _BACKEND = "sqlite"

    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Give SQLite more room to breathe under concurrent load.  The warmup bot
    # opens a single connection that is shared across many asyncio tasks.  When
    # several of those tasks try to write at the same time the default settings
    # tend to raise ``database is locked`` errors.  Enabling WAL drastically
    # improves writer concurrency and the busy timeout makes SQLite wait for a
    # short period instead of failing immediately.
    conn = await aiosqlite.connect(db_path, timeout=30)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout = 5000")

    await _init_sqlite_schema(conn)
    _CONN = conn

async def _init_sqlite_schema(conn: aiosqlite.Connection) -> None:
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
            reactions_enabled INTEGER NOT NULL DEFAULT 1,
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

    async def _ensure_column(table: str, column: str, definition: str) -> None:
        try:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:  # pragma: no cover - defensive branch
            if "duplicate column name" not in str(exc).lower():
                raise

    await _ensure_column("accounts", "reaction_emojis", "TEXT")
    await _ensure_column("accounts", "reaction_chance", "INTEGER")
    await _ensure_column("accounts", "reaction_discussion_chance", "INTEGER")
    await _ensure_column("accounts", "discussion_reply_prompt", "TEXT")
    await _ensure_column("accounts", "discussion_reply_chance", "INTEGER")
    await _ensure_column("accounts", "reaction_sleep_min", "INTEGER")
    await _ensure_column("accounts", "reaction_sleep_max", "INTEGER")
    await _ensure_column("accounts", "reaction_limit_per_message", "INTEGER")
    await _ensure_column("accounts", "last_reaction_at", "TEXT")
    await _ensure_column("accounts", "reactions_enabled", "INTEGER NOT NULL DEFAULT 1")

    await conn.commit()


async def _init_postgres_schema() -> None:
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
                reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (user_id, phone),
                CHECK (mode IN ('warmup', 'standard'))
            )
            """
        )

        await connection.execute(
            """
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
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT,
                message_id BIGINT,
                status TEXT NOT NULL,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE
            """
        )


async def close_db() -> None:
    global _CONN, _POOL, _BACKEND
    if _CONN is not None:
        await _CONN.close()
        _CONN = None
    if _POOL is not None:
        await _POOL.close()
        _POOL = None
    _BACKEND = None


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

    await _execute(
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
    await _commit()


async def get_warmup_settings() -> Dict[str, int]:
    """Fetch the warmup settings row."""

    row = await _fetchone(
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
    )

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

    await _execute(query, tuple(params))
    await _commit()


async def ensure_user(user_id: int) -> None:
    await _execute(
        """
        INSERT INTO users (user_id)
        VALUES (?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """,
        (user_id,),
    )
    await _commit()


async def set_user_authenticated(user_id: int, value: bool) -> None:
    await _execute(
        "UPDATE users SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (1 if value else 0, user_id),
    )
    await _commit()


async def is_user_authenticated(user_id: int) -> bool:
    row = await _fetchone("SELECT is_authenticated FROM users WHERE user_id = ?", (user_id,))
    return bool(row["is_authenticated"]) if row else False


async def ensure_account(user_id: int, phone: str, session_path: str) -> Dict[str, Any]:
    await _execute(
        """
        INSERT INTO accounts (user_id, phone, session_path)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, phone) DO UPDATE SET
            session_path = excluded.session_path,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, phone, session_path),
    )
    await _commit()
    account = await get_account_by_session(user_id, phone)
    if account is None:  # pragma: no cover - defensive branch
        raise RuntimeError("Failed to create or update account")
    return account


async def _fetch_accounts(query: str, params: Iterable[Any]) -> List[Dict[str, Any]]:
    rows = await _fetchall(query, tuple(params))
    return [_convert_account_row(row) for row in rows]


def _convert_account_row(row: Any) -> Dict[str, Any]:
    data = dict(row)
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
    row = await _fetchone(
        "SELECT * FROM accounts WHERE user_id = ? AND phone = ?",
        (user_id, phone),
    )
    return _convert_account_row(row) if row else None


async def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    row = await _fetchone(
        "SELECT * FROM accounts WHERE id = ?",
        (account_id,),
    )
    return _convert_account_row(row) if row else None


async def get_warmup_queue_stats(account_id: int) -> Dict[str, int]:
    row = await _fetchone(
        """
        SELECT
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN status = 'joined' THEN 1 ELSE 0 END) AS joined_count,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count
        FROM warmup_channels
        WHERE account_id = ?
        """,
        (account_id,),
    )
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
            if _is_postgres():
                values.append(bool(reactions_enabled))
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

    async def _perform_update() -> None:
        await _execute(query, tuple(values))
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_perform_update, max_retries=5)
    else:
        await _perform_update()

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
            if _is_postgres():
                values.append(bool(reactions_enabled))
            else:
                values.append(1 if reactions_enabled else 0)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")

    values.append(user_id)
    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}
        WHERE user_id = ?
    """

    async def _perform_update() -> None:
        await _execute(query, tuple(values))
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_perform_update, max_retries=5)
    else:
        await _perform_update()


async def update_last_reaction_at(account_id: int, timestamp: Optional[datetime]) -> None:
    value = timestamp.isoformat() if timestamp is not None else None
    await _execute(
        """
        UPDATE accounts
        SET last_reaction_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (value, account_id),
    )
    await _commit()


async def set_account_mode(account_id: int, mode: str, warmup_days: Optional[int] = None) -> None:
    updates: List[str] = ["mode = ?"]
    params: List[Any] = [mode]

    if warmup_days is not None:
        target = datetime.utcnow() + timedelta(days=warmup_days)
        updates.append("warmup_end_at = ?")
        params.append(target.isoformat())

    if mode == "warmup":
        updates.extend(
            [
                "warmup_joined_today = 0",
                "warmup_last_join = CURRENT_DATE",
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

    await _execute(query, tuple(params))
    await _commit()


async def sync_warmup_channels(account_id: int, channels: List[str]) -> None:
    seen: set[str] = set()
    unique_channels = [x for x in channels if not (x in seen or seen.add(x))]

    await _execute("DELETE FROM warmup_channels WHERE account_id = ?", (account_id,))

    for idx, channel in enumerate(unique_channels, start=1):
        try:
            await _execute(
                """
                INSERT INTO warmup_channels (account_id, channel, position)
                VALUES (?, ?, ?)
                """,
                (account_id, channel, idx),
            )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - defensive branch
            if "unique" not in str(exc).lower():
                raise
        except AsyncpgUniqueViolationError:  # pragma: no cover - PostgreSQL duplicate guard
            continue

    await _execute(
        """
        UPDATE accounts
        SET warmup_channels = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (_serialize_list(unique_channels), account_id),
    )

    await _commit()


async def get_warmup_pending(
    account_id: int, *, limit: int = 15, reset_if_empty: bool = False
) -> List[Dict[str, Any]]:
    records = await _fetchall(
        """
        SELECT *
        FROM warmup_channels
        WHERE account_id = ? AND status = 'pending'
        ORDER BY position
        LIMIT ?
        """,
        (account_id, limit),
    )

    if (not records) and reset_if_empty:
        account = await get_account_by_id(account_id)
        if account:
            base_channels = account.get("warmup_channels") or []
            active_channels = set(account.get("channels") or [])
            queue = [chl for chl in base_channels if chl not in active_channels]
            if queue:
                await sync_warmup_channels(account_id, queue)
                records = await _fetchall(
                    """
                    SELECT *
                    FROM warmup_channels
                    WHERE account_id = ? AND status = 'pending'
                    ORDER BY position
                    LIMIT ?
                    """,
                    (account_id, limit),
                )

    return [dict(record) for record in records]


async def mark_warmup_channel_joined(account_id: int, channel: str) -> None:
    await _execute(
        """
        UPDATE warmup_channels
        SET status = 'joined',
            joined_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE account_id = ? AND channel = ?
        """,
        (account_id, channel),
    )
    await _commit()


async def record_warmup_channel_error(account_id: int, channel: str, error: str) -> None:
    await _execute(
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
    await _commit()


async def reset_warmup_daily_state(account_id: int) -> None:
    await _execute(
        """
        UPDATE accounts
        SET warmup_joined_today = 0,
            warmup_last_join = CURRENT_DATE,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account_id,),
    )
    await _commit()


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

    await _execute(query, tuple(params))
    await _commit()


async def increment_warmup_joined(account_id: int) -> None:
    await _execute(
        """
        UPDATE accounts
        SET warmup_joined_today = warmup_joined_today + 1,
            warmup_last_join = CURRENT_DATE,
            warmup_last_join_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account_id,),
    )
    await _commit()


async def get_warmup_stats(account_id: int) -> Optional[Dict[str, Any]]:
    row = await _fetchone(
        """
        SELECT warmup_joined_today, warmup_last_join, warmup_end_at, mode
        FROM accounts
        WHERE id = ?
        """,
        (account_id,),
    )
    return dict(row) if row else None


async def mark_account_running(account_id: int) -> None:
    async def _execute_update() -> None:
        await _execute(
            """
            UPDATE accounts
            SET status = 'running',
                last_started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_execute_update, max_retries=5)
    else:
        await _execute_update()


async def mark_account_stopped(account_id: int) -> None:
    async def _execute_update() -> None:
        await _execute(
            """
            UPDATE accounts
            SET status = 'stopped',
                last_stopped_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_execute_update, max_retries=5)
    else:
        await _execute_update()


async def delete_account(user_id: int, phone: str) -> None:
    await _execute(
        "DELETE FROM accounts WHERE user_id = ? AND phone = ?",
        (user_id, phone),
    )
    await _commit()


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
        await _execute(
            """
            INSERT INTO comment_logs (account_id, channel, message_id, status, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_id, channel, message_id, status, error),
        )
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_execute_log)
    else:
        await _execute_log()


async def count_reactions_for_message(channel: str, message_id: int) -> int:
    row = await _fetchone(
        """
        SELECT COUNT(*) AS reaction_count
        FROM comment_logs
        WHERE status = 'reaction_success' AND channel = ? AND message_id = ?
        """,
        (channel, message_id),
    )
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

    row = await _fetchone(
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
    )

    return row is not None


async def cleanup_comment_logs(retention_days: int = 2) -> int:
    """Remove comment log entries older than the specified number of days."""

    if _is_sqlite():
        query = "DELETE FROM comment_logs WHERE created_at < datetime('now', ?)"
        params: Tuple[Any, ...] = (f"-{retention_days} day",)
    else:
        query = "DELETE FROM comment_logs WHERE created_at < (CURRENT_TIMESTAMP - ($1 * INTERVAL '1 day'))"
        params = (retention_days,)

    deleted = await _execute_rowcount(query, params)
    await _commit()
    return max(int(deleted), 0)


async def get_global_statistics() -> Dict[str, Any]:
    """Aggregate high-level metrics for accounts, comments and warmup channels."""

    async def _fetch_counts(query: str, params: Iterable[Any] = ()) -> Dict[str, int]:
        rows = await _fetchall(query, tuple(params))
        return {str(row[0]): int(row[1] or 0) for row in rows}

    total_row = await _fetchone("SELECT COUNT(*) FROM accounts")
    accounts_total = int(total_row[0] or 0) if total_row else 0

    accounts_by_status = await _fetch_counts(
        "SELECT status, COUNT(*) FROM accounts GROUP BY status"
    )
    accounts_by_mode = await _fetch_counts(
        "SELECT mode, COUNT(*) FROM accounts GROUP BY mode"
    )
    running_by_mode = await _fetch_counts(
        "SELECT mode, COUNT(*) FROM accounts WHERE status = 'running' GROUP BY mode"
    )

    if _is_sqlite():
        comment_counts_query = (
            """
            SELECT status, COUNT(*)
            FROM comment_logs
            WHERE created_at >= datetime('now', '-1 day')
            GROUP BY status
            """
        )
    else:
        comment_counts_query = (
            """
            SELECT status, COUNT(*)
            FROM comment_logs
            WHERE created_at >= (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY status
            """
        )

    comment_counts = await _fetch_counts(comment_counts_query)
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

    attempts_row = await _fetchone(
        "SELECT COALESCE(SUM(attempts), 0) FROM warmup_channels"
    )
    warmup_attempts = int(attempts_row[0] or 0) if attempts_row else 0

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
