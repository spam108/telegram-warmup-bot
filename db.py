import os
import json
import importlib
import importlib.util
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import asyncpg as asyncpg_type

_asyncpg_spec = importlib.util.find_spec("asyncpg")

if _asyncpg_spec is not None:
    asyncpg = importlib.import_module("asyncpg")
else:  # pragma: no cover - executed only when asyncpg is not installed
    asyncpg = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

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


_POOL: Optional[AsyncpgPool] = None
_BACKEND: str = "postgres"
_UNSET = object()

REQUIRED_TABLES = {
    "users",
    "accounts",
    "comment_logs",
    "warmup_channels",
    "warmup_pending_channels",
    "warmup_logs",
    "warmup_settings",
    "posts",
    "channel_blacklist",
}

DEFAULT_REACTION_EMOJIS = ['❤️', '👍', '🔥', '🎉', '👏']


def _normalise_postgres_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    if dsn.startswith("postgres+asyncpg://"):
        return "postgres://" + dsn[len("postgres+asyncpg://") :]
    return dsn


class DatabaseNotInitialized(RuntimeError):
    pass


def _is_postgres() -> bool:
    return _BACKEND == "postgres"


def _is_sqlite() -> bool:
    return _BACKEND == "sqlite"


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
    if _POOL is None:
        raise DatabaseNotInitialized("PostgreSQL connection pool is not initialised. Call init_db() first.")
    return _POOL


def _as_tuple(params: Iterable[Any]) -> Tuple[Any, ...]:
    if isinstance(params, tuple):
        return params
    if isinstance(params, list):
        return tuple(params)
    return tuple(params)


def _convert_placeholders(query: str) -> str:
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


def adapt_bool(value: bool) -> Any:
    """Universal adapter for boolean values across database backends."""

    if _is_postgres():
        return value
    return 1 if value else 0


def _adapt_value(value: Any) -> Any:
    if isinstance(value, bool):
        return adapt_bool(value)
    if isinstance(value, (list, tuple)):
        value_type = type(value)
        return value_type(_adapt_value(item) for item in value)
    return value


def adapt_params(params: Sequence[Any]) -> Tuple[Any, ...]:
    """Adapt a sequence of parameters for the active database backend."""

    if not params:
        return tuple()
    return tuple(_adapt_value(param) for param in params)


def _prepare_query(query: str, params: Sequence[Any]) -> Tuple[str, Tuple[Any, ...]]:
    prepared_query = _convert_placeholders(query)
    prepared_params = adapt_params(params)
    return prepared_query, prepared_params


async def _execute(query: str, params: Sequence[Any] = ()) -> None:
    params_tuple = _as_tuple(params)
    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    logger.debug("Executing query: %s with params: %s", prepared_query, prepared_params)
    async with pool.acquire() as connection:
        await connection.execute(prepared_query, *prepared_params)


async def _execute_rowcount(query: str, params: Sequence[Any] = ()) -> int:
    params_tuple = _as_tuple(params)
    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    logger.debug("Executing query for rowcount: %s with params: %s", prepared_query, prepared_params)
    async with pool.acquire() as connection:
        result = await connection.execute(prepared_query, *prepared_params)
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):  # pragma: no cover - defensive branch
        return 0


async def _fetchone(query: str, params: Sequence[Any] = ()):
    params_tuple = _as_tuple(params)
    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    logger.debug("Fetching one: %s with params: %s", prepared_query, prepared_params)
    async with pool.acquire() as connection:
        return await connection.fetchrow(prepared_query, *prepared_params)


async def _fetchall(query: str, params: Sequence[Any] = ()):
    params_tuple = _as_tuple(params)
    pool = _require_pool()
    prepared_query, prepared_params = _prepare_query(query, params_tuple)
    logger.debug("Fetching all: %s with params: %s", prepared_query, prepared_params)
    async with pool.acquire() as connection:
        return await connection.fetch(prepared_query, *prepared_params)


