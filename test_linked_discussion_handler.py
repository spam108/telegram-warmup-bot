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
        self.available_reactions = [types.SimpleNamespace(emoji="🔥")]

    async def send_message(self, chat_id, text, reply_to_message_id=None):
        self.sent_messages.append((chat_id, text, reply_to_message_id))
        reply_to = types.SimpleNamespace(
            forward_from_chat=types.SimpleNamespace(username="source_channel"),
            forward_from_message_id=777,
        )
        return types.SimpleNamespace(id=999, reply_to_message=reply_to)

    async def send_reaction(self, chat_id, message_id, emoji):
        self.sent_reactions.append((chat_id, message_id, emoji))

    async def get_available_reactions(self, chat_id=None):
        return self.available_reactions


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def clear_reaction_cache():
    original_cache = dict(main._chat_available_reactions_cache)
    main._chat_available_reactions_cache.clear()
    try:
        yield
    finally:
        main._chat_available_reactions_cache.clear()
        main._chat_available_reactions_cache.update(original_cache)


@pytest.mark.anyio
async def test_discussion_without_settings_skips_actions(monkeypatch):
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

    comment_logs = []

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_bot_send_message(*args, **kwargs):
        return None

    async def fake_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

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
            reaction_discussion_chance=100,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert result is None
    assert client.sent_messages == []
    assert client.sent_reactions == []
    assert comment_logs == []


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
            reaction_discussion_chance=100,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
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
async def test_reaction_skipped_when_not_in_allowed_set(monkeypatch):
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
    client.available_reactions = [types.SimpleNamespace(emoji="👍")]

    chat = types.SimpleNamespace(id=-2000000000, permissions=None, type="supergroup")
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Channel post",
        caption=None,
        id=111,
        reply_to_message=None,
        from_user=from_user,
    )

    comment_logs = []
    skip_logs = []

    async def fake_bot_send_message(*args, **kwargs):
        return None

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    async def fake_update_last_reaction_at(*args, **kwargs):
        return None

    def fake_enqueue_skip_log(session_name, event_type, message_text):
        skip_logs.append((session_name, event_type, message_text))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main, "enqueue_skip_log", fake_enqueue_skip_log)

    try:
        await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=0,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_discussion_chance=100,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert not client.sent_reactions, "Реакция не должна отправляться при отсутствии доступных эмодзи"
    assert skip_logs, "Должен быть записан пропуск реакции"
    assert any("no allowed quick reactions" in entry[2] for entry in skip_logs)

    reaction_statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert any(status and status.startswith("reaction_skipped") for status in reaction_statuses)
    assert any(
        "no allowed quick reactions" in kwargs.get("error", "")
        for _, kwargs in comment_logs
    )


@pytest.mark.anyio
async def test_discussion_and_channel_reaction_chances_are_distinct(monkeypatch):
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

    discussion_message = types.SimpleNamespace(
        chat=chat,
        text="Discussion reply",
        caption=None,
        id=111,
        reply_to_message=reply_to_message,
        from_user=from_user,
    )

    channel_message = types.SimpleNamespace(
        chat=chat,
        text="Channel post",
        caption=None,
        id=222,
        reply_to_message=None,
        from_user=from_user,
    )

    async def fake_bot_send_message(chat_id, text):
        return None

    async def fake_add_comment_log(*args, **kwargs):
        return None

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
            discussion_message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_discussion_chance=0,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )

        assert not client.sent_reactions, "Реакции на обсуждение не должны отправляться при нулевом шансе"

        await main._handle_linked_channel_message(
            client,
            channel_message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_discussion_chance=0,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert client.sent_reactions, "Реакция для поста канала должна быть отправлена"
    assert len(updated_reactions) >= 1


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
            reaction_discussion_chance=100,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
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
            reaction_discussion_chance=100,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
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
            reaction_discussion_chance=100,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
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


@pytest.mark.anyio
async def test_reaction_happens_when_comment_skipped(monkeypatch):
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
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Original post",
        caption=None,
        id=111,
        reply_to_message=None,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fail_generate_comment(*args, **kwargs):  # pragma: no cover - should not be used
        raise AssertionError("generate_comment should not be called when comment is skipped")

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    rolls = [50, 1]

    def fake_randint(a, b):
        return rolls.pop(0) if rolls else 1

    def fake_uniform(a, b):
        return 0

    def fake_choice(seq):
        return seq[0]

    updated_reactions: list[tuple[int, datetime]] = []

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fail_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)

    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()

    try:
        result = await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=10,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_discussion_chance=100,
            discussion_reply_prompt="discussion",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )
        comment_counter = main.skip_log_counters.get(session)
        assert comment_counter is not None
        assert comment_counter["comment"] == 1
        assert "проверяем реакцию" in main.skip_log_last_reasons[session]["comment"]
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)
        main.skip_log_counters.clear()
        main.skip_log_last_reasons.clear()

    assert result is not None
    assert client.sent_messages == []
    assert client.sent_reactions == [(message.chat.id, message.id, "🔥")]
    assert any("поставил реакцию" in log[1] for log in bot_logs)
    statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert "comment_skipped" in statuses
    assert "reaction_success_no_comment" in statuses
    assert updated_reactions
    assert updated_reactions[-1][0] == account_id
    assert updated_reactions[-1][1] == result


