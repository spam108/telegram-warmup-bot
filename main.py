import json
import os
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Dict, List, Optional, Set, Any, Union, Tuple
import random
from pyrogram import Client, filters
from pyrogram.errors import UserAlreadyParticipant
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from comment_engine import generate_comment
from db import (
    add_comment_log,
    delete_account,
    ensure_account,
    ensure_user,
    get_account_by_id,
    get_account_by_session,
    get_accounts_for_user,
    get_running_accounts,
    get_warmup_pending,
    get_warmup_queue_stats,
    get_warmup_settings,
    init_db,
    is_user_authenticated,
    mark_account_running,
    mark_account_stopped,
    mark_warmup_channel_joined,
    record_warmup_channel_error,
    reset_warmup_daily_state,
    ensure_warmup_settings,
    db_update_warmup_schedule,
    set_account_mode,
    set_user_authenticated,
    sync_warmup_channels,
    update_account_settings,
    increment_warmup_joined,
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
BOT_TOKEN = env_vars.get("BOT_TOKEN") or os.getenv("BOT_TOKEN")
print(f"BOT_TOKEN loaded: {BOT_TOKEN}")
#APCDXBOT0310 @AP_comment_bot
log_channel = -1003123025616 # cloveend #-1002711973256 #-1002678984799

API_ID = int(env_vars.get("API_ID") or os.getenv("API_ID"))
print(f"API_ID loaded: {API_ID}")
API_HASH = env_vars.get("API_HASH") or os.getenv("API_HASH")
print(f"API_HASH loaded: {API_HASH}")
#1823


# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.INFO)



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


class warmupsettings(StatesGroup):
    limit = State()
    window = State()
    interval = State()


class warmupmanage(StatesGroup):
    channels = State()

active_sessions: Dict[str, bool] = {}  # Глобальный словарь для хранения активных сессий
active_account_ids: Dict[str, int] = {}
quiet_sessions_notified: Set[str] = set()

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
    """Проверяет, находимся ли мы в тихом периоде (00:30-07:30 МСК = 21:30-04:30 UTC)"""
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
    try:
        client = Client(
            name=f"sessions/{user_id}/{phone}",
            api_id=API_ID,
            api_hash=API_HASH)

        await client.connect()
        await client.get_me()
        await client.disconnect()
    
        return True
    except Exception as e:
        await client.disconnect()  # На случай, если connect() был успешным
        await asyncio.sleep(1)
       
        await bot.send_message(user_id, f"Аккаунт удален ошибка: {str(e)}")

        os.remove(f'sessions/{user_id}/{phone}.session')
        await delete_account(user_id, phone)
        return False

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

        button_info = types.InlineKeyboardButton(text=f"ℹ️ {call}", callback_data=f"info_{call}")
        button_status = types.InlineKeyboardButton(text=status_button_text, callback_data=status_button_callback)
        button_delete = types.InlineKeyboardButton(text="Удалить", callback_data=f"del_{call}")
        button_mode = types.InlineKeyboardButton(text="Режим", callback_data=f"mode_{call}")
        button_warmup = types.InlineKeyboardButton(text="Прогрев", callback_data=f"warmup_{call}")

        if is_running:
            builder.row(button_info, button_status, button_mode, button_warmup)
        else:
            builder.row(button_info, button_status, button_mode, button_warmup)
            builder.row(button_delete)


    builder.row(
        types.InlineKeyboardButton(text="Добавить аккаунт", callback_data="add_account"),
        types.InlineKeyboardButton(text="Добавить прогрев", callback_data="add_warmup"),
        types.InlineKeyboardButton(text="Настройки прогрева", callback_data="warmup_settings"),
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


    elif 'info_' in call:

        session = str(call).split('_')[1]

        key = make_session_key(callback_query.from_user.id, session)
        if active_sessions.get(key):
            await bot.send_message(callback_query.from_user.id, f"Аккаунт {session} в работе")
            return

        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        # Получаем реальные подписки аккаунта из Telegram
        real_channels = []
        try:
            session_path = account_row.get('session_path', '')
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
            print(f"Ошибка при получении подписок для аккаунта {session}: {e}")
            # Если не удалось получить реальные подписки, используем из БД
            real_channels = account_row.get("channels") or []

        channels = account_row.get("channels") or []
        warmup_stats = await get_warmup_queue_stats(account_row["id"])

        info_lines = [f"Аккаунт {session}"]
        
        # Показываем реальные подписки
        if real_channels:
            info_lines.append(f"Активные каналы ({len(real_channels)}):\n" + "\n".join(real_channels[:20]))
            if len(real_channels) > 20:
                info_lines.append(f"... и ещё {len(real_channels) - 20}")
        else:
            info_lines.append("Активные каналы: нет")

        pending_channels = await get_warmup_pending(account_row["id"], limit=20)
        if pending_channels:
            pending_list = [entry["channel"] for entry in pending_channels]
            info_lines.append(f"Очередь прогрева ({warmup_stats['pending']}):\n" + "\n".join(pending_list))
            if warmup_stats['pending'] > 20:
                info_lines.append(f"... и ещё {warmup_stats['pending'] - 20}")
        else:
            info_lines.append("Очередь прогрева: пусто")

        info_lines.append(f"Подписок добавлено в прогреве: {warmup_stats['joined']}")
        info_lines.append(f"Количество ошибок прогрева: {warmup_stats['error']}")

        await bot.send_message(callback_query.from_user.id, "\n".join(info_lines))
        await main_message(callback_query)


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
        warmup_display = await format_channels_display(warmup_list, "Очередь прогрева", 10)

        await state.update_data({"account": session, "account_id": account_id})
        await state.set_state(warmupmanage.channels)

        prompt_lines = [
            f"Аккаунт {session}",
            warmup_display,
            "",
            "Отправьте каналы для прогрева (каждый канал на новой строке).",
            "Отправьте '-' чтобы очистить очередь и перевести аккаунт в стандартный режим.",
        ]

        await bot.send_message(
            callback_query.from_user.id,
            "\n".join(line for line in prompt_lines if line),
        )
        return


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

                # Все аккаунты проходят через диалог настройки
                account_id = account_row["id"]
                await state.update_data({"account": session, "account_id": account_id})

                current_chance = account_row.get("chance")
                if current_chance is None:
                    chance_hint = "не задан"
                else:
                    chance_hint = f"{current_chance}%"

                await bot.send_message(
                    callback_query.from_user.id,
                    "Текущий шанс комментирования: {hint}.\n"
                    "Отправьте новое значение от 0 до 100 или '-' для сохранения текущего.".format(
                        hint=chance_hint
                    ),
                )
                await state.set_state(startaccount.chance)
            except Exception as e:
                await bot.send_message(callback_query.from_user.id, f"Ошибка: {str(e)}")
                await main_message(callback_query)
        else:   
            await main_message(callback_query)
            return

    elif 'mode_' in call:
        session = str(call).split('_')[1]
        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        mode = account_row.get("mode", "warmup")
        warmup_end = account_row.get("warmup_end_at")
        warmup_joined_today = account_row.get("warmup_joined_today", 0)
        warmup_last_join = account_row.get("warmup_last_join")

        next_action_text = "Перевести в стандарт" if mode == "warmup" else "Вернуть в прогрев"
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text=next_action_text, callback_data=f"togglemode_{session}"),
            types.InlineKeyboardButton(text="Сбросить прогрев", callback_data=f"warmreset_{session}"),
        )

        text = [
            f"Аккаунт {session}",
            f"Текущий режим: {mode}",
        ]
        if warmup_end:
            text.append(f"Окончание прогрева: {warmup_end:%Y-%m-%d %H:%M}")
        text.append(f"Количество подписок сегодня: {warmup_joined_today}")
        if warmup_last_join:
            text.append(f"Последняя подписка: {warmup_last_join:%Y-%m-%d}")

        await bot.send_message(callback_query.from_user.id, "\n".join(text), reply_markup=builder.as_markup())

    elif 'togglemode_' in call:
        session = str(call).split('_')[1]
        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        account_id = account_row["id"]
        current_mode = account_row.get("mode", "warmup")
        if current_mode == "warmup":
            await set_account_mode(account_id, "standard", warmup_days=None)
            await bot.send_message(callback_query.from_user.id, f"Аккаунт {session} переведён в стандартный режим")
        else:
            await set_account_mode(account_id, "warmup", warmup_days=7)
            await bot.send_message(callback_query.from_user.id, f"Аккаунт {session} переведён в режим прогрева на 7 дней")

        await main_message(callback_query)

    elif 'warmreset_' in call:
        session = str(call).split('_')[1]
        account_row = await get_account_by_session(callback_query.from_user.id, session)
        if not account_row:
            await bot.send_message(callback_query.from_user.id, "Аккаунт не найден в базе данных")
            await main_message(callback_query)
            return

        account_id = account_row["id"]
        channels = account_row.get("channels") or []
        await sync_warmup_channels(account_id, channels)
        await set_account_mode(account_id, "warmup", warmup_days=7)
        await bot.send_message(callback_query.from_user.id, f"Прогрев аккаунта {session} перезапущен на 7 дней")
        await main_message(callback_query)

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
        await message.answer("Пришлите список каналов или '-' для очистки очереди.")
        return

    if incoming == "-":
        channels: List[str] = []
    else:
        channels = [line.strip() for line in incoming.splitlines() if line.strip()]

    seen: Set[str] = set()
    unique_channels: List[str] = []
    for channel in channels:
        if channel not in seen:
            seen.add(channel)
            unique_channels.append(channel)

    try:
        await sync_warmup_channels(account_id, unique_channels)
        if unique_channels:
            await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
            result_text = f"Очередь прогрева обновлена. Запланировано {len(unique_channels)} каналов."
        else:
            await set_account_mode(account_id, "standard", warmup_days=None)
            result_text = "Очередь прогрева очищена. Аккаунт переведён в стандартный режим."
    except Exception as exc:
        await message.answer(f"Ошибка при обновлении каналов прогрева: {exc}")
        await state.clear()
        await main_message(message)
        return

    updated_records = await get_warmup_pending(account_id, limit=100)
    updated_list = [entry["channel"] for entry in updated_records] if updated_records else []
    display = await format_channels_display(updated_list, "Очередь прогрева", 10)

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

    await _prompt_system_prompt(message, state)
    await state.set_state(startaccount.systempromt)
        


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
    data, _, account = await _load_account_data(state)

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

    data = await state.get_data()
    session = data.get("account")

    channels: List[str] = []

    app = Client(
        name=f"sessions/{message.from_user.id}/{session}",
        api_id=API_ID,
        api_hash=API_HASH)

    if await check_account(message.from_user.id, session):
        async with app:
            async for dialog in app.get_dialogs():
                chat = dialog.chat
                if str(chat.type) == "ChatType.CHANNEL":
                    if chat.username is not None:
                        channels.append(f"@{chat.username}")

        # Используем унифицированное отображение каналов
        channels_display = await format_channels_display(channels, "Аккаунт подписан на каналы", 10)
        await bot.send_message(message.from_user.id, f'{channels_display}\n\nПришлите каналы на которые нужно подписаться\n(если не нужно пришлите -)')
        await state.set_state(startaccount.regular_channels)
    else:
        await state.clear()
        await main_message(message)


