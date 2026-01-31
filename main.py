import json
import os
import asyncio
import logging
import shutil
import sqlite3
import atexit
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Awaitable, Callable, Dict, List, Optional, Set, Any, Union, Tuple, AsyncIterator, IO
from types import SimpleNamespace
import random
import re
from pyrogram import Client, filters
from pyrogram.errors import (
    ChatWriteForbidden,
    MessageIdInvalid,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    ReactionInvalid,
    SessionPasswordNeeded,
    UserAlreadyParticipant,
    UserBannedInChannel,
    Forbidden,
    FloodWait,
)
from sqlite3 import OperationalError
from threading import Lock
from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from comment_engine import generate_comment
from db import (
    add_comment_log,
    add_reaction_log,
    add_to_channel_blacklist,
    bulk_update_reaction_settings,
    count_reactions_for_message,
    db_update_warmup_schedule,
    has_successful_comment_log_entry,
    delete_account,
    ensure_account,
    ensure_user,
    cleanup_comment_logs,
    ensure_warmup_settings,
    get_account_by_id,
    get_account_by_session,
    get_accounts_for_user,
    get_global_statistics,
    get_running_standard_accounts,
    get_running_warmup_accounts,
    get_running_accounts,
    get_warmup_pending,
    get_warmup_settings,
    increment_warmup_joined,
    init_db,
    is_user_authenticated,
    is_channel_blacklisted,
    mark_account_running,
    mark_account_stopped,
    mark_warmup_channel_joined,
    record_warmup_channel_error,
    record_post,
    reset_warmup_daily_state,
    set_account_mode,
    set_user_authenticated,
    sync_warmup_channels,
    update_account_settings,
    update_last_reaction_at,
    update_warmup_settings,
    _require_pool,
    DatabaseNotInitialized,
    get_channel_blacklist,
    get_problematic_channels_report,
    auto_analyze_and_mark_problematic_channels,
    get_channel_blacklist_info,
)


# Logger будет создан после настройки логирования
# logger = logging.getLogger(__name__)  # Перенесено ниже после настройки логирования

REACTION_ENGINE_AVAILABLE = False
reaction_engine_instance = None

try:
    from reaction_engine import ReactionEngine
except ImportError as exc:
    logging.error("❌ Failed to import ReactionEngine: %s", exc)
    ReactionEngine = None
else:
    REACTION_ENGINE_AVAILABLE = True
    logging.info("✅ ReactionEngine imported successfully")

from dotenv import load_dotenv

load_dotenv()


# Глобальный кэш для доступных реакций
_chat_available_reactions_cache: Dict[int, Set[str]] = {}


# Глобальные переменные для реакций
REACTION_MIN_INTERVAL_SECONDS = 10  # ⚡ default fallback value, can be overridden via config


# Вспомогательные функции


def _record_skip_log_event(session: str, event_type: str, reason: str) -> None:
    """Adds skip statistics for delayed aggregated reporting."""

    counters = skip_log_counters[session]
    counters[event_type] += 1
    skip_log_last_reasons[session][event_type] = reason


def enqueue_skip_log(session: str, action: str, reason: str) -> None:
    """Логирование пропущенных действий - УПРОЩЕННАЯ ВЕРСИЯ"""

    logging.info(f"SKIP {action.upper()} for {session}: {reason}")
    _record_skip_log_event(session, action, reason)


async def update_last_reaction_at_with_logging(account_id: int, timestamp: datetime) -> None:
    """Обновление времени последней реакции с логированием"""

    logging.info(f"Last reaction updated for account {account_id} at {timestamp}")
    try:
        await update_last_reaction_at(account_id, timestamp)
    except DatabaseNotInitialized as exc:
        logging.warning(
            "Database not initialised while updating last reaction for %s: %s",
            account_id,
            exc,
        )
    except Exception:
        logging.exception(
            "Unexpected error updating last reaction for account %s", account_id
        )


# Конфигурация
# Загрузка переменных окружения
def load_env_file():
    """Load environment variables from .env file manually"""
    env_vars = {}
    try:
        with open('.env', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    value = value.strip()
                    # Убираем кавычки из начала и конца значения (если есть)
                    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    env_vars[key.strip()] = value
    except FileNotFoundError:
        pass
    return env_vars

# Load environment variables
env_vars = load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = env_vars.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None:
        return default
    return value


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = env_vars.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_env(name: str) -> Optional[int]:
    value = env_vars.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logging.warning("Invalid integer value for %s: %s", name, value)
        return None


BOT_NAME = _get_env("BOT_NAME", "DefaultBot")
SESSIONS_BASE_DIR = _get_env("SESSIONS_DIR", "sessions") or "sessions"
LOGS_BASE_DIR = _get_env("LOGS_DIR", "logs") or "logs"
DATA_BASE_DIR = _get_env("DATA_DIR", "data") or "data"

for directory in (SESSIONS_BASE_DIR, LOGS_BASE_DIR, DATA_BASE_DIR):
    if directory:
        os.makedirs(directory, exist_ok=True)

DB_NAME = _get_env("DB_NAME", "pgbot1010")
DB_USER = _get_env("DB_USER", "postgres")
DB_PASSWORD = _get_env("DB_PASSWORD", "postgres")
# По умолчанию localhost для запуска вне Docker, "postgres" для Docker
DB_HOST = _get_env("DB_HOST", "localhost")
DB_PORT = _get_env("DB_PORT", "5432")

DATABASE_URL = _get_env("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

os.environ.setdefault("DATABASE_URL", DATABASE_URL)

BOT_TOKEN = _get_env("BOT_TOKEN", "8231470375:AAHYlfZSvQyBsYOOQlWwnpKrffAWTNZj0C0")
#APCDXBOT0310 @AP_comment_bot
log_channel = -1003123025616 # cloveend #-1002711973256 #-1002678984799

API_ID = int(_get_env("API_ID", "0"))
API_HASH = _get_env("API_HASH")
#1823

WARMUP_VERBOSE_LOGS = _get_bool_env("WARMUP_VERBOSE_LOGS", default=False)
WARMUP_VERBOSE_NOTIFICATIONS = _get_bool_env("WARMUP_VERBOSE_NOTIFICATIONS", default=False)

DEFAULT_REACTION_LIMIT_PER_MESSAGE = _get_int_env("REACTION_LIMIT_PER_MESSAGE")
REACTION_MIN_INTERVAL_SECONDS = (
    _get_int_env("REACTION_MIN_INTERVAL_SECONDS") or REACTION_MIN_INTERVAL_SECONDS
)


def validate_configuration():
    """Проверяет что все необходимые переменные окружения заданы"""

    required_vars = ["BOT_TOKEN", "API_ID", "API_HASH"]
    missing = [var for var in required_vars if not _get_env(var)]

    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    logging.info(f"Bot instance: {BOT_NAME}")
    logging.info(f"Sessions directory: {SESSIONS_BASE_DIR}")
    logging.info(f"Database: {_get_env('DB_NAME', 'pgbot1010')}")

# Инициализация бота
# Все логи в bot_log.txt (logs_od1/bot_log.txt локально, logs/bot_log.txt в Docker)
log_file_path = os.path.join(LOGS_BASE_DIR, "bot_log.txt")
try:
    # Создаем file handler с режимом append
    file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG if WARMUP_VERBOSE_LOGS else logging.INFO)
    file_handler.setFormatter(logging.Formatter(f"[{BOT_NAME}] %(asctime)s %(levelname)s %(name)s: %(message)s"))
    
    # Создаем stream handler для консоли
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if WARMUP_VERBOSE_LOGS else logging.INFO)
    console_handler.setFormatter(logging.Formatter(f"[{BOT_NAME}] %(asctime)s %(levelname)s %(name)s: %(message)s"))
    
    # Настраиваем root logger
    logging.basicConfig(
        level=logging.DEBUG if WARMUP_VERBOSE_LOGS else logging.INFO,
        format=f"[{BOT_NAME}] %(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_handler, console_handler],
        force=True,  # Перезаписываем существующую конфигурацию
    )
    
    # Настраиваем логирование для всех модулей
    logging.getLogger('pyrogram').setLevel(logging.WARNING)  # Уменьшаем шум от pyrogram
    logging.getLogger('aiogram').setLevel(logging.INFO)
    
    logging.info(f"Logging configured: all logs will be written to {log_file_path}")
