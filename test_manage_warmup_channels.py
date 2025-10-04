import asyncio
import os
import types
from datetime import datetime, timezone
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


def test_manage_warmup_channels_dash_keeps_queue_and_resets_schedule(monkeypatch):
    state = DummyState({"account": "session", "account_id": 42})
    message = DummyMessage("-")

    sentinel_next = datetime(2024, 1, 1, tzinfo=timezone.utc)

    calls: Dict[str, Any] = {
        "sync": False,
        "mode": [],
        "main_message": False,
        "plan": None,
        "db_update": None,
    }

    async def fake_sync(account_id: int, channels: Any) -> None:
        calls["sync"] = True

    async def fake_set_account_mode(account_id: int, mode: str, **kwargs: Any) -> None:
        calls["mode"].append((account_id, mode, kwargs))

    async def fake_get_pending(account_id: int, limit: int = 100):
        return [{"channel": "@one"}, {"channel": "@two"}]

    async def fake_ensure_latest_warmup_settings(force: bool = False):
        return "settings"

    def fake_plan_next_warmup_join(now, settings):
        calls["plan"] = (now, settings)
        return sentinel_next

    async def fake_db_update(account_id: int, *, next_join=None, last_join=None):
        calls["db_update"] = {"account_id": account_id, "next_join": next_join, "last_join": last_join}

    async def fake_format(channels, title="Каналы", max_display=10):
        return f"{title}: {', '.join(channels)}" if channels else f"{title}: нет"

    async def fake_main_message(msg):
        calls["main_message"] = True

    monkeypatch.setattr(main, "sync_warmup_channels", fake_sync)
    monkeypatch.setattr(main, "set_account_mode", fake_set_account_mode)
    monkeypatch.setattr(main, "get_warmup_pending", fake_get_pending)
    monkeypatch.setattr(main, "ensure_latest_warmup_settings", fake_ensure_latest_warmup_settings)
    monkeypatch.setattr(main, "plan_next_warmup_join", fake_plan_next_warmup_join)
    monkeypatch.setattr(main, "db_update_warmup_schedule", fake_db_update)
    monkeypatch.setattr(main, "format_channels_display", fake_format)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    asyncio.run(main.manage_warmup_channels(message, state))

    assert not calls["sync"], "Очередь не должна обновляться при вводе '-'"
    assert calls["mode"] == [(42, "warmup", {"warmup_days": main.WARMUP_DEFAULT_DAYS})]
    assert calls["plan"] is not None, "Должно обновляться расписание прогрева"
    assert calls["db_update"] == {"account_id": 42, "next_join": sentinel_next, "last_join": None}
    assert state.cleared, "Состояние должно очищаться после обработки"
    assert calls["main_message"], "Должен показываться главный экран после обработки"
    assert any("не изменена" in ans for ans in message.answers), "Пользователь должен получать уведомление об отсутствии изменений"


def test_manage_warmup_channels_clear_switches_to_standard(monkeypatch):
    state = DummyState({"account": "session", "account_id": 99})
    message = DummyMessage("clear")

    calls: Dict[str, Any] = {
        "sync": None,
        "mode": [],
        "plan": False,
        "db_update": False,
    }

    async def fake_sync(account_id: int, channels: Any) -> None:
        calls["sync"] = (account_id, channels)

    async def fake_set_account_mode(account_id: int, mode: str, **kwargs: Any) -> None:
        calls["mode"].append((account_id, mode, kwargs))

    async def fake_get_pending(account_id: int, limit: int = 100):
        return []

    async def fake_ensure_latest_warmup_settings(force: bool = False):
        calls["plan"] = True
        return "settings"

    def fake_plan_next_warmup_join(now, settings):
        calls["plan"] = True
        return datetime.now(timezone.utc)

    async def fake_db_update(account_id: int, *, next_join=None, last_join=None):
        calls["db_update"] = True

    async def fake_format(channels, title="Каналы", max_display=10):
        return "Очередь: нет"

    async def fake_main_message(msg):
        pass

    monkeypatch.setattr(main, "sync_warmup_channels", fake_sync)
    monkeypatch.setattr(main, "set_account_mode", fake_set_account_mode)
    monkeypatch.setattr(main, "get_warmup_pending", fake_get_pending)
    monkeypatch.setattr(main, "ensure_latest_warmup_settings", fake_ensure_latest_warmup_settings)
    monkeypatch.setattr(main, "plan_next_warmup_join", fake_plan_next_warmup_join)
    monkeypatch.setattr(main, "db_update_warmup_schedule", fake_db_update)
    monkeypatch.setattr(main, "format_channels_display", fake_format)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    asyncio.run(main.manage_warmup_channels(message, state))

    assert calls["sync"] == (99, []), "Очередь должна очищаться"
    assert calls["mode"] == [(99, "standard", {"warmup_days": None})]
    assert calls["plan"] is False, "Не должно планироваться расписание при пустой очереди"
    assert calls["db_update"] is False, "Не должно обновляться расписание при пустой очереди"
