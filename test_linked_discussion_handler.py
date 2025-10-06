import os
import types
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import main


class DummyClient:
    def __init__(self, discussion_chat_id=-4000000000):
        self.sent_messages = []
        self.sent_reactions = []
        self.available_reactions = [types.SimpleNamespace(emoji="🔥")]
        self.discussion_chat_id = discussion_chat_id

    async def send_message(self, chat_id, text, reply_to_message_id=None):
        self.sent_messages.append((chat_id, text, reply_to_message_id))
        reply_to = types.SimpleNamespace(
            forward_from_chat=types.SimpleNamespace(username="source_channel"),
            forward_from_message_id=777,
        )
        return types.SimpleNamespace(
            id=999,
            reply_to_message=reply_to,
            chat=types.SimpleNamespace(id=self.discussion_chat_id),
        )

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


def test_discussion_reply_detection_for_account_comment():
    chat = types.SimpleNamespace(type="supergroup")
    reply_author = types.SimpleNamespace(is_self=True)
    reply = types.SimpleNamespace(
        forward_from_chat=None,
        from_user=reply_author,
    )
    message = types.SimpleNamespace(chat=chat, reply_to_message=reply)

    assert main._is_discussion_reply_message(message)


def test_discussion_reply_detection_skips_private_chat():
    chat = types.SimpleNamespace(type="private")
    reply_author = types.SimpleNamespace(is_self=True)
    reply = types.SimpleNamespace(
        forward_from_chat=None,
        from_user=reply_author,
    )
    message = types.SimpleNamespace(chat=chat, reply_to_message=reply)

    assert not main._is_discussion_reply_message(message)


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
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Original post",
        caption=None,
        id=111,
        reply_to_message=None,
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
async def test_comment_not_sent_if_session_stops_during_sleep(monkeypatch):
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

    comment_logs = []
    notifications = []
    generate_called = False

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_bot_send_message(*args, **kwargs):
        notifications.append((args, kwargs))

    def fake_generate_comment(*args, **kwargs):
        nonlocal generate_called
        generate_called = True
        return "generated"

    sleep_calls = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        if len(sleep_calls) == 1:
            main.active_sessions[key] = False
        return None

    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.random, "randint", lambda a, b: 1)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: (a + b) / 2)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

    try:
        result = await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=1,
            ysleep=2,
            system_promt="prompt",
            reaction_emojis=[],
            reaction_chance=0,
            reaction_discussion_chance=None,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=None,
            reactions_enabled=False,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert result is None
    assert client.sent_messages == []
    assert generate_called is False
    assert notifications == []
    assert comment_logs == [] or comment_logs[0][1].get("status") == "comment_skipped"


@pytest.mark.anyio
async def test_comment_not_sent_if_quiet_starts_during_sleep(monkeypatch):
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

    comment_logs = []
    notifications = []
    generate_called = False
    quiet_state = {"value": False}
    quiet_checks = []

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_bot_send_message(*args, **kwargs):
        notifications.append((args, kwargs))

    def fake_generate_comment(*args, **kwargs):
        nonlocal generate_called
        generate_called = True
        return "generated"

    def fake_is_quiet_period():
        quiet_checks.append(quiet_state["value"])
        return quiet_state["value"]

    sleep_calls = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        if len(sleep_calls) == 1:
            quiet_state["value"] = True
        return None

    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.random, "randint", lambda a, b: 1)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: (a + b) / 2)
    monkeypatch.setattr(main, "is_quiet_period", fake_is_quiet_period)

    try:
        result = await main._handle_linked_channel_message(
            client,
            message,
            userid=userid,
            session=session,
            account_id=account_id,
            chance=100,
            xsleep=1,
            ysleep=2,
            system_promt="prompt",
            reaction_emojis=[],
            reaction_chance=0,
            reaction_discussion_chance=None,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=None,
            reactions_enabled=False,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert result is None
    assert client.sent_messages == []
    assert generate_called is False
    assert quiet_checks.count(True) >= 1
    assert len(notifications) == 1
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
    channels = {kwargs.get("channel") for _, kwargs in comment_logs}
    assert str(client.discussion_chat_id) in channels
    assert len(updated_reactions) == 1


@pytest.mark.anyio
async def test_reply_to_account_comment_without_reply_object(monkeypatch):
    userid = 555
    session = "+200"
    account_id = 99

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-3000000000, permissions=None, type="supergroup")
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Follow-up reply",
        caption=None,
        id=222,
        reply_to_message=None,
        reply_to_message_id=777,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_has_successful_comment_log_entry(account, channel, msg_id):
        return account == account_id and channel == str(chat.id) and msg_id == 777

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_uniform(a, b):
        return 0

    updated_reactions = []

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "has_successful_comment_log_entry", fake_has_successful_comment_log_entry)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
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
            chance=0,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=0,
            reaction_discussion_chance=0,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=None,
            reactions_enabled=True,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert not client.sent_messages, "Комментарий не должен отправляться"
    assert client.sent_reactions, "Реакция должна быть установлена"
    assert any("поставил реакцию" in log[1] for log in bot_logs)
    statuses = {kwargs.get("status") for _, kwargs in comment_logs}
    assert statuses == {"reaction_success_reply"}
    assert len(updated_reactions) == 1