except Exception as e:
    # Если не удалось настроить логирование в файл, используем только консоль
    logging.basicConfig(
        level=logging.DEBUG if WARMUP_VERBOSE_LOGS else logging.INFO,
        format=f"[{BOT_NAME}] %(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.error(f"Failed to configure file logging: {e}")

# Создаем logger после настройки логирования
logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("Logging system initialized")
logger.info(f"Log file: {log_file_path}")
logger.info("=" * 60)

validate_configuration()
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logger.info("Starting bot instance: %s", BOT_NAME)



# Состояние для ввода пароля
class AuthState(StatesGroup):
    waiting_for_password = State()

class addsession(StatesGroup):
    number = State()
    code = State()
    password = State()
    code_hash = State()
    client = State()

class startaccount(StatesGroup):
    channels = State()
    account = State()
    system_prompt = State()
    sleeps = State()
    chance = State()
    regular_channels = State()
    warmup_channels = State()


class reactionsettings(StatesGroup):
    limit = State()
    post_chance = State()
    discussion_reaction_chance = State()
    discussion_reply_chance = State()
    discussion_prompt = State()
    sleeps = State()
    emojis = State()
    apply_all = State()


class warmupsettings(StatesGroup):
    limit = State()
    window = State()
    interval = State()


class warmupmanage(StatesGroup):
    channels = State()


class UnsubscribeStates(StatesGroup):
    account = State()
    channel = State()
    reason = State()

active_sessions: Dict[str, bool] = {}  # Глобальный словарь для хранения активных сессий
active_account_ids: Dict[str, int] = {}
quiet_sessions_notified: Set[str] = set()
# Отслеживание последнего времени обработки сообщений для каждого аккаунта (для задержек)
last_message_processed_at: Dict[int, datetime] = {}
_chat_available_reactions_cache: Dict[int, Set[str]] = {}
class _NoOpAsyncContextManager:
    """A lightweight async context manager that does nothing."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> Optional[bool]:
        return None


_NOOP_ASYNC_CONTEXT = _NoOpAsyncContextManager()


class AsyncSessionLock:
    """Wrap a threading.Lock for use with ``async with``."""

    def __init__(self) -> None:
        self._lock = Lock()

    async def __aenter__(self) -> "AsyncSessionLock":
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._lock.acquire)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._lock.release()


session_locks: Dict[str, AsyncSessionLock] = {}
active_pyrogram_clients: Dict[str, Client] = {}
active_client_locks: Dict[str, asyncio.Lock] = {}

PROCESS_LOCK_PATH = os.environ.get(
    "BOT_PROCESS_LOCK_FILE", os.path.join(os.getcwd(), "bot_process.lock")
)
_PROCESS_LOCK_HANDLE: Optional[IO[str]] = None
_PROCESS_LOCK_SIZE = 32


def get_session_lock(session_path: str) -> AsyncSessionLock:
    """Return a shared async-compatible lock for the given session path."""

    lock = session_locks.get(session_path)
    if lock is None:
        lock = AsyncSessionLock()
        session_locks[session_path] = lock
    return lock


def _release_session_lock(key: str) -> None:
    """Remove cached locks for the given session key when no longer needed."""

    session_locks.pop(key, None)


def _acquire_process_lock(lock_path: Optional[str] = None) -> None:
    """Ensure only a single bot process is running at a time."""

    global _PROCESS_LOCK_HANDLE

    if _PROCESS_LOCK_HANDLE is not None:
        return

    target_path = os.path.abspath(lock_path or PROCESS_LOCK_PATH)
    directory = os.path.dirname(target_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    logger.debug("Attempting to acquire process lock at %s", target_path)

    lock_file = open(target_path, "a+")

    def _raise_already_running(exc: BaseException) -> None:
        lock_file.seek(0)
        existing_owner = lock_file.read().strip() or "unknown"
        lock_file.close()
        logger.error(
            "Failed to acquire process lock at %s; existing owner pid=%s",
            target_path,
            existing_owner,
        )
        raise RuntimeError(
            f"Another instance of the bot is already running (PID {existing_owner})."
        ) from exc

    try:
        if os.name == "posix":
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                _raise_already_running(exc)
        elif os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, _PROCESS_LOCK_SIZE)
            except OSError as exc:
                _raise_already_running(exc)
        else:
            lock_file.close()
            raise RuntimeError(
                f"Process locking is not supported on platform: {os.name}"
            )
    except Exception:
        raise

    pid_text = str(os.getpid())
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(pid_text)
    lock_file.flush()

    _PROCESS_LOCK_HANDLE = lock_file
    logger.info("Process lock acquired at %s by PID %s", target_path, pid_text)
    atexit.register(_release_process_lock)


def _release_process_lock() -> None:
    """Release the process lock and clean up the lock file."""

    global _PROCESS_LOCK_HANDLE

    if _PROCESS_LOCK_HANDLE is None:
        return

    lock_file = _PROCESS_LOCK_HANDLE
    lock_path = lock_file.name

    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        elif os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, _PROCESS_LOCK_SIZE)
            except OSError:
                logger.debug(
                    "Ignoring error while unlocking process file %s on Windows",
                    lock_path,
                )
    finally:
        lock_file.close()
        with suppress(FileNotFoundError):
            os.unlink(lock_path)
        _PROCESS_LOCK_HANDLE = None
        logger.info("Process lock released at %s", lock_path)

async def safe_session_operation(
    session_path: str,
    coro_func: Callable[[Client], Awaitable[Any]],
    *,
    lock_key: Optional[str] = None,
    connect_timeout: Optional[float] = None,
    operation_timeout: Optional[float] = None,
    disconnect_timeout: Optional[float] = None,
    operation_name: Optional[str] = None,
    acquire_lock: bool = True,
    start_client: bool = True,
    client_kwargs: Optional[Dict[str, Any]] = None,
):
    """Execute ``coro_func`` with exclusive access to a Pyrogram session."""

    key = lock_key or session_path
    op_name = operation_name or session_path

    loop = asyncio.get_running_loop()
    wait_started_at = loop.time()
    if acquire_lock:
        logging.debug("safe_session_operation[%s]: waiting for lock", op_name)
        lock_context = get_session_lock(key)
    else:
        logging.debug(
            "safe_session_operation[%s]: skipping lock acquisition (already held)",
            op_name,
        )
        lock_context = _NOOP_ASYNC_CONTEXT

    async with lock_context:
        lock_acquired_at = loop.time()
        if acquire_lock:
            logging.debug(
                "safe_session_operation[%s]: lock acquired in %.2fs",
                op_name,
                lock_acquired_at - wait_started_at,
            )
        else:
            logging.debug(
                "safe_session_operation[%s]: lock acquisition skipped in %.2fs",
                op_name,
                lock_acquired_at - wait_started_at,
            )

        ensure_session_file_permissions(f"{session_path}.session")
        permissions_ready_at = loop.time()
        logging.debug(
            "safe_session_operation[%s]: session file prepared in %.2fs",
            op_name,
            permissions_ready_at - lock_acquired_at,
        )

        logging.debug(
            "safe_session_operation[%s]: instantiating Client(api_id=%s, session_path=%s)",
            op_name,
            API_ID,
            session_path,
        )

        client_options: Dict[str, Any] = dict(client_kwargs or {})
        app = Client(
            session_path,
            api_id=API_ID,
            api_hash=API_HASH,
            **client_options,
        )
        client_created_at = loop.time()
        logging.debug(
            "safe_session_operation[%s]: client object created in %.2fs",
            op_name,
            client_created_at - permissions_ready_at,
        )

        entered_context = False
        connect_started_at = loop.time()

        try:
            if start_client:
                try:
                    if connect_timeout is not None:
                        await asyncio.wait_for(
                            app.__aenter__(),
                            timeout=connect_timeout,
                        )
                    else:
                        await app.__aenter__()
                except asyncio.TimeoutError:
                    logging.error(
                        "safe_session_operation[%s]: timeout while connecting after %.2fs",
                        op_name,
                        loop.time() - connect_started_at,
                    )
                    raise
            else:
                try:
                    if connect_timeout is not None:
                        await asyncio.wait_for(app.connect(), timeout=connect_timeout)
                    else:
                        await app.connect()
                except asyncio.TimeoutError:
                    logging.error(
                        "safe_session_operation[%s]: timeout while connecting after %.2fs",
                        op_name,
                        loop.time() - connect_started_at,
                    )
                    raise

            entered_context = True
            connected_at = loop.time()
            logging.debug(
                "safe_session_operation[%s]: client %s in %.2fs",
                op_name,
                "started" if start_client else "connected",
                connected_at - connect_started_at,
            )

            operation_started_at = connected_at
            try:
                if operation_timeout is not None:
                    result = await asyncio.wait_for(
                        coro_func(app),
                        timeout=operation_timeout,
                    )
                else:
                    result = await coro_func(app)
            except asyncio.TimeoutError:
                logging.error(
                    "safe_session_operation[%s]: timeout while running operation after %.2fs",
                    op_name,
                    loop.time() - operation_started_at,
                )
                raise

            finished_at = loop.time()
            logging.debug(
                "safe_session_operation[%s]: coroutine completed in %.2fs",
                op_name,
                finished_at - operation_started_at,
            )

            return result
        finally:
            if entered_context:
                disconnect_started_at = loop.time()
                try:
                    if start_client:
                        if disconnect_timeout is not None:
                            await asyncio.wait_for(
                                app.__aexit__(None, None, None),
                                timeout=disconnect_timeout,
                            )
                        else:
                            await app.__aexit__(None, None, None)
                    else:
                        if disconnect_timeout is not None:
                            await asyncio.wait_for(
                                app.disconnect(),
                                timeout=disconnect_timeout,
                            )
                        else:
                            await app.disconnect()
                except asyncio.TimeoutError:
                    logging.error(
                        "safe_session_operation[%s]: timeout while closing client after %.2fs",
                        op_name,
                        loop.time() - disconnect_started_at,
                    )
                    raise
                except Exception:
                    logging.exception(
                        "safe_session_operation[%s]: unexpected error while closing client",
                        op_name,
                    )
                    raise
                else:
                    disconnected_at = loop.time()
                    logging.debug(
                        "safe_session_operation[%s]: client closed in %.2fs",
                        op_name,
                        disconnected_at - disconnect_started_at,
                    )


async def with_retry(
    session_path: str,
    coro_func: Callable[[Client], Awaitable[Any]],
    *,
    lock_key: Optional[str] = None,
    max_retries: int = 3,
    acquire_lock: bool = True,
    **session_kwargs: Any,
):
    """Run a session-bound coroutine with retry logic for SQLite locks."""

    for attempt in range(max_retries):
        try:
            return await safe_session_operation(
                session_path,
                coro_func,
                lock_key=lock_key,
                acquire_lock=acquire_lock,
                **session_kwargs,
            )
        except OperationalError as exc:
            if "database is locked" in str(exc) and attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise


skip_log_counters: Dict[str, Counter] = defaultdict(Counter)
skip_log_last_reasons: Dict[str, Dict[str, str]] = defaultdict(dict)
skip_log_last_flush_at: Optional[datetime] = None

SKIP_LOG_FLUSH_INTERVAL_SECONDS = 600
SKIP_LOG_EVENT_LABELS = {
    "comment": "Комментарии",
    "reaction": "Реакции",
}

SKIP_SUMMARY_BUTTON_TEXT = "🕒 Сводка пропусков (лог-канал)"


def ensure_session_file_permissions(session_file: str) -> None:
    """Ensure session file is writable with SQLite optimizations"""

    try:
        directory = os.path.dirname(session_file)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)

        if directory and not os.access(directory, os.W_OK):
            try:
                os.chmod(directory, 0o700)
            except PermissionError:
                logging.warning("Не удалось изменить права доступа каталога сессий: %s", directory)

        if os.path.exists(session_file):
            desired_mode = 0o666 if os.name == "nt" else 0o600
            try:
                os.chmod(session_file, desired_mode)
            except PermissionError:
                logging.warning(
                    "Не удалось изменить права доступа к файлу сессии: %s", session_file
                )

            try:
                conn = sqlite3.connect(session_file, timeout=30.0)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=10000")
                conn.close()
            except Exception:
                pass
    except Exception:
        logging.exception("Ошибка при настройке прав доступа для файла сессии: %s", session_file)


COMMENT_LOG_RETENTION_DAYS = 2
COMMENT_LOG_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60


_SQLITE_LOCK_MESSAGES: Tuple[str, ...] = ("database is locked", "db is locked")


async def _run_with_sqlite_retries(
    action: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 8,
    base_delay: float = 0.5,
    on_retry: Optional[Callable[[int, BaseException], None]] = None,
) -> Any:
    """Execute an async callable and retry when SQLite reports a locked database."""

    delay = base_delay
    last_exc: Optional[BaseException] = None

    for attempt in range(attempts):
        try:
            return await action()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if any(marker in message for marker in _SQLITE_LOCK_MESSAGES) and attempt + 1 < attempts:
                last_exc = exc
                if on_retry is not None:
                    try:
                        on_retry(attempt, exc)
                    except Exception:
                        logging.exception(
                            "Не удалось повторно подготовить сессию после ошибки блокировки"
                        )
                await asyncio.sleep(delay + random.uniform(0, base_delay))
                delay *= 2
                continue
            raise

    if last_exc is not None:
        raise last_exc


async def _connect_client_with_retries(
    client: Client,
    *,
    session_file: Optional[str] = None,
    attempts: int = 8,
    base_delay: float = 0.5,
) -> None:
    def _prepare_session(_: int, __: BaseException) -> None:
        if session_file:
            ensure_session_file_permissions(session_file)

    await _run_with_sqlite_retries(
        client.connect,
        attempts=attempts,
        base_delay=base_delay,
        on_retry=_prepare_session if session_file else None,
    )


async def _start_client_with_retries(
    client: Client,
    *,
    session_file: Optional[str] = None,
    attempts: int = 8,
    base_delay: float = 0.5,
) -> None:
    def _prepare_session(_: int, __: BaseException) -> None:
        if session_file:
            ensure_session_file_permissions(session_file)

    await _run_with_sqlite_retries(
        client.start,
        attempts=attempts,
        base_delay=base_delay,
        on_retry=_prepare_session if session_file else None,
    )


async def _stop_client_with_retries(
    client: Client,
    *,
    session_file: Optional[str] = None,
    attempts: int = 5,
    base_delay: float = 0.5,
) -> None:
    def _prepare_session(_: int, __: BaseException) -> None:
        if session_file:
            ensure_session_file_permissions(session_file)

    await _run_with_sqlite_retries(
        client.stop,
        attempts=attempts,
        base_delay=base_delay,
        on_retry=_prepare_session if session_file else None,
    )


@asynccontextmanager
async def _client_session(
    client: Client,
    *,
    session_file: Optional[str] = None,
    attempts: int = 8,
    base_delay: float = 0.5,
    lock_key: Optional[str] = None,
) -> AsyncIterator[Client]:
    key = lock_key or session_file or getattr(client, "name", None) or str(id(client))

    async with get_session_lock(key):
        await _start_client_with_retries(
            client,
            session_file=session_file,
            attempts=attempts,
            base_delay=base_delay,
        )
        try:
            yield client
        finally:
            try:
                await _stop_client_with_retries(
                    client,
                    session_file=session_file,
                    attempts=max(3, attempts // 2),
                    base_delay=base_delay,
                )
            except sqlite3.OperationalError:
                logging.exception(
                    "Не удалось корректно остановить клиента %s из-за блокировки БД",
                    getattr(client, "name", "<unknown>"),
                )
            except Exception:
                logging.exception(
                    "Не удалось корректно остановить клиента %s",
                    getattr(client, "name", "<unknown>"),
                )


async def _disconnect_client_with_retries(
    client: Client,
    *,
    session_file: Optional[str] = None,
    attempts: int = 5,
    base_delay: float = 0.5,
) -> None:
    def _prepare_session(_: int, __: BaseException) -> None:
        if session_file:
            ensure_session_file_permissions(session_file)

    await _run_with_sqlite_retries(
        client.disconnect,
        attempts=attempts,
        base_delay=base_delay,
        on_retry=_prepare_session if session_file else None,
    )


CHECK_ACCOUNT_SHUTDOWN_TIMEOUT = 5.0
CHECK_ACCOUNT_SHUTDOWN_INTERVAL = 0.2
CHECK_ACCOUNT_RETRY_TIMEOUT = 30.0
CHECK_ACCOUNT_CONNECT_TIMEOUT = 15.0
CHECK_ACCOUNT_OPERATION_TIMEOUT = 10.0
CHECK_ACCOUNT_DISCONNECT_TIMEOUT = 10.0
CHECK_ACCOUNT_GET_ME_TIMEOUT = 10.0
_CORRUPTED_SESSION_ERRORS = (
    "database disk image is malformed",
    "file is not a database",
    "file is encrypted or is not a database",
    "AUTH_KEY_UNREGISTRED",
    "401",
    "auth key",
    "key is not registered",
)


async def _is_session_valid(session_path: str) -> bool:
    """Проверяет, что сессия валидна (авторизация завершена). Если нет — удаляет файл и возвращает False."""
    session_base = session_path.replace(".session.session", "").replace(".session", "")
    client: Optional[Client] = None
    try:
        client = Client(
            session_base,
            api_id=API_ID,
            api_hash=API_HASH,
            no_updates=True,
        )
        await client.connect()
        await asyncio.wait_for(client.get_me(), timeout=10.0)
        return True
    except Exception as exc:
        exc_str = str(exc).lower()
        if any(m in exc_str for m in ("auth_key", "401", "unregistred", "key is not registered")):
            logging.warning("Invalid/incomplete session %s: %s", session_path, exc)
            try:
                for p in (f"{session_base}.session", f"{session_base}.session.session"):
                    if os.path.exists(p):
                        os.remove(p)
                        logging.info("Removed invalid session file %s", p)
            except OSError:
                logging.exception("Failed to remove invalid session %s", session_path)
        return False
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


def _is_regular_reply_message(message: Any) -> bool:
    """Определить обычные reply-сообщения (ответы на комментарии)."""

    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return False

    forward_chat = getattr(reply, "forward_from_chat", None)
    if forward_chat is not None:
        return False

    chat = getattr(message, "chat", None)
    if not chat:
        return False

    chat_type = getattr(chat, "type", None)
    return chat_type in ["group", "supergroup"]


def _is_discussion_reply_message(message: Any) -> bool:
    """Определить сообщения в обсуждениях (пересланные из каналов или обычные reply)."""

    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return False

    forward_chat = getattr(reply, "forward_from_chat", None)
    if forward_chat is not None:
        return True

    return _is_regular_reply_message(message)


def _is_self_generated_message(message: Any) -> bool:
    from_user = getattr(message, "from_user", None)
    if not from_user:
        return False
    return bool(getattr(from_user, "is_self", False))


def _is_reply_to_own_comment(message: Any) -> bool:
    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return False
    reply_author = getattr(reply, "from_user", None)
    if not reply_author:
        return False
    if not getattr(reply_author, "is_self", False):
        return False
    from_user = getattr(message, "from_user", None)
    if from_user and getattr(from_user, "is_self", False):
        return False
    return True


async def _is_reply_to_account_comment(message: Any, account_id: int) -> bool:
    """Check whether message replies to a comment previously sent by the account."""

    if _is_reply_to_own_comment(message):
        return True

    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return False

    reply_message_id = getattr(reply, "id", None)
    if reply_message_id is None:
        return False

    chat = getattr(message, "chat", None)
    channel_id = getattr(chat, "id", None)
    if channel_id is None:
        return False

    channel_for_lookup = str(channel_id)

    try:
        return await has_successful_comment_log_entry(
            account_id,
            channel_for_lookup,
            reply_message_id,
        )
    except Exception:
        logging.exception("Не удалось проверить лог комментариев для ответа")
        return False


def _chat_allows_sending_message(message: Any) -> Tuple[bool, Optional[str]]:
    chat = getattr(message, "chat", None)
    if not chat:
        return False, "no chat"

    permissions = getattr(chat, "permissions", None)
    can_send_messages = getattr(permissions, "can_send_messages", None) if permissions else None

    if can_send_messages is False:
        return False, "comments disabled"

    if can_send_messages is True:
        return True, None

    chat_type = getattr(chat, "type", None)
    if chat_type in {"supergroup", "group"}:
        return True, None

    # По умолчанию считаем, что можно отправлять комментарии, если явного запрета нет
    return True, None


def _extract_available_reaction_emojis(available: Any) -> Set[str]:
    emojis: Set[str] = set()
    if not available:
        return emojis

    for item in available:
        if item is None:
            continue

        emoji_value = getattr(item, "emoji", None)
        if emoji_value:
            emojis.add(emoji_value)
            continue

        nested = getattr(item, "reaction", None)
        emoji_value = getattr(nested, "emoji", None)
        if emoji_value:
            emojis.add(emoji_value)

    return emojis


async def _get_chat_available_quick_reactions(
    client: Client,
    chat_id: Optional[int],
    *,
    force_refresh: bool = False,
) -> Optional[Set[str]]:
    if chat_id is None:
        return None

    if force_refresh:
        _chat_available_reactions_cache.pop(chat_id, None)
    else:
        cached = _chat_available_reactions_cache.get(chat_id)
        if cached is not None:
            return cached

    if not hasattr(client, "get_available_reactions"):
        return None

    try:
        try:
            available = await client.get_available_reactions(chat_id)
        except TypeError:
            available = await client.get_available_reactions()
    except Exception:
        logging.exception(
            "Failed to fetch available reactions for chat %s",
            chat_id,
        )
        return None

    allowed = _extract_available_reaction_emojis(available)
    _chat_available_reactions_cache[chat_id] = allowed
    return allowed


async def _maybe_send_reaction(
    *,
    client: Client,
    message: Any,
    session: str,
    account_id: int,
    reaction_emojis: List[str],
    reaction_sleep_min: int,
    reaction_sleep_max: int,
    reaction_limit_per_message: Optional[int],
    reactions_enabled: bool,
    selected_reaction_chance: int,
    reaction_comment_context: str,
    status_suffix: str,
    post_base_link: Optional[str],
    current_last_reaction_at: Optional[datetime],
    force: bool = False,
    ignore_cooldown: bool = False,
) -> Tuple[Optional[datetime], bool]:
    # Проверка тихого периода - блокируем реакции во время сна
    if is_quiet_period():
        return current_last_reaction_at, False
    
    if not reactions_enabled or not reaction_emojis:
        return current_last_reaction_at, False

    effective_chance = selected_reaction_chance or 0
    if force:
        effective_chance = 100

    if effective_chance <= 0:
        return current_last_reaction_at, False

    channel_for_reactions = str(getattr(getattr(message, "chat", None), "id", ""))
    message_id = getattr(message, "id", None)
    limit = reaction_limit_per_message

    if limit is not None:
        if limit <= 0:
            reason = f'reaction limit {limit} reached'
            enqueue_skip_log(
                session,
                "reaction",
                f"{reason}{reaction_comment_context}",
            )
            await asyncio.sleep(0.2)
            await add_comment_log(
                account_id,
                channel=channel_for_reactions,
                message_id=message_id,
                status=f'reaction_skipped{status_suffix}',
                error=reason,
            )
            return current_last_reaction_at, True
        if channel_for_reactions and message_id is not None:
            try:
                reaction_count = await count_reactions_for_message(
                    channel_for_reactions,
                    message_id,
                )
            except DatabaseNotInitialized as exc:
                logging.warning(
                    "Database not initialised while counting reactions for %s/%s: %s",
                    channel_for_reactions,
                    message_id,
                    exc,
                )
                reaction_count = 0
            except Exception:
                logging.exception(
                    "Unexpected error counting reactions for %s/%s",
                    channel_for_reactions,
                    message_id,
                )
                reaction_count = 0
            if reaction_count >= limit:
                reason = f'reaction limit {reaction_count}/{limit}'
                enqueue_skip_log(
                    session,
                    "reaction",
                    f"{reason}{reaction_comment_context}",
                )
                await asyncio.sleep(0.2)
                await add_comment_log(
                    account_id,
                    channel=channel_for_reactions,
                    message_id=message_id,
                    status=f'reaction_skipped{status_suffix}',
                    error=reason,
                )
                return current_last_reaction_at, True

    reaction_roll = 0
    if not force:
        reaction_roll = random.randint(1, 100)
        if reaction_roll > effective_chance:
            reason = f'reaction random {reaction_roll} > chance {effective_chance}'
            enqueue_skip_log(
                session,
                "reaction",
                f"{reason}{reaction_comment_context}",
            )
            await asyncio.sleep(0.2)
            await add_comment_log(
                account_id,
                channel=str(getattr(getattr(message, "chat", None), "id", "")),
                message_id=message.id,
                status=f'reaction_skipped{status_suffix}',
                error=reason,
            )
            return current_last_reaction_at, False

    chat_obj = getattr(message, "chat", None)
    chat_id_for_reactions = getattr(chat_obj, "id", None)
    working_reaction_emojis = list(reaction_emojis)
    allowed_reaction_emojis = await _get_chat_available_quick_reactions(
        client,
        chat_id_for_reactions,
    )
    if allowed_reaction_emojis is not None:
        if not allowed_reaction_emojis:
            reason = "no allowed quick reactions"
            enqueue_skip_log(
                session,
                "reaction",
                f"{reason}{reaction_comment_context}",
            )
            await asyncio.sleep(0.2)
            await add_comment_log(
                account_id,
                channel=str(getattr(getattr(message, "chat", None), "id", "")),
                message_id=message.id,
                status=f'reaction_skipped{status_suffix}',
                error=f"{reason} (available: none)",
            )
            return current_last_reaction_at, True
        configured_set = set(working_reaction_emojis)
        if not configured_set.issubset(allowed_reaction_emojis):
            logging.debug(
                "Configured reactions %s not fully present in allowed set %s; proceeding with configured list",
                working_reaction_emojis,
                sorted(allowed_reaction_emojis),
            )

    if not working_reaction_emojis:
        available_text: Optional[str] = None
        if allowed_reaction_emojis is not None:
            available_text = (
                " ".join(sorted(allowed_reaction_emojis))
                if allowed_reaction_emojis
                else "none"
            )
        reason = "no allowed quick reactions"
        if available_text is not None:
            reason = f"{reason} (available: {available_text})"

        enqueue_skip_log(
            session,
            "reaction",
            f"{reason}{reaction_comment_context}",
        )
        await asyncio.sleep(0.2)
        await add_comment_log(
            account_id,
            channel=str(getattr(getattr(message, "chat", None), "id", "")),
            message_id=message.id,
            status=f'reaction_skipped{status_suffix}',
            error=reason,
        )
        return current_last_reaction_at, True

    max_reaction_attempts = 3
    reaction_sent = False
    last_reaction_error: Optional[BaseException] = None
    last_reaction_error_text: Optional[str] = None
    attempts_performed = 0
    for reaction_attempt in range(1, max_reaction_attempts + 1):
        if not working_reaction_emojis:
            break

        reaction_emoji = random.choice(working_reaction_emojis)
        working_reaction_emojis.remove(reaction_emoji)
        attempts_performed = reaction_attempt
        await asyncio.sleep(random.uniform(reaction_sleep_min, reaction_sleep_max))

        try:
            now = datetime.now(timezone.utc)
            previous = current_last_reaction_at
            if previous is not None and previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            cooldown_seconds = REACTION_MIN_INTERVAL_SECONDS
            if (
                previous is not None
                and cooldown_seconds
                and not ignore_cooldown
            ):
                if now - previous < timedelta(seconds=cooldown_seconds):
                    reason = (
                        'reaction cooldown '
                        f"{int((now - previous).total_seconds())}/{cooldown_seconds}s"
                    )
                    enqueue_skip_log(
                        session,
                        "reaction",
                        f"{reason}{reaction_comment_context}",
                    )
                    await asyncio.sleep(0.2)
                    await add_comment_log(
                        account_id,
                        channel=str(message.chat.id),
                        message_id=message.id,
                        status=f'reaction_skipped{status_suffix}',
                        error=reason,
                    )
                    return current_last_reaction_at, True

            await client.send_reaction(message.chat.id, message.id, reaction_emoji)
            reaction_link = post_base_link or _build_post_link(message, message)
            if (
                reaction_link
                and _is_discussion_reply_message(message)
                and getattr(message, "id", None) is not None
            ):
                reaction_link = f"{reaction_link}?comment={message.id}"

            if log_channel:
                reaction_link_text = reaction_link or ""
                link_suffix = f"\n{reaction_link_text}" if reaction_link_text else ""
                log_text = (
                    f'Аккаунт {session} поставил реакцию {reaction_emoji}'
                    f'{reaction_comment_context}{link_suffix}'
                )
                try:
                    await bot.send_message(log_channel, log_text)
                except Exception:
                    logging.exception(
                        "Не удалось отправить лог реакции для аккаунта %s", session
                    )
            await update_last_reaction_at_with_logging(account_id, now)
            current_last_reaction_at = now
            await asyncio.sleep(0.2)
            channel_id = str(getattr(getattr(message, "chat", None), "id", ""))
            await add_comment_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                status=f'reaction_success{status_suffix}',
                emoji=reaction_emoji,
            )
            # Также логируем в reaction_logs для правильного учета
            await add_reaction_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                emoji=reaction_emoji,
                status='success',
                error_message=None,
            )
            reaction_sent = True
            break
        except UserBannedInChannel as ban_error:
            if channel_for_reactions:
                try:
                    await add_to_channel_blacklist(
                        account_id,
                        channel_for_reactions,
                        "USER_BANNED_IN_CHANNEL",
                    )
                except Exception:
                    logging.exception(
                        "Failed to add channel %s to blacklist",
                        channel_for_reactions,
                    )
            logging.warning(
                "Account %s banned in channel %s while sending reaction: %s",
                account_id,
                channel_for_reactions,
                ban_error,
            )
            channel_id = str(getattr(getattr(message, "chat", None), "id", ""))
            await add_reaction_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                emoji=reaction_emoji,
                status='failed',
                error_message=str(ban_error),
            )
            return current_last_reaction_at, False
        except MessageIdInvalid as invalid_error:
            logging.warning(
                "Invalid message ID for account %s while sending reaction: %s",
                account_id,
                invalid_error,
            )
            channel_id = str(getattr(getattr(message, "chat", None), "id", ""))
            await add_reaction_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                emoji=reaction_emoji,
                status='failed',
                error_message=str(invalid_error),
            )
            return current_last_reaction_at, False
        except ReactionInvalid as reaction_error:
            logging.warning(
                "Invalid reaction for account %s in channel %s: %s",
                account_id,
                channel_for_reactions,
                reaction_error,
            )
            channel_id = str(getattr(getattr(message, "chat", None), "id", ""))
            await add_reaction_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                emoji=reaction_emoji,
                status='failed',
                error_message=str(reaction_error),
            )
            return current_last_reaction_at, False
        except FloodWait as flood_error:
            # Обрабатываем FloodWait - ждем указанное время
            wait_time = getattr(flood_error, 'value', 20)
            # Ограничиваем максимальное время ожидания до 60 секунд для предотвращения зависаний
            wait_time = min(wait_time, 60)
            logging.warning(
                "FloodWait for account %s: waiting %d seconds before retrying reaction",
                session,
                wait_time,
            )
            if reaction_attempt < max_reaction_attempts:
                # Проверяем тихий период перед ожиданием - если наступил, прекращаем попытки
                if is_quiet_period():
                    logging.info(
                        "Quiet period started during FloodWait for account %s, stopping reaction attempts",
                        session,
                    )
                    return current_last_reaction_at, False
                await asyncio.sleep(wait_time)
                # Повторная проверка тихого периода после ожидания
                if is_quiet_period():
                    logging.info(
                        "Quiet period started after FloodWait for account %s, stopping reaction attempts",
                        session,
                    )
                    return current_last_reaction_at, False
                continue
            else:
                await asyncio.sleep(0.2)
                channel_id = str(getattr(getattr(message, "chat", None), "id", ""))
                await add_comment_log(
                    account_id,
                    channel=channel_id,
                    message_id=message.id,
                    status=f'reaction_error{status_suffix}',
                    error=f'FloodWait {wait_time}s exceeded max attempts',
                    emoji=reaction_emoji,
                )
                await add_reaction_log(
                    account_id,
                    channel=channel_id,
                    message_id=message.id,
                    emoji=reaction_emoji,
                    status='failed',
                    error_message=f'FloodWait {wait_time}s exceeded max attempts',
                )
                return current_last_reaction_at, False
        except Exception as reaction_error:
            last_reaction_error = reaction_error
            last_reaction_error_text = str(reaction_error)
            logging.warning(
                "Attempt %s/%s failed to send reaction for %s: %s",
                reaction_attempt,
                max_reaction_attempts,
                session,
                reaction_error,
                exc_info=True,
            )

            error_text_lower = (last_reaction_error_text or "").lower()
            retryable_error = any(
                keyword in error_text_lower for keyword in ("invalid", "reaction", "not_supported")
            )

            should_retry = (
                retryable_error
                and reaction_attempt < max_reaction_attempts
                and bool(working_reaction_emojis)
            )

            if should_retry:
                await asyncio.sleep(3)
                continue

            await asyncio.sleep(0.2)
            channel_id = str(getattr(getattr(message, "chat", None), "id", ""))
            await add_comment_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                status=f'reaction_error{status_suffix}',
                error=last_reaction_error_text,
                emoji=reaction_emoji,
            )
            await add_reaction_log(
                account_id,
                channel=channel_id,
                message_id=message.id,
                emoji=reaction_emoji,
                status='failed',
                error_message=last_reaction_error_text,
            )
            return current_last_reaction_at, False

    return current_last_reaction_at, reaction_sent


async def send_reaction_safe(
    client: Client,
    chat_id: int,
    message_id: int,
    emoji: str,
    max_attempts: int = 3,
) -> bool:
    """Send reaction with basic retry logic"""

    for attempt in range(1, max_attempts + 1):
        try:
            await client.send_reaction(chat_id, message_id, emoji)
            return True
        except Exception as e:
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
                continue
            logging.warning("Failed to send reaction after %s attempts: %s", max_attempts, e)
            return False

    return False


def _extract_post_text(message: Any) -> Optional[str]:
    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)
    return text if text is not None else caption


def _message_has_media(message: Any) -> bool:
    media_attributes = (
        "photo",
        "video",
        "animation",
        "document",
        "audio",
        "voice",
        "sticker",
    )
    return any(getattr(message, attr, None) is not None for attr in media_attributes)


def _build_post_link(sent_message: Any, original_message: Any) -> str:
    reply = getattr(sent_message, "reply_to_message", None)
    if (
        reply
        and hasattr(reply, "forward_from_chat")
        and reply.forward_from_chat
        and getattr(reply.forward_from_chat, "username", None)
    ):
        username = reply.forward_from_chat.username
        forward_message_id = getattr(reply, "forward_from_message_id", None)
        if forward_message_id is not None:
            return f"https://t.me/{username}/{forward_message_id}"

    chat = getattr(original_message, "chat", None)
    chat_id = getattr(chat, "id", "")
    return f'https://t.me/c/{str(chat_id).replace("-", "")}/{getattr(original_message, "id", "")}'
async def flush_skip_logs() -> None:
    """Sends an aggregated skip report and resets collected data."""

    global skip_log_last_flush_at

    entries: List[Tuple[str, str, int, Optional[str]]] = []
    for session, counters in skip_log_counters.items():
        for event_type, count in counters.items():
            if count <= 0:
                continue
            reason = skip_log_last_reasons.get(session, {}).get(event_type)
            entries.append((session, event_type, count, reason))

    now = datetime.now(timezone.utc)

    if not entries:
        skip_log_last_flush_at = now
        return

    timestamp = now.strftime('%H:%M:%S')
    lines = [f"🕒 Сводка пропусков ({timestamp} UTC)"]

    grouped: Dict[str, List[Tuple[str, int, Optional[str]]]] = defaultdict(list)
    for session, event_type, count, reason in entries:
        grouped[session].append((event_type, count, reason))

    for session in sorted(grouped):
        lines.append(f"Аккаунт {session}:")
        for event_type, count, reason in sorted(grouped[session], key=lambda item: item[0]):
            label = SKIP_LOG_EVENT_LABELS.get(event_type, event_type)
            reason_suffix = f" (последняя причина: {reason})" if reason else ""
            lines.append(f"• {label}: {count}{reason_suffix}")

    message = "\n".join(lines)

    try:
        await bot.send_message(log_channel, message)
    except Exception:
        logging.exception("Не удалось отправить сводку пропусков")
    finally:
        skip_log_counters.clear()
        skip_log_last_reasons.clear()
        skip_log_last_flush_at = now


async def skip_log_flush_worker() -> None:
    """Background task that periodically flushes skip statistics."""

    while True:
        try:
            await asyncio.sleep(SKIP_LOG_FLUSH_INTERVAL_SECONDS)
            await flush_skip_logs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Ошибка фоновой отправки сводки пропусков")


async def comment_log_cleanup_worker() -> None:
    """Periodically remove outdated comment log entries."""

    while True:
        try:
            await asyncio.sleep(COMMENT_LOG_CLEANUP_INTERVAL_SECONDS)
            deleted = await cleanup_comment_logs(COMMENT_LOG_RETENTION_DAYS)
            logging.info("Удалено устаревших записей comment_logs: %d", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Ошибка очистки журнала комментариев")


def _coerce_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


async def process_channel_reactions(
    client: Client,
    account: Dict[str, Any],
    channel: Any,
    last_reaction_at: Optional[datetime],
    *,
    reaction_emojis: List[str],
    reaction_sleep_min: int,
    reaction_sleep_max: int,
    reaction_limit_per_message: Optional[int],
    reactions_enabled: bool,
    reaction_chance: int,
) -> Optional[datetime]:
    if not reactions_enabled:
        return last_reaction_at

    account_id = account.get("id")
    phone = account.get("phone")
    session_label = str(phone or account_id)

    if isinstance(channel, str):
        channel_name = channel.strip()
    else:
        channel_name = channel

    candidate_identifier = str(channel_name)
    if candidate_identifier and await is_channel_blacklisted(account_id, candidate_identifier):
        logging.debug(
            "Skipping blacklisted channel %s for account %s before reaction fetch",
            candidate_identifier,
            account_id,
        )
        return last_reaction_at

    try:
        chat = await client.get_chat(channel_name)
    except asyncio.CancelledError:
        raise
    except UserBannedInChannel as exc:
        identifier = str(channel_name)
        try:
            await add_to_channel_blacklist(account_id, identifier, "USER_BANNED_IN_CHANNEL")
        except Exception:
            logging.exception(
                "Failed to add channel %s to blacklist for account %s",
                identifier,
                account_id,
            )
        logging.warning(
            "Account %s banned in channel %s during reaction scan: %s",
            account_id,
            identifier,
            exc,
        )
        return last_reaction_at
    except Exception as exc:
        logging.error(
            "Error loading channel %s for account %s: %s",
            channel_name,
            account_id,
            exc,
        )
        return last_reaction_at

    channel_identifier = str(getattr(chat, "id", channel_name))
    if channel_identifier and await is_channel_blacklisted(account_id, channel_identifier):
        logging.debug(
            "Skipping blacklisted channel %s for account %s during reaction scan",
            channel_identifier,
            account_id,
        )
        return last_reaction_at

    try:
        history = await client.get_chat_history(chat.id, limit=1)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.error(
            "Error loading history for channel %s (account %s): %s",
            channel_name,
            account_id,
            exc,
        )
        return last_reaction_at

    if not history:
        logging.debug(
            "No recent messages in channel %s for account %s",
            channel_name,
            account_id,
        )
        return last_reaction_at

    message = history[0]
    post_base_link = _build_post_link(message, message)

    try:
        updated_last_reaction_at, _ = await _maybe_send_reaction(
            client=client,
            message=message,
            session=session_label,
            account_id=account_id,
            reaction_emojis=reaction_emojis,
            reaction_sleep_min=reaction_sleep_min,
            reaction_sleep_max=reaction_sleep_max,
            reaction_limit_per_message=reaction_limit_per_message,
            reactions_enabled=reactions_enabled,
            selected_reaction_chance=reaction_chance,
            reaction_comment_context=" (background scan)",
            status_suffix="_background",
            post_base_link=post_base_link,
            current_last_reaction_at=last_reaction_at,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.error(
            "Reaction processing error for account %s channel %s: %s",
            account_id,
            channel_name,
            exc,
        )
        return last_reaction_at

    return updated_last_reaction_at


async def process_account_reactions(account: Dict[str, Any]) -> None:
    account_id = account.get("id")
    if account_id is None:
        return

    user_id_raw = account.get("user_id")
    phone_raw = account.get("phone")

    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        logging.debug("Skipping reactions for account %s: invalid user id", account_id)
        return

    if not phone_raw:
        logging.debug("Skipping reactions for account %s: phone is missing", account_id)
        return

    phone = str(phone_raw)
    key = make_session_key(user_id, phone)

    if active_sessions.get(key):
        logging.debug(
            "Skipping background reactions for account %s: session already active",
            account_id,
        )
        return

    reactions_enabled_raw = account.get("reactions_enabled")
    reactions_enabled = True if reactions_enabled_raw is None else bool(reactions_enabled_raw)

    if not reactions_enabled:
        logging.debug("Reactions disabled for account %s", account_id)
        return

    reaction_emojis_raw = account.get("reaction_emojis") or []
    reaction_emojis = [
        emoji.strip()
        for emoji in reaction_emojis_raw
        if isinstance(emoji, str) and emoji.strip()
    ]

    if not reaction_emojis:
        logging.debug("No reaction emojis configured for account %s", account_id)
        return

    channels = account.get("channels") or []
    if not channels:
        logging.debug("No channels configured for account %s", account_id)
        return

    session_path = account.get("session_path")
    if not session_path:
        logging.debug("Session path missing for account %s", account_id)
        return

    if not os.path.exists(session_path):
        logging.warning(
            "Session file %s missing for account %s; skipping reaction processing",
            session_path,
            account_id,
        )
        return

    ensure_session_file_permissions(session_path)

    if session_path.endswith(".session"):
        session_name = session_path[:-len(".session")]
    else:
        session_name = session_path

    reaction_sleep_min = _coerce_int(account.get("reaction_sleep_min"), 0)
    reaction_sleep_max = _coerce_int(account.get("reaction_sleep_max"), 0)

    if reaction_sleep_min <= 0 or reaction_sleep_max <= 0:
        sleep_min_fallback = _coerce_int(account.get("sleep_min"), 10)
        sleep_max_fallback = _coerce_int(account.get("sleep_max"), sleep_min_fallback)
        if reaction_sleep_min <= 0:
            reaction_sleep_min = sleep_min_fallback
        if reaction_sleep_max <= 0:
            reaction_sleep_max = sleep_max_fallback

    if reaction_sleep_min > reaction_sleep_max:
        reaction_sleep_min, reaction_sleep_max = reaction_sleep_max, reaction_sleep_min

    reaction_limit_per_message = account.get("reaction_limit_per_message")
    if reaction_limit_per_message is None:
        reaction_limit_per_message = DEFAULT_REACTION_LIMIT_PER_MESSAGE

    reaction_chance = _coerce_int(account.get("reaction_chance"), 0)

    last_reaction_at = _parse_warmup_datetime(account.get("last_reaction_at"))
    if last_reaction_at is None:
        last_reaction_at = datetime.now(timezone.utc) - timedelta(hours=1)
    elif last_reaction_at.tzinfo is None:
        last_reaction_at = last_reaction_at.replace(tzinfo=timezone.utc)

    async def _runner(client: Client) -> None:
        nonlocal last_reaction_at
        # Получаем задержку между каналами из настроек аккаунта
        sleep_min = _coerce_int(account.get("sleep_min"), 10)
        sleep_max = _coerce_int(account.get("sleep_max"), sleep_min)
        if sleep_max < sleep_min:
            sleep_max = sleep_min
        
        for idx, channel in enumerate(channels):
            # Добавляем задержку между обработкой разных каналов (кроме первого)
            if idx > 0:
                delay = random.uniform(sleep_min, sleep_max)
                logging.debug(
                    "Задержка между каналами для аккаунта %s: %.2f сек (канал %d/%d)",
                    account_id,
                    delay,
                    idx + 1,
                    len(channels),
                )
                await asyncio.sleep(delay)
            
            updated = await process_channel_reactions(
                client,
                account,
                channel,
                last_reaction_at,
                reaction_emojis=reaction_emojis,
                reaction_sleep_min=reaction_sleep_min,
                reaction_sleep_max=reaction_sleep_max,
                reaction_limit_per_message=reaction_limit_per_message,
                reactions_enabled=reactions_enabled,
                reaction_chance=reaction_chance,
            )
            if updated and updated != last_reaction_at:
                last_reaction_at = updated
                await update_last_reaction_at(account_id, updated)

    try:
        await with_retry(session_name, _runner, lock_key=key)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.error("Error processing account %s reactions: %s", account_id, exc)


async def process_account_comments(account: Dict[str, Any]) -> None:
    account_id = account.get("id")
    if account_id is None:
        return

    user_id_raw = account.get("user_id")
    phone_raw = account.get("phone")

    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        logging.debug("Skipping comments for account %s: invalid user id", account_id)
        return

    if not phone_raw:
        logging.debug("Skipping comments for account %s: phone is missing", account_id)
        return

    phone = str(phone_raw)
    key = make_session_key(user_id, phone)

    if active_sessions.get(key):
        return

    session_path = account.get("session_path")
    if not session_path:
        logging.debug("Session path missing for account %s", account_id)
        return

    if not os.path.exists(session_path):
        logging.warning(
            "Session file %s missing for account %s; stopping account",
            session_path,
            account_id,
        )
        try:
            await mark_account_stopped(account_id)
        except Exception as exc:
            logging.error(
                "Failed to mark account %s stopped after missing session: %s",
                account_id,
                exc,
            )
        return

    ensure_session_file_permissions(session_path)

    active_sessions[key] = True
    active_account_ids[key] = account_id
    quiet_sessions_notified.discard(key)

    logging.info(
        "Scheduling comment worker for account %s (%s)",
        account_id,
        phone,
    )

    _schedule_safe_send_comments(user_id, phone, account_id)


async def process_single_standard_account(account: Dict[str, Any]) -> None:
    account_id = account.get("id")
    phone = account.get("phone")
    logging.debug("Processing standard account %s (%s)", account_id, phone)

    try:
        await process_account_reactions(account)
    except asyncio.CancelledError:
        raise
    except Exception as reaction_error:
        logging.warning(
            "Failed to process reactions for account %s (phone %s): %s",
            account_id,
            phone,
            reaction_error,
        )

    try:
        await process_account_comments(account)
    except asyncio.CancelledError:
        raise
    except Exception as comment_error:
        logging.warning(
            "Failed to process comments for account %s (phone %s): %s",
            account_id,
            phone,
            comment_error,
        )


async def process_standard_accounts() -> None:
    """Main loop for processing standard mode accounts with improved error handling."""
    logging.info("✅ Processing standard accounts...")
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            accounts = await get_running_standard_accounts()
            logging.info("Found %d running standard accounts", len(accounts))
            consecutive_errors = 0  # Reset on successful iteration
            
            for account in accounts:
                try:
                    await process_single_standard_account(account)
                except asyncio.CancelledError:
                    raise
                except Exception as account_error:
                    error_str = str(account_error)
                    account_id = account.get("id")
                    phone = account.get("phone")
                    
                    if "fromisoformat" in error_str:
                        logging.warning(
                            "Datetime parsing error for account %s (%s). Setting default values and continuing...",
                            account_id,
                            phone,
                        )
                        continue
                    
                    # Check for critical errors that require account stopping
                    critical_keywords = ("auth", "session", "phone", "flood", "ban", "deleted", "unauthorized")
                    if any(keyword in error_str.lower() for keyword in critical_keywords):
                        logging.error(
                            "CRITICAL error for account %s (phone %s): %s. Stopping account.",
                            account_id,
                            phone,
                            account_error,
                        )
                        try:
                            await mark_account_stopped(account_id)
                        except Exception as stop_error:
                            logging.error(
                                "Failed to stop account %s after critical error: %s",
                                account_id,
                                stop_error,
                            )
                        continue
                    
                    logging.warning(
                        "Unhandled error while processing account %s (phone %s): %s",
                        account_id,
                        phone,
                        account_error,
                        exc_info=True,  # Include full traceback
                    )
            
            await asyncio.sleep(ACCOUNT_CHECK_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as loop_error:
            consecutive_errors += 1
            logging.error(
                "Standard accounts loop error (consecutive: %d/%d): %s",
                consecutive_errors,
                max_consecutive_errors,
                loop_error,
                exc_info=True,
            )
            
            if consecutive_errors >= max_consecutive_errors:
                logging.critical(
                    "Too many consecutive errors in standard accounts loop. "
                    "Waiting longer before retry..."
                )
                await asyncio.sleep(300)  # Wait 5 minutes before retry
                consecutive_errors = 0
            else:
                await asyncio.sleep(60)


async def _handle_linked_channel_message(
    client: Client,
    message: Any,
    *,
    userid: int,
    session: str,
    account_id: int,
    chance: int,
    xsleep: int,
    ysleep: int,
    system_prompt: str,
    reaction_emojis: List[str],
    reaction_chance: int,
    reaction_discussion_chance: Optional[int],
    discussion_reply_prompt: Optional[str],
    discussion_reply_chance: Optional[int],
    reaction_sleep_min: int,
    reaction_sleep_max: int,
    reaction_limit_per_message: Optional[int],
    reactions_enabled: bool,
    last_reaction_at: Optional[datetime] = None,
    force_discussion: bool = False,
    comment_chance_override: Optional[int] = None,
    comment_prompt_override: Optional[str] = None,
    selected_reaction_chance_override: Optional[int] = None,
) -> Optional[datetime]:
    key = make_session_key(userid, session)
    if not active_sessions.get(key, False):
        return last_reaction_at

    current_last_reaction_at = last_reaction_at

    if await _is_reply_to_account_comment(message, account_id):
        reaction_comment_context = ' (ответ на комментарий аккаунта)'
        status_suffix = '_reply'
        previous_last_reaction_at = current_last_reaction_at
        updated_last_reaction_at, should_exit = await _maybe_send_reaction(
            client=client,
            message=message,
            session=session,
            account_id=account_id,
            reaction_emojis=reaction_emojis,
            reaction_sleep_min=reaction_sleep_min,
            reaction_sleep_max=reaction_sleep_max,
            reaction_limit_per_message=reaction_limit_per_message,
            reactions_enabled=reactions_enabled,
            selected_reaction_chance=reaction_chance,
            reaction_comment_context=reaction_comment_context,
            status_suffix=status_suffix,
            post_base_link=None,
            current_last_reaction_at=current_last_reaction_at,
            force=True,
            ignore_cooldown=True,
        )
        current_last_reaction_at = updated_last_reaction_at
        if should_exit and updated_last_reaction_at == previous_last_reaction_at:
            return current_last_reaction_at

    is_discussion_message = _is_discussion_reply_message(message)
    if force_discussion:
        is_discussion_message = True
    if is_discussion_message and _is_self_generated_message(message):
        logging.debug("Обнаружен собственный комментарий в обсуждении для %s", session)
        return current_last_reaction_at

    if is_discussion_message:
        comment_chance_value = (
            comment_chance_override
            if comment_chance_override is not None
            else discussion_reply_chance
        )
        comment_prompt_value = (
            comment_prompt_override
            if comment_prompt_override is not None
            else discussion_reply_prompt
        )
        selected_reaction_chance_value = (
            selected_reaction_chance_override
            if selected_reaction_chance_override is not None
            else reaction_discussion_chance
        )

        if comment_prompt_value is not None and comment_chance_value is not None:
            comment_chance = comment_chance_value
            comment_prompt = comment_prompt_value
            selected_reaction_chance = selected_reaction_chance_value
        else:
            comment_chance = chance
            comment_prompt = system_prompt
            selected_reaction_chance = reaction_chance
    else:
        comment_chance = chance
        comment_prompt = system_prompt
        selected_reaction_chance = reaction_chance

    post_text = _extract_post_text(message)
    if post_text is None:
        await add_comment_log(
            account_id,
            channel=str(getattr(getattr(message, "chat", None), "id", "")),
            message_id=getattr(message, "id", None),
            status='no_comments',
            error='no text or caption',
        )
        return current_last_reaction_at

    channel_identifier = str(getattr(getattr(message, "chat", None), "id", ""))
    raw_message_id = getattr(message, "id", None)
    try:
        numeric_message_id = int(raw_message_id)
    except (TypeError, ValueError):
        numeric_message_id = None

    if channel_identifier:
        try:
            if await is_channel_blacklisted(account_id, channel_identifier):
                logging.debug(
                    "Skipping blacklisted channel %s for account %s in linked handler",
                    channel_identifier,
                    account_id,
                )
                return current_last_reaction_at
        except DatabaseNotInitialized:
            logging.debug("Skipping blacklist check for %s: database not initialised", channel_identifier)
        except Exception as blacklist_error:
            logging.exception("Failed to check blacklist for %s: %s", channel_identifier, blacklist_error)

    if channel_identifier and numeric_message_id is not None:
        try:
            await record_post(
                account_id,
                channel=channel_identifier,
                post_id=numeric_message_id,
                message=post_text,
                has_media=_message_has_media(message),
            )
        except Exception:
            logging.exception("Не удалось сохранить информацию о посте для реакций")

    can_send, reason = _chat_allows_sending_message(message)
    if not can_send:
        await add_comment_log(
            account_id,
            channel=str(getattr(getattr(message, "chat", None), "id", "")),
            message_id=getattr(message, "id", None),
            status='no_comments',
            error=reason or 'comments disabled',
        )
        return current_last_reaction_at

    # Проверка наличия linked_chat (чата комментариев) перед комментированием
    if channel_identifier:
        try:
            channel_obj = await client.get_chat(channel_identifier)
            linked_chat = getattr(channel_obj, "linked_chat", None)
            if not linked_chat:
                # Канал без linked_chat - логируем и добавляем в blacklist
                await add_comment_log(
                    account_id,
                    channel=channel_identifier,
                    message_id=numeric_message_id,
                    status='no_linked_chat',
                    error='channel has no linked chat',
                )
                await add_to_channel_blacklist(account_id, channel_identifier, "NO_LINKED_CHAT")
                logging.debug(
                    "Channel %s has no linked_chat, added to blacklist for account %s",
                    channel_identifier,
                    account_id,
                )
                return current_last_reaction_at
        except UserBannedInChannel as ban_error:
            # Пользователь забанен в канале - сразу в blacklist
            await add_to_channel_blacklist(account_id, channel_identifier, "USER_BANNED_IN_CHANNEL")
            await add_comment_log(
                account_id,
                channel=channel_identifier,
                message_id=numeric_message_id,
                status='banned',
                error='USER_BANNED_IN_CHANNEL',
            )
            logging.warning(
                "Account %s banned in channel %s: %s",
                account_id,
                channel_identifier,
                ban_error,
            )
            return current_last_reaction_at
        except Exception as check_error:
            # Ошибка при проверке - логируем, но продолжаем работу
            logging.warning(
                "Failed to check linked_chat for channel %s (account %s): %s",
                channel_identifier,
                account_id,
                check_error,
            )

    # Проверка на бан перед генерацией комментария (дополнительная проверка)
    if channel_identifier:
        try:
            # Пытаемся получить информацию о канале для проверки бана
            chat_info = await client.get_chat(channel_identifier)
            # Если получили - значит не забанены, продолжаем
        except UserBannedInChannel as ban_error:
            # Пользователь забанен - добавляем в blacklist и прекращаем
            await add_to_channel_blacklist(account_id, channel_identifier, "USER_BANNED_IN_CHANNEL")
            await add_comment_log(
                account_id,
                channel=channel_identifier,
                message_id=numeric_message_id,
                status='banned',
                error='USER_BANNED_IN_CHANNEL',
            )
            logging.warning(
                "Account %s banned in channel %s before commenting: %s",
                account_id,
                channel_identifier,
                ban_error,
            )
            return current_last_reaction_at
        except Exception as check_error:
            # Другие ошибки - логируем, но продолжаем
            logging.debug(
                "Error checking channel %s for ban (account %s): %s",
                channel_identifier,
                account_id,
                check_error,
            )

    try:
        comment_sent = False
        comment_skipped = False
        comment_skip_reason: Optional[str] = None
        post_base_link: Optional[str] = None

        roll = random.randint(1, 100)
        if roll > comment_chance:
            comment_skipped = True
            comment_skip_reason = f'random {roll} > chance {comment_chance}'
            enqueue_skip_log(
                session,
                "comment",
                f"{comment_skip_reason}; проверяем реакцию",
            )
            await add_comment_log(
                account_id,
                channel=str(getattr(getattr(message, "chat", None), "id", "")),
                message_id=getattr(message, "id", None),
                status='comment_skipped',
                error=comment_skip_reason,
            )
        else:
            if is_quiet_period():
                if key not in quiet_sessions_notified:
                    await bot.send_message(
                        log_channel,
                        f'Аккаунт {session} приостановлен до {QUIET_END_MSK_STR} МСК (циркадный режим)'
                    )
                    quiet_sessions_notified.add(key)
                return current_last_reaction_at

            await asyncio.sleep(random.uniform(xsleep, ysleep))

            if not active_sessions.get(key, False):
                reason = 'session stopped during delay'
                enqueue_skip_log(session, "comment", reason)
                await add_comment_log(
                    account_id,
                    channel=str(getattr(getattr(message, "chat", None), "id", "")),
                    message_id=getattr(message, "id", None),
                    status='comment_skipped',
                    error=reason,
                )
                return current_last_reaction_at

            if is_quiet_period():
                if key not in quiet_sessions_notified:
                    await bot.send_message(
                        log_channel,
                        f'Аккаунт {session} приостановлен до {QUIET_END_MSK_STR} МСК (циркадный режим)'
                    )
                    quiet_sessions_notified.add(key)
                return current_last_reaction_at

            comment = generate_comment(post_text, comment_prompt)
            if not comment or not str(comment).strip():
                error_reason = "generated comment empty"
                logging.warning(
                    "Пропуск комментария для %s: %s (возможно, ошибка OpenAI API - проверьте логи)", 
                    session, 
                    error_reason
                )
                # Отправляем предупреждение в лог-канал для каждого аккаунта отдельно
                if log_channel:
                    warning_key = f'_openai_warning_sent_{account_id}'
                    if not hasattr(send_comments, warning_key):
                        try:
                            await bot.send_message(
                                log_channel,
                                f"⚠️ КРИТИЧНО: OpenAI API вернул пустой комментарий для аккаунта {session}.\n"
                                f"Проверьте OPENAI_API_KEY в .env файле на сервере.\n"
                                f"Комментарии НЕ будут отправляться до исправления API ключа.\n"
                                f"Ошибка: неверный или истекший API ключ (401 Unauthorized)"
                            )
                            setattr(send_comments, warning_key, True)
                        except Exception as warn_err:
                            logging.exception("Не удалось отправить предупреждение об OpenAI: %s", warn_err)
                
                if is_discussion_message:
                    comment_skipped = True
                    comment_skip_reason = error_reason
                    enqueue_skip_log(
                        session,
                        "comment",
                        f"{comment_skip_reason}; проверяем реакцию",
                    )
                    await add_comment_log(
                        account_id,
                        channel=str(getattr(getattr(message, "chat", None), "id", "")),
                        message_id=getattr(message, "id", None),
                        status='comment_skipped',
                        error=comment_skip_reason,
                    )
                else:
                    return current_last_reaction_at
            else:
                # Retry logic for sending comments
                max_comment_retries = 3
                comment_sent = False
                msg = None
                
                for comment_attempt in range(1, max_comment_retries + 1):
                    # Проверка тихого периода перед каждой попыткой отправки комментария
                    if is_quiet_period():
                        logging.info(
                            "Quiet period detected during comment attempt %d/%d for account %s, stopping",
                            comment_attempt,
                            max_comment_retries,
                            account_id,
                        )
                        return current_last_reaction_at
                    
                    try:
                        # Добавляем таймаут для отправки сообщения (30 секунд)
                        msg = await asyncio.wait_for(
                            client.send_message(
                                message.chat.id,
                                comment,
                                reply_to_message_id=message.id,
                            ),
                            timeout=30.0,
                        )
                        comment_sent = True
                        break
                    except (ConnectionError, TimeoutError) as network_error:
                        if comment_attempt < max_comment_retries:
                            retry_delay = 2 ** comment_attempt
                            logging.warning(
                                "Network error sending comment (attempt %d/%d) for account %s: %s. Retrying in %ds...",
                                comment_attempt,
                                max_comment_retries,
                                account_id,
                                network_error,
                                retry_delay,
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            logging.error(
                                "Failed to send comment after %d attempts due to network error: %s",
                                max_comment_retries,
                                network_error,
                            )
                            raise
                    except UserBannedInChannel as ban_error:
                        if channel_identifier:
                            try:
                                await add_to_channel_blacklist(
                                    account_id,
                                    channel_identifier,
                                    "USER_BANNED_IN_CHANNEL",
                                )
                            except Exception:
                                logging.exception(
                                    "Failed to add channel %s to blacklist for account %s",
                                    channel_identifier,
                                    account_id,
                                )
                        logging.warning(
                            "Account %s banned in channel %s while commenting: %s",
                            account_id,
                            channel_identifier,
                            ban_error,
                        )
                        await asyncio.sleep(0.2)
                        await add_comment_log(
                            account_id,
                            channel=channel_identifier,
                            message_id=numeric_message_id,
                            status='channel_blacklisted',
                            error='USER_BANNED_IN_CHANNEL',
                        )
                        return current_last_reaction_at
                    except MessageIdInvalid as invalid_error:
                        logging.warning(
                            "Invalid message ID for account %s in channel %s: %s",
                            account_id,
                            channel_identifier,
                            invalid_error,
                        )
                        await asyncio.sleep(0.2)
                        await add_comment_log(
                            account_id,
                            channel=channel_identifier,
                            message_id=numeric_message_id,
                            status='comment_skipped',
                            error='message id invalid',
                        )
                        return current_last_reaction_at
                    except (ChatWriteForbidden, Forbidden) as forbidden_error:
                        # Автоматически добавляем канал в черный список при ошибке доступа
                        if channel_identifier:
                            try:
                                error_reason = "CHAT_WRITE_FORBIDDEN" if isinstance(forbidden_error, ChatWriteForbidden) else "FORBIDDEN"
                                await add_to_channel_blacklist(
                                    account_id,
                                    channel_identifier,
                                    error_reason,
                                )
                                logging.info(
                                    "Channel %s added to blacklist for account %s due to %s",
                                    channel_identifier,
                                    account_id,
                                    error_reason,
                                )
                            except Exception:
                                logging.exception(
                                    "Failed to add channel %s to blacklist for account %s",
                                    channel_identifier,
                                    account_id,
                                )
                        await asyncio.sleep(0.2)
                        await add_comment_log(
                            account_id,
                            channel=channel_identifier,
                            message_id=numeric_message_id,
                            status='channel_blacklisted',
                            error=str(forbidden_error),
                        )
                        return current_last_reaction_at
                    except Exception as comment_error:
                        if comment_attempt < max_comment_retries:
                            retry_delay = 2 ** comment_attempt
                            logging.warning(
                                "Error sending comment (attempt %d/%d) for account %s: %s. Retrying in %ds...",
                                comment_attempt,
                                max_comment_retries,
                                account_id,
                                comment_error,
                                retry_delay,
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            logging.error(
                                "Failed to send comment after %d attempts for account %s: %s",
                                max_comment_retries,
                                account_id,
                                comment_error,
                                exc_info=True,
                            )
                            await asyncio.sleep(0.2)
                            await add_comment_log(
                                account_id,
                                channel=channel_identifier,
                                message_id=numeric_message_id,
                                status='error',
                                error=f'failed after {max_comment_retries} attempts: {str(comment_error)}',
                            )
                            return current_last_reaction_at
                
                if not comment_sent or msg is None:
                    logging.error("Comment sending failed for account %s after all retries", account_id)
                    return current_last_reaction_at

                post_base_link = _build_post_link(msg, message)
                comment_link = f"{post_base_link}?comment={msg.id}"
                await bot.send_message(
                    log_channel,
                    f'Аккаунт {session} отправил комментарий\n{comment_link}'
                )
                # Небольшая пауза перед записью в БД
                await asyncio.sleep(0.2)
                await add_comment_log(
                    account_id,
                    channel=str(message.chat.id),
                    message_id=msg.id,
                    status='success',
                )
                comment_sent = True

        if reactions_enabled and reaction_emojis:
            if post_base_link is None:
                post_base_link = _build_post_link(message, message)
            status_suffix = '' if comment_sent else '_no_comment'
            reaction_comment_context = (
                ' (без комментария)' if not comment_sent and comment_skipped else ''
            )
            # Используем настройки аккаунта для задержки реакции
            if reaction_sleep_min and reaction_sleep_max and reaction_sleep_min > 0 and reaction_sleep_max > 0:
                pause = random.uniform(reaction_sleep_min, reaction_sleep_max)
            else:
                # Fallback на минимальную паузу если настройки не заданы
                pause = COMMENT_TO_REACTION_PAUSE_SECONDS
                if not comment_sent:
                    pause = max(pause / 2, 0.1)
            await asyncio.sleep(pause)
            updated_last_reaction_at, should_exit = await _maybe_send_reaction(
                client=client,
                message=message,
                session=session,
                account_id=account_id,
                reaction_emojis=reaction_emojis,
                reaction_sleep_min=reaction_sleep_min,
                reaction_sleep_max=reaction_sleep_max,
                reaction_limit_per_message=reaction_limit_per_message,
                reactions_enabled=reactions_enabled,
                selected_reaction_chance=selected_reaction_chance,
                reaction_comment_context=reaction_comment_context,
                status_suffix=status_suffix,
                post_base_link=post_base_link,
                current_last_reaction_at=current_last_reaction_at,
            )
            current_last_reaction_at = updated_last_reaction_at
            if should_exit:
                return current_last_reaction_at

    except ChatWriteForbidden as e:
        # Не логируем в канал для ChatWriteForbidden - это нормальная ситуация
        logging.debug(
            "Account %s cannot write in chat %s: %s",
            session,
            getattr(message.chat, "id", "unknown"),
            e
        )
        await asyncio.sleep(0.2)
        await add_comment_log(
            account_id,
            channel=str(message.chat.id),
            message_id=message.id,
            status='no_comments',
            error=str(e),
        )
    except Exception as e:
        # Логируем только критические ошибки в канал
        error_str = str(e)
        if "CHAT_WRITE_FORBIDDEN" not in error_str:
            # Только не-CHAT_WRITE_FORBIDDEN ошибки отправляем в канал
            logging.warning(
                "Account %s comment error (not ChatWriteForbidden): %s",
                session,
                e
            )
        else:
            logging.debug(
                "Account %s cannot write in chat (ChatWriteForbidden): %s",
                session,
                e
            )
        # Пауза перед записью ошибки в БД
        await asyncio.sleep(0.2)
        await add_comment_log(
            account_id,
            channel=str(message.chat.id),
            message_id=message.id,
            status='error',
            error=error_str,
        )

    return current_last_reaction_at


class TransientJoinError(Exception):
    """Raised when a transient error occurs while joining a channel."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


_TRANSIENT_JOIN_ERROR_KEYWORDS: Tuple[str, ...] = (
    "database is locked",
    "db is locked",
    "temporary failure",
    "temporarily unavailable",
)


def _is_transient_join_error(message: str) -> bool:
    """Return True if the error message indicates a transient problem."""

    if not message:
        return False
    lowered = message.lower()
    return any(keyword in lowered for keyword in _TRANSIENT_JOIN_ERROR_KEYWORDS)

# Функция для загрузки настроек расписания
def load_schedule_config():
    """Загружает настройки расписания из schedule.json"""

    default_config = {
        "quiet_period": {"start_hour": 8, "start_minute": 0, "end_hour": 20, "end_minute": 0},
        "warmup_period": {"start_hour": 12, "start_minute": 0, "end_hour": 19, "end_minute": 0},
        # Значения по умолчанию используются как резерв, реальные настройки читаются из БД
        "warmup_settings": {"channels_per_day": 15, "delay_minutes": 7, "default_days": 7},
    }

    try:
        with open('schedule.json', 'r', encoding='utf-8') as f:
            file_config = json.load(f)
    except FileNotFoundError:
        return default_config
    except Exception as e:
        logging.warning(f"Ошибка загрузки schedule.json: {e}")
        return default_config

    if not isinstance(file_config, dict):
        return default_config

    merged = default_config.copy()
    merged.update({k: v for k, v in file_config.items() if k in ("quiet_period", "warmup_period", "warmup_settings")})

    warmup_defaults = merged.setdefault("warmup_settings", {})
    for key, value in default_config["warmup_settings"].items():
        warmup_defaults.setdefault(key, value)

    return merged

# Загружаем настройки расписания (будет перезагружаться при каждом использовании)
_SCHEDULE_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_SCHEDULE_CONFIG_LAST_LOAD: Optional[float] = None
_SCHEDULE_CONFIG_CACHE_TTL = 60  # Перезагружать конфиг каждые 60 секунд

def get_schedule_config() -> Dict[str, Any]:
    """Получает актуальные настройки расписания, перезагружая из файла при необходимости"""
    global _SCHEDULE_CONFIG_CACHE, _SCHEDULE_CONFIG_LAST_LOAD
    
    import time
    current_time = time.time()
    
    # Перезагружаем конфиг если кэш устарел или не загружен
    if (_SCHEDULE_CONFIG_CACHE is None or 
        _SCHEDULE_CONFIG_LAST_LOAD is None or 
        (current_time - _SCHEDULE_CONFIG_LAST_LOAD) > _SCHEDULE_CONFIG_CACHE_TTL):
        _SCHEDULE_CONFIG_CACHE = load_schedule_config()
        _SCHEDULE_CONFIG_LAST_LOAD = current_time
        logging.debug(f"Schedule config reloaded: quiet_period={_SCHEDULE_CONFIG_CACHE.get('quiet_period')}")
    
    return _SCHEDULE_CONFIG_CACHE

# Инициализируем кэш при старте
SCHEDULE_CONFIG = get_schedule_config()

# Настройки времени из schedule.json
def _get_quiet_period_config():
    """Получает актуальные настройки тихого периода"""
    config = get_schedule_config()
    return (
        config["quiet_period"]["start_hour"],
        config["quiet_period"]["start_minute"],
        config["quiet_period"]["end_hour"],
        config["quiet_period"]["end_minute"],
    )

# Инициализируем значения при старте (будут обновляться динамически)
QUIET_START_HOUR, QUIET_START_MINUTE, QUIET_END_HOUR, QUIET_END_MINUTE = _get_quiet_period_config()

MOSCOW_UTC_OFFSET_MINUTES = 3 * 60


def _format_time_with_offset(hour: int, minute: int, offset_minutes: int = 0) -> str:
    total_minutes = (hour * 60 + minute + offset_minutes) % (24 * 60)
    formatted_hour, formatted_minute = divmod(total_minutes, 60)
    return f"{formatted_hour:02d}:{formatted_minute:02d}"


QUIET_START_UTC_STR = f"{QUIET_START_HOUR:02d}:{QUIET_START_MINUTE:02d}"
QUIET_END_UTC_STR = f"{QUIET_END_HOUR:02d}:{QUIET_END_MINUTE:02d}"
QUIET_START_MSK_STR = _format_time_with_offset(QUIET_START_HOUR, QUIET_START_MINUTE, MOSCOW_UTC_OFFSET_MINUTES)
QUIET_END_MSK_STR = _format_time_with_offset(QUIET_END_HOUR, QUIET_END_MINUTE, MOSCOW_UTC_OFFSET_MINUTES)

WARMUP_SCAN_INTERVAL_SECONDS = 60  # Проверка каждую минуту
WARMUP_DEFAULT_DAYS = SCHEDULE_CONFIG["warmup_settings"]["default_days"]


@dataclass
class WarmupSettingsData:
    channels_per_day: int
    delay_minutes: int
    join_start_hour: int
    join_start_minute: int
    join_end_hour: int
    join_end_minute: int

    @property
    def window_start(self) -> time:
        return time(self.join_start_hour, self.join_start_minute)

    @property
    def window_end(self) -> time:
        return time(self.join_end_hour, self.join_end_minute)

    @property
    def spans_midnight(self) -> bool:
        return self.window_start >= self.window_end

    def format_window(self) -> str:
        return (
            f"{self.join_start_hour:02d}:{self.join_start_minute:02d} - "
            f"{self.join_end_hour:02d}:{self.join_end_minute:02d}"
        )


def _make_warmup_settings(data: Dict[str, int]) -> WarmupSettingsData:
    return WarmupSettingsData(
        channels_per_day=int(data.get("channels_per_day", 0)),
        delay_minutes=int(data.get("delay_minutes", 0)),
        join_start_hour=int(data.get("join_start_hour", 0)),
        join_start_minute=int(data.get("join_start_minute", 0)),
        join_end_hour=int(data.get("join_end_hour", 0)),
        join_end_minute=int(data.get("join_end_minute", 0)),
    )


DEFAULT_WARMUP_SETTINGS = WarmupSettingsData(
    channels_per_day=SCHEDULE_CONFIG["warmup_settings"]["channels_per_day"],
    delay_minutes=SCHEDULE_CONFIG["warmup_settings"]["delay_minutes"],
    join_start_hour=SCHEDULE_CONFIG["warmup_period"]["start_hour"],
    join_start_minute=SCHEDULE_CONFIG["warmup_period"]["start_minute"],
    join_end_hour=SCHEDULE_CONFIG["warmup_period"]["end_hour"],
    join_end_minute=SCHEDULE_CONFIG["warmup_period"]["end_minute"],
)

_current_warmup_settings: WarmupSettingsData = DEFAULT_WARMUP_SETTINGS
_last_settings_refresh: Optional[datetime] = None
WARMUP_SETTINGS_REFRESH_INTERVAL_SECONDS = 60


def get_current_warmup_settings() -> WarmupSettingsData:
    return _current_warmup_settings


def _set_current_warmup_settings(settings: WarmupSettingsData) -> None:
    global _current_warmup_settings
    _current_warmup_settings = settings


def format_warmup_settings(settings: WarmupSettingsData) -> str:
    return (
        "Текущие настройки прогрева:\n"
        f"• Лимит вступлений в день: {settings.channels_per_day}\n"
        f"• Окно вступлений: {settings.format_window()}\n"
        f"• Интервал между вступлениями (мин): {settings.delay_minutes}"
    )


def _get_delay_bounds(settings: WarmupSettingsData) -> tuple[int, int]:
    base = max(int(settings.delay_minutes), 1)
    min_delay = max(2, int(base * 0.6))
    max_delay = max(min_delay + 1, int(base * 1.8))
    return min_delay, max_delay


def _parse_time_input(value: str) -> Optional[tuple[int, int]]:
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except (ValueError, AttributeError):
        return None

    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour, minute
    return None


def _parse_window_input(value: str) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    cleaned = value.replace(" ", "")
    parts = cleaned.split("-")
    if len(parts) != 2:
        return None

    start = _parse_time_input(parts[0])
    end = _parse_time_input(parts[1])
    if start is None or end is None:
        return None

    return start, end


async def refresh_warmup_settings_from_db() -> WarmupSettingsData:
    settings = _make_warmup_settings(await get_warmup_settings())
    _set_current_warmup_settings(settings)
    global _last_settings_refresh
    _last_settings_refresh = datetime.now(timezone.utc)
    return settings


async def ensure_latest_warmup_settings(force: bool = False) -> WarmupSettingsData:
    """Периодически обновляет кеш настроек прогрева из БД."""

    global _last_settings_refresh
    now = datetime.now(timezone.utc)
    if (
        force
        or _last_settings_refresh is None
        or (now - _last_settings_refresh).total_seconds() >= WARMUP_SETTINGS_REFRESH_INTERVAL_SECONDS
    ):
        return await refresh_warmup_settings_from_db()

    return get_current_warmup_settings()


# Ограничение одновременных подключений
MAX_CONCURRENT_ACCOUNTS = 5
ACCOUNT_CHECK_INTERVAL = 60  # 1 минута для стандартных аккаунтов (было 5 минут)
WARMUP_CHECK_INTERVAL = 600   # 10 минут для warmup аккаунтов
ACCOUNT_LAUNCH_STAGGER_SECONDS = 3
ACCOUNT_LAUNCH_JITTER_SECONDS = 2
COMMENT_TO_REACTION_PAUSE_SECONDS = 1.5

account_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)


