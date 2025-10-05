import os
import types
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import main


class DummyClient:
    def __init__(self):
        self.sent_messages = []
        self.sent_reactions = []

    async def send_message(self, chat_id, text, reply_to_message_id=None):
        self.sent_messages.append((chat_id, text, reply_to_message_id))
        reply_to = types.SimpleNamespace(
            forward_from_chat=types.SimpleNamespace(username="source_channel"),
            forward_from_message_id=777,
        )
        return types.SimpleNamespace(id=999, reply_to_message=reply_to)

    async def send_reaction(self, chat_id, message_id, emoji):
        self.sent_reactions.append((chat_id, message_id, emoji))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_discussion_reply_from_user_triggers_comment_and_reaction(monkeypatch):
    userid = 123
    session = "+100500"
    account_id = 42

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-2000000000, permissions=None, type="supergroup")
    reply_to_message = types.SimpleNamespace(
        forward_from_chat=types.SimpleNamespace(username="source_channel"),
        forward_from_message_id=321,
    )
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Original post",
        caption=None,
        id=111,
        reply_to_message=reply_to_message,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fake_generate_comment(post_text, system_prompt):
        return f"comment:{post_text}:{system_prompt}"

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    updated_reactions = []

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)

    try:
        await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert client.sent_messages, "Комментарий не был отправлен"
    assert client.sent_reactions, "Реакция не была установлена"
    assert any("отправил комментарий" in log[1] for log in bot_logs)
    statuses = {kwargs.get("status") for _, kwargs in comment_logs}
    assert "success" in statuses
    assert "reaction_success" in statuses
    assert len(updated_reactions) == 1


@pytest.mark.anyio
async def test_reaction_skipped_when_limit_reached(monkeypatch):
    userid = 123
    session = "+100500"
    account_id = 42

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-2000000000, permissions=None, type="supergroup")
    reply_to_message = types.SimpleNamespace(
        forward_from_chat=types.SimpleNamespace(username="source_channel"),
        forward_from_message_id=321,
    )
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Original post",
        caption=None,
        id=111,
        reply_to_message=reply_to_message,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fake_generate_comment(post_text, system_prompt):
        return f"comment:{post_text}:{system_prompt}"

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 5

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_sleep)

    try:
        await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert client.sent_messages, "Комментарий не был отправлен"
    assert not client.sent_reactions, "Реакция не должна отправляться при достижении лимита"
    statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert "success" in statuses
    assert "reaction_skipped" in statuses
    assert all(status != "reaction_success" for status in statuses if status is not None)


@pytest.mark.anyio
async def test_reaction_skipped_when_cooldown_active(monkeypatch):
    userid = 123
    session = "+100500"
    account_id = 42

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-2000000000, permissions=None, type="supergroup")
    reply_to_message = types.SimpleNamespace(
        forward_from_chat=types.SimpleNamespace(username="source_channel"),
        forward_from_message_id=321,
    )
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Original post",
        caption=None,
        id=111,
        reply_to_message=reply_to_message,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fake_generate_comment(post_text, system_prompt):
        return f"comment:{post_text}:{system_prompt}"

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    updated_reactions: list[tuple[int, datetime]] = []

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)

    recent_reaction = datetime.now(timezone.utc)

    try:
        result = await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            last_reaction_at=recent_reaction,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert result == recent_reaction
    assert not client.sent_reactions
    statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert statuses.count("reaction_skipped") >= 1
    cooldown_errors = [
        kwargs.get("error")
        for _, kwargs in comment_logs
        if kwargs.get("status") == "reaction_skipped"
    ]
    assert any("cooldown" in (err or "") for err in cooldown_errors)
    assert not updated_reactions


@pytest.mark.anyio
async def test_reaction_occurs_after_cooldown(monkeypatch):
    userid = 123
    session = "+100500"
    account_id = 42

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-2000000000, permissions=None, type="supergroup")
    reply_to_message = types.SimpleNamespace(
        forward_from_chat=types.SimpleNamespace(username="source_channel"),
        forward_from_message_id=321,
    )
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Original post",
        caption=None,
        id=111,
        reply_to_message=reply_to_message,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fake_generate_comment(post_text, system_prompt):
        return f"comment:{post_text}:{system_prompt}"

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    updated_reactions: list[tuple[int, datetime]] = []

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)

    previous_reaction = datetime.now(timezone.utc) - timedelta(seconds=120)

    try:
        result = await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            last_reaction_at=previous_reaction,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert client.sent_reactions
    statuses = {kwargs.get("status") for _, kwargs in comment_logs}
    assert "reaction_success" in statuses
    assert updated_reactions
    assert result is not None
    assert result > previous_reaction