async def init_db() -> None:
    """Initialise database connection pool and ensure schema exists."""

    global _POOL, _BACKEND

    if _POOL is not None:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    dsn = _normalise_postgres_dsn(dsn)

    if not dsn.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must use the postgres scheme")

    if asyncpg is None:  # pragma: no cover - requires asyncpg installed
        raise RuntimeError(
            "asyncpg is required for PostgreSQL connections. Install the 'asyncpg' package to use a PostgreSQL DSN."
        )

    _POOL = await asyncpg.create_pool(dsn)
    _BACKEND = "postgres"
    await _init_postgres_schema()
    await ensure_warmup_tables()

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
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (account_id, setting_key)
            )
            """
        )

        await ensure_warmup_tables(connection)

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_blacklist (
                account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
                channel_id TEXT NOT NULL,
                reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, channel_id)
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
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_settings (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                setting_key TEXT NOT NULL,
                setting_value TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (account_id, setting_key)
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                post_id BIGINT NOT NULL,
                message TEXT,
                has_media BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, channel, post_id)
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                message_id BIGINT NOT NULL,
                emoji TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                post_id INTEGER NOT NULL,
                message TEXT,
                has_media BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (account_id, channel, post_id)
            )
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_posts_account_id
            ON posts (account_id)
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id
            ON reaction_logs (account_id)
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
                UNIQUE (user_id, phone)
            )
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_account_settings_account_id
            ON account_settings (account_id)
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_telegram_sessions_user_phone
            ON telegram_sessions (user_id, phone)
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_posts_account_id
            ON posts (account_id)
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id
            ON reaction_logs (account_id)
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reaction_logs_channel_message
            ON reaction_logs (channel, message_id)
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

        required_tables = {
            "users",
            "accounts",
            "comment_logs",
            "reaction_settings",
            "warmup_channels",
            "warmup_logs",
            "posts",
        }
        existing = await connection.fetch(
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname = 'public' AND tablename = ANY($1::text[])
            """,
            list(required_tables),
        )
        present_tables = {row["tablename"] for row in existing}
        missing_tables = required_tables - present_tables
        if missing_tables:
            raise RuntimeError(
                f"Missing required database tables: {', '.join(sorted(missing_tables))}"
            )
        logger.info("Verified required database tables: %s", sorted(present_tables))


async def close_db() -> None:
    global _POOL
    if _POOL is not None:
        await _POOL.close()
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