async def _delayed_safe_send_comments(
    user_id: int,
    session: str,
    account_id: int,
    delay: Optional[float] = None,
) -> None:
    pause = delay
    if pause is None:
        jitter = random.uniform(0, ACCOUNT_LAUNCH_JITTER_SECONDS)
        pause = ACCOUNT_LAUNCH_STAGGER_SECONDS + jitter

    await asyncio.sleep(max(pause, 0))
    await safe_send_comments(user_id, session, account_id)


def _schedule_safe_send_comments(
    user_id: int,
    session: str,
    account_id: int,
    *,
    delay: Optional[float] = None,
) -> None:
    asyncio.create_task(_delayed_safe_send_comments(user_id, session, account_id, delay))


def make_session_key(user_id: int, phone: str) -> str:
    return f"{user_id}:{phone}"


def _parse_warmup_datetime(value: Any) -> Optional[datetime]:
    """
    Безопасно парсит datetime из разных форматов.
    Обрабатывает строки, datetime объекты, None и другие типы.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            cleaned_value = value.replace("Z", "+00:00").replace(" ", "T")
            return datetime.fromisoformat(cleaned_value)
        except (ValueError, AttributeError) as e:
            logging.debug("Failed to parse datetime from string '%s': %s", value, e)
            return None

    logging.debug("Unsupported datetime type: %s for value: %s", type(value), value)
    return None


def _get_human_delay_seconds(settings: Optional[WarmupSettingsData] = None) -> int:
    settings = settings or get_current_warmup_settings()
    min_delay, max_delay = _get_delay_bounds(settings)
    return random.randint(min_delay * 60, max_delay * 60)


def _next_join_window_start(reference: datetime, settings: Optional[WarmupSettingsData] = None) -> datetime:
    settings = settings or get_current_warmup_settings()
    start_time = settings.window_start
    start = datetime.combine(reference.date(), start_time).replace(tzinfo=timezone.utc)

    if settings.spans_midnight:
        # Для окон через полночь следующий старт — сегодняшний вечер,
        # если мы еще не достигли его, иначе +1 день
        if reference.time() < settings.window_end and reference < start:
            return start
        if reference >= start:
            return start + timedelta(days=1)
        return start

    if start <= reference:
        start = datetime.combine(
            reference.date() + timedelta(days=1),
            start_time,
        ).replace(tzinfo=timezone.utc)
    return start


def _warmup_window_duration(settings: WarmupSettingsData) -> timedelta:
    start_dt = datetime.combine(datetime.now(timezone.utc).date(), settings.window_start)
    end_dt = datetime.combine(datetime.now(timezone.utc).date(), settings.window_end)
    duration = end_dt - start_dt
    if duration <= timedelta(0):
        duration += timedelta(days=1)
    return duration


def _resolve_window_start(reference: datetime, settings: WarmupSettingsData) -> datetime:
    start_time = settings.window_start
    base_start = datetime.combine(reference.date(), start_time).replace(tzinfo=timezone.utc)

    if settings.spans_midnight:
        if reference.time() < settings.window_end:
            return base_start - timedelta(days=1)
        if reference >= base_start:
            return base_start
        return base_start

    end_dt = datetime.combine(reference.date(), settings.window_end).replace(tzinfo=timezone.utc)
    if reference < base_start:
        # Окно сегодня еще не началось, используем начало окна сегодня (не следующий день)
        return base_start
    if reference >= end_dt:
        # Окно сегодня уже прошло, используем следующий день
        return base_start + timedelta(days=1)
    # Мы находимся в окне, возвращаем начало окна сегодня
    return base_start


def plan_next_warmup_join(earliest: datetime, settings: Optional[WarmupSettingsData] = None) -> datetime:
    settings = settings or get_current_warmup_settings()
    if earliest.tzinfo is None:
        current = earliest.replace(tzinfo=timezone.utc)
    else:
        current = earliest.astimezone(timezone.utc)

    window_duration = _warmup_window_duration(settings)

    # Определяем окно для текущего дня
    if settings.spans_midnight:
        # Для окон через полночь (например, 22:00 - 02:00)
        # Окно начинается вчера вечером и заканчивается сегодня утром
        if current.time() < settings.window_end:
            # Мы находимся в окне, которое началось вчера
            window_start = datetime.combine(current.date() - timedelta(days=1), settings.window_start).replace(tzinfo=timezone.utc)
            window_end = datetime.combine(current.date(), settings.window_end).replace(tzinfo=timezone.utc)
            if window_start <= current < window_end:
                # Мы в активном окне
                delay_seconds = _get_human_delay_seconds(settings)
                candidate = current + timedelta(seconds=delay_seconds)
                if candidate < window_end:
                    return candidate
                # Выходим за пределы окна, используем следующее окно (сегодня вечером)
                next_window_start = datetime.combine(current.date(), settings.window_start).replace(tzinfo=timezone.utc)
                delay_seconds = _get_human_delay_seconds(settings)
                return next_window_start + timedelta(seconds=delay_seconds)
            # Окно уже прошло, используем следующее (сегодня вечером)
            next_window_start = datetime.combine(current.date(), settings.window_start).replace(tzinfo=timezone.utc)
            delay_seconds = _get_human_delay_seconds(settings)
            return next_window_start + timedelta(seconds=delay_seconds)
        else:
            # Мы после окончания окна сегодня утром, но до начала окна сегодня вечером
            # Используем начало окна сегодня вечером
            next_window_start = datetime.combine(current.date(), settings.window_start).replace(tzinfo=timezone.utc)
            if current < next_window_start:
                delay_seconds = _get_human_delay_seconds(settings)
                return next_window_start + timedelta(seconds=delay_seconds)
            # Мы в окне сегодня вечером
            window_end = datetime.combine(current.date() + timedelta(days=1), settings.window_end).replace(tzinfo=timezone.utc)
            delay_seconds = _get_human_delay_seconds(settings)
            candidate = current + timedelta(seconds=delay_seconds)
            if candidate < window_end:
                return candidate
            # Выходим за пределы, используем следующее окно
            next_window_start = datetime.combine(current.date() + timedelta(days=1), settings.window_start).replace(tzinfo=timezone.utc)
            delay_seconds = _get_human_delay_seconds(settings)
            return next_window_start + timedelta(seconds=delay_seconds)
    else:
        # Обычное окно (не через полночь)
        today_start = datetime.combine(current.date(), settings.window_start).replace(tzinfo=timezone.utc)
        today_end = datetime.combine(current.date(), settings.window_end).replace(tzinfo=timezone.utc)
        
        # Если окно сегодня еще не началось, используем начало окна сегодня
        if current < today_start:
            delay_seconds = _get_human_delay_seconds(settings)
            candidate = today_start + timedelta(seconds=delay_seconds)
            if candidate < today_end:
                return candidate
        
        # Если мы находимся в окне сегодня, используем текущее время + задержка
        if today_start <= current < today_end:
            delay_seconds = _get_human_delay_seconds(settings)
            candidate = current + timedelta(seconds=delay_seconds)
            if candidate < today_end:
                return candidate
            # Если candidate выходит за пределы окна, используем начало окна на следующий день
            next_day_start = today_start + timedelta(days=1)
            delay_seconds = _get_human_delay_seconds(settings)
            return next_day_start + timedelta(seconds=delay_seconds)

        # Окно сегодня уже прошло, используем следующий день
        next_day_start = today_start + timedelta(days=1)
        delay_seconds = _get_human_delay_seconds(settings)
        return next_day_start + timedelta(seconds=delay_seconds)


def _get_next_warmup_join(now: datetime, settings: Optional[WarmupSettingsData] = None) -> datetime:
    return plan_next_warmup_join(now, settings)


def is_quiet_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в тихом периоде (загружает актуальные настройки из schedule.json)"""
    # Получаем актуальные настройки (перезагружаются каждые 60 секунд)
    start_hour, start_minute, end_hour, end_minute = _get_quiet_period_config()
    
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    start = time(start_hour, start_minute)
    end = time(end_hour, end_minute)
    
    # Обрабатываем случай, когда период переходит через полночь (21:30-04:30)
    if start > end:  # 21:30 > 04:30
        result = current_time >= start or current_time < end
    else:
        result = start <= current_time < end
    
    # Логируем для отладки (только при изменении состояния)
    if not hasattr(is_quiet_period, '_last_logged_state'):
        is_quiet_period._last_logged_state = None
    if is_quiet_period._last_logged_state != result:
        is_quiet_period._last_logged_state = result
        logging.debug(
            "Quiet period check: now=%s UTC, period=%02d:%02d-%02d:%02d UTC, result=%s",
            current_time.strftime("%H:%M:%S"),
            start_hour, start_minute,
            end_hour, end_minute,
            result
        )
    
    return result


