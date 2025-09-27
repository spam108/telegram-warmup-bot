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
    init_db,
    is_user_authenticated,
    mark_account_running,
    mark_account_stopped,
    mark_warmup_channel_joined,
    record_warmup_channel_error,
    reset_warmup_daily_state,
    db_update_warmup_schedule,
    set_account_mode,
    set_user_authenticated,
    sync_warmup_channels,
    update_account_settings,
    increment_warmup_joined,
    _require_pool,
)
import asyncio
import logging
import os
import random
from typing import Dict, Set
from datetime import datetime, time, timezone, timedelta
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
#APcommentbot @AP_comment_bot
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
    warmup_channels = State()

active_sessions: Dict[str, bool] = {}  # Глобальный словарь для хранения активных сессий
active_account_ids: Dict[str, int] = {}
quiet_sessions_notified: Set[str] = set()

# Функция для загрузки настроек расписания
def load_schedule_config():
    """Загружает настройки расписания из schedule.json"""
    try:
        with open('schedule.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        # Если файл не найден, используем значения по умолчанию
        return {
            "quiet_period": {"start_hour": 8, "start_minute": 0, "end_hour": 20, "end_minute": 0},
            "warmup_period": {"start_hour": 12, "start_minute": 0, "end_hour": 19, "end_minute": 0},
            "warmup_settings": {"channels_per_day": 15, "delay_minutes": 7, "default_days": 7}
        }
    except Exception as e:
        print(f"Ошибка загрузки schedule.json: {e}")
        # Используем значения по умолчанию
        return {
            "quiet_period": {"start_hour": 8, "start_minute": 0, "end_hour": 20, "end_minute": 0},
            "warmup_period": {"start_hour": 12, "start_minute": 0, "end_hour": 19, "end_minute": 0},
            "warmup_settings": {"channels_per_day": 15, "delay_minutes": 7, "default_days": 7}
        }

# Загружаем настройки расписания
SCHEDULE_CONFIG = load_schedule_config()

# Настройки времени из schedule.json
QUIET_START_HOUR = SCHEDULE_CONFIG["quiet_period"]["start_hour"]
QUIET_START_MINUTE = SCHEDULE_CONFIG["quiet_period"]["start_minute"]
QUIET_END_HOUR = SCHEDULE_CONFIG["quiet_period"]["end_hour"]
QUIET_END_MINUTE = SCHEDULE_CONFIG["quiet_period"]["end_minute"]

WARMUP_CHANNELS_PER_DAY = SCHEDULE_CONFIG["warmup_settings"]["channels_per_day"]
WARMUP_DELAY_MINUTES = SCHEDULE_CONFIG["warmup_settings"]["delay_minutes"]
WARMUP_DELAY_SECONDS = WARMUP_DELAY_MINUTES * 60
WARMUP_SCAN_INTERVAL_SECONDS = 60  # Проверка каждую минуту
WARMUP_DEFAULT_DAYS = SCHEDULE_CONFIG["warmup_settings"]["default_days"]
WARMUP_SLEEP_START_HOUR = SCHEDULE_CONFIG["warmup_period"]["start_hour"]
WARMUP_SLEEP_START_MINUTE = SCHEDULE_CONFIG["warmup_period"]["start_minute"]
WARMUP_SLEEP_END_HOUR = SCHEDULE_CONFIG["warmup_period"]["end_hour"]
WARMUP_SLEEP_END_MINUTE = SCHEDULE_CONFIG["warmup_period"]["end_minute"]

# Ограничение одновременных подключений
MAX_CONCURRENT_ACCOUNTS = 5
account_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)


def make_session_key(user_id: int, phone: str) -> str:
    return f"{user_id}:{phone}"


