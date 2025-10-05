import json
import os
import asyncio
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Dict, List, Optional, Set, Any, Union, Tuple
import random
import re
from pyrogram import Client, filters
from pyrogram.errors import ChatWriteForbidden, UserAlreadyParticipant
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from comment_engine import generate_comment
from db import (
    add_comment_log,
    bulk_update_reaction_settings,
    count_reactions_for_message,
    db_update_warmup_schedule,
    delete_account,
    ensure_account,
    ensure_user,
    ensure_warmup_settings,
    get_account_by_id,
    get_account_by_session,
    get_accounts_for_user,
    get_global_statistics,
    get_running_accounts,
    get_warmup_pending,
    get_warmup_settings,
    increment_warmup_joined,
    init_db,
    is_user_authenticated,
    mark_account_running,
    mark_account_stopped,
    mark_warmup_channel_joined,
    record_warmup_channel_error,
    reset_warmup_daily_state,
    set_account_mode,
    set_user_authenticated,
    sync_warmup_channels,
    update_account_settings,
    update_last_reaction_at,
    update_warmup_settings,
    _require_pool,
)
from dotenv import load_dotenv


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
                    env_vars[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env_vars

# Load environment variables
env_vars = load_env_file()


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


BOT_TOKEN = env_vars.get("BOT_TOKEN") or os.getenv("BOT_TOKEN")
print(f"BOT_TOKEN loaded: {BOT_TOKEN}")
#APCDXBOT0310 @AP_comment_bot
log_channel = -1003123025616 # cloveend #-1002711973256 #-1002678984799

API_ID = int(env_vars.get("API_ID") or os.getenv("API_ID"))
print(f"API_ID loaded: {API_ID}")
API_HASH = env_vars.get("API_HASH") or os.getenv("API_HASH")
print(f"API_HASH loaded: {API_HASH}")
#1823

WARMUP_VERBOSE_LOGS = _get_bool_env("WARMUP_VERBOSE_LOGS", default=False)
WARMUP_VERBOSE_NOTIFICATIONS = _get_bool_env("WARMUP_VERBOSE_NOTIFICATIONS", default=False)

DEFAULT_REACTION_LIMIT_PER_MESSAGE = _get_int_env("REACTION_LIMIT_PER_MESSAGE")
REACTION_MIN_INTERVAL_SECONDS = _get_int_env("REACTION_MIN_INTERVAL_SECONDS") or 0

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.DEBUG if WARMUP_VERBOSE_LOGS else logging.INFO)



# Состояние для ввода пароля
class AuthState(StatesGroup):
    waiting_for_password = State()

class addsession(StatesGroup):
    number = State()
    code = State()
    code_hash = State()
    client = State()

class startaccount(StatesGroup):
    channels = State()
    account = State()
    systempromt = State()
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

active_sessions: Dict[str, bool] = {}  # Глобальный словарь для хранения активных сессий
active_account_ids: Dict[str, int] = {}
quiet_sessions_notified: Set[str] = set()
active_pyrogram_clients: Dict[str, Client] = {}
active_client_locks: Dict[str, asyncio.Lock] = {}


CHECK_ACCOUNT_SHUTDOWN_TIMEOUT = 5.0
CHECK_ACCOUNT_SHUTDOWN_INTERVAL = 0.2


def _is_discussion_reply_message(message: Any) -> bool:
    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return False
    forward_chat = getattr(reply, "forward_from_chat", None)
    return forward_chat is not None


def _is_self_generated_message(message: Any) -> bool:
    from_user = getattr(message, "from_user", None)
    if not from_user:
        return False
    return bool(getattr(from_user, "is_self", False))


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


