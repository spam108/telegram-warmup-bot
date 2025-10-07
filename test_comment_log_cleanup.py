import os
import importlib
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def setup_database(postgres_db_url):
    assert postgres_db_url
    global db
    db = importlib.reload(db)
    await db.close_db()
    await db.init_db()
    try:
        yield
    finally:
        await db.close_db()


@pytest.mark.anyio("asyncio")
async def test_cleanup_comment_logs_removes_outdated_entries():
    await db.ensure_user(1)
    account = await db.ensure_account(1, "+100500", "session.session")

    conn = await db._require_conn()
    now = datetime.utcnow().replace(microsecond=0)
    entries = [
        (now - timedelta(days=5), 1),
        (now - timedelta(days=3), 2),
        (now - timedelta(days=2) + timedelta(seconds=1), 3),
        (now - timedelta(days=1), 4),
    ]

    for created_at, message_id in entries:
        await conn.execute(
            """
            INSERT INTO comment_logs (account_id, channel, message_id, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                account["id"],
                "test_channel",
                message_id,
                "test_status",
                None,
                created_at,
            ),
        )
    await conn.commit()

    deleted = await db.cleanup_comment_logs(retention_days=2)
    assert deleted == 2

    async with conn.execute(
        "SELECT message_id, created_at FROM comment_logs ORDER BY message_id"
    ) as cursor:
        remaining_rows = await cursor.fetchall()

    remaining_message_ids = [row["message_id"] for row in remaining_rows]
    assert remaining_message_ids == [3, 4]

    remaining_dates = []
    for row in remaining_rows:
        value = row["created_at"]
        if isinstance(value, datetime):
            normalized = value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
            remaining_dates.append(normalized)
        else:
            remaining_dates.append(datetime.strptime(value, "%Y-%m-%d %H:%M:%S"))
    cutoff = now - timedelta(days=2)
    assert all(cutoff <= date <= now for date in remaining_dates)
