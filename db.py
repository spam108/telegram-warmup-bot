import asyncio
import os
import json
import importlib
import importlib.util
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TYPE_CHECKING
from urllib.parse import urlparse

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


@dataclass
class DatabaseConfig:
    """Runtime configuration for the PostgreSQL database."""

    dsn: str
    user: str
    password: str
    database: str
    host: str
    port: int
    admin_dsn: Optional[str]
    backup_dir: Optional[Path]


class _AsyncpgUniqueViolationError(Exception):
    """Placeholder error used when asyncpg is unavailable."""


if _asyncpg_spec is not None:
    AsyncpgUniqueViolationError = asyncpg.UniqueViolationError  # type: ignore[attr-defined]
else:  # pragma: no cover - executed only when asyncpg is not installed
    AsyncpgUniqueViolationError = _AsyncpgUniqueViolationError


_POOL: Optional[AsyncpgPool] = None
_BACKEND: str = "postgres"
_DB_CONFIG: Optional[DatabaseConfig] = None
_UNSET = object()

DEFAULT_REACTION_EMOJIS = ['❤️', '👍', '🔥', '🎉', '👏']
DEFAULT_REACTION_EMOJIS_JSON = json.dumps(DEFAULT_REACTION_EMOJIS, ensure_ascii=False)
DEFAULT_BACKUP_DIR = Path("backups")