@dp.message(startaccount.channels)
async def add_channels(message: Message, state: FSMContext) -> None:
    print(f"DEBUG: add_channels called with message: {message.text}")
    
    session = (await state.get_data()).get("account")
    sleeps = (await state.get_data()).get("sleeps")
    system_promt = (await state.get_data()).get("systempromt")
    chance = (await state.get_data()).get("chance")

    print(f"DEBUG: State data - sleeps: {sleeps}, system_promt: {system_promt}, chance: {chance}")

    account_id = (await state.get_data()).get("account_id")
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
                        await join_channel(chl, account_id, session, message.from_user.id, is_warmup=False)
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

    print(f"DEBUG: About to save settings - account_id={account_id}, sleep_min={sleep_min}, sleep_max={sleep_max}, chance={chance}, system_promt={system_promt}")
    await bot.send_message(log_channel, f"DEBUG: About to save settings - account_id={account_id}, sleep_min={sleep_min}, sleep_max={sleep_max}, chance={chance}, system_promt={system_promt}")

    await update_account_settings(
        account_id,
        channels=channels,
        sleep_min=sleep_min,
        sleep_max=sleep_max,
        chance=chance,
        system_prompt=system_promt
    )
    
    print(f"DEBUG: Settings saved successfully for account_id={account_id}")
    await bot.send_message(log_channel, f"DEBUG: Settings saved successfully for account_id={account_id}")

    # Переходим к вводу каналов для прогрева
    await state.update_data({"account_id": account_id})


