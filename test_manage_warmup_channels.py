import asyncio
import os
import types
from typing import Any, Dict

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

import main


class DummyState:
    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self.cleared = False

    async def get_data(self) -> Dict[str, Any]:
        return self._data

    async def clear(self) -> None:
        self.cleared = True


class DummyMessage:
    def __init__(self, text: str, user_id: int = 1):
        self.text = text
        self.from_user = types.SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


def test_manage_warmup_channels_dash_keeps_queue(monkeypatch):
    state = DummyState({"account": "session", "account_id": 42})
    message = DummyMessage("-")

    calls: Dict[str, Any] = {"sync": False, "mode": [], "main_message": False}

    async def fake_sync(account_id: int, channels: Any) -> None:
        calls["sync"] = True

    async def fake_set_account_mode(account_id: int, mode: str, **kwargs: Any) -> None:
        calls["mode"].append((account_id, mode, kwargs))

    async def fake_get_pending(account_id: int, limit: int = 100):
        return [{"channel": "@one"}, {"channel": "@two"}]

    async def fake_format(channels, title="Каналы", max_display=10):
        return f"{title}: {', '.join(channels)}" if channels else f"{title}: нет"

    async def fake_main_message(msg):
        calls["main_message"] = True

    monkeypatch.setattr(main, "sync_warmup_channels", fake_sync)
    monkeypatch.setattr(main, "set_account_mode", fake_set_account_mode)
    monkeypatch.setattr(main, "get_warmup_pending", fake_get_pending)
    monkeypatch.setattr(main, "format_channels_display", fake_format)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    asyncio.run(main.manage_warmup_channels(message, state))

    assert not calls["sync"], "Очередь не должна обновляться при вводе '-'"
    assert not calls["mode"], "Режим аккаунта не должен меняться при вводе '-'"
    assert state.cleared, "Состояние должно очищаться после обработки"
    assert calls["main_message"], "Должен показываться главный экран после обработки"
    assert any("не изменена" in ans for ans in message.answers), "Пользователь должен получать уведомление об отсутствии изменений"
