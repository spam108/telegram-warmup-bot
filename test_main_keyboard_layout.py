import os
import types

import pytest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

import main  # noqa: E402  pylint: disable=wrong-import-position


class DummyMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = types.SimpleNamespace(id=user_id)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_main_menu_keyboard_layout_no_accounts(monkeypatch):
    user_id = 777
    existing_dirs: set[str] = set()
    existing_files: set[str] = set()

    def _norm(path: str) -> str:
        return os.path.normpath(path)

    def fake_isdir(path: str) -> bool:
        return _norm(path) in existing_dirs

    def fake_makedirs(path: str, exist_ok: bool = False) -> None:  # noqa: ARG001
        norm_path = _norm(path)
        if norm_path in ("", "."):
            return
        parts = norm_path.split(os.sep)
        cumulative = []
        for part in parts:
            cumulative.append(part)
            existing_dirs.add(_norm(os.sep.join(cumulative)))

    def fake_listdir(path: str) -> list[str]:  # noqa: ARG001
        return []

    def fake_exists(path: str) -> bool:
        return _norm(path) in existing_files

    async def fake_ensure_user(uid: int) -> None:  # noqa: ARG001
        return None

    async def fake_get_accounts(uid: int):  # noqa: ARG001
        return []

    async def fake_ensure_account(user_id: int, phone: str, session_path: str):  # noqa: ARG001
        return None

    sent: dict[str, object] = {}

    async def fake_send_message(chat_id: int, text: str, reply_markup=None, **kwargs):  # noqa: ANN001
        sent.update({
            "chat_id": chat_id,
            "text": text,
            "markup": reply_markup,
            "kwargs": kwargs,
        })
        return types.SimpleNamespace(message_id=1)

    monkeypatch.setattr(main.os.path, "isdir", fake_isdir)
    monkeypatch.setattr(main.os, "makedirs", fake_makedirs)
    monkeypatch.setattr(main.os, "listdir", fake_listdir)
    monkeypatch.setattr(main.os.path, "exists", fake_exists)
    monkeypatch.setattr(main, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(main, "get_accounts_for_user", fake_get_accounts)
    monkeypatch.setattr(main, "ensure_account", fake_ensure_account)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)

    await main.main_message(DummyMessage(user_id))

    markup = sent.get("markup")
    assert markup is not None, "Ожидается, что главное меню отправляется пользователю"

    rows = markup.inline_keyboard
    assert len(rows) == 2, "Без аккаунтов должно быть две строки действий"

    add_row = rows[0]
    assert [button.callback_data for button in add_row] == ["add_account", "add_warmup"], (
        "Первый ряд должен содержать кнопки добавления аккаунта и прогрева"
    )

    bottom_row = rows[1]
    assert [button.callback_data for button in bottom_row] == ["global_stats", "warmup_settings"], (
        "Нижний ряд должен содержать сервисные кнопки статистики и настроек"
    )
    assert bottom_row[0].text == "📊 Общая статистика"
    assert bottom_row[1].text == "⚙️ Настройки прогрева"


@pytest.mark.anyio
async def test_main_menu_keyboard_layout_with_account_shows_reaction_button(monkeypatch):
    user_id = 123
    session_file = os.path.normpath(f"sessions/{user_id}/79990000000.session")
    existing_dirs: set[str] = set()
    existing_files: set[str] = {session_file}

    def _norm(path: str) -> str:
        return os.path.normpath(path)

    def fake_isdir(path: str) -> bool:
        return _norm(path) in existing_dirs

    def fake_makedirs(path: str, exist_ok: bool = False) -> None:  # noqa: ARG001
        norm_path = _norm(path)
        if norm_path in ("", "."):
            return
        parts = norm_path.split(os.sep)
        cumulative = []
        for part in parts:
            cumulative.append(part)
            existing_dirs.add(_norm(os.sep.join(cumulative)))

    def fake_listdir(path: str) -> list[str]:  # noqa: ARG001
        norm_path = _norm(path)
        user_dir = _norm(f"sessions/{user_id}")
        if norm_path == user_dir:
            return ["79990000000.session"]
        return []

    def fake_exists(path: str) -> bool:
        return _norm(path) in existing_files

    async def fake_ensure_user(uid: int) -> None:  # noqa: ARG001
        return None

    async def fake_get_accounts(uid: int):  # noqa: ARG001
        return [{"phone": "79990000000", "status": "stopped"}]

    async def fake_ensure_account(user_id: int, phone: str, session_path: str):  # noqa: ARG001
        return None

    sent: dict[str, object] = {}

    async def fake_send_message(chat_id: int, text: str, reply_markup=None, **kwargs):  # noqa: ANN001
        sent.update(
            {
                "chat_id": chat_id,
                "text": text,
                "markup": reply_markup,
                "kwargs": kwargs,
            }
        )
        return types.SimpleNamespace(message_id=1)

    monkeypatch.setattr(main.os.path, "isdir", fake_isdir)
    monkeypatch.setattr(main.os, "makedirs", fake_makedirs)
    monkeypatch.setattr(main.os, "listdir", fake_listdir)
    monkeypatch.setattr(main.os.path, "exists", fake_exists)
    monkeypatch.setattr(main, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(main, "get_accounts_for_user", fake_get_accounts)
    monkeypatch.setattr(main, "ensure_account", fake_ensure_account)
    monkeypatch.setattr(main.bot, "send_message", fake_send_message)
    main.active_sessions.clear()

    await main.main_message(DummyMessage(user_id))

    markup = sent.get("markup")
    assert markup is not None, "Главное меню должно содержать клавиатуру"

    reaction_buttons = [
        button
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data == "reaction_79990000000"
    ]
    assert reaction_buttons, "Для аккаунта должна появиться кнопка настроек реакций"
    assert reaction_buttons[0].text == "🎯 Реакции на посты и ответы"