SCHEMA_CREATE_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        user_id BIGINT UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        is_authenticated BOOLEAN NOT NULL DEFAULT FALSE,
        last_login TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        session TEXT NOT NULL,
        session_path TEXT,
        phone TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'standard',
        sleep_min INTEGER NOT NULL DEFAULT 10,
        sleep_max INTEGER NOT NULL DEFAULT 30,
        chance INTEGER NOT NULL DEFAULT 50,
        system_prompt TEXT,
        warmup_joined_today INTEGER NOT NULL DEFAULT 0,
        warmup_last_join DATE,
        warmup_last_join_at TIMESTAMPTZ,
        warmup_next_join_at TIMESTAMPTZ,
        warmup_end_at TIMESTAMPTZ,
        last_reaction_at TIMESTAMPTZ,
        channels JSONB NOT NULL DEFAULT '[]'::jsonb,
        warmup_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
        regular_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
        reaction_emojis JSONB NOT NULL DEFAULT '["❤️", "👍", "🔥", "🎉", "👏"]'::jsonb,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        last_activity TIMESTAMPTZ,
        comment_count INTEGER NOT NULL DEFAULT 0,
        reaction_count INTEGER NOT NULL DEFAULT 0,
        last_started_at TIMESTAMPTZ,
        last_stopped_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'stopped',
        reaction_chance INTEGER NOT NULL DEFAULT 50,
        reaction_discussion_chance INTEGER NOT NULL DEFAULT 50,
        discussion_reply_prompt TEXT,
        discussion_reply_chance INTEGER NOT NULL DEFAULT 50,
        reaction_sleep_min INTEGER NOT NULL DEFAULT 10,
        reaction_sleep_max INTEGER NOT NULL DEFAULT 30,
        reaction_limit_per_message INTEGER NOT NULL DEFAULT 50,
        reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        last_comment_at TIMESTAMPTZ,
        channels_last_synced_at TIMESTAMPTZ,
        system_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (mode IN ('warmup', 'standard')),
        UNIQUE (user_id, phone)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_settings (
        id SERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        setting_key TEXT NOT NULL,
        setting_value TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account_id, setting_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS warmup_channels (
        id SERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        channel TEXT NOT NULL,
        position INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TIMESTAMPTZ,
        joined_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account_id, channel),
        CHECK (status IN ('pending', 'joined', 'error'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS warmup_settings (
        id SERIAL PRIMARY KEY,
        channels_per_day INTEGER NOT NULL DEFAULT 15,
        delay_minutes INTEGER NOT NULL DEFAULT 7,
        default_days INTEGER NOT NULL DEFAULT 7,
        join_start_hour INTEGER NOT NULL DEFAULT 1,
        join_start_minute INTEGER NOT NULL DEFAULT 0,
        join_end_hour INTEGER NOT NULL DEFAULT 3,
        join_end_minute INTEGER NOT NULL DEFAULT 0,
        join_limit INTEGER NOT NULL DEFAULT 15,
        window_start TIME NOT NULL DEFAULT TIME '01:00',
        window_end TIME NOT NULL DEFAULT TIME '03:00',
        spans_midnight BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reaction_settings (
        id SERIAL PRIMARY KEY,
        account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
        reaction_limit INTEGER NOT NULL DEFAULT 50,
        post_reaction_chance INTEGER NOT NULL DEFAULT 50,
        discussion_reaction_chance INTEGER NOT NULL DEFAULT 50,
        discussion_reply_chance INTEGER NOT NULL DEFAULT 50,
        discussion_prompt TEXT,
        sleep_min INTEGER NOT NULL DEFAULT 10,
        sleep_max INTEGER NOT NULL DEFAULT 30,
        emojis JSONB NOT NULL DEFAULT '["❤️", "👍", "🔥", "🎉", "👏"]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comment_logs (
        id SERIAL PRIMARY KEY,
        account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
        channel TEXT,
        message_id BIGINT,
        comment TEXT,
        status TEXT,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skip_logs (
        id SERIAL PRIMARY KEY,
        account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
        channel TEXT,
        reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS posts (
        id SERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        channel TEXT NOT NULL,
        post_id BIGINT NOT NULL,
        message TEXT,
        has_media BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account_id, channel, post_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reaction_logs (
        id SERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        channel TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        emoji TEXT NOT NULL,
        status TEXT NOT NULL,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_sessions (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        phone TEXT NOT NULL,
        session_data BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, phone)
    )
    """,
)

TABLE_ALTER_STATEMENTS: Mapping[str, Tuple[str, ...]] = {
    "users": (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_authenticated BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    "accounts": (
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS session TEXT",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS session_path TEXT",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'standard'",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sleep_min INTEGER NOT NULL DEFAULT 10",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sleep_max INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS chance INTEGER NOT NULL DEFAULT 50",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS system_prompt TEXT",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS warmup_joined_today INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS warmup_last_join DATE",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS warmup_last_join_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS warmup_next_join_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS warmup_end_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_reaction_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'stopped'",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_started_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_stopped_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reaction_chance INTEGER NOT NULL DEFAULT 50",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reaction_discussion_chance INTEGER NOT NULL DEFAULT 50",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS discussion_reply_prompt TEXT",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS discussion_reply_chance INTEGER NOT NULL DEFAULT 50",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reaction_sleep_min INTEGER NOT NULL DEFAULT 10",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reaction_sleep_max INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reaction_limit_per_message INTEGER NOT NULL DEFAULT 50",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_comment_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS channels_last_synced_at TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS comment_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reaction_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_activity TIMESTAMPTZ",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS system_tags JSONB NOT NULL DEFAULT '[]'::jsonb",
    ),
    "warmup_settings": (
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS default_days INTEGER NOT NULL DEFAULT 7",
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS join_limit INTEGER NOT NULL DEFAULT 15",
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS window_start TIME NOT NULL DEFAULT TIME '01:00'",
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS window_end TIME NOT NULL DEFAULT TIME '03:00'",
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS spans_midnight BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE warmup_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    "comment_logs": (
        "ALTER TABLE comment_logs ADD COLUMN IF NOT EXISTS comment TEXT",
        "ALTER TABLE comment_logs ADD COLUMN IF NOT EXISTS status TEXT",
        "ALTER TABLE comment_logs ADD COLUMN IF NOT EXISTS error TEXT",
    ),
}

INDEX_DEFINITIONS: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_account_settings_account_id ON account_settings (account_id)",
    "CREATE INDEX IF NOT EXISTS idx_warmup_channels_pending ON warmup_channels (account_id, status, position)",
    "CREATE INDEX IF NOT EXISTS idx_telegram_sessions_user_phone ON telegram_sessions (user_id, phone)",
    "CREATE INDEX IF NOT EXISTS idx_posts_account_id ON posts (account_id)",
    "CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id ON reaction_logs (account_id)",
    "CREATE INDEX IF NOT EXISTS idx_reaction_logs_channel_message ON reaction_logs (channel, message_id)",
    "CREATE INDEX IF NOT EXISTS idx_comment_logs_created ON comment_logs (created_at)",
)

JSONB_COLUMNS: Mapping[str, Mapping[str, str]] = {
    "accounts": {
        "channels": "[]",
        "warmup_channels": "[]",
        "regular_channels": "[]",
        "reaction_emojis": DEFAULT_REACTION_EMOJIS_JSON,
        "system_tags": "[]",
    },
    "reaction_settings": {"emojis": DEFAULT_REACTION_EMOJIS_JSON},
}

DEFAULT_DATA_STATEMENTS: Tuple[Tuple[str, Tuple[Any, ...]], ...] = (
    (
        """
        INSERT INTO warmup_settings (
            id,
            channels_per_day,
            delay_minutes,
            default_days,
            join_start_hour,
            join_start_minute,
            join_end_hour,
            join_end_minute,
            join_limit,
            window_start,
            window_end,
            spans_midnight
        )
        VALUES (1, 15, 7, 7, 1, 0, 3, 0, 15, TIME '01:00', TIME '03:00', TRUE)
        ON CONFLICT (id) DO UPDATE SET
            channels_per_day = excluded.channels_per_day,
            delay_minutes = excluded.delay_minutes,
            default_days = excluded.default_days,
            join_start_hour = excluded.join_start_hour,
            join_start_minute = excluded.join_start_minute,
            join_end_hour = excluded.join_end_hour,
            join_end_minute = excluded.join_end_minute,
            join_limit = excluded.join_limit,
            window_start = excluded.window_start,
            window_end = excluded.window_end,
            spans_midnight = excluded.spans_midnight,
            updated_at = NOW()
        """,
        tuple(),
    ),
)

REQUIRED_COLUMNS: Mapping[str, Set[str]] = {
    "users": {
        "id",
        "user_id",
        "username",
        "first_name",
        "last_name",
        "is_active",
        "is_authenticated",
        "created_at",
        "updated_at",
    },
    "accounts": {
        "id",
        "user_id",
        "session",
        "phone",
        "mode",
        "sleep_min",
        "sleep_max",
        "chance",
        "system_prompt",
        "warmup_joined_today",
        "warmup_last_join",
        "warmup_last_join_at",
        "warmup_next_join_at",
        "warmup_end_at",
        "last_reaction_at",
        "channels",
        "warmup_channels",
        "regular_channels",
        "reaction_emojis",
        "is_active",
        "last_activity",
        "comment_count",
        "reaction_count",
        "created_at",
        "updated_at",
        "reactions_enabled",
        "reaction_limit_per_message",
        "reaction_chance",
        "reaction_discussion_chance",
        "discussion_reply_prompt",
        "discussion_reply_chance",
        "reaction_sleep_min",
        "reaction_sleep_max",
    },
    "warmup_settings": {
        "id",
        "channels_per_day",
        "delay_minutes",
        "default_days",
        "join_start_hour",
        "join_start_minute",
        "join_end_hour",
        "join_end_minute",
        "join_limit",
        "window_start",
        "window_end",
        "spans_midnight",
    },
    "reaction_settings": {
        "id",
        "account_id",
        "reaction_limit",
        "post_reaction_chance",
        "discussion_reaction_chance",
        "discussion_reply_chance",
        "discussion_prompt",
        "sleep_min",
        "sleep_max",
        "emojis",
    },
    "comment_logs": {
        "id",
        "account_id",
        "channel",
        "message_id",
        "comment",
        "created_at",
    },
    "skip_logs": {
        "id",
        "account_id",
        "channel",
        "reason",
        "created_at",
    },
}


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


def _deserialize_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            return [value]
    return []


def _serialize_list(value: Optional[Iterable[str]]) -> str:
    if value is None:
        return json.dumps([])
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


def _build_database_config() -> DatabaseConfig:
    """Construct :class:`DatabaseConfig` from environment variables."""

    dsn_env = os.getenv("DATABASE_URL")
    user_env = os.getenv("DATABASE_USER")
    password_env = os.getenv("DATABASE_PASSWORD")
    database_env = os.getenv("DATABASE_NAME")
    host_env = os.getenv("DATABASE_HOST", "postgres")
    port_env = os.getenv("DATABASE_PORT", "5432")

    dsn = _normalise_postgres_dsn(dsn_env) if dsn_env else None
    user = (user_env or "").strip()
    password = password_env or ""
    database = (database_env or "").strip()
    host = host_env or "postgres"
    port = int(port_env or "5432")

    if dsn:
        parsed = urlparse(dsn)
        database = (parsed.path.lstrip("/") or database).strip()
        user = (parsed.username or user).strip()
        password = parsed.password or password
        host = parsed.hostname or host
        port = parsed.port or port

    if not database or not user:
        raise RuntimeError(
            "Database configuration is incomplete. Provide DATABASE_URL or DATABASE_NAME, DATABASE_USER and DATABASE_PASSWORD."
        )

    if not dsn:
        password_escaped = password.replace("@", "%40")
        dsn = f"postgresql://{user}:{password_escaped}@{host}:{port}/{database}"

    admin_dsn_env = os.getenv("DATABASE_ADMIN_URL")
    admin_dsn = _normalise_postgres_dsn(admin_dsn_env) if admin_dsn_env else None

    backup_dir_env = os.getenv("DATABASE_BACKUP_DIR")
    if backup_dir_env is not None:
        backup_dir_env = backup_dir_env.strip()
    backup_dir: Optional[Path]
    if backup_dir_env == "":
        backup_dir = None
    elif backup_dir_env:
        backup_dir = Path(backup_dir_env).expanduser()
    else:
        backup_dir = DEFAULT_BACKUP_DIR

    return DatabaseConfig(
        dsn=dsn,
        user=user,
        password=password,
        database=database,
        host=host,
        port=port,
        admin_dsn=admin_dsn,
        backup_dir=backup_dir,
    )


async def _provision_database(config: DatabaseConfig) -> None:
    """Ensure the target database and role exist."""

    if config.admin_dsn is None:
        logger.debug("DATABASE_ADMIN_URL is not defined, skipping database provisioning")
        return

    if asyncpg is None:
        raise RuntimeError(
            "asyncpg is required for PostgreSQL connections. Install the 'asyncpg' package to use a PostgreSQL DSN."
        )

    logger.info(
        "Ensuring role %s and database %s exist", config.user, config.database
    )

    async with asyncpg.create_pool(config.admin_dsn, min_size=1, max_size=1) as pool:
        async with pool.acquire() as connection:
            await connection.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = $1) THEN
                        EXECUTE format('CREATE USER %I WITH PASSWORD %L', $1, $2);
                    ELSE
                        EXECUTE format('ALTER USER %I WITH PASSWORD %L', $1, $2);
                    END IF;
                END
                $$;
                """,
                config.user,
                config.password,
            )

            await connection.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = $1) THEN
                        EXECUTE format('CREATE DATABASE %I OWNER %I', $1, $2);
                    END IF;
                END
                $$;
                """,
                config.database,
                config.user,
            )

            await connection.execute(
                """
                DO $$
                BEGIN
                    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', $1, $2);
                END
                $$;
                """,
                config.database,
                config.user,
            )

    admin_database_dsn = config.admin_dsn

    async with asyncpg.connect(admin_database_dsn, database=config.database) as connection:
        await connection.execute(
            """
            DO $$
            BEGIN
                EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', $1);
                EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', $1);
                EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', $1);
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', $1);
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I', $1);
            END
            $$;
            """,
            config.user,
        )


async def _create_pool(dsn: str) -> None:
    """Initialise the asyncpg connection pool if required."""

    global _POOL, _BACKEND

    if _POOL is not None:
        return

    if asyncpg is None:
        raise RuntimeError(
            "asyncpg is required for PostgreSQL connections. Install the 'asyncpg' package to use a PostgreSQL DSN."
        )

    pool_max_size = int(os.getenv("DATABASE_POOL_MAX", "10"))
    _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=pool_max_size)
    _BACKEND = "postgres"


async def _perform_pre_migration_backup(config: DatabaseConfig) -> None:
    """Create a compressed backup before running migrations."""

    if config.backup_dir is None:
        logger.debug("Database backups are disabled via DATABASE_BACKUP_DIR")
        return

    pg_dump_path = shutil.which("pg_dump")
    if pg_dump_path is None:
        logger.warning("pg_dump binary not found in PATH, skipping pre-migration backup")
        return

    pool = _require_pool()
    async with pool.acquire() as connection:
        existing_table_count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )

    if not existing_table_count:
        logger.debug("No existing tables detected, skipping pre-migration backup")
        return

    backup_dir = config.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{config.database}_{timestamp}.dump"

    env = os.environ.copy()
    if config.password:
        env["PGPASSWORD"] = config.password

    cmd = [
        pg_dump_path,
        "--no-owner",
        "--no-privileges",
        "--format",
        "custom",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--username",
        config.user,
        "--file",
        str(backup_path),
        config.database,
    ]

    logger.info("Creating database backup at %s", backup_path)

    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        logger.error("pg_dump failed before migrations: %s", result.stderr.strip())
        raise RuntimeError("pg_dump failed before migrations")


async def _convert_text_column_to_jsonb(table: str, column: str, default_json: str) -> None:
    """Convert a legacy TEXT column into JSONB while preserving data."""

    rows = await _fetchall(f"SELECT id, {column} FROM {table}")
    for row in rows:
        raw_value = row[column]
        if raw_value in (None, ""):
            serialised = default_json
        elif isinstance(raw_value, (list, tuple)):
            serialised = json.dumps(list(raw_value))
        elif isinstance(raw_value, str):
            try:
                json.loads(raw_value)
                serialised = raw_value
            except json.JSONDecodeError:
                serialised = default_json
        else:
            serialised = json.dumps(raw_value)

        await _execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?",
            (serialised, row["id"]),
        )

    await _execute(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSONB USING CASE"
        f" WHEN {column} IS NULL THEN ?::jsonb"
        f" ELSE {column}::jsonb END",
        (default_json,),
    )
    await _execute(
        f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT ?::jsonb",
        (default_json,),
    )
    await _execute(
        f"UPDATE {table} SET {column} = ?::jsonb WHERE {column} IS NULL",
        (default_json,),
    )


async def _ensure_jsonb_column(table: str, column: str, default_json: str) -> None:
    """Ensure a column exists with JSONB type and proper defaults."""

    await _execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} JSONB DEFAULT ?::jsonb",
        (default_json,),
    )

    column_row = await _fetchone(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ? AND column_name = ?
        """,
        (table, column),
    )

    column_type = column_row["data_type"] if column_row else None
    if column_type != "jsonb":
        await _convert_text_column_to_jsonb(table, column, default_json)
    else:
        await _execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT ?::jsonb",
            (default_json,),
        )
        await _execute(
            f"UPDATE {table} SET {column} = ?::jsonb WHERE {column} IS NULL",
            (default_json,),
        )


async def _ensure_users_primary_key() -> None:
    """Guarantee that users.id exists and is used as the primary key."""

    await _execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS id BIGSERIAL")

    await _execute(
        """
        UPDATE users
        SET id = nextval(pg_get_serial_sequence('users', 'id'))
        WHERE id IS NULL
        """
    )

    constraint_row = await _fetchone(
        """
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'users'::regclass AND contype = 'p'
        """
    )

    if constraint_row and constraint_row["conname"] != "users_pkey":
        await _execute(f"ALTER TABLE users DROP CONSTRAINT {constraint_row['conname']}")

    await _execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'users'::regclass
                  AND conname = 'users_pkey'
            ) THEN
                ALTER TABLE users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
            END IF;
        END
        $$;
        """
    )

    await _execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'users'::regclass
                  AND conname = 'users_user_id_key'
            ) THEN
                ALTER TABLE users ADD CONSTRAINT users_user_id_key UNIQUE (user_id);
            END IF;
        END
        $$;
        """
    )


async def _ensure_accounts_session_column() -> None:
    """Ensure the canonical session column exists and is populated."""

    await _execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS session TEXT")
    await _execute(
        "UPDATE accounts SET session = session_path WHERE session IS NULL AND session_path IS NOT NULL"
    )
    await _execute(
        "UPDATE accounts SET session = phone WHERE session IS NULL AND phone IS NOT NULL"
    )
    await _execute(
        "ALTER TABLE accounts ALTER COLUMN session SET NOT NULL"
    )


async def migrate_database() -> None:
    """Run structural migrations ensuring the expected schema is present."""

    logger.info("Running database migrations")

    for statement in SCHEMA_CREATE_STATEMENTS:
        await _execute(statement)

    for table, statements in TABLE_ALTER_STATEMENTS.items():
        for statement in statements:
            await _execute(statement)

    await _ensure_users_primary_key()
    await _ensure_accounts_session_column()

    for table, columns in JSONB_COLUMNS.items():
        for column, default_json in columns.items():
            await _ensure_jsonb_column(table, column, default_json)

    for index_statement in INDEX_DEFINITIONS:
        await _execute(index_statement)

    for query, params in DEFAULT_DATA_STATEMENTS:
        await _execute(query, params)

    logger.info("Database migrations completed")


async def check_database_health(*, log_warnings: bool = True) -> Dict[str, Any]:
    """Validate that all critical tables and columns exist."""

    pool = _require_pool()
    missing_tables: List[str] = []
    missing_columns: Dict[str, List[str]] = {}

    async with pool.acquire() as connection:
        table_rows = await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        existing_tables = {row["table_name"] for row in table_rows}

        for table, required in REQUIRED_COLUMNS.items():
            if table not in existing_tables:
                missing_tables.append(table)
                continue

            column_rows = await connection.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                """,
                table,
            )
            present_columns = {row["column_name"] for row in column_rows}
            missing = sorted(required - present_columns)
            if missing:
                missing_columns[table] = missing

    ok = not missing_tables and not missing_columns
    if not ok and log_warnings:
        logger.warning(
            "Database health check detected issues: missing_tables=%s missing_columns=%s",
            missing_tables,
            missing_columns,
        )

    return {
        "ok": ok,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


async def initialize_database(*, max_retries: int = 5, base_delay: float = 1.0) -> None:
    """Provision the database, run migrations and validate health."""

    global _DB_CONFIG

    config = _build_database_config()
    _DB_CONFIG = config

    attempt = 0
    while True:
        attempt += 1
        try:
            await _provision_database(config)
            await _create_pool(config.dsn)
            await _perform_pre_migration_backup(config)
            await migrate_database()
            health = await check_database_health(log_warnings=False)
            if not health["ok"]:
                raise RuntimeError(
                    f"Database health check failed after migrations: {health}"
                )
            logger.info("Database initialisation successful")
            break
        except Exception as exc:  # pragma: no cover - defensive branch
            if attempt >= max_retries:
                logger.exception("Database initialisation failed after %s attempts", attempt)
                raise
            wait_time = base_delay * attempt
            logger.warning(
                "Database initialisation attempt %s/%s failed: %s. Retrying in %.1f seconds",
                attempt,
                max_retries,
                exc,
                wait_time,
            )
            await asyncio.sleep(wait_time)


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
    """Backward-compatible wrapper that delegates to :func:`initialize_database`."""

    await initialize_database()

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
    default_days: int = 7,
    join_limit: int = 15,
    window_start: str = "01:00",
    window_end: str = "03:00",
    spans_midnight: bool = True,
) -> None:
    """Ensure that a single warmup settings row exists in the database."""

    await _execute(
        """
        INSERT INTO warmup_settings (
            id,
            channels_per_day,
            delay_minutes,
            default_days,
            join_start_hour,
            join_start_minute,
            join_end_hour,
            join_end_minute,
            join_limit,
            window_start,
            window_end,
            spans_midnight
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            channels_per_day = EXCLUDED.channels_per_day,
            delay_minutes = EXCLUDED.delay_minutes,
            default_days = EXCLUDED.default_days,
            join_start_hour = EXCLUDED.join_start_hour,
            join_start_minute = EXCLUDED.join_start_minute,
            join_end_hour = EXCLUDED.join_end_hour,
            join_end_minute = EXCLUDED.join_end_minute,
            join_limit = EXCLUDED.join_limit,
            window_start = EXCLUDED.window_start,
            window_end = EXCLUDED.window_end,
            spans_midnight = EXCLUDED.spans_midnight,
            updated_at = NOW()
        """,
        (
            channels_per_day,
            delay_minutes,
            default_days,
            join_start_hour,
            join_start_minute,
            join_end_hour,
            join_end_minute,
            join_limit,
            window_start,
            window_end,
            adapt_bool(spans_midnight),
        ),
    )


async def get_warmup_settings() -> Dict[str, int]:
    """Fetch the warmup settings row."""

    row = await _fetchone(
        """
        SELECT channels_per_day,
               delay_minutes,
               default_days,
               join_start_hour,
               join_start_minute,
               join_end_hour,
               join_end_minute,
               join_limit,
               window_start,
               window_end,
               spans_midnight
        FROM warmup_settings
        WHERE id = 1
        """
    )

    if not row:
        raise RuntimeError("Warmup settings are not initialised")

    return {
        "channels_per_day": int(row["channels_per_day"]),
        "delay_minutes": int(row["delay_minutes"]),
        "default_days": int(row["default_days"]),
        "join_start_hour": int(row["join_start_hour"]),
        "join_start_minute": int(row["join_start_minute"]),
        "join_end_hour": int(row["join_end_hour"]),
        "join_end_minute": int(row["join_end_minute"]),
        "join_limit": int(row["join_limit"]),
        "window_start": str(row["window_start"]),
        "window_end": str(row["window_end"]),
        "spans_midnight": bool(row["spans_midnight"]),
    }


async def update_warmup_settings(
    *,
    channels_per_day: Optional[int] = None,
    delay_minutes: Optional[int] = None,
    join_start_hour: Optional[int] = None,
    join_start_minute: Optional[int] = None,
    join_end_hour: Optional[int] = None,
    join_end_minute: Optional[int] = None,
    default_days: Optional[int] = None,
    join_limit: Optional[int] = None,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
    spans_midnight: Optional[bool] = None,
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
    if default_days is not None:
        updates.append("default_days = ?")
        params.append(default_days)
    if join_limit is not None:
        updates.append("join_limit = ?")
        params.append(join_limit)
    if window_start is not None:
        updates.append("window_start = ?")
        params.append(window_start)
    if window_end is not None:
        updates.append("window_end = ?")
        params.append(window_end)
    if spans_midnight is not None:
        updates.append("spans_midnight = ?")
        params.append(adapt_bool(spans_midnight))

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
        INSERT INTO accounts (user_id, phone, session, session_path, status, mode)
        VALUES (?, ?, ?, ?, 'stopped', 'warmup')
        ON CONFLICT(user_id, phone) DO UPDATE SET
            session = excluded.session,
            session_path = excluded.session_path,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (user_id, phone, session_path, session_path),
    )
    return int(row["id"]) if row is not None else None


async def _fetch_accounts(query: str, params: Iterable[Any]) -> List[Dict[str, Any]]:
    rows = await _fetchall(query, tuple(params))
    return [_convert_account_row(row) for row in rows]


def _convert_account_row(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["channels"] = _deserialize_list(data.get("channels"))
    data["warmup_channels"] = _deserialize_list(data.get("warmup_channels"))
    data["regular_channels"] = _deserialize_list(data.get("regular_channels"))
    data["reaction_emojis"] = _deserialize_list(data.get("reaction_emojis"))
    data["system_tags"] = _deserialize_list(data.get("system_tags"))
    if "session_path" not in data or not data.get("session_path"):
        data["session_path"] = data.get("session")
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
        updates.append("reaction_emojis = ?::jsonb")
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
        updates.append("channels = ?::jsonb")
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
        updates.append("reaction_emojis = ?::jsonb")
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
        SET warmup_channels = ?::jsonb, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (_serialize_list(unique_channels), account_id),
    )



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
