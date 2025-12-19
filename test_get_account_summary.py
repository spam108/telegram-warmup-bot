import asyncio
import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

import main


def test_get_account_summary_handles_markdown_special_chars(monkeypatch):
    account_data = {
        "phone": "+123_456*789",
        "sleep_min": 5,
        "sleep_max": 15,
        "chance": 42,
        "mode": "test_mode_with_*_char",
        "status": "active_status_with_underscore",
        "system_prompt": "Prompt with _star*",
        "warmup_end_at": "2024-01-02 10:00_utc*",
        "warmup_joined_today": 3,
        "warmup_next_join_at": "2024-01-02 12:00",
        "last_started_at": "2024-01-01 08:00",
        "last_stopped_at": "2024-01-01 22:00",
        "updated_at": "2024-01-01 23:59",
        "channels": ["@db_channel_one", "@db*channel"],
        "session_path": "",
    }

    warmup_channels = [
        {"channel": "@warmup_channel*"},
        {"channel": "@warmup_channel_two"},
    ]

    async def fake_get_account_by_id(account_id):
        return account_data

    async def fake_get_warmup_pending(account_id, limit=100):
        return warmup_channels

    monkeypatch.setattr(main, "get_account_by_id", fake_get_account_by_id)
    monkeypatch.setattr(main, "get_warmup_pending", fake_get_warmup_pending)

    summary = asyncio.run(main.get_account_summary(1))
    assert summary is not None

    assert "+123\\_456\\*789" in summary
    assert "@db\\_channel\\_one" in summary
    assert "@db\\*channel" in summary
    assert "@warmup\\_channel\\*" in summary
    assert "active\\_status\\_with\\_underscore" in summary
    assert "2024-01-02 10:00\\_utc\\*" in summary
    assert "Prompt with \\_star\\*" in summary
    assert "Prompt with \\\\_star" not in summary


def test_format_channels_display_plain_text_has_no_extra_escaping():
    channels = ["@plain_channel", "@channel_with_underscore_"]

    result = asyncio.run(
        main.format_channels_display(
            channels,
            "Список каналов",
            10,
            use_markdown=False,
        )
    )

    assert "\\_" not in result
    assert "@channel_with_underscore_" in result