@pytest.mark.anyio
async def test_reaction_retries_transient_failure_until_success(monkeypatch):
    userid = 123
    session = "+100500"
    account_id = 42

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    class RetryClient(DummyClient):
        def __init__(self):
            super().__init__()
            self.available_reactions = [
                types.SimpleNamespace(emoji="🔥"),
                types.SimpleNamespace(emoji="❤️"),
                types.SimpleNamespace(emoji="👍"),
            ]
            self.attempts = 0

        async def send_reaction(self, chat_id, message_id, emoji):
            self.sent_reactions.append((chat_id, message_id, emoji))
            self.attempts += 1
            if self.attempts < 3:
                raise Exception(f"temporary failure {self.attempts}")

    client = RetryClient()

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
    sleep_calls = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fake_generate_comment(post_text, system_prompt):
        return f"comment:{post_text}:{system_prompt}"

    async def fake_sleep(duration):
        sleep_calls.append(duration)

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    def fake_choice(seq):
        return seq[0]

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
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

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
            reaction_emojis=["🔥", "❤️", "👍"],
            reaction_chance=100,
            reaction_discussion_chance=100,
            discussion_reply_prompt="prompt",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert [emoji for _, _, emoji in client.sent_reactions] == ["🔥", "❤️", "👍"]
    assert any("поставил реакцию" in log[1] for log in bot_logs)
    reaction_logs = [entry for entry in comment_logs if entry[1]["status"].startswith("reaction_")]
    assert reaction_logs
    assert reaction_logs == [reaction_logs[0]]
    assert reaction_logs[0][1]["status"].startswith("reaction_success")
    assert sleep_calls.count(main.REACTION_RETRY_DELAY_SECONDS) == 2
    assert len(updated_reactions) == 1


@pytest.mark.anyio
async def test_reaction_retries_record_single_error(monkeypatch):
    userid = 123
    session = "+100500"
    account_id = 42

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)
    original_skip_counters = {
        key: counter.copy() for key, counter in main.skip_log_counters.items()
    }
    original_skip_reasons = {
        key: dict(value) for key, value in main.skip_log_last_reasons.items()
    }

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    class AlwaysFailClient(DummyClient):
        def __init__(self):
            super().__init__()
            self.available_reactions = [
                types.SimpleNamespace(emoji="🔥"),
                types.SimpleNamespace(emoji="❤️"),
                types.SimpleNamespace(emoji="👍"),
            ]
            self.attempts = 0

        async def send_reaction(self, chat_id, message_id, emoji):
            self.sent_reactions.append((chat_id, message_id, emoji))
            self.attempts += 1
            raise Exception("permanent failure")

    client = AlwaysFailClient()

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
    sleep_calls = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    def fake_generate_comment(post_text, system_prompt):
        return f"comment:{post_text}:{system_prompt}"

    async def fake_sleep(duration):
        sleep_calls.append(duration)

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    def fake_choice(seq):
        return seq[0]

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

    reaction_skip_counter = None
    reaction_skip_reason = None

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
            reaction_emojis=["🔥", "❤️", "👍"],
            reaction_chance=100,
            reaction_discussion_chance=100,
            discussion_reply_prompt="prompt",
            discussion_reply_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=5,
            reactions_enabled=True,
            last_reaction_at=None,
        )
        counter = main.skip_log_counters.get(session)
        if counter is not None:
            reaction_skip_counter = counter.copy()
        reaction_skip_reason = main.skip_log_last_reasons.get(session, {}).get(
            "reaction"
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)
        main.skip_log_counters.clear()
        for key, counter in original_skip_counters.items():
            main.skip_log_counters[key] = counter.copy()
        main.skip_log_last_reasons.clear()
        for key, value in original_skip_reasons.items():
            main.skip_log_last_reasons[key] = dict(value)

    assert [emoji for _, _, emoji in client.sent_reactions] == ["🔥", "❤️", "👍"]
    error_messages = [log for log in bot_logs if "ошибка при установке реакции" in log[1]]
    assert len(error_messages) == 1
    reaction_logs = [entry for entry in comment_logs if entry[1]["status"].startswith("reaction_")]
    assert reaction_logs
    assert reaction_logs == [reaction_logs[0]]
    assert reaction_logs[0][1]["status"].startswith("reaction_error")
    assert reaction_logs[0][1]["error"].startswith("reaction error after")
    assert sleep_calls.count(main.REACTION_RETRY_DELAY_SECONDS) == 2
    assert reaction_skip_counter is not None
    assert reaction_skip_counter["reaction"] == 1
    assert reaction_skip_reason is not None
    assert "reaction error after" in reaction_skip_reason