def is_quiet_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в тихом периоде (настраивается через env)"""
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    start = time(QUIET_START_HOUR, QUIET_START_MINUTE)
    end = time(QUIET_END_HOUR, QUIET_END_MINUTE)
    return start <= current_time < end


def is_warmup_sleep_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в периоде сна для прогрева (настраивается через env)"""
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    start = time(WARMUP_SLEEP_START_HOUR, WARMUP_SLEEP_START_MINUTE)
    end = time(WARMUP_SLEEP_END_HOUR, WARMUP_SLEEP_END_MINUTE)
    result = start <= current_time < end
    return result

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
        if file.endswith('.session'):
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
            continue

        key = make_session_key(user_id, call)
        status_button_text = "Запустить" if not active_sessions.get(key) else "Остановить"
        status_button_callback = f"start_{call}" if not active_sessions.get(key) else f"stop_{call}"

        button_info = types.InlineKeyboardButton(text=f"ℹ️ {call}", callback_data=f"info_{call}")
        button_status = types.InlineKeyboardButton(text=status_button_text, callback_data=status_button_callback)
        button_delete = types.InlineKeyboardButton(text="Удалить", callback_data=f"del_{call}")
        button_mode = types.InlineKeyboardButton(text="Режим", callback_data=f"mode_{call}")

        if active_sessions.get(key):
            builder.row(button_info, button_status, button_mode)
        else:
            builder.row(button_info, button_status, button_mode, button_delete)


    builder.row(
        types.InlineKeyboardButton(text="Добавить аккаунт", callback_data="add_account"),
        types.InlineKeyboardButton(text="Добавить прогрев", callback_data="add_warmup"),
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

        channels = account_row.get("channels") or []
        warmup_stats = await get_warmup_queue_stats(account_row["id"])

        info_lines = [f"Аккаунт {session}"]
        if channels:
            info_lines.append(f"Активные каналы ({len(channels)}):\n" + "\n".join(channels[:20]))
            if len(channels) > 20:
                info_lines.append(f"... и ещё {len(channels) - 20}")
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

                await state.update_data({"account": session, "account_id": account_row["id"]})
                await bot.send_message(callback_query.from_user.id, 'Пришлите шанс комментирования\nПример: 50')
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
            os.remove(f'sessions/{callback_query.from_user.id}/{session}.session')
            await main_message(callback_query)
        except Exception as e:
            await bot.send_message(callback_query.from_user.id, f"Ошибка: {str(e)}")
            await main_message(callback_query)


    elif 'stop_' in call:

        session = str(call).split('_')[1]
        key = make_session_key(callback_query.from_user.id, session)
        if not active_sessions.get(key):
            await bot.send_message(log_channel, f"Аккаунт {session} не запущен")
            await main_message(callback_query)
            return

        try:

            active_sessions.pop(key, None)
            account_id = active_account_ids.pop(key, None)
            if account_id:
                await mark_account_stopped(account_id)

            await bot.send_message(log_channel, f'Аккаунт {session} остановлен')
            await main_message(callback_query)


        except Exception as e:
            await bot.send_message(callback_query.from_user.id, f"Ошибка: {str(e)}")
            await main_message(callback_query)

@dp.message(startaccount.chance)
async def add_chance(message: Message, state: FSMContext) -> None:
    if str(message.text).isdigit():
        await state.update_data({"chance": int(message.text)})
        await bot.send_message(message.from_user.id, 'Пришлите системный промт:')
        await state.set_state(startaccount.systempromt)
        


@dp.message(startaccount.systempromt)
async def add_systempromt(message: Message, state: FSMContext) -> None:
    await state.update_data({"systempromt": str(message.text)})
    await bot.send_message(message.from_user.id, 'Отправьте задержку для аккаунта перед отправлением комментария\nПример: 10-20')
    await state.set_state(startaccount.sleeps)



@dp.message(startaccount.sleeps)
async def add_sleeps(message: Message, state: FSMContext) -> None:

    if '-' in str(message.text):
        try:
            sleeps = str(message.text).split('-')
            if len(sleeps) == 2 and all(sleep.isdigit() for sleep in sleeps):
                await state.update_data({"sleeps": message.text})
            else:
                await main_message(message)
                return
        except Exception as e:
            await bot.send_message(message.from_user.id, f"Ошибка: {str(e)}")
            await main_message(message)
            return


        session = (await state.get_data()).get("account")

        channels = []

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

            await bot.send_message(message.from_user.id,
                                f'Аккаунт подписан на каналы:\n{channels}\n\nПришлите каналы на которые нужно подписаться\n(если не нужно пришлите -)')
            await state.set_state(startaccount.channels)
        else:
            await state.clear()
            await main_message(message)


@dp.message(startaccount.channels)
async def add_channels(message: Message, state: FSMContext) -> None:
    session = (await state.get_data()).get("account")
    sleeps = (await state.get_data()).get("sleeps")
    system_promt = (await state.get_data()).get("systempromt")
    chance = (await state.get_data()).get("chance")

    account_id = (await state.get_data()).get("account_id")
    warmup_channels = []

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

                        try:
                            await app.join_chat(chl)
                            await bot.send_message(log_channel, f'Аккаунт {session} вступил в канал: {chl}')
                        except Exception as e:
                            await bot.send_message(log_channel, f'Ошибка при вступлении в канал {chl}: {e}')
        else:
            await state.clear()
            await main_message(message)
            return
        await update_account_settings(
            account_id,
            channels=channels,
        )

    await update_account_settings(
        account_id,
        chance=chance,
        system_prompt=system_promt,
        sleep_min=int(sleeps.split('-')[0]),
        sleep_max=int(sleeps.split('-')[1]),
    )

    # Показываем существующие каналы для прогрева
    existing_warmup = await get_warmup_pending(account_id, limit=50)
    if existing_warmup:
        warmup_list = [entry["channel"] for entry in existing_warmup]
        await bot.send_message(message.from_user.id, f'Текущие каналы в прогреве:\n' + '\n'.join(warmup_list))
    else:
        await bot.send_message(message.from_user.id, 'Каналы в прогреве: нет')

    # Переходим к вводу каналов для прогрева
    await state.update_data({"account_id": account_id})
    await bot.send_message(message.from_user.id, 'Теперь пришлите каналы для прогрева (каждый канал с новой строки). Для отмены отправьте "-".')
    await state.set_state(startaccount.warmup_channels)


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


async def process_warmup_accounts():
    """Фоновая задача для добавления каналов в режиме прогрева (настраивается через env)"""
    while True:
        try:
            now = datetime.now(timezone.utc)
            is_warmup_time = is_warmup_sleep_period(now)
            
            # Логируем каждые 10 минут для отладки
            if now.minute % 10 == 0:
                await bot.send_message(log_channel, f"Warmup check: {now.strftime('%H:%M')} UTC, is_warmup_time: {is_warmup_time}")
            
            # Проверяем, находимся ли мы в периоде сна для прогрева
            if not is_warmup_time:
                await asyncio.sleep(WARMUP_SCAN_INTERVAL_SECONDS)
                continue
            
            all_accounts = await get_running_accounts()
            accounts = [acc for acc in all_accounts if acc.get("mode") == "warmup"]
            
            await bot.send_message(log_channel, f"Warmup: Found {len(all_accounts)} running accounts, {len(accounts)} in warmup mode")
            await bot.send_message(log_channel, f"Warmup: Active sessions: {list(active_sessions.keys())}")

            for account in accounts:
                if account.get("mode") != "warmup":
                    continue

                # Проверяем, что основной процесс комментирования не запущен
                session_key = account["phone"]
                user_id = account["user_id"]
                key = make_session_key(user_id, session_key)
                
                await bot.send_message(log_channel, f"Warmup: Processing account {session_key}, active: {active_sessions.get(key)}")
                
                if active_sessions.get(key):
                    await bot.send_message(log_channel, f"Warmup: Account {session_key} is active, skipping")
                    continue

                # Проверяем, не истек ли период прогрева
                warmup_end = account.get("warmup_end_at")
                if warmup_end:
                    if warmup_end.tzinfo is None:
                        warmup_end = warmup_end.replace(tzinfo=timezone.utc)
                    if warmup_end <= now:
                        await set_account_mode(account["id"], "standard", warmup_days=None)
                        continue

                # Сбрасываем дневной счетчик если новый день
                warmup_last_join_at = account.get("warmup_last_join_at")
                if warmup_last_join_at and warmup_last_join_at.tzinfo is None:
                    warmup_last_join_at = warmup_last_join_at.replace(tzinfo=timezone.utc)

                if warmup_last_join_at and warmup_last_join_at.date() < now.date():
                    await reset_warmup_daily_state(account["id"])
                    account["warmup_joined_today"] = 0

                # Проверяем, не достигли ли дневного лимита
                joined_today = account.get("warmup_joined_today", 0)
                await bot.send_message(log_channel, f"Warmup: Account {session_key} joined today: {joined_today}/{WARMUP_CHANNELS_PER_DAY}")
                
                if joined_today >= WARMUP_CHANNELS_PER_DAY:
                    await bot.send_message(log_channel, f"Warmup: Account {session_key} reached daily limit, skipping")
                    continue

                # Получаем следующий канал для добавления
                pending_channels = await get_warmup_pending(account["id"], limit=1, reset_if_empty=True)
                await bot.send_message(log_channel, f"Warmup: Account {session_key} pending channels: {len(pending_channels)}")
                
                if not pending_channels:
                    await bot.send_message(log_channel, f"Warmup: Account {session_key} no pending channels, skipping")
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

                # Создаем клиент и пытаемся вступить в канал
                client = Client(
                    name=os.path.join("sessions", str(user_id), session_key),
                    api_id=API_ID,
                    api_hash=API_HASH,
                )

                try:
                    async with client:
                        try:
                            await client.join_chat(channel)
                            await mark_warmup_channel_joined(account["id"], channel)
                            await increment_warmup_joined(account["id"])
                            await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) вступил в канал: {channel}")
                        except UserAlreadyParticipant:
                            await mark_warmup_channel_joined(account["id"], channel)
                        except Exception as e:
                            await record_warmup_channel_error(account["id"], channel, str(e))
                            await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) ошибка вступления в канал {channel}: {e}")
                except Exception as e:
                    error_msg = str(e)
                    if "phone number" in error_msg.lower() or "auth" in error_msg.lower():
                        await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) - сессия истекла, пропускаем прогрев")
                        # Переключаем в стандартный режим если сессия истекла
                        await set_account_mode(account["id"], "standard", warmup_days=None)
                    else:
                        await bot.send_message(log_channel, f"Аккаунт {session_key} (прогрев) ошибка подключения: {e}")

        except Exception as e:
            logging.exception("Warmup loop error: %s", e)

        # Ждем 10 минут до следующей попытки
        await asyncio.sleep(WARMUP_DELAY_SECONDS)

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