def _extract_post_text(message: Any) -> Optional[str]:
    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)
    return text if text is not None else caption


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
    system_promt: str,
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
) -> Optional[datetime]:
    key = make_session_key(userid, session)
    if not active_sessions.get(key, False):
        return last_reaction_at

    current_last_reaction_at = last_reaction_at

    is_discussion_message = _is_discussion_reply_message(message)
    if is_discussion_message and _is_self_generated_message(message):
        logging.debug("Обнаружен собственный комментарий в обсуждении для %s", session)
        return current_last_reaction_at

    if is_discussion_message:
        if discussion_reply_prompt is None or discussion_reply_chance is None:
            logging.debug(
                "Обсуждения отключены для %s: отсутствуют настройки", session
            )
            return current_last_reaction_at
        comment_chance = discussion_reply_chance
        comment_prompt = discussion_reply_prompt
        selected_reaction_chance = reaction_discussion_chance or 0
    else:
        comment_chance = chance
        comment_prompt = system_promt
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

    try:
        comment_sent = False
        post_base_link: Optional[str] = None

        roll = random.randint(1, 100)
        if roll > comment_chance:
            await bot.send_message(
                log_channel,
                f'Аккаунт {session} пропустил комментарий (rnd {roll} > {comment_chance})'
            )
            await add_comment_log(
                account_id,
                channel=str(getattr(getattr(message, "chat", None), "id", "")),
                message_id=getattr(message, "id", None),
                status='comment_skipped',
                error=f'random {roll} > chance {comment_chance}',
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
            comment = generate_comment(post_text, comment_prompt)
            msg = await client.send_message(message.chat.id, comment, reply_to_message_id=message.id)

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

        if (
            reactions_enabled
            and reaction_emojis
            and (selected_reaction_chance or 0) > 0
        ):
            if post_base_link is None:
                post_base_link = _build_post_link(message, message)
            channel_for_reactions = str(getattr(getattr(message, "chat", None), "id", ""))
            message_id = getattr(message, "id", None)
            limit = reaction_limit_per_message
            status_suffix = '' if comment_sent else '_no_comment'

            if limit is not None:
                if limit <= 0:
                    await asyncio.sleep(0.2)
                    await add_comment_log(
                        account_id,
                        channel=channel_for_reactions,
                        message_id=message_id,
                        status=f'reaction_skipped{status_suffix}',
                        error=f'reaction limit {limit} reached',
                    )
                    return current_last_reaction_at
                if channel_for_reactions and message_id is not None:
                    reaction_count = await count_reactions_for_message(channel_for_reactions, message_id)
                    if reaction_count >= limit:
                        await asyncio.sleep(0.2)
                        await add_comment_log(
                            account_id,
                            channel=channel_for_reactions,
                            message_id=message_id,
                            status=f'reaction_skipped{status_suffix}',
                            error=f'reaction limit {reaction_count}/{limit}',
                        )
                        return current_last_reaction_at

            reaction_roll = random.randint(1, 100)
            if reaction_roll <= (selected_reaction_chance or 0):
                await asyncio.sleep(random.uniform(reaction_sleep_min, reaction_sleep_max))
                reaction_emoji = random.choice(reaction_emojis)
                try:
                    now = datetime.now(timezone.utc)
                    previous = current_last_reaction_at
                    if previous is not None and previous.tzinfo is None:
                        previous = previous.replace(tzinfo=timezone.utc)
                    cooldown_seconds = REACTION_MIN_INTERVAL_SECONDS
                    if previous is not None and cooldown_seconds:
                        if now - previous < timedelta(seconds=cooldown_seconds):
                            await asyncio.sleep(0.2)
                            await add_comment_log(
                                account_id,
                                channel=str(message.chat.id),
                                message_id=message.id,
                                status=f'reaction_skipped{status_suffix}',
                                error=(
                                    'reaction cooldown '
                                    f"{int((now - previous).total_seconds())}/{cooldown_seconds}s"
                                ),
                            )
                            return current_last_reaction_at

                    await client.send_reaction(message.chat.id, message.id, reaction_emoji)
                    await bot.send_message(
                        log_channel,
                        f'Аккаунт {session} поставил реакцию {reaction_emoji}\n{post_base_link}'
                    )
                    await update_last_reaction_at(account_id, now)
                    current_last_reaction_at = now
                    await asyncio.sleep(0.2)
                    await add_comment_log(
                        account_id,
                        channel=str(message.chat.id),
                        message_id=message.id,
                        status=f'reaction_success{status_suffix}',
                    )
                except Exception as reaction_error:
                    await bot.send_message(
                        log_channel,
                        f'Аккаунт {session} ошибка при установке реакции: {reaction_error}'
                    )
                    await asyncio.sleep(0.2)
                    await add_comment_log(
                        account_id,
                        channel=str(message.chat.id),
                        message_id=message.id,
                        status=f'reaction_error{status_suffix}',
                        error=str(reaction_error),
                    )
            else:
                await asyncio.sleep(0.2)
                await add_comment_log(
                    account_id,
                    channel=str(message.chat.id),
                    message_id=message.id,
                    status=f'reaction_skipped{status_suffix}',
                    error=f'reaction random {reaction_roll} > chance {selected_reaction_chance}',
                )

    except ChatWriteForbidden as e:
        await bot.send_message(log_channel, f'Аккаунт {session} не может оставить комментарий: {e}')
        await asyncio.sleep(0.2)
        await add_comment_log(
            account_id,
            channel=str(message.chat.id),
            message_id=message.id,
            status='no_comments',
            error=str(e),
        )
    except Exception as e:
        await bot.send_message(log_channel, f'Аккаунт {session} ошибка комментирования: {e}')
        # Пауза перед записью ошибки в БД
        await asyncio.sleep(0.2)
        await add_comment_log(
            account_id,
            channel=str(message.chat.id),
            message_id=message.id,
            status='error',
            error=str(e),
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
        print(f"Ошибка загрузки schedule.json: {e}")
        return default_config

    if not isinstance(file_config, dict):
        return default_config

    merged = default_config.copy()
    merged.update({k: v for k, v in file_config.items() if k in ("quiet_period", "warmup_period", "warmup_settings")})

    warmup_defaults = merged.setdefault("warmup_settings", {})
    for key, value in default_config["warmup_settings"].items():
        warmup_defaults.setdefault(key, value)

    return merged

# Загружаем настройки расписания
SCHEDULE_CONFIG = load_schedule_config()

# Настройки времени из schedule.json
QUIET_START_HOUR = SCHEDULE_CONFIG["quiet_period"]["start_hour"]
QUIET_START_MINUTE = SCHEDULE_CONFIG["quiet_period"]["start_minute"]
QUIET_END_HOUR = SCHEDULE_CONFIG["quiet_period"]["end_hour"]
QUIET_END_MINUTE = SCHEDULE_CONFIG["quiet_period"]["end_minute"]

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
account_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)


def make_session_key(user_id: int, phone: str) -> str:
    return f"{user_id}:{phone}"


def _parse_warmup_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
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
        return base_start
    if reference >= end_dt:
        return base_start + timedelta(days=1)
    return base_start


def plan_next_warmup_join(earliest: datetime, settings: Optional[WarmupSettingsData] = None) -> datetime:
    settings = settings or get_current_warmup_settings()
    if earliest.tzinfo is None:
        current = earliest.replace(tzinfo=timezone.utc)
    else:
        current = earliest.astimezone(timezone.utc)

    window_duration = _warmup_window_duration(settings)

    while True:
        window_start = _resolve_window_start(current, settings)
        window_end = window_start + window_duration

        if current < window_start:
            current = window_start
        elif current >= window_end:
            current = window_start + timedelta(days=1)
            continue

        delay_seconds = _get_human_delay_seconds(settings)
        candidate = current + timedelta(seconds=delay_seconds)

        if candidate < window_end:
            return candidate

        current = window_start + timedelta(days=1)


def _get_next_warmup_join(now: datetime, settings: Optional[WarmupSettingsData] = None) -> datetime:
    return plan_next_warmup_join(now, settings)


def is_quiet_period(now: datetime | None = None) -> bool:
    f"""Проверяет, находимся ли мы в тихом периоде
    ({QUIET_START_MSK_STR}-{QUIET_END_MSK_STR} МСК = {QUIET_START_UTC_STR}-{QUIET_END_UTC_STR} UTC)"""
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    start = time(QUIET_START_HOUR, QUIET_START_MINUTE)
    end = time(QUIET_END_HOUR, QUIET_END_MINUTE)
    
    # Обрабатываем случай, когда период переходит через полночь (21:30-04:30)
    if start > end:  # 21:30 > 04:30
        return current_time >= start or current_time < end
    else:
        return start <= current_time < end


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
    existing_client = active_pyrogram_clients.get(key)

    if existing_client:
        lock = active_client_locks.setdefault(key, asyncio.Lock())
        should_cleanup_lock = False
        async with lock:
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
                should_cleanup_lock = True

        if should_cleanup_lock:
            active_pyrogram_clients.pop(key, None)
            active_client_locks.pop(key, None)

    client = Client(
        name=f"sessions/{user_id}/{phone}",
        api_id=API_ID,
        api_hash=API_HASH,
    )

    try:
        await client.connect()
        await client.get_me()
        return True
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower():
            await bot.send_message(user_id, f"Аккаунт {phone} сейчас используется, попробуйте позже")
            return False
        raise
    except Exception as e:
        await asyncio.sleep(1)
        await bot.send_message(user_id, f"Аккаунт удален ошибка: {str(e)}")

        session_path = f'sessions/{user_id}/{phone}.session'
        if os.path.exists(session_path):
            os.remove(session_path)
        await delete_account(user_id, phone)
        return False
    finally:
        if getattr(client, "is_connected", False):
            await client.disconnect()

async def main_message(message):
    user_id = message.from_user.id
    await ensure_user(user_id)
    if not os.path.isdir("sessions"):
        os.makedirs("sessions", exist_ok=True)
    user_sessions_dir = os.path.join("sessions", str(user_id))
    if not os.path.isdir(user_sessions_dir):
        os.makedirs(user_sessions_dir, exist_ok=True)

    db_accounts = await get_accounts_for_user(user_id)
    existing_accounts = {account["phone"]: account for account in db_accounts}

    for file in os.listdir(user_sessions_dir):
        if file.endswith('.session') and not file.endswith('.session.session'):
            phone = file.replace('.session', '')
            if phone not in existing_accounts:
                session_path = os.path.join(user_sessions_dir, file)
                await ensure_account(user_id, phone, session_path)

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

        builder.row(button_info, button_status, button_warmup)
        builder.row(button_reactions)
        if not is_running:
            builder.row(button_delete)


    builder.row(
        types.InlineKeyboardButton(text="Добавить аккаунт", callback_data="add_account"),
        types.InlineKeyboardButton(text="Добавить прогрев", callback_data="add_warmup"),
    )
    builder.row(
        types.InlineKeyboardButton(text="📊 Общая статистика", callback_data="global_stats"),
        types.InlineKeyboardButton(text="⚙️ Настройки прогрева", callback_data="warmup_settings"),
    )

    await bot.send_message(message.from_user.id, 'Ваши аккаунты', reply_markup=builder.as_markup())



# Проверка пароля при первом запуске
PASSWORD = env_vars.get("PASSWORD") or os.getenv("PASSWORD")

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await ensure_user(message.from_user.id)
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Введите пароль для доступа:")
        await state.set_state(AuthState.waiting_for_password)
    else:
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
        user_sessions_dir = os.path.join("sessions", str(user_id))
        
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
    call = callback_query.data

    if call == "reaction_apply_all":
        await state.update_data({"apply_reactions_to_all": True})
        await callback_query.answer("Применяем настройки ко всем аккаунтам")
        await _save_reaction_settings(
            callback_query.message,
            state,
            finalize=True,
            notify=False,
        )
        return

    await callback_query.message.delete()

    if call == 'add_account':
        await bot.send_message(callback_query.from_user.id, 'Пришлите номер телефона\nПример: 79999999999')
        await state.set_state(addsession.number)

    elif call == 'add_warmup':
        await state.clear()
        await bot.send_message(callback_query.from_user.id, 'Пришлите номер телефона для прогрева\nПример: 79999999999')
        await state.set_state(addsession.number)
        await state.update_data({"warmup_only": True})


    elif call == 'warmup_settings':
        await callback_query.answer()
        await state.clear()
        settings = await refresh_warmup_settings_from_db()
        prompt = (
            f"{format_warmup_settings(settings)}\n\n"
            "Введите новый лимит вступлений в день (число) или '-' чтобы оставить без изменений."
        )
        await bot.send_message(callback_query.from_user.id, prompt)
        await state.set_state(warmupsettings.limit)
        return

    elif call == 'global_stats':
        await callback_query.answer()
        stats = await get_global_statistics()
        report = format_global_statistics_report(stats)
        await bot.send_message(callback_query.from_user.id, report, parse_mode="Markdown")
        if callback_query.from_user.id != log_channel:
            await bot.send_message(log_channel, report, parse_mode="Markdown")
        await main_message(callback_query)
        return


    elif 'info_' in call:
        session = str(call).split('_', 1)[1]

        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        account_id = account_row.get("id")
        if not account_id:
            await bot.send_message(callback_query.from_user.id, f"Не удалось определить идентификатор аккаунта {session}")
            await main_message(callback_query)
            return

        await send_account_summary_to_user(callback_query.from_user.id, account_id, session)
        await main_message(callback_query)
        return


    elif call.startswith('warmclear_'):
        await callback_query.answer()
        session = str(call).split('_', 1)[1]
        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
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
            confirmation_text = f"Не удалось очистить очередь прогрева: {exc}"

        await bot.send_message(callback_query.from_user.id, confirmation_text)
        await state.clear()
        await main_message(callback_query)
        return

    elif call.startswith('warmup_'):
        await callback_query.answer()
        session = str(call).split('_', 1)[1]
        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
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
            callback_query.from_user.id,
            "\n".join(line for line in prompt_lines if line),
            reply_markup=keyboard,
        )
        return


    elif call.startswith('reaction_'):
        session = str(call).split('_', 1)[1]

        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
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

    elif 'start_' in call:

        session = str(call).split('_')[1]

        key = make_session_key(callback_query.from_user.id, session)
        if active_sessions.get(key):
            await bot.send_message(callback_query.from_user.id, f"Аккаунт {session} уже запущен")
            return


        if await check_account(callback_query.from_user.id, session):

            try:
                account_row = await get_account_by_session(callback_query.from_user.id, session)
                if not account_row:
                    await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
                    await main_message(callback_query)
                    return

                account_id = account_row["id"]
                await state.clear()
                await state.update_data({"account": session, "account_id": account_id})

                await callback_query.answer()
                await _prompt_system_prompt(callback_query, state)
                await state.set_state(startaccount.systempromt)
            except Exception as e:
                await bot.send_message(callback_query.from_user.id, f"Ошибка: {str(e)}")
                await main_message(callback_query)
        else:
            await main_message(callback_query)
            return

    elif 'del_' in call:
        session = str(call).split('_')[1]

        
        key = make_session_key(callback_query.from_user.id, session)
        if active_sessions.get(key):
            await bot.send_message(callback_query.from_user.id, f"Аккаунт {session} в работе")
            return

        try:
            await delete_account(callback_query.from_user.id, session)
            
            # Удаляем оба типа файлов сессий
            session_file = f'sessions/{callback_query.from_user.id}/{session}.session'
            session_file_alt = f'sessions/{callback_query.from_user.id}/{session}.session.session'
            
            deleted_files = []
            if os.path.exists(session_file):
                os.remove(session_file)
                deleted_files.append(f"{session}.session")
                
            if os.path.exists(session_file_alt):
                os.remove(session_file_alt)
                deleted_files.append(f"{session}.session.session")
            
            await bot.send_message(log_channel, f"Аккаунт {session} удален. Удалены файлы: {', '.join(deleted_files)}")
            await main_message(callback_query)
        except Exception as e:
            await bot.send_message(callback_query.from_user.id, f"Ошибка: {str(e)}")
            await main_message(callback_query)


    elif 'stop_' in call:

        session = str(call).split('_')[1]
        key = make_session_key(callback_query.from_user.id, session)
        
        # Проверяем, запущен ли аккаунт (в active_sessions или в БД)
        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        is_running = active_sessions.get(key) or account_row.get("status") == "running"
        if not is_running:
            await bot.send_message(callback_query.from_user.id, f"Аккаунт {session} не запущен")
            await main_message(callback_query)
            return

        try:
            # Останавливаем аккаунт
            active_sessions.pop(key, None)
            account_id = active_account_ids.pop(key, None)

            # Останавливаем аккаунт в БД
            await mark_account_stopped(account_row["id"])

            await bot.send_message(callback_query.from_user.id, f'Аккаунт {session} остановлен')
            await bot.send_message(log_channel, f'Аккаунт {session} остановлен')
            await main_message(callback_query)

        except Exception as e:
            await bot.send_message(callback_query.from_user.id, f"Ошибка: {str(e)}")
            await main_message(callback_query)


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
            f"Текущий шанс комментирования: {display}.\n"
            "Отправьте значение от 0 до 100 или '-' для сохранения текущего."
        ),
    )


