import os

from types import SimpleNamespace

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import main


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_skip_logs():
    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()
    main.skip_log_last_flush_at = None
    yield
    main.skip_log_counters.clear()
    main.skip_log_last_reasons.clear()
    main.skip_log_last_flush_at = None


@pytest.mark.anyio
async def test_flush_skip_logs_sends_aggregated_summary(monkeypatch):
    sent_messages = []

    async def fake_send_message(chat_id, text):
        sent_messages.append((chat_id, text))

    monkeypatch.setattr(main.bot, "send_message", fake_send_message)

    main.enqueue_skip_log("+100500", "comment", "reason1")
    main.enqueue_skip_log("+100500", "comment", "reason2")
    main.enqueue_skip_log("+100500", "reaction", "reaction_reason")
    main.enqueue_skip_log("+200600", "reaction", "other_reason")

    await main.flush_skip_logs()

    assert len(sent_messages) == 1
    _, message = sent_messages[0]

    assert "Сводка пропусков" in message
    assert "+100500" in message
    assert "Комментарии: 2" in message
    assert "reason2" in message  # последняя причина для комментариев
    assert "Реакции: 1" in message
    assert "+200600" in message

    assert main.skip_log_counters == {}
    assert main.skip_log_last_reasons == {}
    assert main.skip_log_last_flush_at is not None


class DummyMessage:
    def __init__(self, user_id: int, text: str):
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.answers = []

    async def answer(self, text, **kwargs):  # noqa: D401, ANN001
        self.answers.append(text)


@pytest.mark.anyio
async def test_skip_summary_requires_manual_trigger(monkeypatch):
    sent_messages = []

    async def fake_send_message(chat_id, text, **kwargs):  # noqa: D401, ANN001
        sent_messages.append((chat_id, text))

    async def fake_is_user_authenticated(user_id):  # noqa: D401, ANN001
        return True

    async def fake_main_message(message):  # noqa: D401, ANN001, ARG001
        return None

    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "is_user_authenticated", fake_is_user_authenticated)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    main.enqueue_skip_log("+100500", "comment", "reason1")

    assert sent_messages == []  # Ничего не отправлено автоматически

    message = DummyMessage(user_id=1, text=main.SKIP_SUMMARY_BUTTON_TEXT)

    await main.handle_skip_summary_button(message, SimpleNamespace())

    assert message.answers == ["Сводка пропусков отправлена в лог-канал."]
    assert len(sent_messages) == 1

    chat_id, text = sent_messages[0]
    assert chat_id == main.log_channel
    assert "Сводка пропусков" in text