@pytest.mark.anyio
async def test_reply_to_account_comment_without_reply_object(monkeypatch):
    userid = 555
    session = "+200"
    account_id = 99

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-3000000000, permissions=None, type="supergroup")
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="Follow-up reply",
        caption=None,
        id=222,
        reply_to_message=None,
        reply_to_message_id=777,
        from_user=from_user,
    )

    bot_logs = []
    comment_logs = []

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_has_successful_comment_log_entry(account, channel, msg_id):
        return account == account_id and channel == str(chat.id) and msg_id == 777

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_uniform(a, b):
        return 0

    updated_reactions = []

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "has_successful_comment_log_entry", fake_has_successful_comment_log_entry)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
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
            chance=0,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=0,
            reaction_discussion_chance=0,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=None,
            reactions_enabled=True,
            last_reaction_at=None,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert not client.sent_messages, "Комментарий не должен отправляться"
    assert client.sent_reactions, "Реакция должна быть установлена"
    assert any("поставил реакцию" in log[1] for log in bot_logs)
    statuses = {kwargs.get("status") for _, kwargs in comment_logs}
    assert statuses == {"reaction_success_reply"}
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
async def test_reaction_retry_eventual_success(monkeypatch):
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
    client.available_reactions = [
        types.SimpleNamespace(emoji="🔥"),
        types.SimpleNamespace(emoji="💥"),
        types.SimpleNamespace(emoji="✨"),
    ]

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
    bot_logs = []
    skip_logs = []
    send_reaction_calls = []
    refreshed_requests = []

    class TransientError(Exception):
        pass

    class DummyReactionInvalid(Exception):
        pass

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    def fake_enqueue_skip_log(session_name, event_type, message_text):
        skip_logs.append((session_name, event_type, message_text))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    async def fake_update_last_reaction_at(*args, **kwargs):
        return None

    async def fake_get_chat_available_quick_reactions(client_obj, chat_id, force_refresh=False):
        refreshed_requests.append(force_refresh)
        if force_refresh:
            return {"✨"}
        return {"🔥", "💥", "✨"}

    def fake_choice(sequence):
        return sequence[0]

    def fake_randint(a, b):
        return a

    def fake_uniform(a, b):
        return a

    async def fake_send_reaction(chat_id, message_id, emoji):
        attempt = len(send_reaction_calls)
        send_reaction_calls.append((chat_id, message_id, emoji))
        if attempt == 0:
            raise TransientError("temporary error")
        if attempt == 1:
            raise DummyReactionInvalid("invalid emoji")

    client.send_reaction = fake_send_reaction

    monkeypatch.setattr(main, "ReactionInvalid", DummyReactionInvalid)
    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "enqueue_skip_log", fake_enqueue_skip_log)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main, "_get_chat_available_quick_reactions", fake_get_chat_available_quick_reactions)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

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
            reaction_emojis=["🔥", "💥", "✨"],
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

    assert [call[2] for call in send_reaction_calls] == ["🔥", "💥", "✨"]
    assert refreshed_requests.count(True) == 1
    assert all(event_type != "reaction" for _, event_type, _ in skip_logs)
    assert len(bot_logs) == 1
    assert "поставил реакцию" in bot_logs[0][1]

    reaction_statuses = [
        kwargs.get("status")
        for _, kwargs in comment_logs
        if kwargs.get("status", "").startswith("reaction")
    ]
    success_statuses = [status for status in reaction_statuses if status.startswith("reaction_success")]
    assert len(success_statuses) == 1
    assert all(not status.startswith("reaction_error") for status in reaction_statuses)


