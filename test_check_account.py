import os
from typing import Any

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

import main  # noqa: E402


class _DummyClient:
    def __init__(self) -> None:
        self.is_connected = True

    async def get_me(self) -> Any:  # pragma: no cover - not used but keeps interface
        return {"id": 1}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_check_account_waits_for_existing_client_shutdown(monkeypatch):
    user_id = 123
    phone = "+79990000000"
    key = main.make_session_key(user_id, phone)

    main.active_sessions.pop(key, None)
    main.active_pyrogram_clients[key] = _DummyClient()
    main.active_client_locks.pop(key, None)

    sent_messages: list[tuple[int, str]] = []

    async def fake_send_message(uid: int, text: str, **kwargs: Any) -> None:
        sent_messages.append((uid, text))

    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "CHECK_ACCOUNT_SHUTDOWN_TIMEOUT", 0.05)
    monkeypatch.setattr(main, "CHECK_ACCOUNT_SHUTDOWN_INTERVAL", 0.01)

    class ForbiddenClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - ensures no instantiation
            raise AssertionError("Client should not be created while shutdown is pending")

    monkeypatch.setattr(main, "Client", ForbiddenClient)

    result = await main.check_account(user_id, phone)

    assert result is False
    assert sent_messages == [(user_id, f"Аккаунт {phone} останавливается")]

    main.active_pyrogram_clients.pop(key, None)
    main.active_client_locks.pop(key, None)
