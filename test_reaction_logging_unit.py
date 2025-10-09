import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("API_ID", "123")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import db
from main import send_reaction_safe


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_record_post_upsert(monkeypatch):
    captured = {}

    async def fake_execute(query, params=()):
        captured["query"] = query
        captured["params"] = params

    monkeypatch.setattr(db, "_execute", fake_execute)

    await db.record_post(
        1,
        channel="test_channel",
        post_id=42,
        message="hello",
        has_media=True,
    )

    assert "ON CONFLICT (account_id, channel, post_id)" in captured["query"]
    assert captured["params"] == (1, "test_channel", 42, "hello", True)


@pytest.mark.anyio
async def test_add_reaction_log_sets_placeholder(monkeypatch):
    calls = {}

    async def fake_execute(query, params=()):
        calls["params"] = params

    monkeypatch.setattr(db, "_execute", fake_execute)

    await db.add_reaction_log(
        7,
        channel="chan",
        message_id=55,
        emoji=None,
        status="skipped",
        error_message="limit",
    )

    assert calls["params"] == (7, "chan", 55, "N/A", "skipped", "limit")


@pytest.mark.anyio
async def test_update_last_reaction_at_normalises_timezone(monkeypatch):
    calls = []

    async def fake_execute(query, params=()):
        calls.append(params)

    monkeypatch.setattr(db, "_execute", fake_execute)

    naive_time = datetime(2024, 1, 1, 12, 0, 0)
    await db.update_last_reaction_at(3, naive_time)

    assert calls[0][0].tzinfo is not None
    assert calls[0][1] == 3

    await db.update_last_reaction_at(4, "2024-02-02T10:00:00Z")
    assert calls[1][0].tzinfo is not None
    assert calls[1][1] == 4


@pytest.mark.anyio
async def test_count_reactions_for_message_prefers_reaction_logs(monkeypatch):
    queries = []

    async def fake_fetchone(query, params=()):
        queries.append((query.strip(), params))
        if "FROM reaction_logs" in query:
            return {"reaction_count": 5}
        return {"reaction_count": 2}

    monkeypatch.setattr(db, "_fetchone", fake_fetchone)

    count = await db.count_reactions_for_message("chan", 77)

    assert count == 5
    assert any("FROM reaction_logs" in q[0] for q in queries)
    assert len(queries) == 1


@pytest.mark.anyio
async def test_count_reactions_for_message_falls_back(monkeypatch):
    calls = []

    async def fake_fetchone(query, params=()):
        calls.append(query.strip())
        if len(calls) == 1:
            return {"reaction_count": None}
        return {"reaction_count": 4}

    monkeypatch.setattr(db, "_fetchone", fake_fetchone)

    count = await db.count_reactions_for_message("chan", 10)

    assert count == 4
    assert len(calls) == 2


@pytest.mark.anyio
async def test_send_reaction_safe_handles_invalid():
    events = SimpleNamespace(sent=False)

    class DummyClient:
        async def send_reaction(self, chat_id, message_id, emoji):
            events.sent = True
            raise RuntimeError("RPC_CALL_FAIL: REACTION_INVALID")

    dummy = DummyClient()

    sent = await send_reaction_safe(dummy, 1, 2, "🔥", max_attempts=1)
    assert not sent
    assert events.sent


@pytest.mark.anyio
async def test_send_reaction_safe_success():
    events = SimpleNamespace(sent=False)

    class DummyClient:
        async def send_reaction(self, chat_id, message_id, emoji):
            events.sent = True

    dummy = DummyClient()

    sent = await send_reaction_safe(dummy, 1, 2, "👍")
    assert sent
    assert events.sent
