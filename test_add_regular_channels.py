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
        self.next_state = None

    async def get_data(self) -> Dict[str, Any]:
        return self._data

    async def clear(self) -> None:
        self.cleared = True

    async def set_state(self, state: Any) -> None:
        self.next_state = state

    async def update_data(self, data: Dict[str, Any]) -> None:
        self._data.update(data)


class DummyMessage:
    def __init__(self, text: str, user_id: int):
        self.text = text
        self.from_user = types.SimpleNamespace(id=user_id)


def test_add_regular_channels_success_and_failure(monkeypatch):
    state_data = {
        "account": "test_session",
        "account_id": 123,
        "chance": 25,
        "systempromt": "Test prompt",
        "sleeps": "5-10",
        "reaction_chance": 60,
        "reaction_sleeps": "3-7",
        "reaction_emojis": ["🔥", "👍"],
    }
    state = DummyState(state_data)
    message = DummyMessage("@good\n@bad", user_id=42)

    recorded_messages = []

    class DummyBot:
        async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
            recorded_messages.append((chat_id, text))

    async def fake_join_channel(channel, account_id, session_key, user_id, is_warmup=False):
        if channel == "@good":
            return True, None
        return False, "Ошибка доступа"

    captured_update = {}

    async def fake_update_account_settings(account_id: int, **kwargs: Any) -> None:
        captured_update["account_id"] = account_id
        captured_update["kwargs"] = kwargs

    async def fake_load_account_data(dummy_state):
        return state_data, state_data["account_id"], {"channels": ["@existing"]}

    monkeypatch.setattr(main, "bot", DummyBot())
    monkeypatch.setattr(main, "log_channel", 999)
    monkeypatch.setattr(main, "join_channel", fake_join_channel)
    monkeypatch.setattr(main, "update_account_settings", fake_update_account_settings)
    monkeypatch.setattr(main, "startaccount", types.SimpleNamespace(warmup_channels="warmup_state"))
    monkeypatch.setattr(main, "_load_account_data", fake_load_account_data)

    asyncio.run(main.add_regular_channels(message, state))

    assert captured_update["account_id"] == 123
    assert captured_update["kwargs"]["channels"] == ["@existing", "@good"]
    assert captured_update["kwargs"]["chance"] == 25
    assert captured_update["kwargs"]["system_prompt"] == "Test prompt"
    assert captured_update["kwargs"]["sleep_min"] == 5
    assert captured_update["kwargs"]["sleep_max"] == 10
    assert captured_update["kwargs"]["reaction_chance"] == 60
    assert captured_update["kwargs"]["reaction_sleep_min"] == 3
    assert captured_update["kwargs"]["reaction_sleep_max"] == 7
    assert captured_update["kwargs"]["reaction_emojis"] == ["🔥", "👍"]

    failure_notifications = [msg for msg in recorded_messages if "Не удалось вступить" in msg[1]]
    assert failure_notifications, "Пользователь должен получать уведомление об ошибке"

    summaries = [msg for msg in recorded_messages if "Итоговый список успешно добавленных каналов" in msg[1]]
    assert summaries, "Пользователь должен получать итоговый список каналов"

    assert state.next_state == "warmup_state"
