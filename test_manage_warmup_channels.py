import asyncio
import os
import types
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

import main
import db


class DummyState:
    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self.cleared = False
        self.state_value = None

    async def get_data(self) -> Dict[str, Any]:
        return self._data

    async def update_data(self, updates: Dict[str, Any]) -> None:
        self._data.update(updates)

    async def clear(self) -> None:
        self.cleared = True

    async def set_state(self, value: Any) -> None:
        self.state_value = value


class DummyMessage:
    def __init__(self, text: str, user_id: int = 1):
        self.text = text
        self.from_user = types.SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


class DummyCallback:
    def __init__(self, data: str, user_id: int = 1):
        self.data = data
        self.from_user = types.SimpleNamespace(id=user_id)
        self._deleted = False
        self.message = types.SimpleNamespace(delete=self._delete)
        self.answered = False
        self.answer_text = None
        self.answer_kwargs: Dict[str, Any] = {}

    async def _delete(self) -> None:
        self._deleted = True

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answered = True
        self.answer_text = text
        self.answer_kwargs = kwargs


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

    async def fake_format(channels, title="Каналы", max_display=10, **kwargs: Any):
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

    async def fake_format(channels, title="Каналы", max_display=10, **kwargs: Any):
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


def test_warmclear_callback_clears_queue_and_state(monkeypatch):
    state = DummyState({"account": "session", "account_id": 77})
    callback = DummyCallback("warmclear_session")

    calls: Dict[str, Any] = {
        "sync": None,
        "mode": None,
        "messages": [],
        "main_message": False,
    }

    async def fake_get_account_by_session(user_id: int, session: str):
        assert session == "session"
        return {"id": 77}

    async def fake_sync(account_id: int, channels: Any) -> None:
        calls["sync"] = (account_id, channels)

    async def fake_set_mode(account_id: int, mode: str, **kwargs: Any) -> None:
        calls["mode"] = (account_id, mode, kwargs)

    async def fake_send_message(user_id: int, text: str, **kwargs: Any) -> None:
        calls["messages"].append((user_id, text, kwargs))

    async def fake_main_message(msg):
        calls["main_message"] = True

    monkeypatch.setattr(main, "get_account_by_session", fake_get_account_by_session)
    monkeypatch.setattr(main, "sync_warmup_channels", fake_sync)
    monkeypatch.setattr(main, "set_account_mode", fake_set_mode)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    asyncio.run(main.callbacks(callback, state))

    assert state.cleared, "Состояние должно очищаться после очистки очереди"
    assert calls["sync"] == (77, []), "Очередь должна очищаться через warmclear"
    assert calls["mode"] == (77, "standard", {"warmup_days": None})
    assert calls["main_message"], "После очистки должно обновляться главное меню"
    assert calls["messages"], "Пользователь должен получать уведомление"
    assert callback.answered, "Коллбек должен подтверждаться"


@pytest.mark.anyio("asyncio")
async def test_add_sleeps_syncs_subscriptions(monkeypatch, tmp_path, postgres_db_url):
    assert postgres_db_url
    await db.close_db()
    await db.init_db()

    user_id = 100
    phone = "71234567890"
    session_dir = tmp_path / "sessions" / str(user_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = str(session_dir / f"{phone}.session")

    await db.ensure_user(user_id)
    account = await db.ensure_account(user_id, phone, session_path)

    state = DummyState({"account": phone, "account_id": account["id"]})
    message = DummyMessage("10-20", user_id=user_id)

    async def fake_check_account(user: int, session: str) -> bool:
        assert user == user_id
        assert session == phone
        return True

    class DummyChat:
        def __init__(self, username: str | None):
            self.username = username
            self.type = "ChatType.CHANNEL"

    class DummyDialog:
        def __init__(self, chat: Any):
            self.chat = chat

    class DummyClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._dialogs = [
                DummyDialog(DummyChat("alpha")),
                DummyDialog(DummyChat("beta")),
                DummyDialog(DummyChat("alpha")),
                DummyDialog(DummyChat(None)),
            ]

        async def __aenter__(self) -> "DummyClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get_dialogs(self):
            for dialog in self._dialogs:
                yield dialog

    format_calls: List[List[str]] = []

    async def fake_format(channels, title="Каналы", max_display=10, use_markdown=False):
        format_calls.append(list(channels))
        return f"{title}: {', '.join(channels)}"

    sent_messages: List[tuple[int, str, Dict[str, Any]]] = []

    async def fake_send_message(user: int, text: str, **kwargs: Any) -> None:
        sent_messages.append((user, text, kwargs))

    recorded_calls: List[Dict[str, Any]] = []

    real_update = main.update_account_settings

    async def tracking_update(account_id_arg: int, **kwargs: Any) -> None:
        recorded_calls.append({"account_id": account_id_arg, **kwargs})
        await real_update(account_id_arg, **kwargs)

    monkeypatch.setattr(main, "check_account", fake_check_account)
    monkeypatch.setattr(main, "Client", DummyClient)
    monkeypatch.setattr(main, "format_channels_display", fake_format)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "update_account_settings", tracking_update)

    try:
        await main.add_sleeps(message, state)

        updated_account = await db.get_account_by_id(account["id"])
        assert updated_account is not None
        assert recorded_calls == [], (recorded_calls, format_calls, sent_messages)
        assert format_calls == [], format_calls
        assert sent_messages, "Ожидалось, что пользователю будет отправлено сообщение о настройке реакций"
        assert "шанс реакции" in sent_messages[-1][1].lower()
    finally:
        await db.close_db()
