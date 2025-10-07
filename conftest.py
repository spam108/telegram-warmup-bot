import os
import uuid

import anyio
import asyncpg
import pytest


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@pytest.fixture
def postgres_db_url(monkeypatch):
    base_dsn = os.getenv(
        "POSTGRES_TEST_ADMIN_DSN",
        "postgresql://warmup:warmup@localhost:5432/postgres",
    )
    db_name = f"test_{uuid.uuid4().hex}"

    async def _create_database() -> None:
        admin_conn = await asyncpg.connect(base_dsn)
        try:
            await admin_conn.execute(
                f"CREATE DATABASE {_quote_ident(db_name)} OWNER warmup"
            )
        finally:
            await admin_conn.close()

    anyio.run(_create_database, backend="asyncio")

    database_url = f"postgresql+asyncpg://warmup:warmup@localhost:5432/{db_name}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    try:
        yield database_url
    finally:
        async def _drop_database() -> None:
            admin_conn = await asyncpg.connect(base_dsn)
            try:
                await admin_conn.execute(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    db_name,
                )
                await admin_conn.execute(
                    f"DROP DATABASE IF EXISTS {_quote_ident(db_name)}"
                )
            finally:
                await admin_conn.close()

        anyio.run(_drop_database, backend="asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"
