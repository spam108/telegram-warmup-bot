import os

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
