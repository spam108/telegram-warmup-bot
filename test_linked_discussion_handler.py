import os
import types

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

    def fake_randint(a, b):
        return 1

    def fake_uniform(a, b):
        return 0

    monkeypatch.setattr(main.bot, "send_message", fake_bot_send_message)
    monkeypatch.setattr(main, "add_comment_log", fake_add_comment_log)
    monkeypatch.setattr(main, "generate_comment", fake_generate_comment)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
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
            chance=100,
            xsleep=0,
            ysleep=0,
            system_promt="prompt",
            reaction_emojis=["🔥"],
            reaction_chance=100,
            reaction_sleep_min=0,
            reaction_sleep_max=0,
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
