import asyncio
import importlib
import os
import sys
import types
from datetime import datetime, timedelta, timezone


def _ensure_stubbed_dependencies():
    os.environ.setdefault("API_ID", "1")
    os.environ.setdefault("API_HASH", "dummy")
    os.environ.setdefault("BOT_TOKEN", "dummy")

    if "pyrogram" not in sys.modules:
        pyrogram = types.ModuleType("pyrogram")

        class DummyClient:  # pragma: no cover - helpers for import only
            def __init__(self, *args, **kwargs):
                pass

        pyrogram.Client = DummyClient
        pyrogram.filters = types.SimpleNamespace()
        sys.modules["pyrogram"] = pyrogram

        errors = types.ModuleType("pyrogram.errors")

        class DummyUserAlreadyParticipant(Exception):
            pass

        errors.UserAlreadyParticipant = DummyUserAlreadyParticipant
        sys.modules["pyrogram.errors"] = errors

    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")

        def _noop(*args, **kwargs):
            return None

        dotenv.load_dotenv = _noop
        sys.modules["dotenv"] = dotenv

    if "aiogram" not in sys.modules:
        aiogram = types.ModuleType("aiogram")

        class DummyBot:  # pragma: no cover - helpers for import only
            def __init__(self, *args, **kwargs):
                pass

            async def send_message(self, *args, **kwargs):
                return None

        class DummyDispatcher:  # pragma: no cover - helpers for import only
            def __init__(self, *args, **kwargs):
                pass

            def __getattr__(self, _name):
                def decorator(*_args, **_kwargs):
                    def wrapper(func):
                        return func

                    return wrapper

                return decorator

        aiogram.Bot = DummyBot
        aiogram.Dispatcher = DummyDispatcher

        types_module = types.ModuleType("aiogram.types")

        class DummyMessage:  # pragma: no cover - helpers for import only
            pass

        types_module.Message = DummyMessage
        types_module.CallbackQuery = type("CallbackQuery", (), {})

        def _auto_dummy(attr_name):
            dummy_type = type(attr_name, (), {})
            setattr(types_module, attr_name, dummy_type)
            return dummy_type

        types_module.__getattr__ = staticmethod(_auto_dummy)
        sys.modules["aiogram.types"] = types_module
        aiogram.types = types_module

        filters_module = types.ModuleType("aiogram.filters")

        class DummyCommand:  # pragma: no cover - helpers for import only
            def __init__(self, *args, **kwargs):
                pass

        class DummyCommandStart(DummyCommand):
            pass

        filters_module.Command = DummyCommand
        filters_module.CommandStart = DummyCommandStart
        sys.modules["aiogram.filters"] = filters_module

        fsm_module = types.ModuleType("aiogram.fsm")
        storage_module = types.ModuleType("aiogram.fsm.storage")
        memory_module = types.ModuleType("aiogram.fsm.storage.memory")

        class DummyMemoryStorage:  # pragma: no cover - helpers for import only
            def __init__(self, *args, **kwargs):
                pass

        memory_module.MemoryStorage = DummyMemoryStorage
        storage_module.memory = memory_module
        fsm_module.storage = storage_module
        sys.modules["aiogram.fsm"] = fsm_module
        sys.modules["aiogram.fsm.storage"] = storage_module
        sys.modules["aiogram.fsm.storage.memory"] = memory_module

        context_module = types.ModuleType("aiogram.fsm.context")

        class DummyFSMContext:  # pragma: no cover - helpers for import only
            async def get_data(self):
                return {}

            async def update_data(self, *args, **kwargs):
                return None

            async def set_state(self, *args, **kwargs):
                return None

            async def clear(self):
                return None

        context_module.FSMContext = DummyFSMContext
        sys.modules["aiogram.fsm.context"] = context_module

        state_module = types.ModuleType("aiogram.fsm.state")

        class DummyState:  # pragma: no cover - helpers for import only
            pass

        class DummyStatesGroup:  # pragma: no cover - helpers for import only
            pass

        state_module.State = DummyState
        state_module.StatesGroup = DummyStatesGroup
        sys.modules["aiogram.fsm.state"] = state_module

        utils_module = types.ModuleType("aiogram.utils")
        keyboard_module = types.ModuleType("aiogram.utils.keyboard")

        class DummyInlineKeyboardBuilder:  # pragma: no cover - helpers for import only
            def __init__(self, *args, **kwargs):
                pass

            def row(self, *args, **kwargs):
                return None

            def as_markup(self):
                return None

        keyboard_module.InlineKeyboardBuilder = DummyInlineKeyboardBuilder
        utils_module.keyboard = keyboard_module
        sys.modules["aiogram.utils"] = utils_module
        sys.modules["aiogram.utils.keyboard"] = keyboard_module

        aiogram.filters = filters_module
        aiogram.fsm = fsm_module
        aiogram.utils = utils_module
        sys.modules["aiogram"] = aiogram


def _load_main_module():
    _ensure_stubbed_dependencies()
    if "main" in sys.modules:
        return sys.modules["main"]
    original_run = asyncio.run

    def _dummy_run(coro, *args, **kwargs):  # type: ignore[override]
        try:
            coro.close()
        except Exception:
            pass
        return None

    asyncio.run = _dummy_run  # type: ignore[assignment]
    try:
        return importlib.import_module("main")
    finally:
        asyncio.run = original_run


def _make_settings(main_module):
    return main_module.WarmupSettingsData(
        channels_per_day=3,
        delay_minutes=10,
        join_start_hour=12,
        join_start_minute=0,
        join_end_hour=19,
        join_end_minute=0,
    )


def test_plan_next_after_daily_limit(monkeypatch):
    main_module = _load_main_module()
    settings = _make_settings(main_module)

    monkeypatch.setattr(main_module, "_get_human_delay_seconds", lambda *args, **kwargs: 300)

    now = datetime(2024, 1, 1, 18, 0, tzinfo=timezone.utc)
    next_window_start = main_module._next_join_window_start(now, settings)
    planned = main_module.plan_next_warmup_join(next_window_start, settings)

    assert planned >= next_window_start
    window_end = datetime.combine(
        next_window_start.date(),
        settings.window_end,
        tzinfo=timezone.utc,
    )
    assert planned < window_end
    assert planned.date() == next_window_start.date()


def test_plan_next_near_window_end(monkeypatch):
    main_module = _load_main_module()
    settings = _make_settings(main_module)

    monkeypatch.setattr(main_module, "_get_human_delay_seconds", lambda *args, **kwargs: 900)

    window_end_today = datetime(2024, 1, 1, settings.join_end_hour, settings.join_end_minute, tzinfo=timezone.utc)
    earliest = window_end_today - timedelta(minutes=2)
    planned = main_module.plan_next_warmup_join(earliest, settings)

    next_window_start = datetime(2024, 1, 2, settings.join_start_hour, settings.join_start_minute, tzinfo=timezone.utc)
    assert planned >= next_window_start
    next_window_end = datetime(2024, 1, 2, settings.join_end_hour, settings.join_end_minute, tzinfo=timezone.utc)
    assert planned < next_window_end
