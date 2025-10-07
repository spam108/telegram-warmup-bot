"""Автотесты для проверки сохранения настроек с учётом переменных окружения."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_settings_save(postgres_db_url, monkeypatch):
    """Убеждаемся, что настройки прогрева можно сохранить и обновить."""

    assert postgres_db_url
    monkeypatch.setenv("BOT_TOKEN", "8231470375:TESTTOKEN")
    monkeypatch.setenv("API_ID", "20047744")
    monkeypatch.setenv("API_HASH", "09c81e1d266b98a8d82291abaa75bba7")
    monkeypatch.setenv("PASSWORD", "853211")

    import db

    db = importlib.reload(db)

    await db.close_db()
    await db.init_db()
    await db.ensure_warmup_settings(
        channels_per_day=15,
        delay_minutes=7,
        join_start_hour=10,
        join_start_minute=0,
        join_end_hour=18,
        join_end_minute=0,
    )

    before = await db.get_warmup_settings()
    assert before["channels_per_day"] == 15
    assert before["delay_minutes"] == 7
    assert before["join_start_hour"] == 10
    assert before["join_end_hour"] == 18

    await db.update_warmup_settings(channels_per_day=25, delay_minutes=11)
    after = await db.get_warmup_settings()
    assert after["channels_per_day"] == 25
    assert after["delay_minutes"] == 11

    await db.close_db()