def is_warmup_sleep_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в периоде сна для прогрева (настраивается через env)"""
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    settings = get_current_warmup_settings()
    start = settings.window_start
    end = settings.window_end

    if settings.spans_midnight:
        return current_time >= start or current_time < end

    return start <= current_time < end

def is_warmup_join_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в периоде для вступления в каналы прогрева (во время сна)"""
    now = now or datetime.now(timezone.utc)
    # Вступаем в каналы в период прогрева (12:00-19:00 UTC)
    return is_warmup_sleep_period(now)

async def check_account(user_id, phone):
    key = make_session_key(user_id, phone)
    lock = get_session_lock(key)

    logging.debug("check_account: start for user_id=%s phone=%s", user_id, phone)

    loop = asyncio.get_running_loop()
    started_at = loop.time()

    async with lock:
        lock_acquired_at = loop.time()
        logging.debug(
            "check_account: acquired lock for key=%s in %.2fs",
            key,
            lock_acquired_at - started_at,
        )
        existing_client = active_pyrogram_clients.get(key)

        if existing_client:
            session_active = active_sessions.get(key)
            is_connected = getattr(existing_client, "is_connected", False)

            if session_active:
                if not is_connected:
                    await bot.send_message(
                        user_id, f"Аккаунт {phone} сейчас используется, попробуйте позже"
                    )
                    return False

                try:
                    await existing_client.get_me()
                    return True
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower():
                        await bot.send_message(
                            user_id, f"Аккаунт {phone} сейчас используется, попробуйте позже"
                        )
                        return False
                    raise

            if is_connected and not session_active:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + CHECK_ACCOUNT_SHUTDOWN_TIMEOUT
                while getattr(existing_client, "is_connected", False):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        await bot.send_message(user_id, f"Аккаунт {phone} останавливается")
                        return False
                    await asyncio.sleep(min(CHECK_ACCOUNT_SHUTDOWN_INTERVAL, remaining))

                is_connected = getattr(existing_client, "is_connected", False)

            if not session_active and not is_connected:
                active_pyrogram_clients.pop(key, None)
                _release_session_lock(key)
                existing_client = None

        session_base = os.path.join(SESSIONS_BASE_DIR, str(user_id), str(phone))

        async def _ensure_identity(app: Client) -> bool:
            identity_loop = asyncio.get_running_loop()
            identity_started_at = identity_loop.time()
            logging.debug(
                "check_account: get_me started for user_id=%s phone=%s",
                user_id,
                phone,
            )

            try:
                await asyncio.wait_for(
                    app.get_me(),
                    timeout=CHECK_ACCOUNT_GET_ME_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logging.error(
                    "check_account: get_me timeout for user_id=%s phone=%s after %.2fs",
                    user_id,
                    phone,
                    identity_loop.time() - identity_started_at,
                )
                raise

            identity_completed_at = identity_loop.time()
            logging.debug(
                "check_account: get_me completed for user_id=%s phone=%s in %.2fs",
                user_id,
                phone,
                identity_completed_at - identity_started_at,
            )
            return True

        async def _cleanup_session_state(reason: str) -> None:
            for session_file in (f"{session_base}.session", f"{session_base}.session.session"):
                if os.path.exists(session_file):
                    try:
                        os.remove(session_file)
                        logging.warning(
                            "Removed session file %s due to %s",
                            session_file,
                            reason,
                        )
                    except OSError:
                        logging.exception(
                            "Failed to remove session file %s after %s",
                            session_file,
                            reason,
                        )

            account_row = await get_account_by_session(user_id, phone)
            if account_row:
                logging.info(
                    "Deleting account %s for user %s due to %s",
                    phone,
                    user_id,
                    reason,
                )
                await delete_account(account_row["id"], account_row["phone"])

        try:
            logging.debug(
                "check_account: creating client for user_id=%s phone=%s",
                user_id,
                phone,
            )
            client_started_at = loop.time()
            logging.debug(
                "check_account: invoking with_retry for user_id=%s phone=%s",
                user_id,
                phone,
            )
            result = await asyncio.wait_for(
                with_retry(
                    session_base,
                    _ensure_identity,
                    lock_key=key,
                    acquire_lock=False,
                    start_client=False,
                    client_kwargs={"no_updates": True},
                    connect_timeout=CHECK_ACCOUNT_CONNECT_TIMEOUT,
                    operation_timeout=CHECK_ACCOUNT_OPERATION_TIMEOUT,
                    disconnect_timeout=CHECK_ACCOUNT_DISCONNECT_TIMEOUT,
                    operation_name=f"check_account[{key}]",
                ),
                timeout=CHECK_ACCOUNT_RETRY_TIMEOUT,
            )
            client_completed_at = loop.time()
            logging.debug(
                "check_account: with_retry completed for user_id=%s phone=%s in %.2fs",
                user_id,
                phone,
                client_completed_at - client_started_at,
            )
            total_elapsed = loop.time() - started_at
            logging.debug(
                "check_account: completed successfully for user_id=%s phone=%s in %.2fs",
                user_id,
                phone,
                total_elapsed,
            )
            return result
        except asyncio.TimeoutError:
            total_elapsed = loop.time() - started_at
            logging.error(
                "Timeout while creating client for user_id=%s phone=%s (%.2fs)",
                user_id,
                phone,
                total_elapsed,
            )
            await bot.send_message(
                user_id,
                f"Не удалось запустить аккаунт {phone}: превышено время ожидания",
            )
            return False
        except OperationalError as exc:
            if "database is locked" in str(exc).lower():
                await bot.send_message(
                    user_id, f"Аккаунт {phone} сейчас используется, попробуйте позже"
                )
                return False
            raise
        except Exception as exc:
            logging.exception(
                "Unexpected error in check_account for user_id=%s phone=%s: %s",
                user_id,
                phone,
                exc,
            )

            exc_str = str(exc).lower()
            is_session_error = (
                isinstance(exc, sqlite3.DatabaseError)
                and any(marker in exc_str for marker in ("malformed", "not a database", "encrypted"))
            ) or any(
                marker in exc_str for marker in ("auth_key_unregistred", "401", "key is not registered")
            )

            if is_session_error:
                await bot.send_message(
                    user_id,
                    f"Файл сессии аккаунта {phone} поврежден или авторизация не завершена. Удалите сессию и добавьте аккаунт заново через «Добавить аккаунт».",
                )
                await _cleanup_session_state("invalid or incomplete session")
                return False

            await asyncio.sleep(1)
            await bot.send_message(user_id, f"Аккаунт удален ошибка: {str(exc)}")

            await _cleanup_session_state("unexpected check_account error")
            return False

async def main_message(message):
    user_id = message.from_user.id
    await ensure_user(user_id)
    if not os.path.isdir(SESSIONS_BASE_DIR):
        os.makedirs(SESSIONS_BASE_DIR, exist_ok=True)
    user_sessions_dir = os.path.join(SESSIONS_BASE_DIR, str(user_id))
    if not os.path.isdir(user_sessions_dir):
        os.makedirs(user_sessions_dir, exist_ok=True)

    db_accounts = await get_accounts_for_user(user_id)
    existing_accounts = {account["phone"]: account for account in db_accounts}

    for file in os.listdir(user_sessions_dir):
        if file.endswith('.session') and not file.endswith('.session.session'):
            phone = file.replace('.session', '')
            if phone not in existing_accounts:
                session_path = os.path.join(user_sessions_dir, file)
                if await _is_session_valid(session_path):
                    await ensure_account(user_id, phone, session_path)
                # иначе _is_session_valid уже удалил невалидную сессию

    db_accounts = await get_accounts_for_user(user_id)

    builder = InlineKeyboardBuilder()

    for account in db_accounts:
        call = account["phone"]
        session_file = os.path.join(user_sessions_dir, f"{call}.session")
        if not os.path.exists(session_file):
            # Проверяем также файл .session.session
            session_file_alt = os.path.join(user_sessions_dir, f"{call}.session.session")
            if not os.path.exists(session_file_alt):
                continue
            session_file = session_file_alt

        key = make_session_key(user_id, call)
        # Проверяем статус в базе данных, а не только в active_sessions
        is_running = active_sessions.get(key) or account.get("status") == "running"
        status_button_text = "Запустить" if not is_running else "Остановить"
        status_button_callback = f"start_{call}" if not is_running else f"stop_{call}"

        button_info = types.InlineKeyboardButton(text=f"{call}", callback_data=f"info_{call}")
        button_status = types.InlineKeyboardButton(text=status_button_text, callback_data=status_button_callback)
        button_delete = types.InlineKeyboardButton(text="Удалить", callback_data=f"del_{call}")
        button_warmup = types.InlineKeyboardButton(text="Прогрев", callback_data=f"warmup_{call}")
        button_reactions = types.InlineKeyboardButton(
            text="🎯 Реакции на посты и ответы", callback_data=f"reaction_{call}"
        )
        button_unsubscribe = types.InlineKeyboardButton(
            text="🚫 Отписаться", callback_data=f"unsubscribe_account_{account.get('id')}"
        )

        builder.row(button_info, button_status, button_warmup)
        builder.row(button_reactions)
        builder.row(button_unsubscribe)
        if not is_running:
            builder.row(button_delete)

    await bot.send_message(message.from_user.id, 'Ваши аккаунты', reply_markup=builder.as_markup())
    await bot.send_message(
        message.from_user.id,
        "Доступные действия",
        reply_markup=build_main_actions_keyboard(),
    )


def build_main_actions_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с основными действиями под строкой ввода."""

    keyboard = [
        [
            KeyboardButton(text="Добавить аккаунт"),
            KeyboardButton(text="Добавить прогрев"),
        ],
        [
            KeyboardButton(text="📊 Общая статистика"),
            KeyboardButton(text="⚙️ Настройки прогрева"),
        ],
        [
            KeyboardButton(text="📋 Черный список"),
            KeyboardButton(text="⚠️ Проблемные каналы"),
        ],
        [
            KeyboardButton(text="🔍 Анализ каналов"),
            KeyboardButton(text="🚫 Отписка от каналов"),
        ],
    ]

    # Кнопка перехода в лог-канал полезна только когда включены подробные
    # уведомления/логи.  Без этих режимов она путает пользователей и ломает
    # ожидаемую раскладку клавиатуры в тестах.
    if WARMUP_VERBOSE_LOGS or WARMUP_VERBOSE_NOTIFICATIONS:
        keyboard.append([KeyboardButton(text=SKIP_SUMMARY_BUTTON_TEXT)])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


async def start_add_account_flow(user_id: int, state: FSMContext, *, warmup_only: bool = False) -> None:
    """Запускает сценарий добавления аккаунта или назначения прогрева."""

    data = await state.get_data()
    prev_client = data.get("client")
    if prev_client:
        await cleanup_auth(prev_client, state)
        prev_number = data.get("number")
        if prev_number:
            session_base = os.path.join(SESSIONS_BASE_DIR, str(user_id), str(prev_number))
            for p in (f"{session_base}.session", f"{session_base}.session.session"):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        logging.info("Removed incomplete session %s (new add flow)", p)
                    except OSError:
                        pass

    await state.clear()
    await bot.send_message(user_id, 'Пришлите номер телефона\nПример: 79999999999\nДля отмены отправьте "-"')
    await state.set_state(addsession.number)
    await state.update_data({"warmup_only": warmup_only})


async def open_warmup_settings_dialog(user_id: int, state: FSMContext) -> None:
    """Открывает диалог редактирования общих настроек прогрева."""

    await state.clear()
    settings = await refresh_warmup_settings_from_db()
    prompt = (
        f"{format_warmup_settings(settings)}\n\n"
        "Введите новый лимит вступлений в день (число) или '-' чтобы оставить без изменений."
    )
    await bot.send_message(user_id, prompt)
    await state.set_state(warmupsettings.limit)


async def send_global_stats_report(user_id: int) -> None:
    """Отправляет пользователю и в лог-канал сводку по всем аккаунтам."""

    stats = await get_global_statistics()
    report = format_global_statistics_report(stats)
    await bot.send_message(user_id, report, parse_mode="Markdown")
    if user_id != log_channel:
        await bot.send_message(log_channel, report, parse_mode="Markdown")



# Проверка пароля при первом запуске
PASSWORD = env_vars.get("PASSWORD") or os.getenv("PASSWORD")

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await ensure_user(message.from_user.id)
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Введите пароль для доступа:")
        await state.set_state(AuthState.waiting_for_password)
    else:
        current_state = await state.get_state()
        if current_state and "addsession" in str(current_state):
            data = await state.get_data()
            client = data.get("client")
            number = data.get("number")
            await cleanup_auth(client, state)
            if number:
                session_base = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(number))
                for p in (f"{session_base}.session", f"{session_base}.session.session"):
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                            logging.info("Removed incomplete session %s (user returned to start)", p)
                        except OSError:
                            pass
        await main_message(message)

@dp.message(lambda message: message.text and message.text.startswith('/summary'))
async def show_account_summary(message: types.Message, state: FSMContext):
    """Показывает резюме аккаунта по номеру телефона"""
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    # Извлекаем номер телефона из команды
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /summary <номер_телефона>\nПример: /summary 79991234567")
        return
    
    phone = parts[1]
    
    # Ищем аккаунт в базе данных
    accounts = await get_accounts_for_user(message.from_user.id)
    account = None
    for acc in accounts:
        if acc.get('phone') == phone:
            account = acc
            break
    
    if not account:
        await message.answer(f"Аккаунт с номером {phone} не найден")
        return
    
    # Показываем резюме
    await send_account_summary_to_user(message.from_user.id, account['id'], phone)


@dp.message(lambda message: message.text == "Добавить аккаунт")
async def handle_add_account_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return

    await start_add_account_flow(message.from_user.id, state, warmup_only=False)


@dp.message(lambda message: message.text == "Добавить прогрев")
async def handle_add_warmup_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return

    await start_add_account_flow(message.from_user.id, state, warmup_only=True)


@dp.message(lambda message: message.text == "📊 Общая статистика")
async def handle_global_stats_button(message: types.Message, state: FSMContext):  # noqa: ARG001
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return

    await send_global_stats_report(message.from_user.id)
    await flush_skip_logs()
    await main_message(message)


@dp.message(lambda message: message.text == SKIP_SUMMARY_BUTTON_TEXT)
async def handle_skip_summary_button(message: types.Message, state: FSMContext):  # noqa: ARG001
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return

    if not skip_log_counters:
        await message.answer("Сводка пропусков пуста, в лог-канал ничего не отправлено.")
    else:
        await message.answer("Сводка пропусков отправлена в лог-канал.")

    await flush_skip_logs()
    await main_message(message)


@dp.message(lambda message: message.text == "⚙️ Настройки прогрева")
async def handle_warmup_settings_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return

    await open_warmup_settings_dialog(message.from_user.id, state)


@dp.message(lambda message: message.text == "📋 Черный список")
async def handle_blacklist_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    accounts = await get_accounts_for_user(message.from_user.id)
    if not accounts:
        await message.answer("У вас нет аккаунтов")
        return
    
    keyboard = InlineKeyboardBuilder()
    for acc in accounts:
        phone = acc.get("phone", "unknown")
        acc_id = acc.get("id")
        keyboard.button(text=f"{phone}", callback_data=f"blacklist_{acc_id}")
    keyboard.adjust(1)
    await message.answer("Выберите аккаунт для просмотра черного списка:", reply_markup=keyboard.as_markup())


@dp.message(lambda message: message.text == "⚠️ Проблемные каналы")
async def handle_problematic_channels_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    accounts = await get_accounts_for_user(message.from_user.id)
    if not accounts:
        await message.answer("У вас нет аккаунтов")
        return
    
    keyboard = InlineKeyboardBuilder()
    for acc in accounts:
        phone = acc.get("phone", "unknown")
        acc_id = acc.get("id")
        keyboard.button(text=f"{phone}", callback_data=f"problematic_{acc_id}")
    keyboard.adjust(1)
    await message.answer("Выберите аккаунт для просмотра проблемных каналов:", reply_markup=keyboard.as_markup())


@dp.message(lambda message: message.text == "🔍 Анализ каналов")
async def handle_analyze_channels_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    accounts = await get_accounts_for_user(message.from_user.id)
    if not accounts:
        await message.answer("У вас нет аккаунтов")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="📊 Анализировать все аккаунты", callback_data="analyze_all")
    keyboard.adjust(1)
    
    for acc in accounts:
        phone = acc.get("phone", "unknown")
        acc_id = acc.get("id")
        keyboard.button(text=f"📊 {phone}", callback_data=f"analyze_{acc_id}")
    keyboard.adjust(1)
    
    await message.answer("Выберите аккаунт для анализа каналов:", reply_markup=keyboard.as_markup())


@dp.message(lambda message: message.text == "🚫 Отписка от каналов")
async def handle_unsubscribe_button(message: types.Message, state: FSMContext):
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return

    accounts = await get_accounts_for_user(message.from_user.id)
    if not accounts:
        await message.answer("У вас нет аккаунтов")
        return

    if len(accounts) == 1:
        account = accounts[0]
        account_id = account.get("id")
        channels = list(set(
            (account.get("channels") or []) + (account.get("warmup_channels") or [])
        ))
        if not channels:
            await message.answer("У единственного аккаунта нет каналов в БД.")
            return
        await state.update_data({"unsubscribe_account_id": account_id})
        keyboard = InlineKeyboardBuilder()
        for channel in channels[:20]:
            keyboard.button(text=channel, callback_data=f"unsubscribe_channel_{account_id}_{channel}")
        keyboard.adjust(1)
        await message.answer("Выберите канал для отписки:", reply_markup=keyboard.as_markup())
        return

    keyboard = InlineKeyboardBuilder()
    for acc in accounts:
        phone = acc.get("phone", "unknown")
        acc_id = acc.get("id")
        keyboard.button(text=f"{phone}", callback_data=f"unsubscribe_account_{acc_id}")
    keyboard.adjust(1)
    await message.answer("Выберите аккаунт для отписки от канала:", reply_markup=keyboard.as_markup())


@dp.message(Command("testwarmup"))
async def test_warmup_command(message: Message) -> None:
    """Команда для тестирования режима прогрева"""
    try:
        settings = await ensure_latest_warmup_settings()
        now = datetime.now(timezone.utc)
        is_quiet = is_quiet_period(now)
        is_warmup_join = is_warmup_join_period(now)
        is_warmup_sleep = is_warmup_sleep_period(now)
        warmup_window = settings.format_window()

        text = f"""🕐 Текущее время UTC: {now.strftime('%H:%M:%S')}

📊 Статус периодов:
• Циркадный ритм (8:00-20:00): {'✅ АКТИВЕН' if is_quiet else '❌ Неактивен'}
• Период прогрева ({warmup_window}): {'✅ АКТИВЕН' if is_warmup_sleep else '❌ Неактивен'}
• Время добавления каналов: {'✅ АКТИВЕН' if is_warmup_join else '❌ Неактивен'}

⚙️ Настройки:
• Quiet: {QUIET_START_HOUR}:{QUIET_START_MINUTE:02d} - {QUIET_END_HOUR}:{QUIET_END_MINUTE:02d}
• Warmup: {warmup_window}
"""
        await message.answer(text)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        logging.exception("Error in test_warmup_command: %s", e)


@dp.message(Command("cleansessions"))
async def clean_sessions_command(message: Message) -> None:
    """Команда для удаления .session.session файлов"""
    try:
        user_id = message.from_user.id
        user_sessions_dir = os.path.join(SESSIONS_BASE_DIR, str(user_id))
        
        if not os.path.isdir(user_sessions_dir):
            await message.answer("❌ Директория сессий не найдена")
            return
        
        deleted_files = []
        for file in os.listdir(user_sessions_dir):
            if file.endswith('.session.session'):
                file_path = os.path.join(user_sessions_dir, file)
                try:
                    os.remove(file_path)
                    deleted_files.append(file)
                    await bot.send_message(log_channel, f"Удален файл сессии: {file}")
                except Exception as e:
                    await message.answer(f"❌ Ошибка удаления {file}: {e}")
                    return
        
        if deleted_files:
            await message.answer(f"✅ Удалено файлов: {len(deleted_files)}\n{chr(10).join(deleted_files)}")
        else:
            await message.answer("ℹ️ Файлы .session.session не найдены")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        logging.exception("Error in clean_sessions_command: %s", e)

@dp.message(Command("blacklist"))
async def blacklist_command(message: Message) -> None:
    """Команда для вывода черного списка каналов"""
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    parts = message.text.split()
    account_id = None
    
    if len(parts) > 1:
        try:
            account_id = int(parts[1])
            # Проверяем, что аккаунт принадлежит пользователю
            account = await get_account_by_id(account_id)
            if not account or account.get("user_id") != message.from_user.id:
                await message.answer("Аккаунт не найден или не принадлежит вам")
                return
        except ValueError:
            await message.answer("Использование: /blacklist [account_id]\nПример: /blacklist 123")
            return
    
    if account_id is None:
        # Показываем список аккаунтов для выбора
        accounts = await get_accounts_for_user(message.from_user.id)
        if not accounts:
            await message.answer("У вас нет аккаунтов")
            return
        
        keyboard = InlineKeyboardBuilder()
        for acc in accounts:
            phone = acc.get("phone", "unknown")
            acc_id = acc.get("id")
            keyboard.button(text=f"{phone}", callback_data=f"blacklist_{acc_id}")
        keyboard.adjust(1)
        await message.answer("Выберите аккаунт для просмотра черного списка:", reply_markup=keyboard.as_markup())
        return
    
    # Получаем черный список
    blacklist = await get_channel_blacklist(account_id)
    
    if not blacklist:
        await message.answer(f"Черный список для аккаунта {account_id} пуст")
        return
    
    # Формируем сообщение
    text = f"📋 Черный список аккаунта {account_id}:\n\n"
    for idx, item in enumerate(blacklist[:20], 1):  # Показываем первые 20
        channel_id = item.get("channel_id", "unknown")
        reason = item.get("reason", "не указана")
        created_at = item.get("created_at")
        if isinstance(created_at, str):
            date_str = created_at[:16] if len(created_at) > 16 else created_at
        else:
            date_str = str(created_at)[:16] if created_at else "неизвестно"
        
        text += f"{idx}. {channel_id}\n"
        text += f"   • Причина: {reason}\n"
        text += f"   • Дата: {date_str}\n\n"
    
    if len(blacklist) > 20:
        text += f"\n... и еще {len(blacklist) - 20} каналов"
    
    await message.answer(text)


@dp.message(Command("problematic_channels"))
async def problematic_channels_command(message: Message) -> None:
    """Команда для вывода проблемных каналов"""
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    parts = message.text.split()
    account_id = None
    
    if len(parts) > 1:
        try:
            account_id = int(parts[1])
            account = await get_account_by_id(account_id)
            if not account or account.get("user_id") != message.from_user.id:
                await message.answer("Аккаунт не найден или не принадлежит вам")
                return
        except ValueError:
            await message.answer("Использование: /problematic_channels [account_id]\nПример: /problematic_channels 123")
            return
    
    if account_id is None:
        accounts = await get_accounts_for_user(message.from_user.id)
        if not accounts:
            await message.answer("У вас нет аккаунтов")
            return
        
        keyboard = InlineKeyboardBuilder()
        for acc in accounts:
            phone = acc.get("phone", "unknown")
            acc_id = acc.get("id")
            keyboard.button(text=f"{phone}", callback_data=f"problematic_{acc_id}")
        keyboard.adjust(1)
        await message.answer("Выберите аккаунт для просмотра проблемных каналов:", reply_markup=keyboard.as_markup())
        return
    
    await message.answer("Анализирую каналы...")
    
    # Получаем отчет о проблемных каналах
    problematic = await get_problematic_channels_report(account_id)
    
    if not problematic:
        await message.answer(f"Проблемных каналов для аккаунта {account_id} не найдено")
        return
    
    # Формируем сообщение
    text = f"⚠️ Проблемные каналы аккаунта {account_id}:\n\n"
    for idx, item in enumerate(problematic[:15], 1):  # Показываем первые 15
        channel_id = item.get("channel_id", "unknown")
        reason = item.get("reason", "неизвестно")
        stats = item.get("stats", {})
        problematic_count = item.get("problematic_count", 0)
        total_posts = item.get("total_posts", 0)
        
        text += f"{idx}. {channel_id}\n"
        text += f"   • Причина: {reason}\n"
        text += f"   • Последние 10 постов: {problematic_count} проблемных, {total_posts - problematic_count} пропущено\n"
        text += f"   • Статистика: успешных={stats.get('success', 0)}, ошибок={stats.get('error', 0)}, no_comments={stats.get('no_comments', 0)}\n\n"
    
    if len(problematic) > 15:
        text += f"\n... и еще {len(problematic) - 15} каналов"
    
    await message.answer(text)


@dp.message(Command("unsubscribe"))
async def unsubscribe_command(message: Message, state: FSMContext) -> None:
    """Команда для отписки от каналов"""
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    accounts = await get_accounts_for_user(message.from_user.id)
    if not accounts:
        await message.answer("У вас нет аккаунтов")
        return
    
    keyboard = InlineKeyboardBuilder()
    for acc in accounts:
        phone = acc.get("phone", "unknown")
        acc_id = acc.get("id")
        keyboard.button(text=f"{phone}", callback_data=f"unsubscribe_account_{acc_id}")
    keyboard.adjust(1)
    await message.answer("Выберите аккаунт для отписки от канала:", reply_markup=keyboard.as_markup())


@dp.message(Command("analyze_channels"))
async def analyze_channels_command(message: Message) -> None:
    """Команда для автоматического анализа каналов и проставления отметок"""
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Сначала авторизуйтесь командой /start")
        return
    
    parts = message.text.split()
    account_id = None
    
    if len(parts) > 1:
        try:
            account_id = int(parts[1])
            account = await get_account_by_id(account_id)
            if not account or account.get("user_id") != message.from_user.id:
                await message.answer("Аккаунт не найден или не принадлежит вам")
                return
        except ValueError:
            await message.answer("Использование: /analyze_channels [account_id]\nПример: /analyze_channels 123\nИли без параметров для анализа всех аккаунтов")
            return
    
    await message.answer("Запускаю анализ каналов... Это может занять некоторое время.")
    
    try:
        result = await auto_analyze_and_mark_problematic_channels(account_id)
        
        text = "✅ Анализ завершен!\n\n"
        text += f"• Проанализировано каналов: {result.get('total_analyzed', 0)}\n"
        text += f"• Добавлено в blacklist (забаненные): {result.get('banned_channels', 0)}\n"
        text += f"• Добавлено в blacklist (без linked_chat): {result.get('no_linked_chat_channels', 0)}\n"
        text += f"• Всего добавлено: {result.get('banned_channels', 0) + result.get('no_linked_chat_channels', 0)}"
        
        await message.answer(text)
    except Exception as e:
        logging.exception("Error in analyze_channels_command")
        await message.answer(f"Ошибка при анализе: {e}")


@dp.message(Command("fixmode"))
async def fix_mode_command(message: Message) -> None:
    """Команда для переключения аккаунта в стандартный режим"""
    try:
        # Находим все аккаунты пользователя в режиме warmup
        accounts = await get_accounts_for_user(message.from_user.id)
        warmup_accounts = [acc for acc in accounts if acc.get("mode") == "warmup"]
        
        if not warmup_accounts:
            await message.answer("❌ У вас нет аккаунтов в режиме прогрева")
            return
        
        # Переключаем все аккаунты в стандартный режим
        for account in warmup_accounts:
            await set_account_mode(account["id"], "standard", warmup_days=None)
            await message.answer(f"✅ Аккаунт {account['phone']} переключен в стандартный режим")
        
        await message.answer("🎉 Все аккаунты переключены в стандартный режим! Теперь комментирование должно работать.")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        logging.exception("Error in fix_mode_command: %s", e)


@dp.message(AuthState.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    if message.text == PASSWORD:
        await ensure_user(message.from_user.id)
        await set_user_authenticated(message.from_user.id, True)
        await message.answer("Доступ разрешён!")
        await main_message(message)
    else:
        await message.answer("Неверный пароль. Попробуйте снова:")


@dp.callback_query()
async def callbacks(callback_query: types.CallbackQuery, state: FSMContext):
    call = callback_query.data or ""
    user_id = callback_query.from_user.id

    try:
        await init_db()
    except RuntimeError as exc:
        logging.warning("Database init skipped for callback: %s", exc)
        if "postgres" not in str(exc).lower():
            raise
    logging.info("Callback received: %s from user %s", call, user_id)

    if call == "reaction_apply_all":
        await state.update_data({"apply_reactions_to_all": True})
        await callback_query.answer("Применяем настройки ко всем аккаунтам")
        await _save_reaction_settings(
            callback_query.message,
            state,
            finalize=True,
            notify=False,
            user_id=user_id,
        )
        return

    try:
        await callback_query.message.delete()
    except Exception as exc:
        logging.debug("Failed to delete callback message: %s", exc)

    if call == "add_account":
        logging.info("Starting add_account flow for user %s", user_id)
        await callback_query.answer()
        await start_add_account_flow(user_id, state, warmup_only=False)
        return

    if call == "add_warmup":
        logging.info("Starting add_warmup flow for user %s", user_id)
        await callback_query.answer()
        await start_add_account_flow(user_id, state, warmup_only=True)
        return

    if call == "warmup_settings":
        logging.info("Opening warmup settings for user %s", user_id)
        await callback_query.answer()
        await open_warmup_settings_dialog(user_id, state)
        return

    if call == "global_stats":
        logging.info("Sending global stats to user %s", user_id)
        await callback_query.answer()
        await send_global_stats_report(user_id)
        await main_message(callback_query)
        return
    
    if call.startswith("blacklist_"):
        account_id = int(call.split("_")[1])
        await callback_query.answer()
        blacklist = await get_channel_blacklist(account_id)
        if not blacklist:
            await callback_query.message.answer(f"Черный список для аккаунта {account_id} пуст")
            return
        text = f"📋 Черный список аккаунта {account_id}:\n\n"
        for idx, item in enumerate(blacklist[:20], 1):
            channel_id = item.get("channel_id", "unknown")
            reason = item.get("reason", "не указана")
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                date_str = created_at[:16] if len(created_at) > 16 else created_at
            else:
                date_str = str(created_at)[:16] if created_at else "неизвестно"
            text += f"{idx}. {channel_id}\n"
            text += f"   • Причина: {reason}\n"
            text += f"   • Дата: {date_str}\n\n"
        if len(blacklist) > 20:
            text += f"\n... и еще {len(blacklist) - 20} каналов"
        await callback_query.message.answer(text)
        return
    
    if call.startswith("problematic_"):
        account_id = int(call.split("_")[1])
        await callback_query.answer("Анализирую каналы...")
        problematic = await get_problematic_channels_report(account_id)
        if not problematic:
            await callback_query.message.answer(f"Проблемных каналов для аккаунта {account_id} не найдено")
            return
        text = f"⚠️ Проблемные каналы аккаунта {account_id}:\n\n"
        for idx, item in enumerate(problematic[:15], 1):
            channel_id = item.get("channel_id", "unknown")
            reason = item.get("reason", "неизвестно")
            stats = item.get("stats", {})
            problematic_count = item.get("problematic_count", 0)
            total_posts = item.get("total_posts", 0)
            text += f"{idx}. {channel_id}\n"
            text += f"   • Причина: {reason}\n"
            text += f"   • Последние 10 постов: {problematic_count} проблемных, {total_posts - problematic_count} пропущено\n"
            text += f"   • Статистика: успешных={stats.get('success', 0)}, ошибок={stats.get('error', 0)}, no_comments={stats.get('no_comments', 0)}\n\n"
        if len(problematic) > 15:
            text += f"\n... и еще {len(problematic) - 15} каналов"
        await callback_query.message.answer(text)
        return
    
    if call.startswith("unsubscribe_account_"):
        account_id = int(call.split("_")[2])
        await callback_query.answer()
        account = await get_account_by_id(account_id)
        if not account or account.get("user_id") != user_id:
            await callback_query.message.answer("Аккаунт не найден")
            return
        channels = list(set(
            (account.get("channels") or []) + (account.get("warmup_channels") or [])
        ))
        if not channels:
            await callback_query.message.answer("У аккаунта нет каналов в БД. Добавьте каналы в настройках.")
            return
        await state.update_data({"unsubscribe_account_id": account_id})
        keyboard = InlineKeyboardBuilder()
        for channel in channels[:20]:  # Показываем первые 20
            keyboard.button(text=channel, callback_data=f"unsubscribe_channel_{account_id}_{channel}")
        keyboard.adjust(1)
        await callback_query.message.answer("Выберите канал для отписки:", reply_markup=keyboard.as_markup())
        return
    
    if call.startswith("unsubscribe_channel_"):
        parts = call.split("_")
        account_id = int(parts[2])
        channel = "_".join(parts[3:])  # Канал может содержать подчеркивания
        await callback_query.answer()
        await state.update_data({"unsubscribe_channel": channel, "unsubscribe_account_id": account_id})
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Нет чата комментариев", callback_data=f"unsubscribe_reason_{account_id}_NO_LINKED_CHAT")
        keyboard.button(text="Пользователь забанен", callback_data=f"unsubscribe_reason_{account_id}_USER_BANNED_IN_CHANNEL")
        keyboard.button(text="Комментарии ограничены", callback_data=f"unsubscribe_reason_{account_id}_COMMENTS_DISABLED")
        keyboard.button(text="Другое", callback_data=f"unsubscribe_reason_{account_id}_OTHER")
        keyboard.adjust(1)
        await callback_query.message.answer(f"Выберите причину отписки от {channel}:", reply_markup=keyboard.as_markup())
        return
    
    if call.startswith("unsubscribe_reason_"):
        parts = call.split("_")
        account_id = int(parts[2])
        reason_code = parts[3]
        await callback_query.answer()
        data = await state.get_data()
        channel = data.get("unsubscribe_channel")
        if not channel:
            await callback_query.message.answer("Ошибка: канал не найден")
            return
        account = await get_account_by_id(account_id)
        if not account or account.get("user_id") != user_id:
            await callback_query.message.answer("Аккаунт не найден")
            return
        reason_text = {
            "NO_LINKED_CHAT": "Нет чата комментариев",
            "USER_BANNED_IN_CHANNEL": "Пользователь забанен",
            "COMMENTS_DISABLED": "Комментарии ограничены",
            "OTHER": "Другое",
        }.get(reason_code, reason_code)
        # Отписываемся от канала
        try:
            session_path = account.get("session_path")
            session_file = session_path
            if not session_path.endswith(".session"):
                session_file = session_path + ".session"
            if session_path and (os.path.exists(session_path) or os.path.exists(session_file)):
                session_base = session_path.replace(".session.session", "").replace(".session", "")
                async def _unsubscribe(client: Client):
                    await client.leave_chat(channel)

                try:
                    await safe_session_operation(
                        session_base,
                        _unsubscribe,
                        operation_name="unsubscribe",
                    )
                    success = True
                    error = None
                except Exception as e:
                    success = False
                    error = str(e)
                
                if success:
                    await add_to_channel_blacklist(account_id, channel, reason_code)
                    channels = [c for c in (account.get("channels") or []) if c != channel]
                    await update_account_settings(account_id, channels=channels)
                    warmup = [c for c in (account.get("warmup_channels") or []) if c != channel]
                    if warmup != (account.get("warmup_channels") or []):
                        await sync_warmup_channels(account_id, warmup)
                    await callback_query.message.answer(f"✅ Отписались от {channel}\nПричина: {reason_text}")
                else:
                    await callback_query.message.answer(f"❌ Ошибка при отписке: {error}")
            else:
                await callback_query.message.answer("❌ Файл сессии не найден")
        except Exception as e:
            logging.exception("Error in unsubscribe")
            await callback_query.message.answer(f"❌ Ошибка: {e}")
        await state.clear()
        return

    if call.startswith("info_"):
        session = call.split("_", 1)[1]
        logging.info("Info requested for session %s by user %s", session, user_id)
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            logging.warning("Account %s not found for user %s", session, user_id)
            await bot.send_message(user_id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        account_id = account_row.get("id")
        if not account_id:
            logging.error("Account id missing for session %s user %s", session, user_id)
            await bot.send_message(
                user_id, f"Не удалось определить идентификатор аккаунта {session}"
            )
            await main_message(callback_query)
            return

        await send_account_summary_to_user(user_id, account_id, session)
        await main_message(callback_query)
        return

    if call.startswith("warmclear_"):
        session = call.split("_", 1)[1]
        logging.info("Clearing warmup queue for %s by user %s", session, user_id)
        await callback_query.answer()
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            logging.warning("Account %s not found for user %s during warmclear", session, user_id)
            await bot.send_message(user_id, "Аккаунт не найден в базе данных")
            await state.clear()
            await main_message(callback_query)
            return

        account_id = account_row["id"]

        try:
            await sync_warmup_channels(account_id, [])
            await set_account_mode(account_id, "standard", warmup_days=None)
            confirmation_text = (
                f"Очередь прогрева для {session} очищена. Аккаунт переведён в стандартный режим."
            )
        except Exception as exc:
            logging.exception("Failed to clear warmup queue for %s: %s", session, exc)
            confirmation_text = f"Не удалось очистить очередь прогрева: {exc}"

        await bot.send_message(user_id, confirmation_text)
        await state.clear()
        await main_message(callback_query)
        return

    if call.startswith("warmup_"):
        session = call.split("_", 1)[1]
        logging.info("Opening warmup manager for %s by user %s", session, user_id)
        await callback_query.answer()
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            logging.warning("Account %s not found for user %s during warmup", session, user_id)
            await bot.send_message(user_id, "Аккаунт не найден в базе данных")
            await state.clear()
            await main_message(callback_query)
            return

        account_id = account_row["id"]
        warmup_records = await get_warmup_pending(account_id, limit=100)
        warmup_list = [entry["channel"] for entry in warmup_records] if warmup_records else []
        warmup_display = await format_channels_display(
            warmup_list, "Очередь прогрева", 10, use_markdown=False
        )

        await state.update_data({"account": session, "account_id": account_id})
        await state.set_state(warmupmanage.channels)

        prompt_lines = [
            f"Аккаунт {session}",
            warmup_display,
            "",
            "Отправьте каналы для прогрева (каждый канал на новой строке).",
            "Отправьте '-' чтобы оставить очередь без изменений или воспользуйтесь кнопкой Clear для очистки и перехода в стандартный режим.",
        ]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Clear", callback_data=f"warmclear_{session}")]
            ]
        )

        await bot.send_message(
            user_id,
            "\n".join(line for line in prompt_lines if line),
            reply_markup=keyboard,
        )
        return

    if call.startswith("reaction_"):
        session = call.split("_", 1)[1]
        logging.info("Opening reaction settings for %s by user %s", session, user_id)
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            logging.warning("Account %s not found for user %s during reaction setup", session, user_id)
            await bot.send_message(user_id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        await state.clear()
        await state.update_data(
            {
                "account": session,
                "account_id": account_row["id"],
                "reaction_limit_set": False,
                "apply_reactions_to_all": False,
            }
        )
        await callback_query.answer()
        await _prompt_reaction_limit(callback_query, state)
        await state.set_state(reactionsettings.limit)
        return

    if call.startswith("start_"):
        session = call.split("_", 1)[1]
        logging.info("Starting account %s for user %s", session, user_id)
        key = make_session_key(user_id, session)
        if active_sessions.get(key):
            logging.info("Account %s already running for user %s", session, user_id)
            await callback_query.answer("Аккаунт уже запущен", show_alert=True)
            return

        if await check_account(user_id, session):
            try:
                account_row = await get_account_by_session(user_id, session)
                if not account_row:
                    logging.warning(
                        "Account %s not found for user %s during start", session, user_id
                    )
                    await bot.send_message(user_id, "Аккаунт не найден в базе данных")
                    await main_message(callback_query)
                    return

                account_id = account_row["id"]
                await state.clear()
                await state.update_data({"account": session, "account_id": account_id})

                await callback_query.answer()
                await _prompt_system_prompt(callback_query, state)
                await state.set_state(startaccount.system_prompt)
            except Exception as exc:
                logging.exception("Failed to start account %s for user %s: %s", session, user_id, exc)
                await bot.send_message(user_id, f"Ошибка: {str(exc)}")
                await main_message(callback_query)
        else:
            logging.info("Account %s failed check for user %s", session, user_id)
            await main_message(callback_query)
        return

    if call.startswith("del_"):
        session = call.split("_", 1)[1]
        logging.info("Deleting account %s for user %s", session, user_id)
        await callback_query.answer()
        key = make_session_key(user_id, session)
        if active_sessions.get(key):
            logging.info("Account %s currently active for user %s", session, user_id)
            await bot.send_message(user_id, f"Аккаунт {session} в работе")
            return

        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            logging.warning("Account %s not found for user %s during delete", session, user_id)
            await bot.send_message(user_id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        try:
            await delete_account(account_row["id"], account_row["phone"])

            session_file = os.path.join(SESSIONS_BASE_DIR, str(user_id), f"{session}.session")
            session_file_alt = os.path.join(SESSIONS_BASE_DIR, str(user_id), f"{session}.session.session")

            deleted_files = []
            if os.path.exists(session_file):
                os.remove(session_file)
                deleted_files.append(f"{session}.session")

            if os.path.exists(session_file_alt):
                os.remove(session_file_alt)
                deleted_files.append(f"{session}.session.session")

            deleted_files_str = ", ".join(deleted_files) if deleted_files else "нет файлов"
            await bot.send_message(
                log_channel,
                f"Аккаунт {session} удален. Удалены файлы: {deleted_files_str}",
            )
            await main_message(callback_query)
        except Exception as exc:
            logging.exception("Failed to delete account %s for user %s: %s", session, user_id, exc)
            await bot.send_message(user_id, f"Ошибка: {str(exc)}")
            await main_message(callback_query)
        return

    if call.startswith("stop_"):
        session = call.split("_", 1)[1]
        logging.info("Stopping account %s for user %s", session, user_id)
        await callback_query.answer()
        key = make_session_key(user_id, session)

        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            logging.warning("Account %s not found for user %s during stop", session, user_id)
            await bot.send_message(user_id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        is_running = active_sessions.get(key) or account_row.get("status") == "running"
        if not is_running:
            logging.info("Account %s already stopped for user %s", session, user_id)
            await bot.send_message(user_id, f"Аккаунт {session} не запущен")
            await main_message(callback_query)
            return

        try:
            active_sessions.pop(key, None)
            account_id = active_account_ids.pop(key, None)
            logging.debug(
                "Removed active session for %s (account_id=%s) user %s", session, account_id, user_id
            )

            await mark_account_stopped(account_row["id"])

            await bot.send_message(user_id, f"Аккаунт {session} остановлен")
            await bot.send_message(log_channel, f"Аккаунт {session} остановлен")
            await main_message(callback_query)
        except Exception as exc:
            logging.exception("Failed to stop account %s for user %s: %s", session, user_id, exc)
            await bot.send_message(user_id, f"Ошибка: {str(exc)}")
            await main_message(callback_query)
        return

    logging.warning("Unhandled callback received: %s from user %s", call, user_id)
    await callback_query.answer("Неизвестная команда", show_alert=True)

@dp.message(warmupsettings.limit)
async def process_warmup_limit(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text and text != '-':
        if not text.isdigit() or int(text) <= 0:
            await message.answer("Введите положительное число или '-' для пропуска.")
            return
        await state.update_data({"channels_per_day": int(text)})

    settings = get_current_warmup_settings()
    await message.answer(
        f"Текущее окно вступлений: {settings.format_window()}\n"
        "Введите новое окно в формате HH:MM-HH:MM или '-' чтобы оставить без изменений."
    )
    await state.set_state(warmupsettings.window)


@dp.message(warmupsettings.window)
async def process_warmup_window(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text and text != '-':
        parsed = _parse_window_input(text)
        if not parsed:
            await message.answer("Неверный формат. Используйте HH:MM-HH:MM или '-' для пропуска.")
            return
        (start_hour, start_minute), (end_hour, end_minute) = parsed
        await state.update_data(
            {
                "join_start_hour": start_hour,
                "join_start_minute": start_minute,
                "join_end_hour": end_hour,
                "join_end_minute": end_minute,
            }
        )

    settings = get_current_warmup_settings()
    await message.answer(
        f"Текущий интервал между вступлениями: {settings.delay_minutes} минут\n"
        "Введите новый интервал (в минутах) или '-' чтобы оставить без изменений."
    )
    await state.set_state(warmupsettings.interval)


@dp.message(warmupsettings.interval)
async def process_warmup_interval(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text and text != '-':
        if not text.isdigit() or int(text) <= 0:
            await message.answer("Введите положительное число или '-' для пропуска.")
            return
        await state.update_data({"delay_minutes": int(text)})

    data = await state.get_data()
    updates: Dict[str, Any] = {}
    for key in ("channels_per_day", "delay_minutes", "join_start_hour", "join_start_minute", "join_end_hour", "join_end_minute"):
        if key in data:
            updates[key] = data[key]

    if updates:
        await update_warmup_settings(**updates)
        new_settings = await refresh_warmup_settings_from_db()
        summary = format_warmup_settings(new_settings)
        await message.answer(f"✅ Настройки прогрева обновлены.\n\n{summary}")
    else:
        await message.answer("Настройки прогрева не изменены.")

    await state.clear()
    await main_message(message)


@dp.message(warmupmanage.channels)
async def manage_warmup_channels(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    session = data.get("account")
    account_id = data.get("account_id")

    if not account_id or not session:
        await message.answer("Не удалось определить аккаунт для управления прогревом. Попробуйте ещё раз.")
        await state.clear()
        await main_message(message)
        return

    incoming = (message.text or "").strip()
    if not incoming:
        await message.answer(
            "Пришлите список каналов, '-' чтобы оставить очередь и при наличии каналов перезапустить прогрев, или воспользуйтесь кнопкой Clear для очистки."
        )
        return

    normalized = incoming.lower()
    if incoming == "-":
        channels: Optional[List[str]] = None
    elif normalized == "clear":
        channels = []
    else:
        channels = [line.strip() for line in incoming.splitlines() if line.strip()]

    unique_channels: Optional[List[str]]
    if channels is None:
        unique_channels = None
    else:
        seen: Set[str] = set()
        deduped: List[str] = []
        for channel in channels:
            if channel not in seen:
                seen.add(channel)
                deduped.append(channel)
        unique_channels = deduped

    try:
        if unique_channels is None:
            result_text = "Очередь прогрева не изменена."
        else:
            await sync_warmup_channels(account_id, unique_channels)
            if unique_channels:
                result_text = f"Очередь прогрева обновлена. Запланировано {len(unique_channels)} каналов."
            else:
                result_text = "Очередь прогрева очищена. Аккаунт переведён в стандартный режим."
    except Exception as exc:
        await message.answer(f"Ошибка при обновлении каналов прогрева: {exc}")
        await state.clear()
        await main_message(message)
        return

    updated_records = await get_warmup_pending(account_id, limit=100)
    if updated_records:
        warmup_settings = await ensure_latest_warmup_settings()
        await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
        next_join = plan_next_warmup_join(datetime.now(timezone.utc), warmup_settings)
        await db_update_warmup_schedule(account_id, next_join=next_join)
    else:
        await set_account_mode(account_id, "standard", warmup_days=None)
    updated_list = [entry["channel"] for entry in updated_records] if updated_records else []
    display = await format_channels_display(
        updated_list, "Очередь прогрева", 10, use_markdown=False
    )

    await message.answer(f"{result_text}\n\n{display}")

    await state.clear()
    await main_message(message)


async def _load_account_data(state: FSMContext) -> Tuple[Dict[str, Any], Optional[int], Optional[Dict[str, Any]]]:
    data = await state.get_data()
    account_id = data.get("account_id")
    account = await get_account_by_id(account_id) if account_id else None
    return data, account_id, account


def _format_chance(value: Optional[Union[int, str]]) -> str:
    if value is None:
        return "не задан"
    try:
        return f"{int(value)}%"
    except (TypeError, ValueError):
        return str(value)


def _format_reaction_limit(value: Optional[Union[int, str]]) -> str:
    if value is None or value == "":
        return "не задан"
    return str(value)


async def _prompt_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)
    stored = account.get("chance") if account else None
    if stored is None:
        stored = data.get("chance")

    display = _format_chance(stored)
    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий шанс реакции (комментирования): {display}.\n"
            "Отправьте значение от 0 до 100 или '-' для сохранения текущего."
        ),
    )


async def _prompt_system_prompt(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)
    stored = account.get("system_prompt") if account else None
    if stored is None:
        stored = data.get("system_prompt")

    display = stored if stored else "не задан"
    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий системный промт: {display}.\n"
            "Отправьте новое значение или '-' для сохранения текущего."
        ),
    )


def _format_sleep_range(sleep_min: Optional[int], sleep_max: Optional[int]) -> Optional[str]:
    if sleep_min is None or sleep_max is None:
        return None
    return f"{sleep_min}-{sleep_max}"


async def _prompt_sleeps(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_range: Optional[str] = None
    if account:
        sleep_min = account.get("sleep_min")
        sleep_max = account.get("sleep_max")
        stored_range = _format_sleep_range(sleep_min, sleep_max)

    if stored_range is None:
        stored_range = data.get("sleeps")

    display = stored_range if stored_range else "не заданы"
    await bot.send_message(
        message.from_user.id,
        (
            f"Текущая задержка перед комментарием: {display}.\n"
            "Отправьте диапазон в формате 10-20 или '-' для сохранения текущего."
        ),
    )


async def _prompt_reaction_limit(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored: Optional[Union[int, str]] = None
    if data.get("reaction_limit_set"):
        stored = data.get("reaction_limit")
    elif account:
        stored = account.get("reaction_limit_per_message")
        if stored is None:
            stored = data.get("reaction_limit")
    else:
        stored = data.get("reaction_limit")

    display = _format_reaction_limit(stored)
    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий лимит реакций на пост: {display}.\n"
            "Отправьте целое число ≥ 0 или '-' для сохранения текущего. Значение 0 отключит реакции, 'none' — снимет лимит."
        ),
    )


async def _prompt_post_reaction_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)
    stored = account.get("reaction_chance") if account else None
    if stored is None:
        stored = data.get("reaction_chance")

    display = _format_chance(stored) if stored is not None else "0% (по умолчанию)"
    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий шанс реакций на посты: {display}.\n"
            "Отправьте число от 0 до 100 или '-' для сохранения текущего значения."
        ),
    )


async def _prompt_discussion_reaction_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored = account.get("reaction_discussion_chance") if account else None
    if stored is None and data is not None:
        stored = data.get("reaction_discussion_chance")

    if stored is None:
        display = "не задан (обсуждения выключены)"
    else:
        display = _format_chance(stored)

    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий шанс реакций в обсуждениях: {display}.\n"
            "Отправьте число от 0 до 100, '-' чтобы оставить текущее значение или 'none'/'off' чтобы отключить реакции в обсуждениях."
        ),
    )


async def _prompt_discussion_reply_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored = account.get("discussion_reply_chance") if account else None
    if stored is None and data is not None:
        stored = data.get("discussion_reply_chance")

    if stored is None:
        display = "не задан (обсуждения выключены)"
    else:
        display = _format_chance(stored)

    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий шанс текстовых ответов в обсуждениях: {display}.\n"
            "Отправьте число от 0 до 100, '-' чтобы оставить текущее значение или 'none'/'off' чтобы отключить текстовые ответы."
        ),
    )


async def _prompt_discussion_reply_prompt(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored = account.get("discussion_reply_prompt") if account else None
    if stored is None and data is not None:
        stored = data.get("discussion_reply_prompt")

    if stored:
        display = stored
    else:
        display = "не задан (обсуждения выключены)"

    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий промт для ответов в обсуждениях: {display}.\n"
            "Отправьте новый промт, '-' чтобы оставить текущее значение или 'none'/'off' чтобы отключить ответы в обсуждениях."
        ),
    )


@dp.message(reactionsettings.limit)
async def add_reaction_limit(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    if data.get("reaction_limit_set"):
        stored_limit: Optional[Union[int, str]] = data.get("reaction_limit")
    elif account:
        stored_limit = account.get("reaction_limit_per_message")
    else:
        stored_limit = data.get("reaction_limit")

    incoming_raw = message.text or ""
    incoming = incoming_raw.strip()

    if incoming == "-":
        await state.update_data(
            {
                "reaction_limit": stored_limit,
                "reaction_limit_set": False,
            }
        )
        await _prompt_post_reaction_chance(message, state)
        await state.set_state(reactionsettings.post_chance)
        return

    lowered = incoming.lower()
    if lowered in {"none", "нет", "no", "off"}:
        await state.update_data(
            {
                "reaction_limit": None,
                "reaction_limit_set": True,
            }
        )
        await _prompt_post_reaction_chance(message, state)
        await state.set_state(reactionsettings.post_chance)
        return

    try:
        limit_value = int(incoming)
    except ValueError:
        current_display = _format_reaction_limit(stored_limit)
        await bot.send_message(
            message.from_user.id,
            (
                f"Некорректное значение. Текущий лимит: {current_display}.\n"
                "Отправьте целое число ≥ 0, '-' для сохранения текущего или 'none' для отключения лимита."
            ),
        )
        await _prompt_reaction_limit(message, state)
        return

    if limit_value < 0:
        current_display = _format_reaction_limit(stored_limit)
        await bot.send_message(
            message.from_user.id,
            (
                f"Лимит не может быть отрицательным. Текущий лимит: {current_display}.\n"
                "Укажите число ≥ 0, '-' для сохранения текущего или 'none' для отключения лимита."
            ),
        )
        await _prompt_reaction_limit(message, state)
        return

    await state.update_data(
        {
            "reaction_limit": limit_value,
            "reaction_limit_set": True,
        }
    )
    await _prompt_post_reaction_chance(message, state)
    await state.set_state(reactionsettings.post_chance)


async def _prompt_reaction_sleeps(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_range: Optional[str] = None
    if account:
        reaction_min = account.get("reaction_sleep_min")
        reaction_max = account.get("reaction_sleep_max")
        stored_range = _format_sleep_range(reaction_min, reaction_max)

    if stored_range is None:
        stored_range = data.get("reaction_sleeps")

    if stored_range:
        display = stored_range
        suffix = ""
    else:
        display = "не заданы"
        suffix = " (используются задержки комментирования)"

    await bot.send_message(
        message.from_user.id,
        (
            f"Текущая задержка перед реакцией: {display}{suffix}.\n"
            "Отправьте диапазон в формате 5-15 или '-' для сохранения текущего."
        ),
    )


async def _prompt_reaction_emojis(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_emojis: Optional[List[str]] = None
    if account:
        stored_emojis = account.get("reaction_emojis")
    if stored_emojis is None:
        stored_emojis = data.get("reaction_emojis")

    if stored_emojis:
        display = " ".join(stored_emojis)
    elif stored_emojis == []:
        display = "не заданы"
    else:
        display = "не заданы (по умолчанию)"
    await bot.send_message(
        message.from_user.id,
        (
            f"Текущий набор эмодзи для реакций: {display}.\n"
            "Отправьте эмодзи через пробел или в столбик. Используйте '-' для сохранения текущего."
        ),
    )


async def _prompt_reaction_apply_all(message: Message) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="Применить всем аккаунтам", callback_data="reaction_apply_all")
    builder.adjust(1)

    await bot.send_message(
        message.from_user.id,
        (
            "Настройки реакций сохранены для выбранного аккаунта.\n"
            "Нажмите кнопку ниже, чтобы применить их ко всем аккаунтам,"
            " или отправьте '-' для завершения без применения ко всем."
        ),
        reply_markup=builder.as_markup(),
    )


async def _prepare_regular_channels_prompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    session = data.get("account")
    account_id = data.get("account_id")

    channels: List[str] = []
    seen_channels: Set[str] = set()

    session_name = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(session))
    key = make_session_key(message.from_user.id, str(session))

    async def _collect_channels(app: Client) -> List[str]:
        collected: List[str] = []
        async for dialog in app.get_dialogs():
            chat = dialog.chat
            if str(chat.type) == "ChatType.CHANNEL" and chat.username is not None:
                channel_handle = f"@{chat.username}"
                if channel_handle not in seen_channels:
                    collected.append(channel_handle)
        return collected

    if await check_account(message.from_user.id, session):
        fetched = await with_retry(
            session_name,
            _collect_channels,
            lock_key=key,
        )
        for handle in fetched:
            if handle not in seen_channels:
                channels.append(handle)
                seen_channels.add(handle)

        if account_id:
            await update_account_settings(account_id, channels=channels)

        channels_display = await format_channels_display(
            channels, "Аккаунт подписан на каналы", 10, use_markdown=False
        )
        await bot.send_message(
            message.from_user.id,
            f'{channels_display}\n\nПришлите каналы на которые нужно подписаться\n(если не нужно пришлите -)'
        )
        await state.set_state(startaccount.regular_channels)
    else:
        await state.clear()
        await main_message(message)


@dp.message(reactionsettings.post_chance)
async def add_post_reaction_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored: Optional[Union[int, str]] = None
    if account:
        stored = account.get("reaction_chance")
    if stored is None:
        stored = data.get("reaction_chance")

    incoming = (message.text or "").strip()

    def _parse_value(raw: Union[str, int, None]) -> int:
        value = int(raw)
        if value < 0 or value > 100:
            raise ValueError
        return value

    if incoming == "-":
        if stored is None or stored == "":
            await bot.send_message(
                message.from_user.id,
                "Значение не задано. Укажите число от 0 до 100.",
            )
            await _prompt_post_reaction_chance(message, state)
            return
        try:
            chance_value = _parse_value(stored)
        except (TypeError, ValueError):
            await bot.send_message(
                message.from_user.id,
                "Не удалось определить сохранённый шанс. Укажите число от 0 до 100.",
            )
            await _prompt_post_reaction_chance(message, state)
            return
    else:
        if not incoming.isdigit():
            current_display = _format_chance(stored)
            await bot.send_message(
                message.from_user.id,
                (
                    f"Некорректное значение. Текущий шанс реакций на посты: {current_display}.\n"
                    "Отправьте число от 0 до 100 или '-' для сохранения текущего."
                ),
            )
            await _prompt_post_reaction_chance(message, state)
            return
        try:
            chance_value = _parse_value(incoming)
        except ValueError:
            current_display = _format_chance(stored)
            await bot.send_message(
                message.from_user.id,
                (
                    f"Шанс должен быть от 0 до 100. Текущее значение: {current_display}."
                ),
            )
            await _prompt_post_reaction_chance(message, state)
            return

    await state.update_data({"reaction_chance": chance_value})

    await _prompt_discussion_reaction_chance(message, state)
    await state.set_state(reactionsettings.discussion_reaction_chance)


@dp.message(reactionsettings.discussion_reaction_chance)
async def add_discussion_reaction_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored: Optional[Union[int, str]] = None
    if account:
        stored = account.get("reaction_discussion_chance")
    if stored is None:
        stored = data.get("reaction_discussion_chance")

    incoming = (message.text or "").strip()
    incoming_lower = incoming.lower()

    if incoming == "-":
        await state.update_data({"reaction_discussion_chance": stored})
    elif incoming_lower in {"none", "off"}:
        await state.update_data({"reaction_discussion_chance": None})
    else:
        if not incoming.isdigit():
            current_display = _format_chance(stored)
            await bot.send_message(
                message.from_user.id,
                (
                    f"Некорректное значение. Текущий шанс реакций в обсуждениях: {current_display}.\n"
                    "Отправьте число от 0 до 100, '-' чтобы оставить текущее значение или 'none'/'off' чтобы отключить реакции."
                ),
            )
            await _prompt_discussion_reaction_chance(message, state)
            return
        value = int(incoming)
        if value < 0 or value > 100:
            current_display = _format_chance(stored)
            await bot.send_message(
                message.from_user.id,
                (
                    f"Шанс должен быть от 0 до 100. Текущее значение: {current_display}."
                ),
            )
            await _prompt_discussion_reaction_chance(message, state)
            return
        await state.update_data({"reaction_discussion_chance": value})

    await _prompt_discussion_reply_chance(message, state)
    await state.set_state(reactionsettings.discussion_reply_chance)


@dp.message(reactionsettings.discussion_reply_chance)
async def add_discussion_reply_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored: Optional[Union[int, str]] = None
    if account:
        stored = account.get("discussion_reply_chance")
    if stored is None:
        stored = data.get("discussion_reply_chance")

    incoming = (message.text or "").strip()
    incoming_lower = incoming.lower()

    if incoming == "-":
        await state.update_data({"discussion_reply_chance": stored})
    elif incoming_lower in {"none", "off"}:
        await state.update_data({"discussion_reply_chance": None})
    else:
        if not incoming.isdigit():
            current_display = _format_chance(stored)
            await bot.send_message(
                message.from_user.id,
                (
                    f"Некорректное значение. Текущий шанс ответов в обсуждениях: {current_display}.\n"
                    "Отправьте число от 0 до 100, '-' чтобы оставить текущее значение или 'none'/'off' чтобы отключить ответы."
                ),
            )
            await _prompt_discussion_reply_chance(message, state)
            return
        value = int(incoming)
        if value < 0 or value > 100:
            current_display = _format_chance(stored)
            await bot.send_message(
                message.from_user.id,
                (
                    f"Шанс должен быть от 0 до 100. Текущее значение: {current_display}."
                ),
            )
            await _prompt_discussion_reply_chance(message, state)
            return
        await state.update_data({"discussion_reply_chance": value})

    await _prompt_discussion_reply_prompt(message, state)
    await state.set_state(reactionsettings.discussion_prompt)


@dp.message(reactionsettings.discussion_prompt)
async def add_discussion_reply_prompt(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored: Optional[str] = None
    if account:
        stored = account.get("discussion_reply_prompt")
    if stored is None:
        stored = data.get("discussion_reply_prompt")

    incoming = (message.text or "").strip()
    incoming_lower = incoming.lower()

    if incoming == "-":
        prompt_value = stored
    elif incoming_lower in {"none", "off"}:
        prompt_value = None
    elif not incoming:
        current_display = stored if stored else "не задан"
        await bot.send_message(
            message.from_user.id,
            (
                f"Промт не может быть пустым. Текущее значение: {current_display}."
            ),
        )
        await _prompt_discussion_reply_prompt(message, state)
        return
    else:
        prompt_value = incoming

    await state.update_data({"discussion_reply_prompt": prompt_value})

    await _prompt_reaction_sleeps(message, state)
    await state.set_state(reactionsettings.sleeps)


@dp.message(reactionsettings.sleeps)
async def add_reaction_sleeps(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_range: Optional[str] = None
    if account:
        reaction_min = account.get("reaction_sleep_min")
        reaction_max = account.get("reaction_sleep_max")
        stored_range = _format_sleep_range(reaction_min, reaction_max)
    if stored_range is None:
        stored_range = data.get("reaction_sleeps")

    incoming = (message.text or "").strip()

    if incoming == "-":
        await state.update_data({"reaction_sleeps": stored_range})
    else:
        parsed_range = _parse_sleep_range_input(incoming)
        if not parsed_range:
            current_display = stored_range if stored_range else "не заданы"
            await bot.send_message(
                message.from_user.id,
                (
                    f"Неверный формат задержки реакции: {current_display}.\n"
                    "Отправьте диапазон в формате 5-15 или '-' для сохранения текущего."
                ),
            )
            await _prompt_reaction_sleeps(message, state)
            return

        reaction_min, reaction_max = parsed_range
        await state.update_data({"reaction_sleeps": f"{reaction_min}-{reaction_max}"})

    await _prompt_reaction_emojis(message, state)
    await state.set_state(reactionsettings.emojis)


@dp.message(reactionsettings.emojis)
async def add_reaction_emojis(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_emojis: Optional[List[str]] = None
    if account:
        stored_emojis = account.get("reaction_emojis")
    if stored_emojis is None:
        stored_emojis = data.get("reaction_emojis")

    incoming = (message.text or "").strip()

    if incoming == "-":
        emoji_list = stored_emojis
    else:
        normalized = incoming.replace("\n", " ").replace(",", " ")
        tokens = [token for token in normalized.split(" ") if token]
        unique_emojis: List[str] = []
        seen: Set[str] = set()
        for token in tokens:
            if token not in seen:
                unique_emojis.append(token)
                seen.add(token)

        if not unique_emojis:
            await bot.send_message(
                message.from_user.id,
                "Не удалось распознать эмодзи. Отправьте эмодзи через пробел или '-' для сохранения текущего набора.",
            )
            await _prompt_reaction_emojis(message, state)
            return

        emoji_list = unique_emojis

    await state.update_data({"reaction_emojis": emoji_list, "apply_reactions_to_all": False})

    await _save_reaction_settings(message, state, finalize=False)
    await _prompt_reaction_apply_all(message)
    await state.set_state(reactionsettings.apply_all)


@dp.message(reactionsettings.apply_all)
async def finish_reaction_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await main_message(message)


async def _save_reaction_settings(
    message: Message,
    state: FSMContext,
    *,
    finalize: bool = True,
    notify: bool = True,
    user_id: Optional[int] = None,
) -> None:
    data, account_id, _ = await _load_account_data(state)
    session = data.get("account") if data else None

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    from_user = getattr(message, "from_user", None)
    message_user_id = getattr(from_user, "id", None)
    target_user_id = user_id if user_id is not None else message_user_id

    if chat_id is None and target_user_id is not None:
        chat_id = target_user_id

    if not account_id:
        if chat_id is not None:
            await bot.send_message(chat_id, "Ошибка: аккаунт не найден. Попробуйте снова.")
        await state.clear()
        await main_message(message)
        return

    reaction_sleep_min = reaction_sleep_max = None
    reaction_range_raw = data.get("reaction_sleeps") if data else None
    if reaction_range_raw:
        parsed = _parse_sleep_range_input(str(reaction_range_raw))
        if parsed:
            reaction_sleep_min, reaction_sleep_max = parsed

    update_kwargs: Dict[str, Any] = {}

    if data and "reaction_chance" in data:
        update_kwargs["reaction_chance"] = data.get("reaction_chance")
    if data and "reaction_discussion_chance" in data:
        update_kwargs["reaction_discussion_chance"] = data.get("reaction_discussion_chance")
    if data and "discussion_reply_chance" in data:
        update_kwargs["discussion_reply_chance"] = data.get("discussion_reply_chance")
    if data and "discussion_reply_prompt" in data:
        update_kwargs["discussion_reply_prompt"] = data.get("discussion_reply_prompt")
    if reaction_sleep_min is not None:
        update_kwargs["reaction_sleep_min"] = reaction_sleep_min
    if reaction_sleep_max is not None:
        update_kwargs["reaction_sleep_max"] = reaction_sleep_max
    if data and "reaction_emojis" in data:
        update_kwargs["reaction_emojis"] = data.get("reaction_emojis")

    if data.get("reaction_limit_set"):
        update_kwargs["reaction_limit_per_message"] = data.get("reaction_limit")

    await update_account_settings(account_id, **update_kwargs)

    if data.get("apply_reactions_to_all"):
        bulk_kwargs = dict(update_kwargs)
        bulk_user_id = target_user_id if target_user_id is not None else chat_id
        if bulk_user_id is not None:
            await bulk_update_reaction_settings(bulk_user_id, **bulk_kwargs)
        if chat_id is not None:
            await bot.send_message(
                chat_id,
                "Настройки реакций применены ко всем вашим аккаунтам.",
            )
        await bot.send_message(
            log_channel,
            f"Аккаунт {session}: настройки реакций применены ко всем аккаунтам пользователя.",
        )

    if notify:
        if chat_id is not None:
            await bot.send_message(chat_id, "Настройки реакций сохранены.")
        if session:
            await bot.send_message(
                log_channel,
                f"Аккаунт {session}: настройки реакций обновлены.",
            )

    if finalize:
        await state.clear()
        if target_user_id is not None:
            await main_message(SimpleNamespace(from_user=SimpleNamespace(id=target_user_id)))
        else:
            await main_message(message)


def _parse_sleep_range_input(text: str) -> Optional[Tuple[int, int]]:
    parts = [part.strip() for part in text.split('-')]
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None

    start, end = int(parts[0]), int(parts[1])
    if start < 0 or end < 0 or start > end:
        return None

    return start, end


@dp.message(startaccount.chance)
async def add_chance(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_chance: Optional[Union[int, str]] = None
    if account:
        stored_chance = account.get("chance")
    if stored_chance is None:
        stored_chance = data.get("chance")

    incoming = (message.text or "").strip()

    if incoming == "-":
        if stored_chance is None:
            await bot.send_message(
                message.from_user.id,
                "Текущий шанс комментирования не задан. Укажите значение от 0 до 100.",
            )
            await _prompt_chance(message, state)
            return
        try:
            chance_value = int(stored_chance)
        except (TypeError, ValueError):
            await bot.send_message(
                message.from_user.id,
                "Не удалось определить сохранённый шанс. Введите значение от 0 до 100.",
            )
            await _prompt_chance(message, state)
            return
    elif incoming.isdigit():
        chance_value = int(incoming)
        if chance_value < 0 or chance_value > 100:
            await bot.send_message(
                message.from_user.id,
                "Шанс должен быть в диапазоне от 0 до 100.",
            )
            await _prompt_chance(message, state)
            return
    else:
        current_display = _format_chance(stored_chance)
        await bot.send_message(
            message.from_user.id,
            (
                f"Некорректное значение. Текущий шанс комментирования: {current_display}.\n"
                "Отправьте число от 0 до 100 или '-' для сохранения текущего."
            ),
        )
        await _prompt_chance(message, state)
        return

    await state.update_data({"chance": chance_value})

    await _prepare_regular_channels_prompt(message, state)
        


@dp.message(startaccount.system_prompt)
async def add_systemprompt(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_system_prompt = None
    if account:
        stored_system_prompt = account.get("system_prompt")
    if stored_system_prompt is None:
        stored_system_prompt = data.get("system_prompt")

    incoming = (message.text or "").strip()
    if not incoming:
        await bot.send_message(
            message.from_user.id,
            "Системный промт не может быть пустым. Укажите текст или '-' для сохранения текущего.",
        )
        await _prompt_system_prompt(message, state)
        return

    if incoming == "-":
        if not stored_system_prompt:
            await bot.send_message(
                message.from_user.id,
                "Текущий системный промт не задан. Отправьте новый текст.",
            )
            await _prompt_system_prompt(message, state)
            return
        system_prompt_value = stored_system_prompt
    else:
        system_prompt_value = incoming

    await state.update_data({"system_prompt": system_prompt_value})

    await _prompt_sleeps(message, state)
    await state.set_state(startaccount.sleeps)



@dp.message(startaccount.sleeps)
async def add_sleeps(message: Message, state: FSMContext) -> None:
    data, account_id, account = await _load_account_data(state)

    stored_sleeps: Optional[str] = None
    if account:
        sleep_min = account.get("sleep_min")
        sleep_max = account.get("sleep_max")
        stored_sleeps = _format_sleep_range(sleep_min, sleep_max)
    if stored_sleeps is None:
        stored_sleeps = data.get("sleeps")

    incoming = (message.text or "").strip()

    if incoming == "-":
        if not stored_sleeps:
            await bot.send_message(
                message.from_user.id,
                "Текущая задержка перед комментарием не задана. Отправьте диапазон в формате 10-20.",
            )
            await _prompt_sleeps(message, state)
            return
        await state.update_data({"sleeps": stored_sleeps})
    else:
        parsed_range = _parse_sleep_range_input(incoming)
        if not parsed_range:
            current_display = stored_sleeps if stored_sleeps else "не заданы"
            await bot.send_message(
                message.from_user.id,
                (
                    f"Неверный формат. Текущая задержка: {current_display}.\n"
                    "Отправьте диапазон в формате 10-20 или '-' для сохранения текущего."
                ),
            )
            await _prompt_sleeps(message, state)
            return

        sleep_min, sleep_max = parsed_range
        await state.update_data({"sleeps": f"{sleep_min}-{sleep_max}"})

    await _prompt_chance(message, state)
    await state.set_state(startaccount.chance)


@dp.message(startaccount.channels)
async def add_channels(message: Message, state: FSMContext) -> None:
    
    state_data = await state.get_data()
    session = state_data.get("account")
    sleeps = state_data.get("sleeps")
    system_prompt = state_data.get("system_prompt")
    chance = state_data.get("chance")
    account_id = state_data.get("account_id")
    warmup_channels = []
    channels = []

    if str(message.text) != '-':
        channels = str(message.text).splitlines()

        session_name = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(session))
        key = make_session_key(message.from_user.id, str(session))

        async def _manage_channels(app: Client) -> None:
            for chl in channels:
                await asyncio.sleep(random.uniform(20, 30))

                if chl.startswith('-'):
                    channel_name = chl.replace('-', '')

                    try:
                        chat = await app.get_chat(channel_name)
                        await app.leave_chat(chat.id)
                        await bot.send_message(
                            log_channel,
                            f'Аккаунт {session} вышел из канала: {channel_name}',
                        )

                        if chat.linked_chat:
                            try:
                                await app.leave_chat(chat.linked_chat.id)
                            except Exception:
                                pass
                    except Exception as e:
                        await bot.send_message(
                            log_channel,
                            f'Ошибка при выходе из канала {channel_name}: {e}',
                        )

                else:
                    try:
                        await join_channel(
                            chl, account_id, session, message.from_user.id, is_warmup=False
                        )
                    except TransientJoinError as transient_error:
                        await bot.send_message(
                            log_channel,
                            f"Предупреждение: временная ошибка вступления в канал {chl}: {transient_error.message}",
                        )
                    except Exception as exc:
                        await bot.send_message(
                            log_channel,
                            f"Ошибка при вступлении в канал {chl}: {exc}",
                        )

        if await check_account(message.from_user.id, session):
            await with_retry(
                session_name,
                _manage_channels,
                lock_key=key,
            )
        else:
            await state.clear()
            await main_message(message)
            return

    # Сохраняем все настройки в базу данных
    sleep_min, sleep_max = None, None
    if sleeps and '-' in sleeps:
        try:
            sleep_parts = sleeps.split('-')
            if len(sleep_parts) == 2:
                sleep_min = int(sleep_parts[0])
                sleep_max = int(sleep_parts[1])
        except ValueError:
            pass
    
    update_kwargs: Dict[str, Any] = {}
    if sleep_min is not None:
        update_kwargs["sleep_min"] = sleep_min
    if sleep_max is not None:
        update_kwargs["sleep_max"] = sleep_max
    if chance is not None:
        update_kwargs["chance"] = chance
    if system_prompt is not None:
        update_kwargs["system_prompt"] = system_prompt
    
    await bot.send_message(
        log_channel,
        f"Settings saved - account_id={account_id}, sleep_min={sleep_min}, sleep_max={sleep_max}, chance={chance}, system_prompt={system_prompt}"
    )

    await update_account_settings(account_id, **update_kwargs)

    # Переходим к вводу каналов для прогрева


async def send_comments(userid, session, account_id):
    async with account_semaphore:
        key = make_session_key(userid, session)
        session_name = os.path.join(SESSIONS_BASE_DIR, str(userid), str(session))

        # Проверка тихого периода в начале функции - блокируем запуск обработчиков во время сна
        if is_quiet_period():
            logging.debug(
                "Quiet period active, skipping send_comments for account %s (session %s)",
                account_id,
                session,
            )
            return

        account = await get_account_by_id(account_id)
        if not account:
            active_sessions.pop(make_session_key(userid, session), None)
            return
        
        # Режим прогрева не блокирует комментирование - это стандартный режим + warmup задача

        system_prompt = account.get("system_prompt") or ""
        sleep_min = account.get("sleep_min") or 10
        sleep_max = account.get("sleep_max") or 20
        chance = account.get("chance") or 100

        xsleep, ysleep = sleep_min, sleep_max
        reaction_emojis: List[str] = account.get("reaction_emojis") or []
        reaction_chance = account.get("reaction_chance")
        if reaction_chance is None:
            reaction_chance = 0
        reaction_discussion_chance = account.get("reaction_discussion_chance")
        reaction_sleep_min = account.get("reaction_sleep_min")
        reaction_sleep_max = account.get("reaction_sleep_max")
        reaction_limit_per_message = account.get("reaction_limit_per_message")
        if reaction_limit_per_message is None:
            reaction_limit_per_message = DEFAULT_REACTION_LIMIT_PER_MESSAGE
        if reaction_sleep_min is None:
            reaction_sleep_min = sleep_min
        if reaction_sleep_max is None:
            reaction_sleep_max = sleep_max
        if reaction_sleep_min > reaction_sleep_max:
            reaction_sleep_min, reaction_sleep_max = reaction_sleep_max, reaction_sleep_min

        discussion_reply_prompt = account.get("discussion_reply_prompt")
        discussion_reply_chance = account.get("discussion_reply_chance")
        reactions_enabled_raw = account.get("reactions_enabled")
        reactions_enabled = True if reactions_enabled_raw is None else bool(reactions_enabled_raw)

        last_reaction_at_dt = _parse_warmup_datetime(account.get("last_reaction_at"))
        if last_reaction_at_dt is None:
            last_reaction_at_dt = datetime.now(timezone.utc) - timedelta(hours=1)
        elif last_reaction_at_dt.tzinfo is None:
            last_reaction_at_dt = last_reaction_at_dt.replace(tzinfo=timezone.utc)

        async def _run_session(app: Client) -> None:
            nonlocal last_reaction_at_dt

            active_pyrogram_clients[key] = app

            if REACTION_ENGINE_AVAILABLE and reaction_engine_instance is not None:
                setattr(app, "reaction_engine", reaction_engine_instance)
                setattr(app, "reaction_engine_account_id", account_id)

            @app.on_message(filters.channel)
            async def channel_handler(client: Client, message: Message):
                nonlocal last_reaction_at_dt
                key_inner = make_session_key(userid, session)
                if not active_sessions.get(key_inner, False):
                    return
                
                # Проверка тихого периода - блокируем все действия во время сна
                if is_quiet_period():
                    return
                
                # Применяем задержку между обработкой разных сообщений
                now = datetime.now(timezone.utc)
                if account_id in last_message_processed_at:
                    last_processed = last_message_processed_at[account_id]
                    if last_processed.tzinfo is None:
                        last_processed = last_processed.replace(tzinfo=timezone.utc)
                    time_since_last = (now - last_processed).total_seconds()
                    # Применяем задержку, если прошло меньше минимальной задержки
                    if time_since_last < sleep_min:
                        delay_needed = sleep_min - time_since_last
                        logging.debug(
                            "Задержка между сообщениями для аккаунта %s: %.2f сек (прошло %.2f сек, мин: %d сек)",
                            account_id,
                            delay_needed,
                            time_since_last,
                            sleep_min,
                        )
                        await asyncio.sleep(delay_needed)
                last_message_processed_at[account_id] = datetime.now(timezone.utc)
                
                try:
                    channel = await client.get_chat(message.chat.id)
                    linked_chat = getattr(channel, "linked_chat", None)
                    if linked_chat:
                        with suppress(UserAlreadyParticipant):
                            await client.join_chat(linked_chat.id)
                except Exception:
                    logging.exception(
                        "Failed to join linked chat for channel %s",
                        getattr(message.chat, "id", "<unknown>"),
                    )

                channel_obj = getattr(message, "chat", None)
                channel_id = getattr(channel_obj, "id", None)
                message_id = getattr(message, "id", None)
                if channel_id is None or message_id is None:
                    return

                if REACTION_ENGINE_AVAILABLE and getattr(client, "reaction_engine", None) is not None:
                    try:
                        await client.reaction_engine.handle_linked_channel_message(client, message)
                    except Exception as exc:
                        logger.exception("ReactionEngine channel handler error: %s", exc)

                channel_identifier = str(channel_id)
                if await is_channel_blacklisted(account_id, channel_identifier):
                    logging.debug(
                        "Skipping blacklisted channel %s for account %s in channel handler",
                        channel_identifier,
                        account_id,
                    )
                    return

                try:
                    await record_post(
                        account_id,
                        channel=str(channel_id),
                        post_id=int(message_id),
                        message=_extract_post_text(message),
                        has_media=_message_has_media(message),
                    )
                except Exception:
                    logging.exception("Не удалось сохранить информацию о посте для реакций")

                if not reactions_enabled or not reaction_emojis:
                    return

                reaction_comment_context = " (пост)"
                status_suffix = "_post"
                post_base_link = _build_post_link(message, message)

                updated_last_reaction_at, should_exit = await _maybe_send_reaction(
                    client=client,
                    message=message,
                    session=session,
                    account_id=account_id,
                    reaction_emojis=reaction_emojis,
                    reaction_sleep_min=reaction_sleep_min,
                    reaction_sleep_max=reaction_sleep_max,
                    reaction_limit_per_message=reaction_limit_per_message,
                    reactions_enabled=reactions_enabled,
                    selected_reaction_chance=reaction_chance,
                    reaction_comment_context=reaction_comment_context,
                    status_suffix=status_suffix,
                    post_base_link=post_base_link,
                    current_last_reaction_at=last_reaction_at_dt,
                )
                last_reaction_at_dt = updated_last_reaction_at
                if should_exit:
                    return

            def _is_discussion_reply(_: Client, __, incoming_message: Message) -> bool:
                return _is_discussion_reply_message(incoming_message)

            discussion_filter = filters.create(_is_discussion_reply)

            @app.on_message(filters.linked_channel | discussion_filter)
            async def linked_channel_handler(client: Client, message: Message):
                nonlocal last_reaction_at_dt
                key_inner = make_session_key(userid, session)
                
                # Проверка тихого периода - блокируем все действия во время сна
                if is_quiet_period():
                    return
                
                # Применяем задержку между обработкой разных сообщений
                now = datetime.now(timezone.utc)
                if account_id in last_message_processed_at:
                    last_processed = last_message_processed_at[account_id]
                    if last_processed.tzinfo is None:
                        last_processed = last_processed.replace(tzinfo=timezone.utc)
                    time_since_last = (now - last_processed).total_seconds()
                    # Применяем задержку, если прошло меньше минимальной задержки
                    if time_since_last < sleep_min:
                        delay_needed = sleep_min - time_since_last
                        logging.debug(
                            "Задержка между сообщениями (linked) для аккаунта %s: %.2f сек (прошло %.2f сек, мин: %d сек)",
                            account_id,
                            delay_needed,
                            time_since_last,
                            sleep_min,
                        )
                        await asyncio.sleep(delay_needed)
                last_message_processed_at[account_id] = datetime.now(timezone.utc)
                
                if REACTION_ENGINE_AVAILABLE and getattr(client, "reaction_engine", None) is not None:
                    try:
                        await client.reaction_engine.handle_discussion_message(client, message)
                    except Exception as exc:
                        logger.exception("ReactionEngine discussion handler error: %s", exc)
                last_reaction_at_dt = await _handle_linked_channel_message(
                    client,
                    message,
                    userid=userid,
                    session=session,
                    account_id=account_id,
                    chance=chance,
                    xsleep=xsleep,
                    ysleep=ysleep,
                    system_prompt=system_prompt,
                    reaction_emojis=reaction_emojis,
                    reaction_chance=reaction_chance,
                    reaction_discussion_chance=reaction_discussion_chance,
                    discussion_reply_prompt=discussion_reply_prompt,
                    discussion_reply_chance=discussion_reply_chance,
                    reaction_sleep_min=reaction_sleep_min,
                    reaction_sleep_max=reaction_sleep_max,
                    reaction_limit_per_message=reaction_limit_per_message,
                    reactions_enabled=reactions_enabled,
                    last_reaction_at=last_reaction_at_dt,
                )

            @app.on_message(filters.group)
            async def group_discussion_handler(client: Client, message: Message):
                """
                Обработчик для обычных сообщений в обсуждениях
                НЕ обрабатывает реплаи - их обрабатывает существующий discussion_filter
                """

                nonlocal last_reaction_at_dt
                key_inner = make_session_key(userid, session)

                # Проверяем что сессия активна
                if not active_sessions.get(key_inner, False):
                    return

                # Проверка тихого периода - блокируем все действия во время сна
                if is_quiet_period():
                    return

                # Пропускаем собственные сообщения
                if _is_self_generated_message(message):
                    return

                # Пропускаем реплаи - их обрабатывает существующий discussion_filter
                if _is_discussion_reply_message(message):
                    return

                # Проверяем что это обсуждение (группа связанная с каналом)
                try:
                    chat = await client.get_chat(message.chat.id)
                    if not getattr(chat, "linked_chat", None):
                        return  # Обычная группа, не обсуждение
                except Exception as exc:
                    logging.debug(
                        "Ошибка проверки чата %s: %s",
                        getattr(message.chat, "id", "?"),
                        exc,
                    )
                    return

                # ReactionEngine вызов НЕ ДУБЛИРУЕМ - он будет вызван внутри _handle_linked_channel_message
                # через существующую логику обработки обсуждений

                # Упрощенный вызов - передаем только необходимые параметры
                last_reaction_at_dt = await _handle_linked_channel_message(
                    client=client,
                    message=message,
                    userid=userid,
                    session=session,
                    account_id=account_id,
                    # Обязательные параметры с значениями по умолчанию
                    chance=chance or 20,
                    xsleep=xsleep or 1,
                    ysleep=ysleep or 5,
                    system_prompt=system_prompt or "",
                    reaction_emojis=reaction_emojis or ["👍"],
                    reaction_chance=reaction_chance or 20,
                    reaction_discussion_chance=reaction_discussion_chance,
                    discussion_reply_prompt=discussion_reply_prompt,
                    discussion_reply_chance=discussion_reply_chance,
                    reaction_sleep_min=reaction_sleep_min or 0,
                    reaction_sleep_max=reaction_sleep_max or 0,
                    reaction_limit_per_message=reaction_limit_per_message or 1,
                    reactions_enabled=reactions_enabled,
                    last_reaction_at=last_reaction_at_dt,
                    # Новые параметры для принудительной обработки как обсуждения
                    force_discussion=True,
                    comment_chance_override=discussion_reply_chance or 25,
                    comment_prompt_override=discussion_reply_prompt,
                    selected_reaction_chance_override=reaction_discussion_chance or 45,
                )

            try:
                while active_sessions.get(key, False):
                    await asyncio.sleep(1)
            finally:
                active_pyrogram_clients.pop(key, None)

        try:
            await with_retry(
                session_name,
                _run_session,
                lock_key=key,
            )
        finally:
            key_inner = make_session_key(userid, session)
            account_id_inner = active_account_ids.pop(key_inner, None)
            if account_id_inner:
                try:
                    await mark_account_stopped(account_id_inner)
                except OperationalError as db_exc:
                    logging.error(
                        "Failed to mark account %s stopped after retries: %s",
                        account_id_inner,
                        db_exc,
                    )
            active_sessions.pop(key_inner, None)
            quiet_sessions_notified.discard(key_inner)
            active_pyrogram_clients.pop(key_inner, None)
            _release_session_lock(key_inner)


async def join_channel(
    channel: str,
    account_id: int,
    session_key: str,
    user_id: int,
    is_warmup: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Единая функция для вступления в канал (обычный или прогрев).

    Возвращает кортеж `(успех, причина_ошибки)`.
    """
    try:
        key = make_session_key(user_id, session_key)
        
        # Проверяем, что сессия свободна перед вступлением
        # Сессия должна быть неактивна (не используется для комментирования/реакций/комментирования комментариев)
        session_active = active_sessions.get(key, False)
        if session_active:
            # Сессия занята комментированием/реакциями - вступление невозможно
            busy_message = f"Сессия {session_key} занята комментированием/реакциями. Вступление невозможно пока сессия активна."
            logging.warning(
                "Warmup: Cannot join channel %s for account %s - session is active (commenting/reactions). "
                "active_sessions[%s]=%s. Will retry when session is free.",
                channel,
                session_key,
                key,
                session_active
            )
            return False, busy_message
        
        # Проверка blacklist перед подпиской
        if await is_channel_blacklisted(account_id, channel):
            blacklist_info = await get_channel_blacklist_info(account_id, channel)
            reason = blacklist_info.get("reason", "") if blacklist_info else ""
            # Если канал в blacklist с причиной NO_LINKED_CHAT или COMMENTS_DISABLED - не подписываем
            if "NO_LINKED_CHAT" in reason or "COMMENTS_DISABLED" in reason or "no_linked_chat" in reason.lower():
                error_msg = f"Канал {channel} в blacklist (причина: {reason}), пропускаем подписку"
                logging.info(f"Warmup: Skipping channel {channel} for account {account_id} - in blacklist: {reason}")
                if is_warmup:
                    await record_warmup_channel_error(account_id, channel, f"channel in blacklist: {reason}")
                return False, error_msg
        
        # Создаем клиент
        session_dir = os.path.join(SESSIONS_BASE_DIR, str(user_id))
        session_name = os.path.join(session_dir, session_key)
        session_file = f"{session_name}.session"

        if not os.path.exists(session_file):
            session_file_alt = f"{session_file}.session"
            if os.path.exists(session_file_alt):
                try:
                    shutil.copy2(session_file_alt, session_file)
                except Exception as copy_error:
                    error_message = (
                        f"Аккаунт {session_key} - не удалось подготовить файл сессии: "
                        f"{copy_error}"
                    )
                    await bot.send_message(log_channel, error_message)
                    return False, error_message
                ensure_session_file_permissions(session_file)
            else:
                error_message = f"Аккаунт {session_key} - файл сессии не найден: {session_file}"
                await bot.send_message(log_channel, error_message)
                return False, error_message
        else:
            ensure_session_file_permissions(session_file)

        key = make_session_key(user_id, session_key)

        async def _join_with_client(client_obj: Client) -> Tuple[bool, Optional[str]]:
            """Join channel with retry logic for transient errors."""
            max_retries = 3
            retry_delay = 2
            
            for attempt in range(1, max_retries + 1):
                try:
                    await client_obj.join_chat(channel)

                    if is_warmup:
                        # Для каналов прогрева - обновляем БД
                        await mark_warmup_channel_joined(account_id, channel)
                        await increment_warmup_joined(account_id)
                        await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) вступил в канал: {channel}")
                    else:
                        # Для обычных каналов - просто логируем
                        await bot.send_message(log_channel, f"Аккаунт {session_key} вступил в канал: {channel}")

                    return True, None

                except ChatWriteForbidden as e:
                    error_message = str(e)
                    if is_warmup:
                        await record_warmup_channel_error(account_id, channel, error_message)
                    await bot.send_message(
                        log_channel,
                        f"Аккаунт {session_key} не может вступить в {channel}: {error_message}",
                    )
                    return False, error_message

                except UserAlreadyParticipant:
                    if is_warmup:
                        await mark_warmup_channel_joined(account_id, channel)
                    await bot.send_message(log_channel, f"Аккаунт {session_key} уже состоит в канале: {channel}")
                    return True, None

                except Exception as e:
                    error_message = str(e)
                    
                    # Check if it's a transient error that we should retry
                    if _is_transient_join_error(error_message) and attempt < max_retries:
                        logging.warning(
                            "Transient error joining channel %s (attempt %d/%d): %s. Retrying...",
                            channel,
                            attempt,
                            max_retries,
                            error_message,
                        )
                        await asyncio.sleep(retry_delay * attempt)
                        continue
                    
                    # If it's transient but we've exhausted retries, raise TransientJoinError
                    if _is_transient_join_error(error_message):
                        raise TransientJoinError(error_message)
                    
                    # For other errors, log and return
                    logging.error(
                        "Failed to join channel %s for account %s: %s",
                        channel,
                        session_key,
                        error_message,
                        exc_info=True,
                    )
                    await bot.send_message(
                        log_channel,
                        f"Аккаунт {session_key} ошибка при вступлении в канал {channel}: {error_message}",
                    )
                    return False, error_message

        existing_client = active_pyrogram_clients.get(key)
        session_active = active_sessions.get(key, False)

        if existing_client and session_active:
            lock = get_session_lock(key)
            async with lock:
                if not getattr(existing_client, "is_connected", False):
                    # Ждем пока клиент запустится, чтобы избежать гонок с pyrogram.session
                    # Добавляем таймаут (10 секунд максимум) для предотвращения зависаний
                    max_wait_attempts = 40
                    wait_timeout = 10.0  # секунд
                    wait_start = asyncio.get_event_loop().time()
                    for attempt in range(max_wait_attempts):
                        if not active_sessions.get(key, False):
                            break
                        if getattr(existing_client, "is_connected", False):
                            break
                        # Проверяем таймаут
                        if (asyncio.get_event_loop().time() - wait_start) > wait_timeout:
                            logging.warning(
                                "Timeout waiting for client connection for account %s (session %s)",
                                account_id,
                                session_key,
                            )
                            break
                        await asyncio.sleep(0.25)

                if getattr(existing_client, "is_connected", False):
                    return await _join_with_client(existing_client)

            if active_sessions.get(key, False):
                busy_message = "Аккаунт занят, повторите попытку позже"
                await bot.send_message(log_channel, f"Аккаунт {session_key}: {busy_message}")
                return False, busy_message

        if session_active and existing_client is None:
            busy_message = "Аккаунт запускается, попробуйте позже"
            await bot.send_message(log_channel, f"Аккаунт {session_key}: {busy_message}")
            return False, busy_message

        async def _join_runner(app: Client):
            return await _join_with_client(app)

        return await with_retry(
            session_name,
            _join_runner,
            lock_key=key,
        )

    except TransientJoinError:
        raise
    except Exception as e:
        error_msg = str(e)
        if _is_transient_join_error(error_msg):
            await bot.send_message(
                log_channel,
                f"Аккаунт {session_key} временная ошибка подключения: {error_msg}",
            )
            raise TransientJoinError(error_msg)
        if any(keyword in error_msg.lower() for keyword in ["phone number", "auth", "eof when reading", "session", "unauthorized"]):
            await bot.send_message(log_channel, f"Аккаунт {session_key} - сессия истекла или повреждена: {error_msg}")
            return False, error_msg
        else:
            await bot.send_message(log_channel, f"Аккаунт {session_key} ошибка подключения: {e}")
            return False, error_msg



