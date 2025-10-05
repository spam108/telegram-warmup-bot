"""Автотесты для проверки сохранения настроек аккаунта."""
from __future__ import annotations

import importlib
import os
import types

import pytest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

import main


class DummyState:
    def __init__(self, data: dict[str, object]):
        self._data = data
        self.cleared = False
        self.set_states: list[object] = []

    async def get_data(self) -> dict[str, object]:
        return self._data

    async def update_data(self, update: dict[str, object]) -> None:
        self._data.update(update)

    async def set_state(self, state: object) -> None:
        self.set_states.append(state)

    async def clear(self) -> None:
        self.cleared = True


class DummyMessage:
    def __init__(self, text: str, user_id: int):
        self.text = text
        self.from_user = types.SimpleNamespace(id=user_id)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_settings_save(tmp_path, monkeypatch):
    """Проверяет, что изменения настроек аккаунта сохраняются в базе данных."""

    db_path = tmp_path / "settings.db"
    sessions_dir = tmp_path / "sessions" / "1"
    sessions_dir.mkdir(parents=True)

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Перезагружаем модуль БД, чтобы применились новые переменные окружения.
    import db

    db = importlib.reload(db)

    await db.init_db()
    await db.ensure_user(1)
    account = await db.ensure_account(1, "79990000000", str(sessions_dir / "79990000000.session"))

    await db.update_account_settings(
        account_id=account["id"],
        chance=30,
        system_prompt="Тестовый промпт для проверки",
        sleep_min=15,
        sleep_max=25,
        reaction_chance=70,
        reaction_discussion_chance=55,
        reaction_sleep_min=3,
        reaction_sleep_max=6,
        reaction_emojis=["🔥", "👍"],
    )

    stored = await db.get_account_by_id(account["id"])
    assert stored["chance"] == 30
    assert stored["system_prompt"] == "Тестовый промпт для проверки"
    assert stored["sleep_min"] == 15
    assert stored["sleep_max"] == 25
    assert stored["reaction_chance"] == 70
    assert stored["reaction_discussion_chance"] == 55
    assert stored["reaction_sleep_min"] == 3
    assert stored["reaction_sleep_max"] == 6
    assert stored["reaction_emojis"] == ["🔥", "👍"]

    await db.close_db()


@pytest.mark.anyio("asyncio")
async def test_reaction_settings_flow_saves_values(monkeypatch):
    user_id = 42
    state_data = {"account": "79990000000", "account_id": 123}
    state = DummyState(state_data)
    account_info = {
        "reaction_chance": None,
        "reaction_discussion_chance": None,
        "reaction_sleep_min": None,
        "reaction_sleep_max": None,
        "reaction_emojis": None,
    }

    captured_update: dict[str, object] = {}
    captured_bulk: list[tuple[int, dict[str, object]]] = []
    sent_messages: list[tuple[int, str]] = []
    main_message_called = False

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_update_account_settings(account_id: int, **kwargs):  # noqa: ANN001
        captured_update["account_id"] = account_id
        captured_update["kwargs"] = kwargs

    async def fake_bulk_update(user: int, **kwargs):  # noqa: ANN001
        captured_bulk.append((user, kwargs))

    async def fake_send_message(chat_id: int, text: str, **kwargs):  # noqa: ANN001
        sent_messages.append((chat_id, text))
        return types.SimpleNamespace(message_id=1)

    async def fake_main_message(message):  # noqa: ANN001
        nonlocal main_message_called
        main_message_called = True

    monkeypatch.setattr(main, "_load_account_data", fake_load_account_data)
    monkeypatch.setattr(main, "update_account_settings", fake_update_account_settings)
    monkeypatch.setattr(main, "bulk_update_reaction_settings", fake_bulk_update)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    await main.add_reaction_limit(DummyMessage("10", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.chance

    await main.add_reaction_chance(DummyMessage("70/50", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.sleeps

    await main.add_reaction_sleeps(DummyMessage("2-4", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.emojis

    await main.add_reaction_emojis(DummyMessage("🔥 👍", user_id), state)

    assert captured_update, "update_account_settings должна вызываться"
    assert captured_update["account_id"] == 123
    kwargs = captured_update["kwargs"]
    assert kwargs["reaction_limit_per_message"] == 10
    assert kwargs["reaction_chance"] == 70
    assert kwargs["reaction_discussion_chance"] == 50
    assert kwargs["reaction_sleep_min"] == 2
    assert kwargs["reaction_sleep_max"] == 4
    assert kwargs["reaction_emojis"] == ["🔥", "👍"]
    assert not captured_bulk, "Применение ко всем аккаунтам не должно вызываться без кнопки"
    assert state.cleared is True
    assert main_message_called is True
    assert sent_messages, "Пользователь должен получить подтверждение сохранения"


@pytest.mark.anyio("asyncio")
async def test_reaction_settings_flow_with_defaults(monkeypatch):
    user_id = 43
    state_data = {"account": "79995550000", "account_id": 321}
    state = DummyState(state_data)
    account_info = {
        "reaction_chance": None,
        "reaction_discussion_chance": None,
        "reaction_sleep_min": None,
        "reaction_sleep_max": None,
        "reaction_emojis": None,
    }

    captured_update: dict[str, object] = {}

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_update_account_settings(account_id: int, **kwargs):  # noqa: ANN001
        captured_update["account_id"] = account_id
        captured_update["kwargs"] = kwargs

    async def fake_send_message(chat_id: int, text: str, **kwargs):  # noqa: ANN001
        return types.SimpleNamespace(message_id=1)

    async def fake_main_message(message):  # noqa: ANN001
        return None

    monkeypatch.setattr(main, "_load_account_data", fake_load_account_data)
    monkeypatch.setattr(main, "update_account_settings", fake_update_account_settings)
    monkeypatch.setattr(main, "bulk_update_reaction_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "main_message", fake_main_message)

    await main.add_reaction_limit(DummyMessage("-", user_id), state)
    await main.add_reaction_chance(DummyMessage("-", user_id), state)
    await main.add_reaction_sleeps(DummyMessage("-", user_id), state)
    await main.add_reaction_emojis(DummyMessage("-", user_id), state)

    assert captured_update["account_id"] == 321
    kwargs = captured_update["kwargs"]
    assert "reaction_limit_per_message" not in kwargs
    assert kwargs["reaction_chance"] is None
    assert kwargs["reaction_discussion_chance"] is None
    assert kwargs["reaction_sleep_min"] is None
    assert kwargs["reaction_sleep_max"] is None
    assert kwargs["reaction_emojis"] is None
