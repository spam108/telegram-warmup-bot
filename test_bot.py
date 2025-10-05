#!/usr/bin/env python3
import importlib
import os
import sys

import pytest
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test environment variables
bot_token = os.getenv("BOT_TOKEN")
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
password = os.getenv("PASSWORD")

# Write to file instead of stdout
with open("bot_test_output.txt", "w") as f:
    f.write(f"BOT_TOKEN: {bot_token}\n")
    f.write(f"API_ID: {api_id}\n")
    f.write(f"API_HASH: {api_hash}\n")
    f.write(f"PASSWORD: {password}\n")
    
    if bot_token and api_id and api_hash and password:
        f.write("All environment variables loaded successfully!\n")
    else:
        f.write("Some environment variables are missing!\n")

print("Test completed - check bot_test_output.txt")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_global_statistics_and_report(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")

    import db as db_module

    db_module = importlib.reload(db_module)
    await db_module.init_db()

    await db_module.ensure_user(1)
    account1 = await db_module.ensure_account(1, "+100", "sessions/1/+100.session")
    account2 = await db_module.ensure_account(1, "+101", "sessions/1/+101.session")
    account3 = await db_module.ensure_account(1, "+102", "sessions/1/+102.session")

    await db_module.mark_account_running(account1["id"])
    await db_module.mark_account_running(account2["id"])
    await db_module.set_account_mode(account2["id"], "standard", warmup_days=None)

    await db_module.add_comment_log(account1["id"], channel="chat", message_id=1, status="success")
    await db_module.add_comment_log(account1["id"], channel="chat", message_id=2, status="error", error="boom")
    await db_module.add_comment_log(account2["id"], channel="chat", message_id=3, status="skipped", error="rnd")
    await db_module.add_comment_log(account2["id"], channel="chat", message_id=4, status="no_comments", error="forbidden")
    await db_module.add_comment_log(account2["id"], channel="chat", message_id=5, status="reaction_success")
    await db_module.add_comment_log(account2["id"], channel="chat", message_id=6, status="reaction_error")
    await db_module.add_comment_log(account3["id"], channel="chat", message_id=7, status="reaction_skipped")

    await db_module.sync_warmup_channels(account1["id"], ["@one", "@two"])
    await db_module.mark_warmup_channel_joined(account1["id"], "@one")
    await db_module.record_warmup_channel_error(account1["id"], "@two", "denied")

    stats = await db_module.get_global_statistics()

    assert stats["accounts"]["total"] == 3
    assert stats["accounts"]["by_status"].get("running") == 2
    assert stats["accounts"]["by_status"].get("stopped") == 1
    assert stats["accounts"]["by_mode"].get("warmup") == 2
    assert stats["accounts"]["by_mode"].get("standard") == 1
    assert stats["accounts"]["running_by_mode"].get("warmup") == 1
    assert stats["accounts"]["running_by_mode"].get("standard") == 1

    assert stats["comments"]["total"] == 7
    assert stats["comments"]["by_status"].get("success") == 1
    assert stats["comments"]["by_status"].get("error") == 1
    assert stats["comments"]["by_status"].get("skipped") == 1
    assert stats["comments"]["by_status"].get("no_comments") == 1
    assert stats["comments"]["reactions_total"] == 3
    assert stats["comments"]["reactions"].get("success") == 1
    assert stats["comments"]["reactions"].get("error") == 1
    assert stats["comments"]["reactions"].get("skipped") == 1

    assert stats["warmup"]["by_status"].get("joined") == 1
    assert stats["warmup"]["by_status"].get("error") == 1
    assert stats["warmup"]["by_status"].get("pending", 0) == 0
    assert stats["warmup"]["total_attempts"] == 1

    import main as main_module

    main_module = importlib.reload(main_module)
    report = main_module.format_global_statistics_report(stats)

    assert "📊" in report
    assert "Аккаунты" in report
    assert "Всего: 3" in report
    assert "Активны: 2" in report
    assert "Успешные: 1" in report
    assert "Нет ветки/запрещено: 1" in report
    assert "Прогрев" in report
    assert "Попыток вступления: 1" in report
    assert "😊 *Реакции*" in report
    assert "😊 *Реакции*\n• Всего: 3" in report

    await db_module.close_db()


def test_format_global_statistics_report_escapes_markdown_special_chars(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")

    import importlib
    import main as main_module

    main_module = importlib.reload(main_module)

    stats = {
        "accounts": {
            "total": 1,
            "by_status": {
                "running_status": 1,
                "custom_status": 2,
            },
            "by_mode": {
                "mode_with_underscores": 3,
            },
            "running_by_mode": {
                "mode_with_underscores": 2,
            },
        },
        "comments": {
            "total": 0,
            "by_status": {
                "success": 0,
                "error": 0,
                "skipped": 0,
                "no_comments": 0,
                "custom_comment_status": 1,
            },
            "reactions_total": 0,
            "reactions": {
                "success": 0,
                "error": 0,
                "skipped": 0,
                "custom_reaction_status": 1,
            },
        },
        "warmup": {
            "by_status": {
                "joined": 0,
                "pending": 0,
                "error": 0,
                "custom_warmup_status": 1,
            },
            "total_attempts": 0,
        },
    }

    report = main_module.format_global_statistics_report(stats)

    import re

    assert not re.search(r"(?<!\\)_", report)