async def process_single_warmup_account(
    account: Dict[str, Any],
    *,
    now: datetime,
    current_settings: Any,
    daily_limit: int,
    add_summary: Any,
) -> None:
    """Обрабатывает отдельный аккаунт прогрева: комментарии, реакции и вступление в каналы."""

    await process_single_standard_account(account)

    account_id = account.get("id")
    if account_id is None:
        return

    session_key_raw = account.get("phone")
    user_id_raw = account.get("user_id")

    if session_key_raw is None or user_id_raw is None:
        return

    session_key = str(session_key_raw)

    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        logging.debug("Warmup: invalid user id for account %s", account_id)
        return

    key = make_session_key(user_id, session_key)

    logging.debug("Warmup: Processing account %s, active: %s", session_key, active_sessions.get(key))
    add_summary("debug", f"Processing account {session_key}, active={active_sessions.get(key)}")

    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    warmup_end = _parse_warmup_datetime(account.get("warmup_end_at"))
    if warmup_end and warmup_end.tzinfo is None:
        warmup_end = warmup_end.replace(tzinfo=timezone.utc)
    if not warmup_end:
        warmup_end = now_utc + timedelta(days=7)
        account["warmup_end_at"] = warmup_end
        try:
            await db_update_warmup_schedule(account_id, warmup_end=warmup_end)
        except Exception:
            logging.exception(
                "Warmup: Failed to persist default warmup end timestamp for account %s",
                account_id,
            )
    else:
        account["warmup_end_at"] = warmup_end
        if warmup_end <= now_utc:
            await set_account_mode(account_id, "standard", warmup_days=None)
            return

    warmup_last_join_at = _parse_warmup_datetime(account.get("warmup_last_join_at"))
    if warmup_last_join_at and warmup_last_join_at.tzinfo is None:
        warmup_last_join_at = warmup_last_join_at.replace(tzinfo=timezone.utc)
    if not warmup_last_join_at:
        warmup_last_join_at = now_utc
        account["warmup_last_join_at"] = warmup_last_join_at
        try:
            await db_update_warmup_schedule(account_id, last_join=warmup_last_join_at)
        except Exception:
            logging.exception(
                "Warmup: Failed to persist default last join timestamp for account %s",
                account_id,
            )
    else:
        account["warmup_last_join_at"] = warmup_last_join_at

    if warmup_last_join_at and warmup_last_join_at.date() < now_utc.date():
        await reset_warmup_daily_state(account_id)
        account["warmup_joined_today"] = 0

    next_join_at = _parse_warmup_datetime(account.get("warmup_next_join_at"))
    if next_join_at and next_join_at.tzinfo is None:
        next_join_at = next_join_at.replace(tzinfo=timezone.utc)
    
    # Если время не установлено или уже прошло, планируем вступление
    if not next_join_at or next_join_at <= now_utc:
        # Используем plan_next_warmup_join для планирования следующего вступления
        # Эта функция учитывает окно вступлений из настроек (00:50-23:00)
        next_join_at = plan_next_warmup_join(now_utc, current_settings)
        account["warmup_next_join_at"] = next_join_at
        try:
            await db_update_warmup_schedule(account_id, next_join=next_join_at)
            logging.info(
                "Warmup: Reset next_join_at for account %s (%s) to %s (was in past or not set)",
                account_id,
                session_key,
                next_join_at.isoformat()
            )
        except Exception:
            logging.exception(
                "Warmup: Failed to persist next join timestamp for account %s",
                account_id,
            )
        add_summary("debug", f"{session_key}: next join reset to {next_join_at}")
    else:
        account["warmup_next_join_at"] = next_join_at
    
    if next_join_at and next_join_at > now_utc:
        logging.debug(
            "Warmup: Account %s (%s) next_join_at=%s is in future, waiting. now=%s",
            session_key, account_id, next_join_at.isoformat(), now_utc.isoformat()
        )
        return

    joined_today = account.get("warmup_joined_today", 0)
    logging.debug("Warmup: Account %s joined today: %s/%s", session_key, joined_today, daily_limit)
    add_summary("debug", f"{session_key}: {joined_today}/{daily_limit} joins")

    if joined_today >= daily_limit:
        message = f"Account {session_key} reached daily limit, skipping"
        logging.info("Warmup: %s", message)
        add_summary("info", message)
        next_window_start = _next_join_window_start(now_utc, current_settings)
        next_time = plan_next_warmup_join(next_window_start, current_settings)
        await db_update_warmup_schedule(account_id, next_join=next_time)
        logging.info(
            "Warmup schedule: account %s (%s) next join at %s",
            account_id,
            session_key,
            next_time.isoformat(),
        )
        account["warmup_next_join_at"] = next_time
        return

    # Сначала пытаемся получить каналы, при необходимости синхронизируя из резюме
    pending_channels = await get_warmup_pending(account_id, limit=1, reset_if_empty=True)
    logging.info("Warmup: Account %s pending channels: %d", session_key, len(pending_channels))
    add_summary("info", f"{session_key}: pending channels {len(pending_channels)}")
    
    # Если каналов нет, пытаемся синхронизировать из accounts.channels (резюме аккаунта)
    if not pending_channels:
        account_data = await get_account_by_id(account_id)
        if account_data:
            real_channels = account_data.get("channels") or []
            if real_channels:
                logging.info(
                    "Warmup: Account %s has no pending channels, syncing %d channels from resume",
                    session_key,
                    len(real_channels),
                )
                await sync_warmup_channels(account_id, real_channels)
                # Пытаемся получить каналы снова после синхронизации
                pending_channels = await get_warmup_pending(account_id, limit=1, reset_if_empty=False)
                logging.info(
                    "Warmup: After sync, Account %s has %d pending channels",
                    session_key,
                    len(pending_channels),
                )
    
    if pending_channels:
        logging.info("Warmup: Account %s will join channel: %s", session_key, pending_channels[0].get("channel"))
    else:
        # Проверяем все каналы для диагностики
        all_warmup_channels = await get_warmup_pending(account_id, limit=100, reset_if_empty=False)
        logging.warning(
            "Warmup: Account %s has no pending channels. Total warmup channels: %d",
            session_key,
            len(all_warmup_channels),
        )
        if all_warmup_channels:
            statuses = {}
            for ch in all_warmup_channels:
                status = ch.get("status", "unknown")
                statuses[status] = statuses.get(status, 0) + 1
            logging.warning(
                "Warmup: Account %s channel statuses: %s",
                session_key,
                statuses,
            )

    if not pending_channels:
        message = f"Account {session_key} has no pending channels, skipping"
        logging.warning("Warmup: %s", message)
        add_summary("warning", message)
        next_time = _get_next_warmup_join(now_utc, current_settings)
        await db_update_warmup_schedule(account_id, next_join=next_time)
        logging.info(
            "Warmup schedule: account %s (%s) next join at %s",
            account_id,
            session_key,
            next_time.isoformat(),
        )
        account["warmup_next_join_at"] = next_time
        return

    channel_entry = pending_channels[0]
    channel = channel_entry["channel"]
    
    logging.info(
        "Warmup: Account %s attempting to join channel %s (position %s, status %s)",
        session_key,
        channel,
        channel_entry.get("position"),
        channel_entry.get("status"),
    )

    session_file = os.path.join(SESSIONS_BASE_DIR, str(user_id), f"{session_key}.session")
    if not os.path.exists(session_file):
        warning_message = (
            f"Аккаунт {session_key} (прогрев) - файл сессии не найден: {session_file}"
        )
        logging.warning("Warmup: %s", warning_message)
        add_summary("warning", warning_message)
        await set_account_mode(account_id, "standard", warmup_days=None)
        return

    try:
        logging.info(
            "Warmup: 🔵 Attempting to join channel %s for account %s (id=%d, user_id=%d, session=%s)",
            channel, session_key, account_id, user_id, session_key
        )
        success, error_reason = await join_channel(
            channel, account_id, session_key, user_id, is_warmup=True
        )
        if success:
            logging.info(
                "Warmup: ✅ Successfully joined channel %s for account %s",
                channel, session_key
            )
        else:
            logging.warning(
                "Warmup: ❌ Failed to join channel %s for account %s: %s",
                channel, session_key, error_reason
            )
    except TransientJoinError as transient_error:
        transient_message = (
            transient_error.message if hasattr(transient_error, "message") else str(transient_error)
        )
        warning_message = (
            f"Account {session_key} временная ошибка вступления в {channel}: {transient_message}. Повторим позже."
        )
        logging.warning("Warmup: %s", warning_message)
        add_summary("warning", warning_message)
        backoff_seconds = random.uniform(15, 45)
        retry_time = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
        await db_update_warmup_schedule(account_id, next_join=retry_time)
        account["warmup_next_join_at"] = retry_time
        await asyncio.sleep(min(backoff_seconds, 5))
        return

    if not success:
        # Записываем ошибку в БД, если она еще не записана в join_channel
        if error_reason:
            try:
                await record_warmup_channel_error(account_id, channel, error_reason)
            except Exception as e:
                logging.warning("Failed to record warmup channel error: %s", e)
        
        if error_reason and any(
            phrase in error_reason.lower()
            for phrase in ("занят", "запускается")
        ):
            # Сессия занята - устанавливаем время следующей попытки через несколько минут
            retry_delay_minutes = 5
            retry_time = datetime.now(timezone.utc) + timedelta(minutes=retry_delay_minutes)
            await db_update_warmup_schedule(account_id, next_join=retry_time)
            account["warmup_next_join_at"] = retry_time
            info_message = f"Account {session_key} занят ({error_reason}), следующая попытка в {retry_time.isoformat()}"
            logging.info("Warmup: %s", info_message)
            add_summary("info", info_message)
            return
        await set_account_mode(account_id, "standard", warmup_days=None)
        return

    success_message = f"Account {session_key} joined {channel}"
    logging.info("Warmup: %s", success_message)
    add_summary("info", success_message)

    # Обновляем счетчик вступлений в локальном объекте account
    account["warmup_joined_today"] = account.get("warmup_joined_today", 0) + 1
    account["warmup_last_join_at"] = datetime.now(timezone.utc)

    post_join_now = datetime.now(timezone.utc)
    next_time = _get_next_warmup_join(post_join_now, current_settings)
    await db_update_warmup_schedule(account_id, next_join=next_time)
    logging.info(
        "Warmup schedule: account %s (%s) next join at %s",
        account_id,
        session_key,
        next_time.isoformat(),
    )
    account["warmup_next_join_at"] = next_time


