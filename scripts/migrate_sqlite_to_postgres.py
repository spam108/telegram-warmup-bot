#!/usr/bin/env python
"""Migrate warmup bot data from the legacy SQLite database to PostgreSQL."""

import argparse
import asyncio
import os
import sqlite3
from typing import Iterable, Sequence

import asyncpg


def _rows(cursor: sqlite3.Cursor) -> Iterable[sqlite3.Row]:
    row = cursor.fetchone()
    while row is not None:
        yield row
        row = cursor.fetchone()


async def _copy_table(
    pg: asyncpg.Connection,
    rows: Sequence[sqlite3.Row],
    insert_sql: str,
    *,
    batch_size: int = 500,
) -> None:
    if not rows:
        return

    for idx in range(0, len(rows), batch_size):
        chunk = rows[idx : idx + batch_size]
        await pg.executemany(insert_sql, [tuple(row) for row in chunk])


async def migrate(sqlite_path: str, postgres_dsn: str) -> None:
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    async with asyncpg.create_pool(postgres_dsn) as pool:
        async with pool.acquire() as pg:
            async with pg.transaction():
                # Users
                user_rows = list(
                    sqlite_conn.execute("SELECT user_id, is_authenticated, created_at, updated_at FROM users")
                )
                await _copy_table(
                    pg,
                    user_rows,
                    """
                    INSERT INTO users (user_id, is_authenticated, created_at, updated_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id) DO UPDATE SET
                        is_authenticated = EXCLUDED.is_authenticated,
                        created_at = LEAST(users.created_at, EXCLUDED.created_at),
                        updated_at = GREATEST(users.updated_at, EXCLUDED.updated_at)
                    """,
                )

                account_rows = list(
                    sqlite_conn.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            phone,
                            session_path,
                            chance,
                            system_prompt,
                            sleep_min,
                            sleep_max,
                            reaction_emojis,
                            reaction_chance,
                            reaction_discussion_chance,
                            discussion_reply_prompt,
                            discussion_reply_chance,
                            reaction_sleep_min,
                            reaction_sleep_max,
                            reaction_limit_per_message,
                            last_reaction_at,
                            channels,
                            warmup_channels,
                            status,
                            last_started_at,
                            last_stopped_at,
                            created_at,
                            updated_at,
                            mode,
                            warmup_end_at,
                            warmup_joined_today,
                            warmup_last_join,
                            warmup_last_join_at,
                            warmup_next_join_at,
                            reactions_enabled
                        FROM accounts
                        """
                    )
                )
                await _copy_table(
                    pg,
                    account_rows,
                    """
                    INSERT INTO accounts (
                        id, user_id, phone, session_path, chance, system_prompt,
                        sleep_min, sleep_max, reaction_emojis, reaction_chance,
                        reaction_discussion_chance, discussion_reply_prompt,
                        discussion_reply_chance, reaction_sleep_min, reaction_sleep_max,
                        reaction_limit_per_message, last_reaction_at, channels,
                        warmup_channels, status, last_started_at, last_stopped_at,
                        created_at, updated_at, mode, warmup_end_at, warmup_joined_today,
                        warmup_last_join, warmup_last_join_at, warmup_next_join_at,
                        reactions_enabled
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12,
                        $13, $14, $15,
                        $16, $17, $18,
                        $19, $20, $21, $22,
                        $23, $24, $25, $26, $27,
                        $28, $29, $30, $31,
                        $32
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        phone = EXCLUDED.phone,
                        session_path = EXCLUDED.session_path,
                        chance = EXCLUDED.chance,
                        system_prompt = EXCLUDED.system_prompt,
                        sleep_min = EXCLUDED.sleep_min,
                        sleep_max = EXCLUDED.sleep_max,
                        reaction_emojis = EXCLUDED.reaction_emojis,
                        reaction_chance = EXCLUDED.reaction_chance,
                        reaction_discussion_chance = EXCLUDED.reaction_discussion_chance,
                        discussion_reply_prompt = EXCLUDED.discussion_reply_prompt,
                        discussion_reply_chance = EXCLUDED.discussion_reply_chance,
                        reaction_sleep_min = EXCLUDED.reaction_sleep_min,
                        reaction_sleep_max = EXCLUDED.reaction_sleep_max,
                        reaction_limit_per_message = EXCLUDED.reaction_limit_per_message,
                        last_reaction_at = EXCLUDED.last_reaction_at,
                        channels = EXCLUDED.channels,
                        warmup_channels = EXCLUDED.warmup_channels,
                        status = EXCLUDED.status,
                        last_started_at = EXCLUDED.last_started_at,
                        last_stopped_at = EXCLUDED.last_stopped_at,
                        created_at = LEAST(accounts.created_at, EXCLUDED.created_at),
                        updated_at = GREATEST(accounts.updated_at, EXCLUDED.updated_at),
                        mode = EXCLUDED.mode,
                        warmup_end_at = EXCLUDED.warmup_end_at,
                        warmup_joined_today = EXCLUDED.warmup_joined_today,
                        warmup_last_join = EXCLUDED.warmup_last_join,
                        warmup_last_join_at = EXCLUDED.warmup_last_join_at,
                        warmup_next_join_at = EXCLUDED.warmup_next_join_at,
                        reactions_enabled = EXCLUDED.reactions_enabled
                    """,
                )

                warmup_channels_rows = list(
                    sqlite_conn.execute(
                        """
                        SELECT id, account_id, channel, position, status, error, attempts,
                               last_attempt_at, joined_at, created_at, updated_at
                        FROM warmup_channels
                        """
                    )
                )
                await _copy_table(
                    pg,
                    warmup_channels_rows,
                    """
                    INSERT INTO warmup_channels (
                        id, account_id, channel, position, status, error, attempts,
                        last_attempt_at, joined_at, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10, $11
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        channel = EXCLUDED.channel,
                        position = EXCLUDED.position,
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        attempts = EXCLUDED.attempts,
                        last_attempt_at = EXCLUDED.last_attempt_at,
                        joined_at = EXCLUDED.joined_at,
                        created_at = LEAST(warmup_channels.created_at, EXCLUDED.created_at),
                        updated_at = GREATEST(warmup_channels.updated_at, EXCLUDED.updated_at)
                    """,
                )

                settings_rows = list(
                    sqlite_conn.execute(
                        "SELECT id, channels_per_day, delay_minutes, join_start_hour, join_start_minute, join_end_hour, join_end_minute, updated_at FROM warmup_settings"
                    )
                )
                await _copy_table(
                    pg,
                    settings_rows,
                    """
                    INSERT INTO warmup_settings (
                        id, channels_per_day, delay_minutes, join_start_hour, join_start_minute,
                        join_end_hour, join_end_minute, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE SET
                        channels_per_day = EXCLUDED.channels_per_day,
                        delay_minutes = EXCLUDED.delay_minutes,
                        join_start_hour = EXCLUDED.join_start_hour,
                        join_start_minute = EXCLUDED.join_start_minute,
                        join_end_hour = EXCLUDED.join_end_hour,
                        join_end_minute = EXCLUDED.join_end_minute,
                        updated_at = EXCLUDED.updated_at
                    """,
                )

                comment_rows = list(
                    sqlite_conn.execute(
                        "SELECT id, account_id, channel, message_id, status, error, created_at FROM comment_logs"
                    )
                )
                await _copy_table(
                    pg,
                    comment_rows,
                    """
                    INSERT INTO comment_logs (id, account_id, channel, message_id, status, error, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO UPDATE SET
                        channel = EXCLUDED.channel,
                        message_id = EXCLUDED.message_id,
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        created_at = LEAST(comment_logs.created_at, EXCLUDED.created_at)
                    """,
                )

                for sequence_name, table_name in (
                    ("accounts_id_seq", "accounts"),
                    ("warmup_channels_id_seq", "warmup_channels"),
                    ("comment_logs_id_seq", "comment_logs"),
                ):
                    next_value = await pg.fetchval(
                        f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table_name}"
                    )
                    await pg.execute("SELECT setval($1, $2, false)", sequence_name, next_value)

    sqlite_conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL")
    parser.add_argument(
        "sqlite_path",
        nargs="?",
        help="Path to the legacy SQLite database file",
    )
    parser.add_argument(
        "postgres_dsn",
        nargs="?",
        help="PostgreSQL DSN (e.g. postgresql+asyncpg://user:pass@host:5432/db)",
    )
    parser.add_argument(
        "--sqlite-path",
        dest="sqlite_path_opt",
        help="Optional flag alternative to the positional SQLite path",
    )
    parser.add_argument(
        "--postgres-dsn",
        dest="postgres_dsn_opt",
        help="Optional flag alternative to the positional PostgreSQL DSN",
    )

    args = parser.parse_args()
    sqlite_path = (
        args.sqlite_path
        or args.sqlite_path_opt
        or os.getenv("SQLITE_PATH")
    )
    postgres_dsn = (
        args.postgres_dsn
        or args.postgres_dsn_opt
        or os.getenv("POSTGRES_DSN")
    )

    if not sqlite_path:
        parser.error(
            "SQLite path must be provided either as a positional argument, via --sqlite-path, or the SQLITE_PATH environment variable."
        )

    if not postgres_dsn:
        parser.error(
            "PostgreSQL DSN must be provided either as a positional argument, via --postgres-dsn, or the POSTGRES_DSN environment variable."
        )

    args.sqlite_path = sqlite_path
    args.postgres_dsn = postgres_dsn
    return args


def main() -> None:
    args = parse_args()
    postgres_dsn = args.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    asyncio.run(migrate(args.sqlite_path, postgres_dsn))


if __name__ == "__main__":
    main()
