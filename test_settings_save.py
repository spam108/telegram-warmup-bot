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

    captured_updates: list[tuple[int, dict[str, object]]] = []
    captured_bulk: list[tuple[int, dict[str, object]]] = []
    sent_messages: list[tuple[int, str]] = []
    main_message_called = False

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_update_account_settings(account_id: int, **kwargs):  # noqa: ANN001
        captured_updates.append((account_id, kwargs))

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
    assert state.set_states[-1] == main.reactionsettings.post_chance

    await main.add_post_reaction_chance(DummyMessage("70", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.discussion_reaction_chance

    await main.add_discussion_reaction_chance(DummyMessage("50", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.discussion_reply_chance

    await main.add_discussion_reply_chance(DummyMessage("100", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.discussion_prompt

    await main.add_discussion_reply_prompt(DummyMessage("Промт", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.sleeps

    await main.add_reaction_sleeps(DummyMessage("2-4", user_id), state)
    assert state.set_states[-1] == main.reactionsettings.emojis

    await main.add_reaction_emojis(DummyMessage("🔥 👍", user_id), state)

    assert state.set_states[-1] == main.reactionsettings.apply_all

    assert captured_updates, "update_account_settings должна вызываться"
    account_id, kwargs = captured_updates[-1]
    assert account_id == 123
    assert kwargs["reaction_limit_per_message"] == 10
    assert kwargs["reaction_chance"] == 70
    assert kwargs["reaction_discussion_chance"] == 50
    assert kwargs["discussion_reply_chance"] == 100
    assert kwargs["discussion_reply_prompt"] == "Промт"
    assert kwargs["reaction_sleep_min"] == 2
    assert kwargs["reaction_sleep_max"] == 4
    assert kwargs["reaction_emojis"] == ["🔥", "👍"]
    assert not captured_bulk, "Применение ко всем аккаунтам не должно вызываться без кнопки"
    assert state.cleared is False
    assert main_message_called is False
    assert sent_messages, "Пользователь должен получить подтверждение сохранения"
    assert any("Настройки реакций сохранены." in text for _, text in sent_messages)
    assert any("Нажмите кнопку" in text for _, text in sent_messages)

    await main.finish_reaction_settings(DummyMessage("-", user_id), state)

    assert len(captured_updates) == 1

    assert state.cleared is True
    assert main_message_called is True


@pytest.mark.anyio("asyncio")
async def test_reaction_settings_apply_all_triggers_bulk(monkeypatch):
    user_id = 55
    state_data = {"account": "78889990000", "account_id": 222}
    state = DummyState(state_data)
    account_info = {
        "reaction_chance": None,
        "reaction_discussion_chance": None,
        "reaction_sleep_min": None,
        "reaction_sleep_max": None,
        "reaction_emojis": None,
    }

    captured_updates: list[tuple[int, dict[str, object]]] = []
    captured_bulk: list[tuple[int, dict[str, object]]] = []
    sent_messages: list[tuple[int, str]] = []
    main_message_called = False

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_update_account_settings(account_id: int, **kwargs):  # noqa: ANN001
        captured_updates.append((account_id, kwargs))

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

    await main.add_reaction_limit(DummyMessage("5", user_id), state)
    await main.add_post_reaction_chance(DummyMessage("70", user_id), state)
    await main.add_discussion_reaction_chance(DummyMessage("30", user_id), state)
    await main.add_discussion_reply_chance(DummyMessage("60", user_id), state)
    await main.add_discussion_reply_prompt(DummyMessage("Промт", user_id), state)
    await main.add_reaction_sleeps(DummyMessage("1-2", user_id), state)
    await main.add_reaction_emojis(DummyMessage("🔥", user_id), state)

    assert captured_updates, "Настройки должны сохраняться для текущего аккаунта"
    assert state.set_states[-1] == main.reactionsettings.apply_all
    assert captured_bulk == []

    class DummyCallback:
        def __init__(self) -> None:
            self.data = "reaction_apply_all"
            self.from_user = types.SimpleNamespace(id=user_id)
            self.message = DummyMessage("", user_id)

        async def answer(self, *args, **kwargs):  # noqa: ANN001
            return None

    await main.callbacks(DummyCallback(), state)

    assert len(captured_updates) == 2
    assert captured_bulk and captured_bulk[-1][0] == user_id
    bulk_kwargs = captured_bulk[-1][1]
    assert bulk_kwargs["reaction_emojis"] == ["🔥"]
    assert bulk_kwargs["reaction_sleep_min"] == 1
    assert bulk_kwargs["reaction_sleep_max"] == 2
    assert state.cleared is True
    assert main_message_called is True
    assert any("Настройки реакций применены" in text for _, text in sent_messages)


@pytest.mark.anyio("asyncio")
async def test_reaction_settings_flow_with_defaults(monkeypatch):
    user_id = 43
    state_data = {"account": "79995550000", "account_id": 321}
    state = DummyState(state_data)
    account_info = {
        "reaction_limit_per_message": 5,
        "reaction_chance": 60,
        "reaction_discussion_chance": 40,
        "discussion_reply_chance": 25,
        "discussion_reply_prompt": "Старый промт",
        "reaction_sleep_min": 3,
        "reaction_sleep_max": 7,
        "reaction_emojis": ["🔥", "👍"],
    }

    captured_updates: list[tuple[int, dict[str, object]]] = []

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_update_account_settings(account_id: int, **kwargs):  # noqa: ANN001
        captured_updates.append((account_id, kwargs))

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
    await main.add_post_reaction_chance(DummyMessage("-", user_id), state)
    await main.add_discussion_reaction_chance(DummyMessage("-", user_id), state)
    await main.add_discussion_reply_chance(DummyMessage("-", user_id), state)
    await main.add_discussion_reply_prompt(DummyMessage("-", user_id), state)
    await main.add_reaction_sleeps(DummyMessage("-", user_id), state)
    await main.add_reaction_emojis(DummyMessage("-", user_id), state)
    await main.finish_reaction_settings(DummyMessage("-", user_id), state)

    assert captured_updates, "update_account_settings должна вызываться"
    account_id, kwargs = captured_updates[-1]
    assert account_id == 321
    assert "reaction_limit_per_message" not in kwargs
    assert kwargs["reaction_chance"] == 60
    assert kwargs["reaction_discussion_chance"] == 40
    assert kwargs["discussion_reply_chance"] == 25
    assert kwargs["discussion_reply_prompt"] == "Старый промт"
    assert kwargs["reaction_sleep_min"] == 3
    assert kwargs["reaction_sleep_max"] == 7
    assert kwargs["reaction_emojis"] == ["🔥", "👍"]


@pytest.mark.anyio("asyncio")
async def test_discussion_settings_dash_keeps_previous(monkeypatch):
    user_id = 99
    state_data = {"account": "79000000000", "account_id": 555}
    state = DummyState(state_data)

    account_info = {
        "reaction_discussion_chance": 42,
        "discussion_reply_chance": 17,
        "discussion_reply_prompt": "Старый промт",
    }

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_send_message(chat_id: int, text: str, **kwargs):  # noqa: ANN001
        return types.SimpleNamespace(message_id=1)

    async def fake_prompt_discussion_reply_chance(message, dummy_state):  # noqa: ANN001
        return None

    async def fake_prompt_discussion_reply_prompt(message, dummy_state):  # noqa: ANN001
        return None

    async def fake_prompt_reaction_sleeps(message, dummy_state):  # noqa: ANN001
        return None

    monkeypatch.setattr(main, "_load_account_data", fake_load_account_data)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "_prompt_discussion_reply_chance", fake_prompt_discussion_reply_chance)
    monkeypatch.setattr(main, "_prompt_discussion_reply_prompt", fake_prompt_discussion_reply_prompt)
    monkeypatch.setattr(main, "_prompt_reaction_sleeps", fake_prompt_reaction_sleeps)

    await main.add_discussion_reaction_chance(DummyMessage("-", user_id), state)
    assert state._data["reaction_discussion_chance"] == 42

    await main.add_discussion_reply_chance(DummyMessage("-", user_id), state)
    assert state._data["discussion_reply_chance"] == 17

    await main.add_discussion_reply_prompt(DummyMessage("-", user_id), state)
    assert state._data["discussion_reply_prompt"] == "Старый промт"

    await main.add_discussion_reaction_chance(DummyMessage("off", user_id), state)
    assert state._data["reaction_discussion_chance"] is None

    await main.add_discussion_reply_chance(DummyMessage("none", user_id), state)
    assert state._data["discussion_reply_chance"] is None

    await main.add_discussion_reply_prompt(DummyMessage("off", user_id), state)
    assert state._data["discussion_reply_prompt"] is None


@pytest.mark.anyio("asyncio")
async def test_reaction_emojis_rejects_unsupported(monkeypatch):
    user_id = 77
    state_data = {"account": "79991110000", "account_id": 900}
    state = DummyState(state_data)
    account_info = {"reaction_emojis": None}

    sent_messages: list[tuple[int, str]] = []
    reprompt_called = False
    save_calls = 0

    async def fake_load_account_data(dummy_state):  # noqa: ANN001
        return state_data, state_data["account_id"], account_info

    async def fake_send_message(chat_id: int, text: str, **kwargs):  # noqa: ANN001
        sent_messages.append((chat_id, text))
        return types.SimpleNamespace(message_id=1)

    async def fake_prompt_reaction_emojis(message, dummy_state):  # noqa: ANN001
        nonlocal reprompt_called
        reprompt_called = True

    async def fake_save_reaction_settings(message, dummy_state, *, finalize=True, notify=True):  # noqa: ANN001,ARG001
        nonlocal save_calls
        save_calls += 1

    async def fake_get_available(user: int, session: str | None, chat_id: int | None = None):  # noqa: ANN001
        assert user == user_id
        assert session == state_data["account"]
        assert chat_id is None
        return {"🔥", "👍"}

    monkeypatch.setattr(main, "_load_account_data", fake_load_account_data)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    monkeypatch.setattr(main, "_prompt_reaction_emojis", fake_prompt_reaction_emojis)
    monkeypatch.setattr(main, "_save_reaction_settings", fake_save_reaction_settings)
    monkeypatch.setattr(main, "_get_available_quick_reaction_emojis", fake_get_available)

    await main.add_reaction_emojis(DummyMessage("😀 😎", user_id), state)

    assert save_calls == 0, "Настройки не должны сохраняться при полностью неподдерживаемом вводе"
    assert reprompt_called is True, "Пользователь должен получить повторный запрос"
    assert "reaction_emojis" not in state._data, "Ввод не должен попадать в состояние"
    assert any(
        "Ни один из указанных эмодзи недоступен" in text for _, text in sent_messages
    )
    assert any(
        "Доступные реакции:" in text and "🔥" in text and "👍" in text
        for _, text in sent_messages
    )