async def process_warmup_accounts():
    """Фоновая задача для добавления каналов в режиме прогрева (во время сна)"""

    logging.info("✅ Processing warmup accounts...")

    while True:
        current_settings = get_current_warmup_settings()
        iteration_notifications: List[Tuple[str, str]] = []

        def add_summary(level: str, message: str) -> None:
            if level in ("warning", "error") or WARMUP_VERBOSE_NOTIFICATIONS:
                iteration_notifications.append((level, message))

        try:
            current_settings = await ensure_latest_warmup_settings()
            now = datetime.now(timezone.utc)
            is_warmup_join_time = is_warmup_join_period(now)
            daily_limit = current_settings.channels_per_day

            # Логируем настройки для диагностики
            if now.minute % 10 == 0:
                message = (
                    f"Warmup check: {now.strftime('%H:%M')} UTC, "
                    f"is_warmup_join_time: {is_warmup_join_time}, "
                    f"window: {current_settings.format_window()}, "
                    f"spans_midnight: {current_settings.spans_midnight}"
                )
                logging.info(message)
                add_summary("info", message)

            warmup_accounts = await get_running_warmup_accounts()
            all_accounts = await get_running_accounts()

            # Проверяем, есть ли аккаунты, у которых время вступления уже наступило
            # Также обрабатываем аккаунты без установленного времени, чтобы установить его
            accounts_ready_to_join = []
            accounts_without_schedule = []
            for account in warmup_accounts:
                next_join_at = _parse_warmup_datetime(account.get("warmup_next_join_at"))
                if next_join_at:
                    if next_join_at.tzinfo is None:
                        next_join_at = next_join_at.replace(tzinfo=timezone.utc)
                    if next_join_at <= now:
                        accounts_ready_to_join.append(account)
                        logging.debug(
                            "Warmup: Account %s ready to join (next_join_at=%s <= now=%s)",
                            account.get("phone"),
                            next_join_at.isoformat(),
                            now.isoformat()
                        )
                else:
                    # Аккаунт без установленного времени - нужно обработать, чтобы установить
                    accounts_without_schedule.append(account)
                    logging.debug(
                        "Warmup: Account %s has no next_join_at, will process to set schedule",
                        account.get("phone")
                    )

            if not is_warmup_join_time and not accounts_ready_to_join and not accounts_without_schedule:
                await asyncio.sleep(WARMUP_SCAN_INTERVAL_SECONDS)
                continue

            if is_warmup_join_time:
                logging.info("Found %d running warmup accounts (warmup window active)", len(warmup_accounts))
            else:
                logging.info("Found %d running warmup accounts, %d ready to join, %d without schedule (outside window)", 
                           len(warmup_accounts), len(accounts_ready_to_join), len(accounts_without_schedule))
            
            add_summary(
                "info",
                f"Running accounts: {len(all_accounts)}, warmup: {len(warmup_accounts)}, ready: {len(accounts_ready_to_join)}, without schedule: {len(accounts_without_schedule)}",
            )

            # Если окно прогрева активно, обрабатываем все аккаунты
            # Если окно не активно, обрабатываем те, у которых время наступило, и те, у которых время не установлено
            if is_warmup_join_time:
                accounts_to_process = warmup_accounts
                logging.info("Warmup: Processing all %d warmup accounts (warmup window is active)", len(warmup_accounts))
            else:
                accounts_to_process = accounts_ready_to_join + accounts_without_schedule
                logging.info(
                    "Warmup: Processing %d accounts ready to join + %d without schedule (outside warmup window)",
                    len(accounts_ready_to_join), len(accounts_without_schedule)
                )
            
            if not accounts_to_process:
                await asyncio.sleep(WARMUP_SCAN_INTERVAL_SECONDS)
                continue

            random.shuffle(accounts_to_process)

            logging.debug("Warmup: Active sessions: %s", list(active_sessions.keys()))
            add_summary("debug", f"Active sessions: {list(active_sessions.keys())}")

            for account in accounts_to_process:
                account_phone = account.get("phone", "unknown")
                account_id = account.get("id")
                logging.debug(
                    "Warmup: 🔵 Processing account %s (id=%s) for warmup join",
                    account_phone, account_id
                )
                try:
                    await process_single_warmup_account(
                        account,
                        now=now,
                        current_settings=current_settings,
                        daily_limit=daily_limit,
                        add_summary=add_summary,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as account_error:
                    if "fromisoformat" in str(account_error):
                        logging.warning(
                            "Warmup datetime parsing error for account %s: %s. Skipping...",
                            account.get("id"),
                            account_error,
                        )
                        add_summary(
                            "warning",
                            f"Datetime parsing error for warmup account {account.get('phone')}: {account_error}",
                        )
                        continue
                    phone = account.get("phone")
                    logging.warning(
                        "Failed to process warmup account %s (phone %s): %s",
                        account.get("id"),
                        phone,
                        account_error,
                    )
                    add_summary(
                        "warning",
                        f"Failed to process warmup account {phone}: {account_error}",
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_str = str(e)
            logging.exception("Warmup loop error: %s", e)
            add_summary("error", f"Warmup loop error: {e}")
            
            # Check for database connection errors
            if "connection" in error_str.lower() or "pool" in error_str.lower():
                logging.critical("Database connection error in warmup loop. Waiting before retry...")
                await asyncio.sleep(30)  # Wait before retry on DB errors

        if iteration_notifications:
            summary_lines = [f"{level.upper()}: {message}" for level, message in iteration_notifications]
            timestamp = datetime.now(timezone.utc).strftime('%H:%M:%S')
            summary_text = f"Warmup summary ({timestamp} UTC):\n" + "\n".join(summary_lines)
            if len(summary_text) > 3800:
                summary_text = summary_text[:3797] + "..."
            try:
                await bot.send_message(log_channel, summary_text)
            except Exception:
                logging.exception("Failed to send warmup summary notification")

        delay_seconds = max(WARMUP_CHECK_INTERVAL, _get_human_delay_seconds(current_settings))
        await asyncio.sleep(delay_seconds)


async def _disconnect_client_safely(client: Optional[Client]) -> None:
    if not client:
        return

    try:
        await client.disconnect()
    except Exception:
        logging.exception("Failed to disconnect temporary auth client")


async def cleanup_auth(client: Optional[Client], state: FSMContext) -> None:
    await _disconnect_client_safely(client)
    await state.clear()


async def save_session_and_cleanup(
    message: Message,
    state: FSMContext,
    client: Optional[Client],
    phone: str,
) -> None:
    session_path = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), f"{phone}.session")
    try:
        os.makedirs(os.path.dirname(session_path), exist_ok=True)
        await ensure_account(message.from_user.id, phone, session_path)
    except Exception as exc:
        logging.exception("Failed to persist session for %s", phone)
        await message.answer(f"Не удалось сохранить аккаунт: {exc}")
    finally:
        await cleanup_auth(client, state)

    await main_message(message)