async def _prompt_system_prompt(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)
    stored = account.get("system_prompt") if account else None
    if stored is None:
        stored = data.get("systempromt")

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

    app = Client(
        name=f"sessions/{message.from_user.id}/{session}",
        api_id=API_ID,
        api_hash=API_HASH)

    if await check_account(message.from_user.id, session):
        async with app:
            async for dialog in app.get_dialogs():
                chat = dialog.chat
                if str(chat.type) == "ChatType.CHANNEL" and chat.username is not None:
                    channel_handle = f"@{chat.username}"
                    if channel_handle not in seen_channels:
                        channels.append(channel_handle)
                        seen_channels.add(channel_handle)

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
        value = int(raw)  # type: ignore[arg-type]
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


def _extract_available_reaction_emojis(available: Any) -> Set[str]:
    result: Set[str] = set()
    if not available:
        return result

    for item in available:
        if item is None:
            continue

        emoji_value = getattr(item, "emoji", None)
        if emoji_value:
            result.add(emoji_value)
            continue

        nested = getattr(item, "reaction", None)
        emoji_value = getattr(nested, "emoji", None)
        if emoji_value:
            result.add(emoji_value)

    return result


async def _get_available_quick_reaction_emojis(
    user_id: int,
    session: Optional[str],
    chat_id: Optional[int] = None,
) -> Optional[Set[str]]:
    if not session:
        return None

    key = make_session_key(user_id, session)
    existing_client = active_pyrogram_clients.get(key)

    async def _query(client_obj: Client) -> Optional[Set[str]]:
        try:
            if chat_id is not None:
                try:
                    available = await client_obj.get_available_reactions(chat_id)
                except TypeError:
                    available = await client_obj.get_available_reactions()
            else:
                available = await client_obj.get_available_reactions()
        except Exception:
            logging.exception(
                "Failed to request available reactions for session %s", session
            )
            return None

        return _extract_available_reaction_emojis(available)

    if existing_client and getattr(existing_client, "is_connected", False):
        return await _query(existing_client)

    session_dir = os.path.join("sessions", str(user_id))
    session_name = os.path.join(session_dir, session)
    session_file = f"{session_name}.session"

    if not os.path.exists(session_file):
        alt_session_file = f"{session_file}.session"
        if os.path.exists(alt_session_file):
            try:
                shutil.copy2(alt_session_file, session_file)
            except Exception:
                logging.exception(
                    "Не удалось подготовить файл сессии для получения доступных реакций: %s",
                    alt_session_file,
                )
                return None
        else:
            logging.warning(
                "Session file %s not found while fetching available reactions", session_file
            )
            return None

    client = Client(
        name=session_name,
        api_id=API_ID,
        api_hash=API_HASH,
    )

    try:
        async with client:
            return await _query(client)
    except Exception:
        logging.exception(
            "Failed to fetch available reactions for session %s", session
        )
        return None


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

        session_name = data.get("account")
        chat_id = data.get("reaction_channel_id")
        available_emojis = await _get_available_quick_reaction_emojis(
            message.from_user.id,
            str(session_name) if isinstance(session_name, str) else None,
            int(chat_id) if isinstance(chat_id, int) else None,
        )

        if available_emojis is not None:
            filtered = [emoji for emoji in unique_emojis if emoji in available_emojis]
            invalid = [emoji for emoji in unique_emojis if emoji not in available_emojis]

            available_text = " ".join(sorted(available_emojis)) if available_emojis else "нет доступных реакций"

            if not filtered:
                await bot.send_message(
                    message.from_user.id,
                    (
                        "Ни один из указанных эмодзи недоступен для быстрых реакций.\n"
                        f"Доступные реакции: {available_text}."
                    ),
                )
                await _prompt_reaction_emojis(message, state)
                return

            if invalid:
                invalid_text = " ".join(invalid)
                await bot.send_message(
                    message.from_user.id,
                    (
                        "Следующие эмодзи недоступны и будут пропущены: "
                        f"{invalid_text}.\nДоступные реакции: {available_text}."
                    ),
                )

            emoji_list = filtered
        else:
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
    message: Message, state: FSMContext, *, finalize: bool = True, notify: bool = True
) -> None:
    data, account_id, _ = await _load_account_data(state)
    session = data.get("account") if data else None

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        from_user = getattr(message, "from_user", None)
        chat_id = getattr(from_user, "id", None)

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
        if chat_id is not None:
            await bulk_update_reaction_settings(chat_id, **bulk_kwargs)
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
        