async def ensure_warmup_tables(connection: Optional[Any] = None) -> None:
    async def _ensure(conn: Any) -> None:
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
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS warmup_channels (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TIMESTAMPTZ,
                joined_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (account_id, channel),
                CHECK (status IN ('pending', 'joined', 'error', 'processing'))
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS warmup_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT,
                status TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await conn.execute(
            "DROP INDEX IF EXISTS idx_warmup_channels_pending"
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warmup_channels_status
            ON warmup_channels (account_id, status, position)
            """
        )

        await conn.execute(
            "DROP INDEX IF EXISTS idx_warmup_pending_channels_status"
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS warmup_pending_channels (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                channel_username TEXT,
                position INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (account_id, channel)
            )
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warmup_pending_account
            ON warmup_pending_channels(account_id, position)
            """
        )

        await conn.execute(
            """
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS warmup_joined_today INTEGER NOT NULL DEFAULT 0
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ADD COLUMN IF NOT EXISTS position INTEGER
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ALTER COLUMN position SET DEFAULT 0
            """
        )

        await conn.execute(
            """
            UPDATE warmup_channels
            SET position = 0
            WHERE position IS NULL
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ALTER COLUMN position SET NOT NULL
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ADD COLUMN IF NOT EXISTS status TEXT
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ALTER COLUMN status SET DEFAULT 'pending'
            """
        )

        await conn.execute(
            """
            UPDATE warmup_channels
            SET status = 'pending'
            WHERE status IS NULL
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ALTER COLUMN status SET NOT NULL
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            DROP CONSTRAINT IF EXISTS warmup_channels_status_check
            """
        )

        await conn.execute(
            """
            ALTER TABLE warmup_channels
            ADD CONSTRAINT warmup_channels_status_check
            CHECK (status IN ('pending', 'joined', 'error', 'processing'))
            """
        )

    if connection is not None:
        await _ensure(connection)
        return

    pool = _require_pool()
    async with pool.acquire() as connection_obj:
        await _ensure(connection_obj)


async def ensure_user(user_id: int) -> None:
    await _execute(
        """
        INSERT INTO users (user_id)
        VALUES (?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """,
        (user_id,),
    )


async def set_user_authenticated(user_id: int, value: bool) -> None:
    await _execute(
        "UPDATE users SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (adapt_bool(value), user_id),
    )


async def is_user_authenticated(user_id: int) -> bool:
    row = await _fetchone("SELECT is_authenticated FROM users WHERE user_id = ?", (user_id,))
    return bool(row["is_authenticated"]) if row else False


async def ensure_account(user_id: int, phone: str, session_path: str) -> Optional[int]:
    """Create or update an account and return its database identifier."""

    row = await _fetchone(
        """
        INSERT INTO accounts (user_id, phone, session_path, status, mode)
        VALUES (?, ?, ?, 'stopped', 'warmup')
        ON CONFLICT(user_id, phone) DO UPDATE SET
            session_path = excluded.session_path,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (user_id, phone, session_path),
    )
    return int(row["id"]) if row is not None else None


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


async def get_all_accounts() -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts ORDER BY id",
        (),
    )


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
    reaction_emojis: Any = _UNSET,
    reaction_limit_per_message: Any = _UNSET,
    last_reaction_at: Any = _UNSET,
    channels: Optional[List[str]] = None,
    reactions_enabled: Any = _UNSET,
) -> None:
    logger.debug(
        "update_account_settings called with account_id=%s, chance=%s, sleep_min=%s, sleep_max=%s, system_prompt=%s",
        account_id,
        chance,
        sleep_min,
        sleep_max,
        system_prompt,
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
    if reaction_emojis is not _UNSET:
        if reaction_emojis is None:
            reaction_emojis = DEFAULT_REACTION_EMOJIS
        updates.append("reaction_emojis = ?")
        values.append(_serialize_list(reaction_emojis))
    if reaction_limit_per_message is not _UNSET:
        updates.append("reaction_limit_per_message = ?")
        values.append(reaction_limit_per_message)
    if last_reaction_at is not _UNSET:
        updates.append("last_reaction_at = ?")
        if isinstance(last_reaction_at, datetime):
            values.append(last_reaction_at)
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
            values.append(adapt_bool(bool(reactions_enabled)))

    if not updates:
        logger.debug("update_account_settings: no updates to apply for account_id=%s", account_id)
        return

    values.append(account_id)
    assignments = ", ".join(updates)
    query = f"""
        UPDATE accounts
        SET {assignments}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """

    await _execute(query, tuple(values))

    logger.debug("update_account_settings completed for account_id=%s", account_id)


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
            values.append(adapt_bool(bool(reactions_enabled)))

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")

    values.append(user_id)
    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}
        WHERE user_id = ?
    """

    await _execute(query, tuple(values))


async def update_last_reaction_at(
    account_id: int,
    reaction_time: Optional[Any],
) -> None:
    """Persist the timestamp of the last reaction for an account.

    The function accepts timezone-aware :class:`datetime.datetime` objects,
    naive datetimes (which are normalised to UTC) or ISO formatted strings.  The
    relaxed parsing helps when external integrations provide serialized values
    while keeping the column consistent in UTC.
    """

    if reaction_time is None:
        value: Optional[datetime] = None
    else:
        dt_value: datetime
        if isinstance(reaction_time, str):
            normalised = reaction_time.replace("Z", "+00:00")
            dt_value = datetime.fromisoformat(normalised)
        elif isinstance(reaction_time, datetime):
            dt_value = reaction_time
        else:
            raise TypeError("reaction_time must be datetime, str or None")

        if dt_value.tzinfo is None:
            value = dt_value.replace(tzinfo=timezone.utc)
        else:
            value = dt_value.astimezone(timezone.utc)

    await _execute(
        """
        UPDATE accounts
        SET last_reaction_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (value, account_id),
    )


async def set_account_mode(account_id: int, mode: str, warmup_days: Optional[int] = None) -> None:
    if mode not in {"standard", "warmup"}:
        raise ValueError("mode must be either 'standard' or 'warmup'")

    updates: List[str] = ["mode = ?"]
    params: List[Any] = [mode]

    if warmup_days is not None:
        target = datetime.utcnow() + timedelta(days=warmup_days)
        updates.append("warmup_end_at = ?")
        params.append(target)

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
        updates.extend(
            [
                "warmup_joined_today = 0",
                "warmup_last_join = NULL",
                "warmup_last_join_at = NULL",
                "warmup_next_join_at = NULL",
            ]
        )

    params.append(account_id)

    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """

    await _execute(query, tuple(params))


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



async def get_warmup_channels(account_id: int) -> List[Dict[str, Any]]:
    """Return all warmup channels for the specified account ordered by queue position."""
    records = await _fetchall(
        """
        SELECT *
        FROM warmup_channels
        WHERE account_id = ?
        ORDER BY position, id
        """,
        (account_id,),
    )

    return [dict(record) for record in records]


async def get_warmup_pending(
    account_id: int, *, limit: int = 1, reset_if_empty: bool = False
) -> List[Dict[str, Any]]:
    """Fetch pending warmup channels for an account ordered by queue position."""
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
    """Mark a warmup channel as successfully joined for the given account."""
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


async def db_update_warmup_schedule(
    account_id: int,
    *,
    next_join: Optional[datetime] = None,
    last_join: Optional[datetime] = None,
    warmup_end: Optional[datetime] = None,
) -> None:
    updates: List[str] = []
    params: List[Any] = []



    if next_join is not None:
        updates.append("warmup_next_join_at = ?")
        params.append(next_join)

    if last_join is not None:
        updates.append("warmup_last_join_at = ?")
        params.append(last_join)
        updates.append("warmup_last_join = ?")
        params.append(last_join.date())

    if warmup_end is not None:
        updates.append("warmup_end_at = ?")
        params.append(warmup_end)

    if not updates:
        return

    params.append(account_id)
    query = f"""
        UPDATE accounts
        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """

    await _execute(query, tuple(params))


async def increment_warmup_joined(account_id: int) -> None:
    """Increment the warmup joins counter for an account and update timestamps."""
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
    logger.debug("Marking account %s as running", account_id)
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


async def mark_account_stopped(account_id: int) -> None:
    logger.debug("Marking account %s as stopped", account_id)
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


async def delete_account(account_id: int, phone: str) -> None:
    await _execute(
        "DELETE FROM accounts WHERE id = ? AND phone = ?",
        (account_id, phone),
    )


async def get_running_accounts() -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts WHERE status = 'running'",
        (),
    )


async def get_running_standard_accounts() -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts WHERE status = 'running' AND mode = 'standard'",
        (),
    )


async def get_running_warmup_accounts() -> List[Dict[str, Any]]:
    return await _fetch_accounts(
        "SELECT * FROM accounts WHERE status = 'running' AND mode = 'warmup'",
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
    emoji: Optional[str] = None,
) -> None:
    await _execute(
        """
        INSERT INTO comment_logs (account_id, channel, message_id, status, error)
        VALUES (?, ?, ?, ?, ?)
        """,
        (account_id, channel, message_id, status, error),
    )

    if status.startswith("reaction_"):
        reaction_status = status[len("reaction_") :]
        base_status = reaction_status.split("_", 1)[0]
        if base_status == "error":
            normalized_status = "failed"
        else:
            normalized_status = base_status

        await add_reaction_log(
            account_id,
            channel=channel,
            message_id=message_id,
            emoji=emoji,
            status=normalized_status,
            error_message=error,
        )


async def record_post(
    account_id: int,
    *,
    channel: str,
    post_id: int,
    message: Optional[str],
    has_media: bool,
) -> None:
    await _execute(
        """
        INSERT INTO posts (account_id, channel, post_id, message, has_media)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (account_id, channel, post_id) DO UPDATE SET
            message = EXCLUDED.message,
            has_media = EXCLUDED.has_media,
            created_at = CURRENT_TIMESTAMP
        """,
        (
            account_id,
            channel,
            post_id,
            message,
            adapt_bool(has_media),
        ),
    )


async def add_to_channel_blacklist(
    account_id: int, channel_id: str, reason: Optional[str] = None
) -> None:
    await _execute(
        """
        INSERT INTO channel_blacklist (account_id, channel_id, reason)
        VALUES (?, ?, ?)
        ON CONFLICT (account_id, channel_id) DO UPDATE SET
            reason = EXCLUDED.reason
        """,
        (account_id, channel_id, reason),
    )


async def is_channel_blacklisted(account_id: int, channel_id: str) -> bool:
    row = await _fetchone(
        """
        SELECT 1
        FROM channel_blacklist
        WHERE account_id = ? AND channel_id = ?
        """,
        (account_id, channel_id),
    )
    return row is not None


async def add_reaction_log(
    account_id: int,
    *,
    channel: Optional[str],
    message_id: Optional[int],
    emoji: Optional[str],
    status: str,
    error_message: Optional[str] = None,
) -> None:
    emoji_value = emoji if emoji else "N/A"
    channel_value = channel or ""
    message_value = message_id if message_id is not None else 0
    await _execute(
        """
        INSERT INTO reaction_logs (account_id, channel, message_id, emoji, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (account_id, channel_value, message_value, emoji_value, status, error_message),
    )


async def count_reactions_for_message(channel: str, message_id: int) -> int:
    row = await _fetchone(
        """
        SELECT COUNT(*) AS reaction_count
        FROM reaction_logs
        WHERE status = 'success' AND channel = ? AND message_id = ?
        """,
        (channel, message_id),
    )
    if row and row["reaction_count"] is not None:
        return int(row["reaction_count"])

    fallback_row = await _fetchone(
        """
        SELECT COUNT(*) AS reaction_count
        FROM comment_logs
        WHERE status = 'reaction_success' AND channel = ? AND message_id = ?
        """,
        (channel, message_id),
    )
    if not fallback_row:
        return 0
    return int(fallback_row["reaction_count"] or 0)


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

    query = "DELETE FROM comment_logs WHERE created_at < (CURRENT_TIMESTAMP - ($1 * INTERVAL '1 day'))"
    params = (retention_days,)

    deleted = await _execute_rowcount(query, params)
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