@dp.message(addsession.number)
async def add_number(message: Message, state: FSMContext) -> None:
    raw_number = (message.text or "").strip()

    if raw_number == "-":
        await message.answer("Добавление аккаунта отменено.")
        await state.clear()
        await main_message(message)
        return

    if raw_number.isdigit():
        warmup_only = (await state.get_data()).get("warmup_only")

        if warmup_only:
            account_row = await get_account_by_session(message.from_user.id, raw_number)
            if not account_row:
                await message.answer("Аккаунт не найден. Сначала добавьте аккаунт через 'Добавить аккаунт'.")
                await state.clear()
                await main_message(message)
                return

            await state.update_data({
                "account": raw_number,
                "account_id": account_row["id"],
            })
            await message.answer(
                "Пришлите каналы для прогрева (каждый канал с новой строки). Для отмены отправьте '-'."
            )
            await state.set_state(startaccount.warmup_channels)
            return

        client: Optional[Client] = None
        try:
            session_name = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(raw_number))
            os.makedirs(os.path.dirname(session_name), exist_ok=True)

            client = Client(
                session_name,
                api_id=API_ID,
                api_hash=API_HASH,
                no_updates=True,
            )

            await client.connect()
            sent_code = await client.send_code(raw_number)

            await state.update_data({
                "code_hash": sent_code.phone_code_hash,
                "number": raw_number,
                "client": client,
            })

            await message.answer("Код подтверждения отправлен.\nВведите код в формате 6 7 4 3 9")
            await state.set_state(addsession.code)
        except Exception as exc:
            await message.answer(f"Ошибка: {exc}")
            await cleanup_auth(client, state)
            await main_message(message)
    else:
        await message.answer(
            "Пришлите номер телефона (только цифры, например 79999999999) или '-' для отмены."
        )

@dp.message(addsession.code)
async def add_code(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    code = raw.replace(' ', '')

    if raw == "-":
        await message.answer("Добавление аккаунта отменено. Код не введен.")
        state_data = await state.get_data()
        client = state_data.get("client")
        number = state_data.get("number")
        await cleanup_auth(client, state)
        if number:
            session_base = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(number))
            for p in (f"{session_base}.session", f"{session_base}.session.session"):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        logging.info("Removed incomplete session %s (user cancelled)", p)
                    except OSError:
                        pass
        await main_message(message)
        return

    if not code.isdigit():
        await message.answer("Код должен содержать только цифры, или '-' для отмены")
        return

    state_data = await state.get_data()
    client: Optional[Client] = state_data.get("client")
    code_hash = state_data.get("code_hash")
    number = state_data.get("number")

    if not all([client, code_hash, number]):
        await message.answer("Ошибка сессии. Начните заново.")
        await cleanup_auth(client, state)
        await main_message(message)
        return

    try:
        await client.sign_in(
            phone_number=number,
            phone_code_hash=code_hash,
            phone_code=code,
        )
    except SessionPasswordNeeded:
        await state.update_data({"code": code})
        await message.answer("🔐 Требуется пароль двухфакторной аутентификации. Введите пароль:")
        await state.set_state(addsession.password)
        return
    except PhoneCodeInvalid:
        await message.answer("❌ Неверный код. Попробуйте снова:")
        return
    except PhoneCodeExpired:
        await message.answer("⏳ Срок действия кода истек. Пожалуйста, запросите новый код.")
        await cleanup_auth(client, state)
        await main_message(message)
        return
    except Exception as exc:
        await message.answer(f"Ошибка: {exc}")
        await cleanup_auth(client, state)
        await main_message(message)
        return

    await message.answer("✅ Успешная авторизация!")
    await save_session_and_cleanup(message, state, client, number)

@dp.message(addsession.password)
async def add_password(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == "-":
        await message.answer("Добавление аккаунта отменено.")
        state_data = await state.get_data()
        client = state_data.get("client")
        number = state_data.get("number")
        await cleanup_auth(client, state)
        if number:
            session_base = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(number))
            for p in (f"{session_base}.session", f"{session_base}.session.session"):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        logging.info("Removed incomplete session %s (user cancelled at 2FA)", p)
                    except OSError:
                        pass
        await main_message(message)
        return

    password = raw
    state_data = await state.get_data()

    client: Optional[Client] = state_data.get("client")
    number = state_data.get("number")

    if not all([client, number]):
        await message.answer("Ошибка сессии. Начните заново.")
        await cleanup_auth(client, state)
        await main_message(message)
        return

    try:
        await client.check_password(password)
    except PasswordHashInvalid:
        await message.answer("❌ Неверный пароль. Попробуйте снова:")
        return
    except Exception as exc:
        await message.answer(f"Ошибка: {exc}")
        await cleanup_auth(client, state)
        await main_message(message)
        return

    await message.answer("✅ Успешная авторизация с 2FA!")
    await save_session_and_cleanup(message, state, client, number)

@dp.message(startaccount.regular_channels)
async def add_regular_channels(message: Message, state: FSMContext) -> None:
    """Обработчик для обычных каналов (немедленное вступление)"""
    data, account_id, account = await _load_account_data(state)
    session = data.get("account") if data else None
    chance = data.get("chance") if data else None
    system_prompt_value = data.get("system_prompt") if data else None
    sleeps = data.get("sleeps") if data else None

    existing_channels_raw: List[str] = []
    if account and isinstance(account, dict):
        stored_channels = account.get("channels")
        if isinstance(stored_channels, list):
            existing_channels_raw = [channel for channel in stored_channels if isinstance(channel, str)]

    existing_channels_lookup: Set[str] = {
        channel.lower() for channel in existing_channels_raw if isinstance(channel, str)
    }

    if not account_id:
        await bot.send_message(message.from_user.id, "Ошибка: аккаунт не найден. Попробуйте снова.")
        await state.clear()
        await main_message(message)
        return

    channels_to_update: Optional[List[str]] = None
    successful_channels: List[str] = []
    if str(message.text) != '-':
        raw_channels = [line.strip() for line in message.text.splitlines() if line.strip()]

        channels: List[str] = []
        seen_new: Set[str] = set()
        for channel in raw_channels:
            if channel.startswith("-"):
                channels.append(channel)
                continue

            normalized = channel.lower()
            if normalized in existing_channels_lookup or normalized in seen_new:
                continue

            seen_new.add(normalized)
            channels.append(channel)

        # Вступаем в обычные каналы сразу
        for channel in channels:
            try:
                success, error_reason = await join_channel(
                    channel, account_id, session, message.from_user.id, is_warmup=False
                )
            except TransientJoinError as transient_error:
                reason_text = transient_error.message if hasattr(transient_error, "message") else str(transient_error)
                await bot.send_message(
                    log_channel,
                    f"Не удалось добавить канал {channel} для аккаунта {session} из-за временной ошибки: {reason_text}",
                )
                await bot.send_message(
                    message.from_user.id,
                    f"Временная ошибка при вступлении в канал {channel}. Попробуйте позже. Детали: {reason_text}",
                )
                continue

            if success:
                successful_channels.append(channel)
            else:
                reason_text = error_reason or "Неизвестная ошибка"
                await bot.send_message(
                    log_channel,
                    f"Не удалось добавить канал {channel} для аккаунта {session}: {reason_text}",
                )
                await bot.send_message(
                    message.from_user.id,
                    f"Не удалось вступить в канал {channel}: {reason_text}",
                )

        # Обновляем список обычных каналов в БД только успешными каналами
        if successful_channels:
            def _merge_channels(
                existing: List[str], new: List[str]
            ) -> Tuple[List[str], bool]:
                seen: Set[str] = set()
                merged_list: List[str] = []

                for item in existing:
                    if item not in seen:
                        merged_list.append(item)
                        seen.add(item)

                added = False
                for item in new:
                    if item not in seen:
                        merged_list.append(item)
                        seen.add(item)
                        added = True

                return merged_list, added

            merged_channels, has_new_channels = _merge_channels(existing_channels_raw, successful_channels)
            if has_new_channels:
                channels_to_update = merged_channels
    else:
        channels = []

    sleep_min, sleep_max = None, None
    reaction_sleep_min, reaction_sleep_max = None, None
    if sleeps:
        parsed_range = _parse_sleep_range_input(str(sleeps))
        if parsed_range:
            sleep_min, sleep_max = parsed_range

    reaction_range_raw = data.get("reaction_sleeps") if data else None
    if reaction_range_raw:
        parsed_reaction = _parse_sleep_range_input(str(reaction_range_raw))
        if parsed_reaction:
            reaction_sleep_min, reaction_sleep_max = parsed_reaction

    update_kwargs = {
        "chance": chance,
        "system_prompt": system_prompt_value,
        "sleep_min": sleep_min,
        "sleep_max": sleep_max,
    }
    if channels_to_update is not None:
        update_kwargs["channels"] = channels_to_update

    await update_account_settings(account_id, **update_kwargs)

    if successful_channels:
        channels_summary = "\n".join(successful_channels)
        summary_text = "Итоговый список успешно добавленных каналов:\n" + channels_summary
    elif str(message.text) == '-':
        summary_text = "Каналы не были добавлены."
    else:
        summary_text = "Не удалось добавить ни один канал."

    await bot.send_message(message.from_user.id, summary_text)

    # Переходим к диалогу каналов прогрева
    await bot.send_message(message.from_user.id, 'Теперь пришлите каналы для прогрева (каждый канал с новой строки). Для отмены отправьте "-".')
    await state.set_state(startaccount.warmup_channels)


