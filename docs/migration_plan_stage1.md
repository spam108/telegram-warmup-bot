# PostgreSQL Migration Implementation

## Overview
The warmup bot now runs on PostgreSQL instead of the legacy SQLite database. The
runtime automatically detects the backend via the `DATABASE_URL` environment
variable and uses an `asyncpg` connection pool when a PostgreSQL DSN is
provided. SQLite support remains available for local development and automated
tests, but Docker deployments are configured to target PostgreSQL by default.

Key improvements:

- Containerised PostgreSQL service with persistent storage and a project level
  `init.sql` that bootstraps the schema required by the bot.
- `db.py` exposes a backend-agnostic API backed by an `asyncpg` connection pool
  and a compatibility layer that keeps existing call sites intact.
- Pyrogram session data is no longer tied to SQLite files on disk. Session
  strings are stored in the `telegram_sessions` table and synchronised whenever
  a client connects, keeping the `sessions/` directory only for backwards
  compatibility.
- A dedicated migration script copies historical data from SQLite into the new
  PostgreSQL schema.

## Infrastructure Changes

| Component          | Previous State                      | New State                                                    |
| ------------------ | ----------------------------------- | ------------------------------------------------------------ |
| Database service    | Only the bot container              | Dedicated `postgres:15` service with named volume             |
| Dependencies        | `aiosqlite`                         | `aiosqlite` (optional) + `asyncpg`                            |
| DB initialisation   | Embedded in application code        | `init.sql` mounted into the PostgreSQL container              |
| Connection string   | `sqlite:///data/CDXBOT0310.db`      | `postgresql+asyncpg://bot_user:${POSTGRES_PASSWORD}@postgres:5432/telegram_bot` |
| Session storage     | Pyrogram SQLite files only          | Database-backed session strings with optional file fallback   |

Docker deployments now require a `POSTGRES_PASSWORD` entry in `.env`. The
compose file declares a `postgres_data` volume for durability and exposes port
5432 for local debugging.

## Database Layer

`db.py` transparently handles both SQLite and PostgreSQL:

- Detects the backend from `DATABASE_URL` and initialises either an `aiosqlite`
  connection or an `asyncpg` pool with a small compatibility wrapper that
  mimics the `aiosqlite.Connection` interface.
- Normalises datetime values so existing business logic continues to work with
  plain dictionaries.
- Creates the new `telegram_sessions` and `account_settings` tables on both
  backends and exposes helpers to upsert, fetch, and delete session payloads.
- Adds helper utilities for PostgreSQL specific tasks such as converting `?`
  placeholders to `$1` style parameters and detecting concurrency errors.

## Session Management Updates

Pyrogram clients now prefer session strings sourced from the database:

1. `_prepare_client` attempts to load the session string from
   `telegram_sessions` and falls back to existing `.session` files if necessary.
2. `_persist_session_string` exports the latest session string after every
   client interaction and updates PostgreSQL, ensuring the database remains the
   source of truth.
3. Login flows store the exported session string immediately after successful
   authorisation, while cleanup paths remove both the file and the database
   entry.

This approach resolves the frequent `database is locked` errors caused by
Pyrogram SQLite files and makes it safe to run multiple workers in parallel.

## Data Migration Script

`scripts/migrate_sqlite_to_postgres.py` copies historic data into PostgreSQL.
Usage:

```bash
python scripts/migrate_sqlite_to_postgres.py ./data/CDXBOT0310.db \
    postgresql+asyncpg://bot_user:password@localhost:5432/telegram_bot
```

The script migrates users, accounts, warmup queues, warmup settings, and comment
logs. Session strings are populated by the application the next time a client is
used. When command-line parameters are omitted, the script now reads
`SQLITE_PATH` and `POSTGRES_DSN` from the environment, making it easier to run
inside Docker containers where secrets are injected via `.env` files.

## Operational Notes

- The bot continues to accept SQLite URLs, which simplifies local testing.
- Session files are still created for backward compatibility, but the bot can
  operate purely from the database after the first synchronisation.
- Removing an account now deletes the related session entry from
  `telegram_sessions`.
- `docker-compose up` launches both the bot and PostgreSQL; the bot waits for the
  database to be ready via connection retries provided by `asyncpg`.

## Next Steps

- Monitor database size and adjust PostgreSQL resource limits if required.
- Extend the migration script if additional tables need to be ported in the
  future.
- Consider pruning old session files once confidence in the database-backed
  approach is established.
