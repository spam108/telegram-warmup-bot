"""Автотесты для проверки сохранения настроек аккаунта."""
from __future__ import annotations

import importlib

import pytest


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
    )

    stored = await db.get_account_by_id(account["id"])
    assert stored["chance"] == 30
    assert stored["system_prompt"] == "Тестовый промпт для проверки"
    assert stored["sleep_min"] == 15
    assert stored["sleep_max"] == 25

    await db.close_db()