async def send_comments(userid, session, account_id):
    async with account_semaphore:
        app = Client(
            name=f"sessions/{userid}/{session}",
            api_id=API_ID,
            api_hash=API_HASH)
        
        account = await get_account_by_id(account_id)
        if not account:
            active_sessions.pop(make_session_key(userid, session), None)
            return
        
        # Режим прогрева не блокирует комментирование - это стандартный режим + warmup задача

        system_promt = account.get("system_prompt") or ""
        sleep_min = account.get("sleep_min") or 10
        sleep_max = account.get("sleep_max") or 20
        chance = account.get("chance") or 100

        xsleep, ysleep = sleep_min, sleep_max

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

        @app.on_message(filters.linked_channel)
        async def linked_channel_handler(client: Client, message: Message):
            key = make_session_key(userid, session)
            if not active_sessions.get(key, False):
                return
            
            if (message.chat.permissions.can_send_messages is True) and (
                    message.text is not None or message.caption is not None):
                
                if random.randint(1, 100) > chance:
                    await bot.send_message(log_channel, f'Аккаунт {session} пропустил комментарий')
                    return

                post_text = message.text or message.caption
                comment = generate_comment(post_text, system_promt)

                try:
                    if is_quiet_period():
                        key = make_session_key(userid, session)
                        if key not in quiet_sessions_notified:
                            await bot.send_message(log_channel, f'Аккаунт {session} приостановлен до 07:00 (циркадный режим)')
                            quiet_sessions_notified.add(key)
                        return
                    await asyncio.sleep(random.uniform(xsleep, ysleep))
                    msg = await client.send_message(message.chat.id, comment, reply_to_message_id=message.id)

                    if hasattr(msg, "reply_to_message") and msg.reply_to_message and hasattr(msg.reply_to_message, "forward_from_chat") and msg.reply_to_message.forward_from_chat:
                        await bot.send_message(log_channel, f'Аккаунт {session} отправил комментарий\n'
                                                        f'https://t.me/{msg.reply_to_message.forward_from_chat.username}/{msg.reply_to_message.forward_from_message_id}?comment={msg.id}')
                    else:
                        await bot.send_message(log_channel, f'Аккаунт {session} отправил комментарий\n'
                                                        f'https://t.me/c/{str(message.chat.id).replace("-", "")}/{msg.id}')
                    # Небольшая пауза перед записью в БД
                    await asyncio.sleep(0.2)
                    await add_comment_log(
                        account_id,
                            channel=str(message.chat.id),
                            message_id=msg.id,
                            status='success',
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
                await mark_account_stopped(account_id)
            active_sessions.pop(key, None)
            quiet_sessions_notified.discard(key)


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
        session_file = os.path.join("sessions", str(user_id), f"{session_key}.session")
        if not os.path.exists(session_file):
            error_message = f"Аккаунт {session_key} - файл сессии не найден: {session_file}"
            await bot.send_message(log_channel, error_message)
            return False, error_message

        client = Client(
            name=session_file,
            api_id=API_ID,
            api_hash=API_HASH,
        )
        
        async with client:
            try:
                await client.join_chat(channel)
                
                if is_warmup:
                    # Для каналов прогрева - обновляем БД
                    await mark_warmup_channel_joined(account_id, channel)
                    await increment_warmup_joined(account_id)
                    await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) вступил в канал: {channel}")
                else:
                    # Для обычных каналов - просто логируем
                    await bot.send_message(log_channel, f"Аккаунт {session_key} вступил в канал: {channel}")

                return True, None

            except UserAlreadyParticipant:
                if is_warmup:
                    await mark_warmup_channel_joined(account_id, channel)
                await bot.send_message(log_channel, f"Аккаунт {session_key} уже состоит в канале: {channel}")
                return True, None

            except Exception as e:
                error_message = str(e)
                if is_warmup:
                    await record_warmup_channel_error(account_id, channel, error_message)
                await bot.send_message(log_channel, f"Аккаунт {session_key} ошибка вступления в канал {channel}: {e}")
                return False, error_message

    except Exception as e:
        error_msg = str(e)
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
        try:
            current_settings = await ensure_latest_warmup_settings()
            now = datetime.now(timezone.utc)
            is_warmup_join_time = is_warmup_join_period(now)
            daily_limit = current_settings.channels_per_day
            
            # Логируем каждые 10 минут для отладки
            if now.minute % 10 == 0:
                await bot.send_message(log_channel, f"Warmup check: {now.strftime('%H:%M')} UTC, is_warmup_join_time: {is_warmup_join_time}")
            
            # Проверяем, находимся ли мы в периоде для вступления в каналы (во время сна)
            if not is_warmup_join_time:
                await asyncio.sleep(WARMUP_SCAN_INTERVAL_SECONDS)
                continue
            
            all_accounts = await get_running_accounts()
            accounts = [acc for acc in all_accounts if acc.get("mode") == "warmup"]
            random.shuffle(accounts)
            
            await bot.send_message(log_channel, f"Warmup: Found {len(all_accounts)} running accounts, {len(accounts)} in warmup mode")
            await bot.send_message(log_channel, f"Warmup: Active sessions: {list(active_sessions.keys())}")

            for account in accounts:
                if account.get("mode") != "warmup":
                    continue

                # Аккаунты в режиме прогрева комментируют как обычно,
                # но дополнительно вступают в каналы во время сна
                session_key = account["phone"]
                user_id = account["user_id"]
                key = make_session_key(user_id, session_key)
                
                await bot.send_message(log_channel, f"Warmup: Processing account {session_key}, active: {active_sessions.get(key)}")

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
                await bot.send_message(log_channel, f"Warmup: Account {session_key} joined today: {joined_today}/{daily_limit}")

                if joined_today >= daily_limit:
                    await bot.send_message(log_channel, f"Warmup: Account {session_key} reached daily limit, skipping")
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
                await bot.send_message(log_channel, f"Warmup: Account {session_key} pending channels: {len(pending_channels)}")

                if not pending_channels:
                    await bot.send_message(log_channel, f"Warmup: Account {session_key} no pending channels, skipping")
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
                    await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) - файл сессии не найден: {session_file}")
                    # Переключаем в стандартный режим если нет сессии
                    await set_account_mode(account["id"], "standard", warmup_days=None)
                    continue

                # Используем единую функцию для вступления в канал прогрева
                success, error_reason = await join_channel(channel, account["id"], session_key, user_id, is_warmup=True)

                if not success:
                    # Если сессия истекла - переключаем в стандартный режим
                    await set_account_mode(account["id"], "standard", warmup_days=None)
                    continue

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

        # Ждем случайный интервал до следующей попытки, чтобы имитировать живое поведение
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

    existing_channels_raw = []
    if account and isinstance(account, dict):
        stored_channels = account.get("channels")
        if isinstance(stored_channels, list):
            existing_channels_raw = [channel for channel in stored_channels if isinstance(channel, str)]

    if not account_id:
        await bot.send_message(message.from_user.id, "Ошибка: аккаунт не найден. Попробуйте снова.")
        await state.clear()
        await main_message(message)
        return

    channels_to_update: Optional[List[str]] = None
    successful_channels: List[str] = []
    if str(message.text) != '-':
        channels = [line.strip() for line in message.text.splitlines() if line.strip()]

        # Вступаем в обычные каналы сразу
        for channel in channels:
            success, error_reason = await join_channel(
                channel, account_id, session, message.from_user.id, is_warmup=False
            )

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
    if sleeps:
        parsed_range = _parse_sleep_range_input(str(sleeps))
        if parsed_range:
            sleep_min, sleep_max = parsed_range

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
    
    # Показываем существующие каналы прогрева перед обработкой
    existing_warmup = await get_warmup_pending(account_id, limit=100)
    warmup_list = [ch["channel"] for ch in existing_warmup] if existing_warmup else []
    if warmup_list:
        warmup_display = await format_channels_display(warmup_list, "Текущие каналы в прогреве", 10)
        await bot.send_message(message.from_user.id, warmup_display)
    
    # Помечаем как обработанное, чтобы избежать повторных вызовов
    await state.update_data({"warmup_processed": True})

    if str(message.text) == '-':
        # Проверяем, есть ли уже каналы в прогреве
        existing_warmup = await get_warmup_pending(account_id, limit=1)
        
        if existing_warmup:
            # Есть каналы в прогреве - запускаем в режиме прогрева
            await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
            # Планируем следующее вступление в период сна (4:00-6:00)
            now = datetime.now(timezone.utc)
            tomorrow_4_30am = now.replace(hour=4, minute=30, second=0, microsecond=0) + timedelta(days=1)
            await db_update_warmup_schedule(account_id, next_join=tomorrow_4_30am)
            logging.info(
                "Warmup schedule: account %s (%s) next join at %s",
                account_id,
                session,
                tomorrow_4_30am.isoformat(),
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
            # Планируем следующее вступление в период сна (4:00-6:00)
            now = datetime.now(timezone.utc)
            tomorrow_4_30am = now.replace(hour=4, minute=30, second=0, microsecond=0) + timedelta(days=1)
            await db_update_warmup_schedule(account_id, next_join=tomorrow_4_30am)
            logging.info(
                "Warmup schedule: account %s (%s) next join at %s",
                account_id,
                session,
                tomorrow_4_30am.isoformat(),
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
        # Планируем следующее вступление в период сна (4:00-6:00)
        now = datetime.now(timezone.utc)
        tomorrow_4_30am = now.replace(hour=4, minute=30, second=0, microsecond=0) + timedelta(days=1)
        await db_update_warmup_schedule(account_id, next_join=tomorrow_4_30am)
        logging.info(
            "Warmup schedule: account %s (%s) next join at %s",
            account_id,
            session,
            tomorrow_4_30am.isoformat(),
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
        await mark_account_stopped(account_id)
        active_sessions.pop(make_session_key(user_id, phone), None)
        active_account_ids.pop(make_session_key(user_id, phone), None)


async def format_channels_display(channels, title="Каналы", max_display=10):
    """Унифицированное отображение списка каналов"""
    if not channels:
        return f"{title}: нет"

    if len(channels) <= max_display:
        return f"{title} ({len(channels)}):\n" + '\n'.join(channels)
    else:
        displayed = channels[:max_display]
        return f"{title} ({len(channels)}):\n" + '\n'.join(displayed) + f"\n... и еще {len(channels) - max_display} каналов"


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

async def get_account_summary(account_id):
    """Получает полное резюме аккаунта из базы данных"""
    account = await get_account_by_id(account_id)
    if not account:
        return None
    
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
    summary = f"""
📊 **Резюме аккаунта {account.get('phone', 'N/A')}**

⚙️ **Настройки:**
• Задержка: {account.get('sleep_min', 'N/A')}-{account.get('sleep_max', 'N/A')} сек
• Шанс комментирования: {account.get('chance', 'N/A')}%
• Режим: {account.get('mode', 'N/A')}
• Статус: {account.get('status', 'N/A')}

📝 **Системный промпт (превью):**
{prompt_block}

📺 **Реальные подписки ({len(real_channels)}):**
{await format_channels_display(real_channels, "Подписки", 10)}

📋 **Каналы в БД ({len(db_channels)}):**
{await format_channels_display(db_channels, "В базе", 5)}

🔥 **Каналы прогрева ({len(warmup_list)}):**
{await format_channels_display(warmup_list, "Прогрев", 10)}

📅 **Время прогрева:**
• Завершение: {account.get('warmup_end_at', 'N/A')}
• Вступлений сегодня: {account.get('warmup_joined_today', 0)}
• Следующее вступление: {account.get('warmup_next_join_at', 'N/A')}

🕐 **Время работы:**
• Запущен: {account.get('last_started_at', 'N/A')}
• Остановлен: {account.get('last_stopped_at', 'N/A')}
• Обновлен: {account.get('updated_at', 'N/A')}
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
                    # Запускаем только аккаунты в стандартном режиме
                    if account.get("mode") == "standard":
                        active_sessions[key] = True
                        active_account_ids[key] = account["id"]
                        asyncio.create_task(safe_send_comments(user_id, phone, account["id"]))
                        log_file.write(f"Started account {phone}\n")
                    else:
                        # Аккаунты в режиме прогрева не запускаем автоматически
                        log_file.write(f"Account {phone} in warmup mode - not started automatically\n")
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