@dp.message(startaccount.systempromt)
async def add_systempromt(message: Message, state: FSMContext) -> None:
    data, _, account = await _load_account_data(state)

    stored_system_prompt = None
    if account:
        stored_system_prompt = account.get("system_prompt")
    if stored_system_prompt is None:
        stored_system_prompt = data.get("systempromt")

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

    await state.update_data({"systempromt": system_prompt_value})

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
    print(f"DEBUG: add_channels called with message: {message.text}")
    
    state_data = await state.get_data()
    session = state_data.get("account")
    sleeps = state_data.get("sleeps")
    system_promt = state_data.get("systempromt")
    chance = state_data.get("chance")
    print(f"DEBUG: State data - sleeps: {sleeps}, system_promt: {system_promt}, chance: {chance}")

    account_id = state_data.get("account_id")
    warmup_channels = []
    channels = []

    if str(message.text) != '-':
        channels = str(message.text).splitlines()

        app = Client(
            name=f"sessions/{message.from_user.id}/{session}",
            api_id=API_ID,
            api_hash=API_HASH)
        if await check_account(message.from_user.id, session):
            async with app:  
                for chl in channels:
                    await asyncio.sleep(random.uniform(20, 30))

                    if chl.startswith('-'):
                        chl = chl.replace('-', '')

                        try:
                            chat = await app.get_chat(chl)
                            await app.leave_chat(chat.id)
                            await bot.send_message(log_channel, f'Аккаунт {session} вышел из канала: {chl}')

                            if chat.linked_chat:
                                try:
                                    await app.leave_chat(chat.linked_chat.id)
                                except Exception:
                                    pass
                        except Exception as e:
                            await bot.send_message(log_channel, f'Ошибка при выходе из канала {chl}: {e}')

                    else:
                        # Обычные каналы - вступаем сразу
                        try:
                            await join_channel(
                                chl, account_id, session, message.from_user.id, is_warmup=False
                            )
                        except TransientJoinError as transient_error:
                            await bot.send_message(
                                log_channel,
                                f"Аккаунт {session} временная ошибка при вступлении в канал {chl}: {transient_error}",
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
    print(
        "DEBUG: About to save settings - "
        f"account_id={account_id}, sleep_min={sleep_min}, sleep_max={sleep_max}, chance={chance}, "
        f"system_promt={system_promt}"
    )
    await bot.send_message(
        log_channel,
        "DEBUG: About to save settings - "
        f"account_id={account_id}, sleep_min={sleep_min}, sleep_max={sleep_max}, chance={chance}, "
        f"system_promt={system_promt}"
    )

    update_kwargs: Dict[str, Any] = {
        "sleep_min": sleep_min,
        "sleep_max": sleep_max,
        "chance": chance,
        "system_prompt": system_promt,
    }

    await update_account_settings(account_id, **update_kwargs)

    print(f"DEBUG: Settings saved successfully for account_id={account_id}")
    await bot.send_message(log_channel, f"DEBUG: Settings saved successfully for account_id={account_id}")

    # Переходим к вводу каналов для прогрева
    await state.update_data({"account_id": account_id})


async def send_comments(userid, session, account_id):
    async with account_semaphore:
        key = make_session_key(userid, session)
        app = Client(
            name=f"sessions/{userid}/{session}",
            api_id=API_ID,
            api_hash=API_HASH)
        active_pyrogram_clients[key] = app
        active_client_locks.setdefault(key, asyncio.Lock())

        account = await get_account_by_id(account_id)
        if not account:
            active_pyrogram_clients.pop(key, None)
            active_client_locks.pop(key, None)
            active_sessions.pop(make_session_key(userid, session), None)
            return
        
        # Режим прогрева не блокирует комментирование - это стандартный режим + warmup задача

        system_promt = account.get("system_prompt") or ""
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

        last_reaction_at_str = account.get("last_reaction_at")
        last_reaction_at_dt: Optional[datetime] = None
        if last_reaction_at_str:
            try:
                parsed = datetime.fromisoformat(last_reaction_at_str)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            last_reaction_at_dt = parsed

        @app.on_message(filters.channel)
        async def channel_handler(client: Client, message: Message):
            key = make_session_key(userid, session)
            if not active_sessions.get(key, False):
                return
            try:
                channel = await client.get_chat(message.chat.id)
                linked_chat = channel.linked_chat
                if linked_chat:
                    await client.join_chat(linked_chat.id)
            except Exception as ex:
                print(ex)

        def _is_discussion_reply(_: Client, __, incoming_message: Message) -> bool:
            return _is_discussion_reply_message(incoming_message)

        discussion_filter = filters.create(_is_discussion_reply)

        @app.on_message(filters.linked_channel | discussion_filter)
        async def linked_channel_handler(client: Client, message: Message):
            nonlocal last_reaction_at_dt
            last_reaction_at_dt = await _handle_linked_channel_message(
                client,
                message,
                userid=userid,
                session=session,
                account_id=account_id,
                chance=chance,
                xsleep=xsleep,
                ysleep=ysleep,
                system_promt=system_promt,
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
        try:
            await app.start()
            key = make_session_key(userid, session)
            while active_sessions.get(key, False):
                await asyncio.sleep(1)
        finally:
            await app.stop()
            key = make_session_key(userid, session)
            account_id = active_account_ids.pop(key, None)
            if account_id:
                try:
                    await mark_account_stopped(account_id)
                except sqlite3.OperationalError as db_exc:
                    logging.error(
                        "Failed to mark account %s stopped after retries: %s",
                        account_id,
                        db_exc,
                    )
            active_sessions.pop(key, None)
            quiet_sessions_notified.discard(key)
            active_pyrogram_clients.pop(key, None)
            active_client_locks.pop(key, None)


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
        # Создаем клиент
        session_dir = os.path.join("sessions", str(user_id))
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
            else:
                error_message = f"Аккаунт {session_key} - файл сессии не найден: {session_file}"
                await bot.send_message(log_channel, error_message)
                return False, error_message

        key = make_session_key(user_id, session_key)

        async def _join_with_client(client_obj: Client) -> Tuple[bool, Optional[str]]:
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
                if _is_transient_join_error(error_message):
                    await bot.send_message(
                        log_channel,
                        f"Аккаунт {session_key} временная ошибка при вступлении в канал {channel}: {error_message}",
                    )
                    raise TransientJoinError(error_message)
                if is_warmup:
                    await record_warmup_channel_error(account_id, channel, error_message)
                await bot.send_message(log_channel, f"Аккаунт {session_key} ошибка вступления в канал {channel}: {e}")
                return False, error_message

        existing_client = active_pyrogram_clients.get(key)
        session_active = active_sessions.get(key, False)

        if existing_client and session_active:
            lock = active_client_locks.setdefault(key, asyncio.Lock())
            async with lock:
                if not getattr(existing_client, "is_connected", False):
                    # Ждем пока клиент запустится, чтобы избежать гонок с pyrogram.session
                    for _ in range(40):
                        if not active_sessions.get(key, False):
                            break
                        if getattr(existing_client, "is_connected", False):
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

        client = Client(
            name=session_name,
            api_id=API_ID,
            api_hash=API_HASH,
        )

        async with client:
            return await _join_with_client(client)

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


async def process_warmup_accounts():
    """Фоновая задача для добавления каналов в режиме прогрева (во время сна)"""
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
            
            # Логируем каждые 10 минут для отладки
            if now.minute % 10 == 0:
                message = (
                    f"Warmup check: {now.strftime('%H:%M')} UTC, "
                    f"is_warmup_join_time: {is_warmup_join_time}"
                )
                logging.debug(message)
                add_summary("debug", message)
            
            # Проверяем, находимся ли мы в периоде для вступления в каналы (во время сна)
            if not is_warmup_join_time:
                await asyncio.sleep(WARMUP_SCAN_INTERVAL_SECONDS)
                continue
            
            all_accounts = await get_running_accounts()
            accounts = [acc for acc in all_accounts if acc.get("mode") == "warmup"]
            random.shuffle(accounts)
            
            logging.debug(
                "Warmup: Found %s running accounts, %s in warmup mode",
                len(all_accounts),
                len(accounts),
            )
            add_summary("info", f"Running accounts: {len(all_accounts)}, warmup: {len(accounts)}")
            logging.debug("Warmup: Active sessions: %s", list(active_sessions.keys()))
            add_summary("debug", f"Active sessions: {list(active_sessions.keys())}")

            for account in accounts:
                if account.get("mode") != "warmup":
                    continue

                # Аккаунты в режиме прогрева комментируют как обычно,
                # но дополнительно вступают в каналы во время сна
                session_key = account["phone"]
                user_id = account["user_id"]
                key = make_session_key(user_id, session_key)
                
                logging.debug("Warmup: Processing account %s, active: %s", session_key, active_sessions.get(key))
                add_summary("debug", f"Processing account {session_key}, active={active_sessions.get(key)}")

                # Проверяем, не истек ли период прогрева
                warmup_end = _parse_warmup_datetime(account.get("warmup_end_at"))
                if warmup_end:
                    account["warmup_end_at"] = warmup_end
                    if warmup_end <= now:
                        await set_account_mode(account["id"], "standard", warmup_days=None)
                        continue

                # Сбрасываем дневной счетчик если новый день
                warmup_last_join_at = _parse_warmup_datetime(account.get("warmup_last_join_at"))
                if warmup_last_join_at:
                    account["warmup_last_join_at"] = warmup_last_join_at

                if warmup_last_join_at and warmup_last_join_at.date() < now.date():
                    await reset_warmup_daily_state(account["id"])
                    account["warmup_joined_today"] = 0

                next_join_at = _parse_warmup_datetime(account.get("warmup_next_join_at"))
                if next_join_at and next_join_at > now:
                    continue

                # Проверяем, не достигли ли дневного лимита
                joined_today = account.get("warmup_joined_today", 0)
                logging.debug("Warmup: Account %s joined today: %s/%s", session_key, joined_today, daily_limit)
                add_summary("debug", f"{session_key}: {joined_today}/{daily_limit} joins")

                if joined_today >= daily_limit:
                    message = f"Account {session_key} reached daily limit, skipping"
                    logging.info("Warmup: %s", message)
                    add_summary("info", message)
                    next_window_start = _next_join_window_start(now, current_settings)
                    next_time = plan_next_warmup_join(next_window_start, current_settings)
                    await db_update_warmup_schedule(account["id"], next_join=next_time)
                    logging.info(
                        "Warmup schedule: account %s (%s) next join at %s",
                        account["id"],
                        session_key,
                        next_time.isoformat(),
                    )
                    account["warmup_next_join_at"] = next_time
                    continue

                # Получаем следующий канал для добавления
                pending_channels = await get_warmup_pending(account["id"], limit=1, reset_if_empty=True)
                logging.debug("Warmup: Account %s pending channels: %s", session_key, len(pending_channels))
                add_summary("debug", f"{session_key}: pending channels {len(pending_channels)}")

                if not pending_channels:
                    message = f"Account {session_key} has no pending channels, skipping"
                    logging.info("Warmup: %s", message)
                    add_summary("info", message)
                    next_time = _get_next_warmup_join(now, current_settings)
                    await db_update_warmup_schedule(account["id"], next_join=next_time)
                    logging.info(
                        "Warmup schedule: account %s (%s) next join at %s",
                        account["id"],
                        session_key,
                        next_time.isoformat(),
                    )
                    account["warmup_next_join_at"] = next_time
                    continue

                channel_entry = pending_channels[0]
                channel = channel_entry["channel"]

                # Проверяем существование файла сессии
                session_file = os.path.join("sessions", str(user_id), f"{session_key}.session")
                if not os.path.exists(session_file):
                    warning_message = (
                        f"Аккаунт {session_key} (прогрев) - файл сессии не найден: {session_file}"
                    )
                    logging.warning("Warmup: %s", warning_message)
                    add_summary("warning", warning_message)
                    # Переключаем в стандартный режим если нет сессии
                    await set_account_mode(account["id"], "standard", warmup_days=None)
                    continue

                # Используем единую функцию для вступления в канал прогрева
                try:
                    success, error_reason = await join_channel(
                        channel, account["id"], session_key, user_id, is_warmup=True
                    )
                except TransientJoinError as transient_error:
                    transient_message = transient_error.message if hasattr(transient_error, "message") else str(transient_error)
                    warning_message = (
                        f"Account {session_key} временная ошибка вступления в {channel}: {transient_message}. Повторим позже."
                    )
                    logging.warning("Warmup: %s", warning_message)
                    add_summary("warning", warning_message)
                    backoff_seconds = random.uniform(15, 45)
                    retry_time = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
                    await db_update_warmup_schedule(account["id"], next_join=retry_time)
                    account["warmup_next_join_at"] = retry_time
                    await asyncio.sleep(min(backoff_seconds, 5))
                    continue

                if not success:
                    if error_reason and any(
                        phrase in error_reason.lower()
                        for phrase in ("занят", "запускается")
                    ):
                        info_message = f"Account {session_key} занят ({error_reason}), повторим позже"
                        logging.info("Warmup: %s", info_message)
                        add_summary("info", info_message)
                        continue
                    # Если сессия истекла - переключаем в стандартный режим
                    await set_account_mode(account["id"], "standard", warmup_days=None)
                    continue

                success_message = f"Account {session_key} joined {channel}"
                logging.info("Warmup: %s", success_message)
                add_summary("info", success_message)

                post_join_now = datetime.now(timezone.utc)
                next_time = _get_next_warmup_join(post_join_now, current_settings)
                await db_update_warmup_schedule(account["id"], next_join=next_time)
                logging.info(
                    "Warmup schedule: account %s (%s) next join at %s",
                    account["id"],
                    session_key,
                    next_time.isoformat(),
                )
                account["warmup_next_join_at"] = next_time

        except Exception as e:
            logging.exception("Warmup loop error: %s", e)
            add_summary("error", f"Warmup loop error: {e}")

        # Ждем случайный интервал до следующей попытки, чтобы имитировать живое поведение
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

        await asyncio.sleep(_get_human_delay_seconds(current_settings))

@dp.message(addsession.number)
async def add_number(message: Message, state: FSMContext) -> None:
    if str(message.text).isdigit():
        warmup_only = (await state.get_data()).get("warmup_only")

        if warmup_only:
            account_row = await get_account_by_session(message.from_user.id, message.text)
            if not account_row:
                await message.answer("Аккаунт не найден. Сначала добавьте аккаунт через 'Добавить аккаунт'.")
                await state.clear()
                await main_message(message)
                return

            await state.update_data({
                "account": message.text,
                "account_id": account_row["id"],
            })
            await message.answer("Пришлите каналы для прогрева (каждый канал с новой строки). Для отмены отправьте '-'.")
            await state.set_state(startaccount.warmup_channels)
            return

        client = Client(
            name=f"sessions/{message.from_user.id}/{message.text}",
            api_id=API_ID,
            api_hash=API_HASH)

        try:
            await client.connect()
            sent_code = await client.send_code(str(message.text))

            await state.update_data({"client": client})
            await state.update_data({"code_hash": sent_code.phone_code_hash})
            await state.update_data({"number": message.text})


            await message.answer("Код подтверждения отправлен.\nВведите код в формате 6 7 4 3 9")
            await state.set_state(addsession.code)
        except Exception as e:
            await message.answer(f"Ошибка: {str(e)}")
            await state.clear()
            await main_message(message)


@dp.message(addsession.code)
async def add_code(message: Message, state: FSMContext) -> None:
    code = str(message.text).replace(' ', '')

    if code.isdigit():
        code_hash = (await state.get_data()).get("code_hash")
        number = (await state.get_data()).get("number")


        try:

            client = (await state.get_data()).get("client")

            await client.sign_in(
                phone_number=number,
                phone_code_hash=code_hash,
                phone_code=code
            )

            await message.answer("✅ Успешная авторизация!")
            session_path = f'sessions/{message.from_user.id}/{number}.session'
            await ensure_account(message.from_user.id, number, session_path)
        except Exception as e:
            await message.answer(f"Ошибка: {str(e)}")
            await client.disconnect()
            await asyncio.sleep(1)
            os.remove(f'sessions/{message.from_user.id}/{number}.session')
        finally:
            await main_message(message)

    await state.clear()


@dp.message(startaccount.regular_channels)
async def add_regular_channels(message: Message, state: FSMContext) -> None:
    """Обработчик для обычных каналов (немедленное вступление)"""
    data, account_id, account = await _load_account_data(state)
    session = data.get("account") if data else None
    chance = data.get("chance") if data else None
    system_prompt_value = data.get("systempromt") if data else None
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
        existing_warmup = await get_warmup_pending(account_id, limit=1)
        
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
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Запускаем комментирование
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
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Запускаем комментирование
            return

    channels = [line.strip() for line in message.text.splitlines() if line.strip()]
    warmup_channels = [chl for chl in channels if not chl.startswith('-')]
    
    # Удаляем дубликаты, сохраняя порядок
    seen_warmup = set()
    warmup_channels = [x for x in warmup_channels if not (x in seen_warmup or seen_warmup.add(x))]

    if not warmup_channels:
        # Проверяем, есть ли уже каналы в прогреве
        existing_warmup = await get_warmup_pending(account_id, limit=1)
        
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
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Запускаем комментирование
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
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Запускаем комментирование
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
    asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Запускаем комментирование


async def safe_send_comments(user_id, phone, account_id):
    """Безопасная обертка для send_comments с обработкой исключений"""
    try:
        await send_comments(user_id, phone, account_id)
    except Exception as e:
        logging.exception("Error in send_comments for account %s: %s", account_id, e)
        # Останавливаем аккаунт при критической ошибке
        try:
            await mark_account_stopped(account_id)
        except sqlite3.OperationalError as db_exc:
            logging.error(
                "Failed to mark account %s stopped after retries: %s",
                account_id,
                db_exc,
            )
        key = make_session_key(user_id, phone)
        active_sessions.pop(key, None)
        active_account_ids.pop(key, None)
        active_pyrogram_clients.pop(key, None)
        active_client_locks.pop(key, None)


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
            app = Client(
                name=session_path.replace('.session', ''),
                api_id=API_ID,
                api_hash=API_HASH
            )
            async with app:
                async for dialog in app.get_dialogs():
                    chat = dialog.chat
                    if str(chat.type) == "ChatType.CHANNEL" and chat.username:
                        real_channels.append(f"@{chat.username}")
    except Exception as e:
        print(f"Ошибка при получении подписок для аккаунта {account_id}: {e}")
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
    try:
        with open("bot_log.txt", "w") as log_file:
            log_file.write("Starting bot initialization...\n")
            log_file.flush()
            
            await init_db()
            await ensure_warmup_settings(
                channels_per_day=DEFAULT_WARMUP_SETTINGS.channels_per_day,
                delay_minutes=DEFAULT_WARMUP_SETTINGS.delay_minutes,
                join_start_hour=DEFAULT_WARMUP_SETTINGS.join_start_hour,
                join_start_minute=DEFAULT_WARMUP_SETTINGS.join_start_minute,
                join_end_hour=DEFAULT_WARMUP_SETTINGS.join_end_hour,
                join_end_minute=DEFAULT_WARMUP_SETTINGS.join_end_minute,
            )
            await ensure_latest_warmup_settings(force=True)
            log_file.write("Database initialized successfully\n")
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
                session_file = os.path.join("sessions", str(user_id), f"{phone}.session")

                if os.path.exists(session_file):
                    # Перед повторным запуском очищаем прошлые записи, чтобы избежать дублирования
                    active_sessions.pop(key, None)
                    active_account_ids.pop(key, None)
                    active_pyrogram_clients.pop(key, None)
                    active_client_locks.pop(key, None)

                    if account.get("status") != "running":
                        await mark_account_running(account["id"])

                    active_sessions[key] = True
                    active_account_ids[key] = account["id"]
                    asyncio.create_task(safe_send_comments(user_id, phone, account["id"]))
                    log_file.write(
                        f"Started account {phone} in {account.get('mode', 'unknown')} mode\n"
                    )
                    log_file.flush()
                else:
                    await mark_account_stopped(account["id"])
                    log_file.write(f"Stopped account {phone} - no session file\n")
                    log_file.flush()
            
            asyncio.create_task(process_warmup_accounts())
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