@pytest.mark.anyio
async def test_reaction_retry_total_failure(monkeypatch):
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
    client.available_reactions = [
        types.SimpleNamespace(emoji="🔥"),
        types.SimpleNamespace(emoji="💥"),
        types.SimpleNamespace(emoji="✨"),
    ]

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
    bot_logs = []
    skip_logs = []
    send_reaction_calls = []
    refreshed_requests = []

    class TransientError(Exception):
        pass

    class DummyReactionInvalid(Exception):
        pass

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    def fake_enqueue_skip_log(session_name, event_type, message_text):
        skip_logs.append((session_name, event_type, message_text))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    async def fake_update_last_reaction_at(*args, **kwargs):
        return None

    async def fake_get_chat_available_quick_reactions(client_obj, chat_id, force_refresh=False):
        refreshed_requests.append(force_refresh)
        if force_refresh:
            return {"✨"}
        return {"🔥", "💥", "✨"}

    def fake_choice(sequence):
        return sequence[0]

    def fake_randint(a, b):
        return a

    def fake_uniform(a, b):
        return a

    async def fake_send_reaction(chat_id, message_id, emoji):
        attempt = len(send_reaction_calls)
        send_reaction_calls.append((chat_id, message_id, emoji))
        if attempt == 0:
            raise TransientError("temporary error")
        if attempt == 1:
            raise DummyReactionInvalid("invalid emoji")
        raise TransientError("permanent failure")

    client.send_reaction = fake_send_reaction

    monkeypatch.setattr(main, "ReactionInvalid", DummyReactionInvalid)
    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "enqueue_skip_log", fake_enqueue_skip_log)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main, "_get_chat_available_quick_reactions", fake_get_chat_available_quick_reactions)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main.random, "randint", fake_randint)
    monkeypatch.setattr(main.random, "uniform", fake_uniform)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

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
            reaction_emojis=["🔥", "💥", "✨"],
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

    assert [call[2] for call in send_reaction_calls] == ["🔥", "💥", "✨"]
    assert refreshed_requests.count(True) == 1
    assert all(event_type != "reaction" for _, event_type, _ in skip_logs)
    assert len(bot_logs) == 1
    assert "ошибка" in bot_logs[0][1]

    reaction_statuses = [
        kwargs.get("status")
        for _, kwargs in comment_logs
        if kwargs.get("status", "").startswith("reaction")
    ]
    error_statuses = [status for status in reaction_statuses if status.startswith("reaction_error")]
    assert len(error_statuses) == 1
    assert all(not status.startswith("reaction_success") for status in reaction_statuses)

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
async def test_reaction_sent_for_reply_to_own_comment(monkeypatch):
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
    reply_author = types.SimpleNamespace(is_self=True)
    reply_message = types.SimpleNamespace(from_user=reply_author)
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="reply",
        caption=None,
        id=222,
        reply_to_message=reply_message,
        from_user=from_user,
    )

    comment_logs = []
    updated_reactions: list[tuple[int, datetime]] = []

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    async def fake_bot_send_message(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()

    recent_reaction = datetime.now(timezone.utc)

    try:
        result = await main._handle_linked_channel_message(
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
            reaction_chance=0,
            reaction_discussion_chance=0,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
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

    assert client.sent_reactions == [(message.chat.id, message.id, "🔥")]
    statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert "reaction_success_reply" in statuses
    assert updated_reactions
    assert updated_reactions[-1][0] == account_id
    assert updated_reactions[-1][1] == result
    assert result >= recent_reaction


@pytest.mark.anyio
async def test_forced_reply_reaction_ignores_limit(monkeypatch):
    userid = 321
    session = "+300"
    account_id = 77

    original_active_sessions = dict(main.active_sessions)
    original_quiet = set(main.quiet_sessions_notified)

    main.active_sessions.clear()
    main.quiet_sessions_notified.clear()
    key = main.make_session_key(userid, session)
    main.active_sessions[key] = True

    client = DummyClient()

    chat = types.SimpleNamespace(id=-5000000000, permissions=None, type="supergroup")
    reply_author = types.SimpleNamespace(is_self=True)
    reply_message = types.SimpleNamespace(from_user=reply_author)
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="reply",
        caption=None,
        id=333,
        reply_to_message=reply_message,
        from_user=from_user,
    )

    comment_logs = []
    skip_logs = []
    updated_reactions: list[tuple[int, datetime]] = []

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    async def fake_bot_send_message(*args, **kwargs):
        return None

    def fake_enqueue_skip_log(session_name, event_type, reason):
        skip_logs.append((session_name, event_type, reason))

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)
    monkeypatch.setattr(main, "enqueue_skip_log", fake_enqueue_skip_log)

    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()

    recent_reaction = datetime.now(timezone.utc)

    try:
        result = await main._handle_linked_channel_message(
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
            reaction_chance=0,
            reaction_discussion_chance=0,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
            reaction_limit_per_message=0,
            reactions_enabled=True,
            last_reaction_at=recent_reaction,
        )
    finally:
        main.active_sessions.clear()
        main.active_sessions.update(original_active_sessions)
        main.quiet_sessions_notified.clear()
        main.quiet_sessions_notified.update(original_quiet)

    assert client.sent_reactions == [(message.chat.id, message.id, "🔥")]
    statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert "reaction_success_reply" in statuses
    assert not skip_logs
    assert updated_reactions
    assert updated_reactions[-1][0] == account_id
    assert updated_reactions[-1][1] == result
    assert result >= recent_reaction