@dp.message(startaccount.warmup_channels)
async def add_warmup_channels(message: Message, state: FSMContext) -> None:
    session = (await state.get_data()).get("account")
    account_id = (await state.get_data()).get("account_id")
    processed = (await state.get_data()).get("warmup_processed", False)
    
    # Логируем для отладки
    await bot.send_message(log_channel, f"Обработка каналов прогрева для {session}: '{message.text[:50]}...' (processed: {processed})")

    # Проверяем, не обрабатывали ли мы уже это состояние
    if not account_id or processed:
        if processed:
            await bot.send_message(message.from_user.id, "Каналы прогрева уже обработаны. Используйте /start для нового аккаунта.")
        else:
            await bot.send_message(message.from_user.id, "Ошибка: аккаунт не найден. Попробуйте снова.")
        await state.clear()
        await main_message(message)
        return

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
            
            # Запускаем аккаунт в режиме прогрева (БЕЗ основной задачи комментирования)
            key = make_session_key(message.from_user.id, session)
            # НЕ устанавливаем active_sessions[key] = True для режима прогрева
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Используются существующие каналы прогрева.')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
            await main_message(message)
            # asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Отключено для режима прогрева
            return
        else:
            # Нет каналов в прогреве - запускаем в стандартном режиме
            await set_account_mode(account_id, "standard", warmup_days=None)
            await sync_warmup_channels(account_id, [])
            
            # Запускаем аккаунт в режиме прогрева (БЕЗ основной задачи комментирования)
            key = make_session_key(message.from_user.id, session)
            # НЕ устанавливаем active_sessions[key] = True для режима прогрева
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, 'Аккаунт запущен в стандартном режиме (без прогрева).')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование')
            await main_message(message)
            # asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Отключено для режима прогрева
            return

    channels = [line.strip() for line in message.text.splitlines() if line.strip()]
    warmup_channels = [chl for chl in channels if not chl.startswith('-')]
    
    # Удаляем дубликаты, сохраняя порядок
    seen = set()
    warmup_channels = [x for x in warmup_channels if not (x in seen or seen.add(x))]

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
            
            # Запускаем аккаунт в режиме прогрева (БЕЗ основной задачи комментирования)
            key = make_session_key(message.from_user.id, session)
            # НЕ устанавливаем active_sessions[key] = True для режима прогрева
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Используются существующие каналы прогрева.')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
            await main_message(message)
            # asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Отключено для режима прогрева
            return
        else:
            # Нет каналов в прогреве - запускаем в стандартном режиме
            await set_account_mode(account_id, "standard", warmup_days=None)
            await sync_warmup_channels(account_id, [])
            
            # Запускаем аккаунт в режиме прогрева (БЕЗ основной задачи комментирования)
            key = make_session_key(message.from_user.id, session)
            # НЕ устанавливаем active_sessions[key] = True для режима прогрева
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, 'Аккаунт запущен в стандартном режиме (без прогрева).')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование')
            await main_message(message)
            # asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))  # Отключено для режима прогрева
            return

    try:
        await sync_warmup_channels(account_id, warmup_channels)
        await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
        # Планируем следующее вступление в период сна (4:00-6:00)
        now = datetime.now(timezone.utc)
        tomorrow_4_30am = now.replace(hour=4, minute=30, second=0, microsecond=0) + timedelta(days=1)
        await db_update_warmup_schedule(account_id, next_join=tomorrow_4_30am)
    except Exception as e:
        await bot.send_message(log_channel, f"Ошибка при сохранении каналов прогрева для {session}: {e}")
        await bot.send_message(message.from_user.id, f"Ошибка при сохранении каналов прогрева: {e}")
        await state.clear()
        await main_message(message)
        return

    # Запускаем аккаунт в режиме прогрева (БЕЗ основной задачи комментирования)
    key = make_session_key(message.from_user.id, session)
    # НЕ устанавливаем active_sessions[key] = True для режима прогрева
    active_account_ids[key] = account_id
    quiet_sessions_notified.discard(key)
    await mark_account_running(account_id)

    await state.clear()
    await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Запланировано {len(warmup_channels)} каналов для прогрева.')
    await bot.send_message(log_channel, f'Аккаунт {session} начал режим прогрева (без комментирования)')
    await main_message(message)
    # НЕ запускаем safe_send_comments для режима прогрева


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


async def main():
    try:
        with open("bot_log.txt", "w") as log_file:
            log_file.write("Starting bot initialization...\n")
            log_file.flush()
            
            await init_db()
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
                    # Проверяем, не в режиме ли прогрева
                    if account.get("mode") == "warmup":
                        # Для аккаунтов в режиме прогрева не запускаем основную задачу комментирования
                        # Они будут обрабатываться только в process_warmup_accounts
                        log_file.write(f"Account {phone} in warmup mode - skipping main comment task\n")
                        log_file.flush()
                        # Убеждаемся, что аккаунт НЕ активен
                        active_sessions.pop(key, None)
                    else:
                        # Обычные аккаунты запускаем как обычно
                        active_sessions[key] = True
                        active_account_ids[key] = account["id"]
                        asyncio.create_task(safe_send_comments(user_id, phone, account["id"]))
                        log_file.write(f"Started account {phone}\n")
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


asyncio.run(main())
# asyncio.run(bot.run())