@dp.message(startaccount.warmup_channels)
async def add_warmup_channels(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    session = data.get("account")
    account_id = data.get("account_id")
    processed = data.get("warmup_processed", False)

    # Проверяем, не обрабатывали ли мы уже это состояние
    if not account_id or processed:
        if processed:
            await bot.send_message(message.from_user.id, "Каналы прогрева уже обработаны. Используйте /start для нового аккаунта.")
        else:
            await bot.send_message(message.from_user.id, "Ошибка: аккаунт не найден. Попробуйте снова.")
        await state.clear()
        await main_message(message)
        return

    warmup_settings = await ensure_latest_warmup_settings()

    # Показываем существующие каналы прогрева перед обработкой
    existing_warmup = await get_warmup_pending(account_id, limit=100)
    warmup_list = [ch["channel"] for ch in existing_warmup] if existing_warmup else []
    if warmup_list:
        warmup_display = await format_channels_display(
            warmup_list, "Текущие каналы в прогреве", 10, use_markdown=False
        )
        await bot.send_message(message.from_user.id, warmup_display)
    
    # Помечаем как обработанное, чтобы избежать повторных вызовов
    await state.update_data({"warmup_processed": True})

    if str(message.text) == '-':
        # Проверяем, есть ли уже каналы в прогреве
        existing_warmup = await get_warmup_pending(account_id, limit=1, reset_if_empty=True)
        
        if existing_warmup:
            # Есть каналы в прогреве - запускаем в режиме прогрева
            await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
            now_utc = datetime.now(timezone.utc)
            # Если окно прогрева активно, устанавливаем время вступления на ближайшее время
            if is_warmup_join_period(now_utc):
                delay_seconds = _get_human_delay_seconds(warmup_settings)
                next_join = now_utc + timedelta(seconds=delay_seconds)
                logging.info(
                    "Warmup window is active, setting next join to %s (in %d seconds)",
                    next_join.isoformat(),
                    delay_seconds,
                )
            else:
                next_join = plan_next_warmup_join(now_utc, warmup_settings)
                logging.info(
                    "Warmup window is not active, setting next join to %s",
                    next_join.isoformat(),
                )
            await db_update_warmup_schedule(account_id, next_join=next_join)
            logging.info(
                "Warmup schedule: account %s (%s) next join at %s",
                account_id,
                session,
                next_join.isoformat(),
            )
            
            # Запускаем аккаунт в режиме прогрева (С комментированием + прогрев)
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True  # Устанавливаем для комментирования
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            warmup_count = len(await get_warmup_pending(account_id, limit=100))
            await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Используются существующие каналы прогрева ({warmup_count} каналов).')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
            
            # Отправляем резюме аккаунта пользователю и в лог-канал
            await send_account_summary_to_user(message.from_user.id, account_id, session)
            await send_account_summary_to_logs(account_id, session)
            
            await main_message(message)
            _schedule_safe_send_comments(message.from_user.id, session, account_id)
            return
        else:
            # Нет каналов в прогреве - пытаемся синхронизировать реальные подписки из Telegram
            session_name = os.path.join(SESSIONS_BASE_DIR, str(message.from_user.id), str(session))
            key = make_session_key(message.from_user.id, str(session))
            
            async def _collect_real_channels(app: Client) -> List[str]:
                collected: List[str] = []
                async for dialog in app.get_dialogs():
                    chat = dialog.chat
                    if str(chat.type) == "ChatType.CHANNEL" and chat.username is not None:
                        channel_handle = f"@{chat.username}"
                        if channel_handle not in collected:
                            collected.append(channel_handle)
                return collected
            
            real_channels = []
            try:
                if await check_account(message.from_user.id, session):
                    logging.info("Fetching real channels from Telegram for account %s (warmup)", session)
                    real_channels = await with_retry(
                        session_name,
                        _collect_real_channels,
                        lock_key=key,
                    )
                    logging.info("Found %d real channels from Telegram for account %s", len(real_channels), session)
                    # Обновляем accounts.channels с реальными подписками
                    if real_channels and account_id:
                        await update_account_settings(account_id, channels=real_channels)
                        logging.info("Updated accounts.channels with %d channels for account %s", len(real_channels), account_id)
                    elif not real_channels:
                        logging.warning("No channels found from Telegram for account %s", session)
                else:
                    logging.warning("Account %s check failed, cannot fetch real channels", session)
            except Exception as e:
                logging.error("Failed to fetch real channels for warmup: %s", e, exc_info=True)
            
            # Проверяем еще раз после синхронизации
            existing_warmup = await get_warmup_pending(account_id, limit=1, reset_if_empty=True)
            
            if existing_warmup:
                # Нашли каналы после синхронизации - запускаем в режиме прогрева
                await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
                now_utc = datetime.now(timezone.utc)
                # Если окно прогрева активно, устанавливаем время вступления на ближайшее время
                if is_warmup_join_period(now_utc):
                    delay_seconds = _get_human_delay_seconds(warmup_settings)
                    next_join = now_utc + timedelta(seconds=delay_seconds)
                    logging.info(
                        "Warmup window is active, setting next join to %s (in %d seconds)",
                        next_join.isoformat(),
                        delay_seconds,
                    )
                else:
                    next_join = plan_next_warmup_join(now_utc, warmup_settings)
                    logging.info(
                        "Warmup window is not active, setting next join to %s",
                        next_join.isoformat(),
                    )
                await db_update_warmup_schedule(account_id, next_join=next_join)
                logging.info(
                    "Warmup schedule: account %s (%s) next join at %s (synced from real subscriptions)",
                    account_id,
                    session,
                    next_join.isoformat(),
                )
                
                # Запускаем аккаунт в режиме прогрева
                active_sessions[key] = True
                active_account_ids[key] = account_id
                quiet_sessions_notified.discard(key)
                await asyncio.sleep(0.1)
                await mark_account_running(account_id)
                
                await state.clear()
                warmup_count = len(await get_warmup_pending(account_id, limit=100))
                await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Синхронизировано {warmup_count} каналов из реальных подписок Telegram.')
                await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева (синхронизировано из реальных подписок)')
                
                await send_account_summary_to_user(message.from_user.id, account_id, session)
                await send_account_summary_to_logs(account_id, session)
                
                await main_message(message)
                _schedule_safe_send_comments(message.from_user.id, session, account_id)
                return
            else:
                # Нет каналов даже после синхронизации - запускаем в стандартном режиме
                await set_account_mode(account_id, "standard", warmup_days=None)
                await sync_warmup_channels(account_id, [])
                
                # Запускаем аккаунт в стандартном режиме
                active_sessions[key] = True
                active_account_ids[key] = account_id
                quiet_sessions_notified.discard(key)
                await asyncio.sleep(0.1)
                await mark_account_running(account_id)
                
                await state.clear()
                await bot.send_message(message.from_user.id, 'Аккаунт запущен в стандартном режиме (без прогрева). Не найдено каналов для прогрева.')
                await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в стандартном режиме')
                
                await send_account_summary_to_user(message.from_user.id, account_id, session)
                await send_account_summary_to_logs(account_id, session)
                
                await main_message(message)
                _schedule_safe_send_comments(message.from_user.id, session, account_id)
                return

    channels = [line.strip() for line in message.text.splitlines() if line.strip()]
    warmup_channels = [chl for chl in channels if not chl.startswith('-')]
    
    # Удаляем дубликаты, сохраняя порядок
    seen_warmup = set()
    warmup_channels = [x for x in warmup_channels if not (x in seen_warmup or seen_warmup.add(x))]

    if not warmup_channels:
        # Проверяем, есть ли уже каналы в прогреве (с автоматической синхронизацией)
        existing_warmup = await get_warmup_pending(account_id, limit=1, reset_if_empty=True)
        
        if existing_warmup:
            # Есть каналы в прогреве - запускаем в режиме прогрева
            await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
            next_join = plan_next_warmup_join(datetime.now(timezone.utc), warmup_settings)
            await db_update_warmup_schedule(account_id, next_join=next_join)
            logging.info(
                "Warmup schedule: account %s (%s) next join at %s",
                account_id,
                session,
                next_join.isoformat(),
            )

            # Запускаем аккаунт в режиме прогрева (С комментированием + прогрев)
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True  # Устанавливаем для комментирования
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Используются существующие каналы прогрева.')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
            
            # Отправляем резюме аккаунта пользователю и в лог-канал
            await send_account_summary_to_user(message.from_user.id, account_id, session)
            await send_account_summary_to_logs(account_id, session)
            
            await main_message(message)
            _schedule_safe_send_comments(message.from_user.id, session, account_id)
            return
        else:
            # Нет каналов в прогреве - запускаем в стандартном режиме
            await set_account_mode(account_id, "standard", warmup_days=None)
            await sync_warmup_channels(account_id, [])
            
            # Запускаем аккаунт в режиме прогрева (С комментированием + прогрев)
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True  # Устанавливаем для комментирования
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, 'Аккаунт запущен в стандартном режиме (без прогрева).')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование')
            
            # Отправляем резюме аккаунта пользователю и в лог-канал
            await send_account_summary_to_user(message.from_user.id, account_id, session)
            await send_account_summary_to_logs(account_id, session)
            
            await main_message(message)
            _schedule_safe_send_comments(message.from_user.id, session, account_id)
            return

    try:
        await sync_warmup_channels(account_id, warmup_channels)
        await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
        next_join = plan_next_warmup_join(datetime.now(timezone.utc), warmup_settings)
        await db_update_warmup_schedule(account_id, next_join=next_join)
        logging.info(
            "Warmup schedule: account %s (%s) next join at %s",
            account_id,
            session,
            next_join.isoformat(),
        )
    except Exception as e:
        await bot.send_message(log_channel, f"Ошибка при сохранении каналов прогрева для {session}: {e}")
        await bot.send_message(message.from_user.id, f"Ошибка при сохранении каналов прогрева: {e}")
        await state.clear()
        await main_message(message)
        return

    # Запускаем аккаунт в режиме прогрева (С комментированием + прогрев)
    key = make_session_key(message.from_user.id, session)
    active_sessions[key] = True  # Устанавливаем для комментирования
    active_account_ids[key] = account_id
    quiet_sessions_notified.discard(key)
    await mark_account_running(account_id)

    await state.clear()
    await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Запланировано {len(warmup_channels)} каналов для прогрева.')
    await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
    
    # Отправляем резюме аккаунта пользователю и в лог-канал
    await send_account_summary_to_user(message.from_user.id, account_id, session)
    await send_account_summary_to_logs(account_id, session)
    
    await main_message(message)
    _schedule_safe_send_comments(message.from_user.id, session, account_id)


async def safe_send_comments(user_id, phone, account_id):
    """Запускает send_comments и различает критические и некритические ошибки."""
    try:
        await send_comments(user_id, phone, account_id)
    except Exception as e:
        error_text = str(e)
        lowered = error_text.lower()
        key = make_session_key(user_id, phone)
        critical_keywords = ("auth", "session", "phone", "flood", "ban", "deleted")

        if any(keyword in lowered for keyword in critical_keywords):
            logging.error("CRITICAL error for account %s: %s", account_id, e)
            try:
                await mark_account_stopped(account_id)
            except OperationalError as db_exc:
                logging.error(
                    "Failed to mark account %s stopped after retries: %s",
                    account_id,
                    db_exc,
                )
            active_sessions.pop(key, None)
            active_account_ids.pop(key, None)
            active_pyrogram_clients.pop(key, None)
            quiet_sessions_notified.discard(key)
            _release_session_lock(key)
        else:
            logging.warning("Non-critical error for account %s: %s", account_id, e)
            # Аккаунт продолжит работу в основном цикле без очистки ресурсов


async def format_channels_display(
    channels,
    title="Каналы",
    max_display=10,
    *,
    use_markdown: bool = True,
):
    """Унифицированное отображение списка каналов."""

    if title is None:
        title_text = "Каналы"
    else:
        title_text = str(title)

    sanitized_title = (
        escape_markdown_text(title_text) if use_markdown else title_text
    )

    if not channels:
        return f"{sanitized_title}: нет"

    if use_markdown:
        prepared_channels = [escape_markdown_text(str(channel)) for channel in channels]
    else:
        prepared_channels = [str(channel) for channel in channels]

    if len(prepared_channels) <= max_display:
        return f"{sanitized_title} ({len(prepared_channels)}):\n" + "\n".join(prepared_channels)
    else:
        displayed = prepared_channels[:max_display]
        return (
            f"{sanitized_title} ({len(prepared_channels)}):\n"
            + "\n".join(displayed)
            + f"\n... и еще {len(prepared_channels) - max_display} каналов"
        )


def escape_markdown_text(text: str) -> str:
    """Экранирует спецсимволы для Telegram Markdown."""
    if not text:
        return ""

    replacements = {
        "\\": "\\\\",
        "_": "\\_",
        "*": "\\*",
        "`": "\\`",
        "[": "\\[",
        "]": "\\]",
    }

    escaped = text
    for symbol, replacement in replacements.items():
        escaped = escaped.replace(symbol, replacement)

    return escaped


def build_prompt_preview(prompt: Optional[str], max_length: int = 200) -> Tuple[str, bool]:
    """Возвращает укороченный текст промта и флаг, был ли он обрезан."""
    if not prompt:
        return "Не задан", False

    normalized = str(prompt).strip()
    if not normalized:
        return "Не задан", False

    if len(normalized) <= max_length:
        return normalized, False

    preview = normalized[:max_length].rstrip()
    return f"{preview}...", True


def format_global_statistics_report(stats: Dict[str, Any]) -> str:
    accounts = stats.get("accounts", {})
    comments = stats.get("comments", {})
    warmup = stats.get("warmup", {})

    accounts_by_status = accounts.get("by_status", {})
    accounts_by_mode = accounts.get("by_mode", {})
    running_by_mode = accounts.get("running_by_mode", {})

    comment_counts = comments.get("by_status", {})
    warmup_counts = warmup.get("by_status", {})
    reaction_counts = comments.get("reactions", {})
    reaction_total = comments.get("reactions_total", 0)

    def _format_additional(counts: Dict[str, Any], known_keys: Set[str]) -> Optional[str]:
        extra = [
            f"{escape_markdown_text(str(key))}: {counts[key]}"
            for key in sorted(counts)
            if key not in known_keys
        ]
        if extra:
            return ", ".join(extra)
        return None

    lines = [
        "📊 *Общая статистика*",
        "",
        "👥 *Аккаунты*",
        f"• Всего: {accounts.get('total', 0)}",
        f"• Активны: {accounts_by_status.get('running', 0)}",
        f"• Остановлены: {accounts_by_status.get('stopped', 0)}",
    ]

    other_account_statuses = _format_additional(accounts_by_status, {"running", "stopped"})
    if other_account_statuses:
        lines.append(f"• Прочие статусы: {other_account_statuses}")

    if accounts_by_mode:
        lines.append("• Режимы:")
        for mode, count in sorted(accounts_by_mode.items()):
            lines.append(f"   ◦ {escape_markdown_text(str(mode))}: {count}")

    if running_by_mode:
        lines.append("• Активные по режимам:")
        for mode, count in sorted(running_by_mode.items()):
            lines.append(f"   ◦ {escape_markdown_text(str(mode))}: {count}")

    lines.extend(
        [
            "",
            "💬 *Комментарии*",
            f"• Всего: {comments.get('total', 0)}",
            f"• Успешные: {comment_counts.get('success', 0)}",
            f"• Ошибки: {comment_counts.get('error', 0)}",
            f"• Пропущено: {comment_counts.get('skipped', 0)}",
            f"• Нет ветки/запрещено: {comment_counts.get('no_comments', 0)}",
        ]
    )

    other_comment_statuses = _format_additional(
        comment_counts, {"success", "error", "skipped", "no_comments"}
    )
    if other_comment_statuses:
        lines.append(f"• Прочие статусы: {other_comment_statuses}")

    lines.extend(
        [
            "",
            "😊 *Реакции*",
            f"• Всего: {reaction_total}",
            f"• Успешные: {reaction_counts.get('success', 0)}",
            f"• Ошибки: {reaction_counts.get('error', 0)}",
            f"• Пропущено: {reaction_counts.get('skipped', 0)}",
        ]
    )

    other_reaction_statuses = _format_additional(
        reaction_counts, {"success", "error", "skipped"}
    )
    if other_reaction_statuses:
        lines.append(f"• Прочие статусы: {other_reaction_statuses}")

    lines.extend(
        [
            "",
            "🔥 *Прогрев*",
            f"• Вступлений: {warmup_counts.get('joined', 0)}",
            f"• В очереди: {warmup_counts.get('pending', 0)}",
            f"• Ошибок: {warmup_counts.get('error', 0)}",
            f"• Попыток вступления: {warmup.get('total_attempts', 0)}",
        ]
    )

    other_warmup_statuses = _format_additional(warmup_counts, {"joined", "pending", "error"})
    if other_warmup_statuses:
        lines.append(f"• Прочие статусы: {other_warmup_statuses}")

    return "\n".join(lines)

async def get_account_summary(account_id):
    """Получает полное резюме аккаунта из базы данных"""
    account = await get_account_by_id(account_id)
    if not account:
        return None

    def sanitize_field(value, default="N/A"):
        """Подготавливает значение для безопасного отображения в Markdown."""
        if value is None or value == "":
            value = default
        return escape_markdown_text(str(value))

    # Получаем реальные подписки аккаунта из Telegram
    real_channels = []
    try:
        session_path = account.get('session_path', '')
        if session_path and os.path.exists(session_path):
            ensure_session_file_permissions(session_path)

            user_id_value = account.get("user_id")
            phone_value = account.get("phone")
            lock_key = None
            if user_id_value is not None and phone_value:
                lock_key = make_session_key(int(user_id_value), str(phone_value))

            session_name = session_path.replace('.session', '')

            async def _fetch_channels(app: Client) -> List[str]:
                channels: List[str] = []
                async for dialog in app.get_dialogs():
                    chat = dialog.chat
                    if str(chat.type) == "ChatType.CHANNEL" and chat.username:
                        channels.append(f"@{chat.username}")
                return channels

            collected = await with_retry(
                session_name,
                _fetch_channels,
                lock_key=lock_key,
            )
            real_channels.extend(collected)
    except Exception as e:
        logging.warning(f"Ошибка при получении подписок для аккаунта {account_id}: {e}")
        # Если не удалось получить реальные подписки, используем из БД
        real_channels = account.get('channels', []) or []
    
    # Получаем каналы из БД (для сравнения)
    db_channels = account.get('channels', []) or []
    
    # Получаем каналы прогрева
    warmup_channels = await get_warmup_pending(account_id, limit=100)
    warmup_list = [ch["channel"] for ch in warmup_channels] if warmup_channels else []
    
    # Формируем превью системного промта
    PROMPT_PREVIEW_LIMIT = 200
    prompt_preview, was_truncated = build_prompt_preview(account.get('system_prompt'), PROMPT_PREVIEW_LIMIT)
    if prompt_preview == "Не задан":
        prompt_block = "Промпт не задан."
    else:
        notice = (
            f"Показана сокращённая версия промта (первые {PROMPT_PREVIEW_LIMIT} символов)."
            if was_truncated
            else "Показана сокращённая версия промта (полный текст помещается в лимит)."
        )
        prompt_block = f"{notice}\n{escape_markdown_text(prompt_preview)}"

    # Формируем резюме
    phone = sanitize_field(account.get('phone', 'N/A'))
    sleep_min = sanitize_field(account.get('sleep_min', 'N/A'))
    sleep_max = sanitize_field(account.get('sleep_max', 'N/A'))
    chance = sanitize_field(account.get('chance', 'N/A'))
    reaction_chance_value = account.get('reaction_chance')
    reaction_chance_display = _format_chance(reaction_chance_value)
    reaction_discussion_chance_value = account.get('reaction_discussion_chance')
    reaction_discussion_chance_display = _format_chance(reaction_discussion_chance_value)
    discussion_reply_chance_value = account.get('discussion_reply_chance')
    discussion_reply_chance_display = _format_chance(discussion_reply_chance_value)
    discussion_prompt_preview, _ = build_prompt_preview(account.get('discussion_reply_prompt'), 100)
    reaction_sleep_min_value = account.get('reaction_sleep_min')
    reaction_sleep_max_value = account.get('reaction_sleep_max')
    reaction_limit_value = account.get('reaction_limit_per_message')
    reaction_limit_display = _format_reaction_limit(reaction_limit_value)
    if reaction_sleep_min_value is None or reaction_sleep_max_value is None:
        reaction_delay_display = "не заданы"
    else:
        reaction_delay_display = f"{reaction_sleep_min_value}-{reaction_sleep_max_value}"
    reaction_emojis_list = account.get('reaction_emojis') or []
    reaction_emojis_display = " ".join(reaction_emojis_list) if reaction_emojis_list else "не заданы"
    reactions_enabled_value = account.get('reactions_enabled')
    if reactions_enabled_value is None:
        reactions_enabled_display = "да"
    else:
        reactions_enabled_display = "да" if reactions_enabled_value else "нет"
    reaction_chance = escape_markdown_text(reaction_chance_display)
    reaction_discussion_chance = escape_markdown_text(reaction_discussion_chance_display)
    discussion_reply_chance = escape_markdown_text(discussion_reply_chance_display)
    discussion_prompt = escape_markdown_text(discussion_prompt_preview)
    reaction_delay = escape_markdown_text(reaction_delay_display)
    reaction_emojis = escape_markdown_text(reaction_emojis_display)
    reaction_limit = escape_markdown_text(reaction_limit_display)
    mode = sanitize_field(account.get('mode', 'N/A'))
    status = sanitize_field(account.get('status', 'N/A'))
    warmup_end_at = sanitize_field(account.get('warmup_end_at', 'N/A'))
    warmup_joined_today = sanitize_field(account.get('warmup_joined_today', 0))
    warmup_next_join_at = sanitize_field(account.get('warmup_next_join_at', 'N/A'))
    last_started_at = sanitize_field(account.get('last_started_at', 'N/A'))
    last_stopped_at = sanitize_field(account.get('last_stopped_at', 'N/A'))
    updated_at = sanitize_field(account.get('updated_at', 'N/A'))

    summary = f"""
📊 **Резюме аккаунта {phone}**

⚙️ **Настройки:**
• Задержка: {sleep_min}-{sleep_max} сек
• Шанс комментирования: {chance}%
• Шанс реакции: {reaction_chance}
• Реакции включены: {reactions_enabled_display}
• Шанс реакции в обсуждениях: {reaction_discussion_chance}
• Шанс ответов в обсуждениях: {discussion_reply_chance}
• Промт обсуждений: {discussion_prompt}
• Задержка реакции: {reaction_delay}
• Эмодзи реакций: {reaction_emojis}
• Лимит реакций на пост: {reaction_limit}
• Режим: {mode}
• Статус: {status}

📝 **Системный промпт (превью):**
{prompt_block}

📺 **Реальные подписки ({len(real_channels)}):**
{await format_channels_display(real_channels, "Подписки", 10)}

📋 **Каналы в БД ({len(db_channels)}):**
{await format_channels_display(db_channels, "В базе", 5)}

🔥 **Каналы прогрева ({len(warmup_list)}):**
{await format_channels_display(warmup_list, "Прогрев", 10)}

📅 **Время прогрева:**
• Завершение: {warmup_end_at}
• Вступлений сегодня: {warmup_joined_today}
• Следующее вступление: {warmup_next_join_at}

🕐 **Время работы:**
• Запущен: {last_started_at}
• Остановлен: {last_stopped_at}
• Обновлен: {updated_at}
"""
    return summary

async def send_account_summary_to_user(user_id, account_id, session_name):
    """Отправляет резюме аккаунта пользователю"""
    summary = await get_account_summary(account_id)
    if summary:
        await bot.send_message(user_id, summary, parse_mode="Markdown")
    else:
        await bot.send_message(user_id, f"❌ Не удалось получить информацию об аккаунте {session_name}")

async def send_account_summary_to_logs(account_id, session_name):
    """Отправляет резюме аккаунта в лог-канал"""
    summary = await get_account_summary(account_id)
    if summary:
        await bot.send_message(log_channel, f"📊 **Резюме аккаунта {session_name}**\n{summary}", parse_mode="Markdown")
    else:
        await bot.send_message(log_channel, f"❌ Не удалось получить информацию об аккаунте {session_name}")

async def main():
    """Main entry point with improved error handling and logging."""
    global reaction_engine_instance, REACTION_ENGINE_AVAILABLE
    
    # Setup logging (уже настроено выше, но обновляем для совместимости)
    log_file_path = os.path.join("logs_od1", "logs.txt")
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    
    # Получаем root logger и добавляем file handler если его еще нет
    root_logger = logging.getLogger()
    has_file_handler = any(isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_file_path) for h in root_logger.handlers)
    if not has_file_handler:
        file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(f"[{BOT_NAME}] %(asctime)s %(levelname)s %(name)s: %(message)s"))
        root_logger.addHandler(file_handler)
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Starting Telegram Comment Bot")
    logger.info("=" * 60)
    
    try:
        _acquire_process_lock()
        logger.info("Process lock acquired")
        
        with open("bot_log.txt", "w") as log_file:
            log_file.write("Starting bot initialization...\n")
            log_file.flush()
            
            logger.info("Initializing database...")
            await init_db()
            logger.info("Database initialized successfully")
            
            logger.info("Setting up warmup settings...")
            await ensure_warmup_settings(
                channels_per_day=DEFAULT_WARMUP_SETTINGS.channels_per_day,
                delay_minutes=DEFAULT_WARMUP_SETTINGS.delay_minutes,
                join_start_hour=DEFAULT_WARMUP_SETTINGS.join_start_hour,
                join_start_minute=DEFAULT_WARMUP_SETTINGS.join_start_minute,
                join_end_hour=DEFAULT_WARMUP_SETTINGS.join_end_hour,
                join_end_minute=DEFAULT_WARMUP_SETTINGS.join_end_minute,
            )
            await ensure_latest_warmup_settings(force=True)

            if REACTION_ENGINE_AVAILABLE and 'ReactionEngine' in globals() and ReactionEngine is not None:
                try:
                    reaction_engine_instance = ReactionEngine()
                    if await reaction_engine_instance.initialize():
                        logger.info("✅ ReactionEngine started successfully")
                        log_file.write("ReactionEngine initialized successfully\n")
                        log_file.flush()
                        try:
                            await reaction_engine_instance.ensure_reaction_tables_exist()
                        except Exception as exc:
                            logger.warning("⚠️ ReactionEngine table verification issue: %s", exc)
                    else:
                        logger.error("❌ Failed to initialize ReactionEngine")
                        log_file.write("ReactionEngine initialization failed\n")
                        log_file.flush()
                        reaction_engine_instance = None
                        REACTION_ENGINE_AVAILABLE = False
                except Exception as exc:
                    logger.error("❌ Critical error during ReactionEngine initialization: %s", exc)
                    log_file.write("ReactionEngine critical initialization error\n")
                    log_file.flush()
                    reaction_engine_instance = None
                    REACTION_ENGINE_AVAILABLE = False
            else:
                logger.warning("⚠️ ReactionEngine not available")
                log_file.write("ReactionEngine not available\n")
                log_file.flush()

            logging.info("✅ Database initialized successfully")
            log_file.write("Database initialized successfully\n")
            log_file.flush()

            deleted_logs = await cleanup_comment_logs(COMMENT_LOG_RETENTION_DAYS)
            logging.info("Удалено устаревших записей comment_logs при запуске: %d", deleted_logs)
            log_file.write(
                f"Removed {deleted_logs} outdated comment log entries at startup\\n"
            )
            log_file.flush()

            await bot.delete_webhook(drop_pending_updates=True)
            log_file.write("Webhook deleted\n")
            log_file.flush()
            
            running_accounts = await get_running_accounts()
            log_file.write(f"Found {len(running_accounts)} running accounts\n")
            log_file.flush()
            
            for account in running_accounts:
                user_id = account["user_id"]
                phone = account["phone"]
                key = make_session_key(user_id, phone)
                session_file = os.path.join(SESSIONS_BASE_DIR, str(user_id), f"{phone}.session")

                if os.path.exists(session_file):
                    # Перед повторным запуском очищаем прошлые записи, чтобы избежать дублирования
                    active_sessions.pop(key, None)
                    active_account_ids.pop(key, None)
                    active_pyrogram_clients.pop(key, None)
                    _release_session_lock(key)

                    if account.get("status") != "running":
                        await mark_account_running(account["id"])

                    active_sessions[key] = True
                    active_account_ids[key] = account["id"]
                    _schedule_safe_send_comments(user_id, phone, account["id"])
                    log_file.write(
                        f"Started account {phone} in {account.get('mode', 'unknown')} mode\n"
                    )
                    log_file.flush()
                else:
                    await mark_account_stopped(account["id"])
                    log_file.write(f"Stopped account {phone} - no session file\n")
                    log_file.flush()

            logging.info("✅ Starting background tasks...")
            log_file.write("Starting background tasks...\n")
            log_file.flush()

            asyncio.create_task(process_warmup_accounts())
            asyncio.create_task(process_standard_accounts())
            asyncio.create_task(comment_log_cleanup_worker())
            log_file.write("Starting bot polling...\n")
            log_file.flush()

            await dp.start_polling(bot)
    except Exception as e:
        with open("bot_error.txt", "w") as error_file:
            error_file.write(f"Error in main: {e}\n")
            error_file.write(f"Exception type: {type(e).__name__}\n")
            import traceback
            error_file.write(traceback.format_exc())
        logging.exception("Main function error: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run(bot.run())