@pytest.mark.anyio
async def test_reaction_for_reply_detected_via_comment_log(monkeypatch):
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
    reply_message = types.SimpleNamespace(id=555, from_user=None)
    from_user = types.SimpleNamespace(is_self=False)
    message = types.SimpleNamespace(
        chat=chat,
        text="reply",
        caption=None,
        id=333,
        reply_to_message=reply_message,
        from_user=from_user,
    )

    comment_logs = []
    updated_reactions: list[tuple[int, datetime]] = []

    async def fake_add_comment_log(*args, **kwargs):
        comment_logs.append((args, kwargs))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_count_reactions(*args, **kwargs):
        return 0

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    async def fake_bot_send_message(*args, **kwargs):
        return None

    async def fake_has_successful_comment(account, channel, message_id):
        assert account == account_id
        assert channel == str(chat.id)
        assert message_id == reply_message.id
        return True

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main, "has_successful_comment_log_entry", fake_has_successful_comment)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()

    recent_reaction = datetime.now(timezone.utc)

    try:
        result = await main._handle_linked_channel_message(
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
            reaction_chance=0,
            reaction_discussion_chance=0,
            discussion_reply_prompt=None,
            discussion_reply_chance=None,
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

    assert client.sent_reactions == [(message.chat.id, message.id, "🔥")]
    statuses = [kwargs.get("status") for _, kwargs in comment_logs]
    assert "reaction_success_reply" in statuses
    assert updated_reactions
    assert updated_reactions[-1][0] == account_id
    assert updated_reactions[-1][1] == result
    assert result >= recent_reaction


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
async def test_reaction_retries_until_success(monkeypatch):
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
    client.available_reactions = [
        types.SimpleNamespace(emoji="🔥"),
        types.SimpleNamespace(emoji="👍"),
        types.SimpleNamespace(emoji="🎉"),
    ]

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

    class DummyReactionInvalid(Exception):
        pass

    attempts = []
    bot_logs = []
    comment_logs = []
    skip_logs = []
    sleep_calls = []
    updated_reactions = []

    async def flaky_send_reaction(chat_id, message_id, emoji):
        attempts.append((chat_id, message_id, emoji))
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        if len(attempts) == 2:
            client.available_reactions = [
                types.SimpleNamespace(emoji="🔥"),
                types.SimpleNamespace(emoji="🎉"),
            ]
            raise DummyReactionInvalid("invalid reaction")
        return None

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*_, **kwargs):
        comment_logs.append(kwargs)

    async def fake_sleep(*args, **kwargs):
        sleep_calls.append((args, kwargs))

    async def fake_update_last_reaction_at(account, ts):
        updated_reactions.append((account, ts))

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_enqueue_skip_log(session_name, event_type, message_text):
        skip_logs.append((session_name, event_type, message_text))

    def fake_choice(seq):
        return seq[0]

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main, "ReactionInvalid", DummyReactionInvalid)
    monkeypatch.setattr(client, "send_reaction", flaky_send_reaction)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "update_last_reaction_at", fake_update_last_reaction_at)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", lambda a, b: 1)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main, "enqueue_skip_log", fake_enqueue_skip_log)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

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
            reaction_emojis=["🔥", "👍", "🎉"],
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

    attempted_emojis = [emoji for _, _, emoji in attempts]
    assert attempted_emojis == ["🔥", "👍", "🎉"]
    assert len(bot_logs) == 1
    assert "поставил реакцию" in bot_logs[0][1]
    statuses = [log.get("status") for log in comment_logs]
    success_logs = [status for status in statuses if status and status.startswith("reaction_success")]
    assert len(success_logs) == 1
    assert not any(status and status.startswith("reaction_error") for status in statuses)
    reaction_skip_logs = [entry for entry in skip_logs if entry[1] == "reaction"]
    assert reaction_skip_logs == []
    assert len(updated_reactions) == 1


@pytest.mark.anyio
async def test_reaction_retries_until_failure(monkeypatch):
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
    client.available_reactions = [
        types.SimpleNamespace(emoji="🔥"),
        types.SimpleNamespace(emoji="👍"),
        types.SimpleNamespace(emoji="🎉"),
    ]

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

    class DummyReactionInvalid(Exception):
        pass

    attempts = []
    bot_logs = []
    comment_logs = []
    skip_logs = []
    sleep_calls = []

    async def flaky_send_reaction(chat_id, message_id, emoji):
        attempts.append((chat_id, message_id, emoji))
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        if len(attempts) == 2:
            client.available_reactions = [
                types.SimpleNamespace(emoji="🎉"),
            ]
            raise DummyReactionInvalid("invalid reaction")
        raise RuntimeError("permanent failure")

    async def fake_bot_send_message(chat_id, text):
        bot_logs.append((chat_id, text))

    async def fake_add_comment_log(*_, **kwargs):
        comment_logs.append(kwargs)

    async def fake_sleep(*args, **kwargs):
        sleep_calls.append((args, kwargs))

    async def fake_count_reactions(*args, **kwargs):
        return 0

    def fake_enqueue_skip_log(session_name, event_type, message_text):
        skip_logs.append((session_name, event_type, message_text))

    def fake_choice(seq):
        return seq[0]

    monkeypatch.setattr(main, "REACTION_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main, "ReactionInvalid", DummyReactionInvalid)
    monkeypatch.setattr(client, "send_reaction", flaky_send_reaction)
    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "count_reactions_for_message", fake_count_reactions)
    monkeypatch.setattr(main.random, "randint", lambda a, b: 1)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(main.random, "choice", fake_choice)
    monkeypatch.setattr(main, "enqueue_skip_log", fake_enqueue_skip_log)
    monkeypatch.setattr(main, "is_quiet_period", lambda: False)

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
            reaction_emojis=["🔥", "👍", "🎉"],
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

    attempted_emojis = [emoji for _, _, emoji in attempts]
    assert attempted_emojis == ["🔥", "👍", "🎉"]
    assert len(bot_logs) == 1
    assert "ошибка при установке реакции" in bot_logs[0][1]
    statuses = [log.get("status") for log in comment_logs]
    error_logs = [status for status in statuses if status and status.startswith("reaction_error")]
    assert len(error_logs) == 1
    assert not any(status and status.startswith("reaction_success") for status in statuses)
    reaction_skip_logs = [entry for entry in skip_logs if entry[1] == "reaction"]
    assert reaction_skip_logs == []
