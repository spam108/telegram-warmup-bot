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
    regular_channels = State()
    warmup_channels = State()

active_sessions: Dict[str, bool] = {}  # Глобальный словарь для хранения активных сессий
active_account_ids: Dict[str, int] = {}
quiet_sessions_notified: Set[str] = set()

WARMUP_CHANNELS_PER_DAY = 15
WARMUP_DELAY_SECONDS = 10 * 60  # 10 минут между добавлениями
WARMUP_SCAN_INTERVAL_SECONDS = 60  # Проверка каждую минуту
WARMUP_DEFAULT_DAYS = 7
WARMUP_SLEEP_START_HOUR = 4  # Начало периода сна (4:00)
WARMUP_SLEEP_END_HOUR = 6    # Конец периода сна (6:00)

# Ограничение одновременных подключений
MAX_CONCURRENT_ACCOUNTS = 5
account_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)


def make_session_key(user_id: int, phone: str) -> str:
    return f"{user_id}:{phone}"


def is_quiet_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в тихом периоде (00:30 - 06:30)"""
    now = now or datetime.now()
    current_time = now.time()
    start = time(0, 30)
    end = time(6, 30)
    return start <= current_time < end


def is_warmup_sleep_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в периоде сна для прогрева (04:00 - 06:00)"""
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    start = time(WARMUP_SLEEP_START_HOUR, 0)
    end = time(WARMUP_SLEEP_END_HOUR, 0)
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
            await state.set_state(startaccount.regular_channels)
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


@dp.message(startaccount.regular_channels)
async def add_regular_channels(message: Message, state: FSMContext) -> None:
    """Обработчик для обычных каналов (немедленное вступление)"""
    session = (await state.get_data()).get("account")
    account_id = (await state.get_data()).get("account_id")
    
    if not account_id:
        await bot.send_message(message.from_user.id, "Ошибка: аккаунт не найден. Попробуйте снова.")
        await state.clear()
        await main_message(message)
        return
    
    if str(message.text) != '-':
        channels = [line.strip() for line in message.text.splitlines() if line.strip()]
        
        # Вступаем в обычные каналы сразу
        for channel in channels:
            await join_channel(channel, account_id, session, message.from_user.id, is_warmup=False)
        
        # Обновляем список обычных каналов в БД
        await update_account_settings(account_id, channels=channels)
    
    # Переходим к диалогу каналов прогрева
    await bot.send_message(message.from_user.id, 'Текущие каналы в прогреве:')
    
    # Показываем существующие каналы прогрева
    existing_warmup = await get_warmup_pending(account_id, limit=10)
    if existing_warmup:
        warmup_list = [ch["channel"] for ch in existing_warmup]
        await bot.send_message(message.from_user.id, '\n'.join(warmup_list))
    else:
        await bot.send_message(message.from_user.id, 'Нет каналов в прогреве')
    
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


async def join_channel(channel: str, account_id: int, session_key: str, user_id: int, is_warmup: bool = False) -> bool:
    """Единая функция для вступления в канал (обычный или прогрев)"""
    try:
        # Создаем клиент
        session_file = os.path.join("sessions", str(user_id), f"{session_key}.session")
        if not os.path.exists(session_file):
            await bot.send_message(log_channel, f"Аккаунт {session_key} - файл сессии не найден: {session_file}")
            return False
            
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
                
                return True
                
            except UserAlreadyParticipant:
                if is_warmup:
                    await mark_warmup_channel_joined(account_id, channel)
                await bot.send_message(log_channel, f"Аккаунт {session_key} уже состоит в канале: {channel}")
                return True
                
            except Exception as e:
                if is_warmup:
                    await record_warmup_channel_error(account_id, channel, str(e))
                await bot.send_message(log_channel, f"Аккаунт {session_key} ошибка вступления в канал {channel}: {e}")
                return False
                
    except Exception as e:
        error_msg = str(e)
        if any(keyword in error_msg.lower() for keyword in ["phone number", "auth", "eof when reading", "session", "unauthorized"]):
            await bot.send_message(log_channel, f"Аккаунт {session_key} - сессия истекла или повреждена: {error_msg}")
            return False
        else:
            await bot.send_message(log_channel, f"Аккаунт {session_key} ошибка подключения: {e}")
            return False


async def process_warmup_accounts():
    """Фоновая задача для добавления каналов в режиме прогрева во время сна (4:00-6:00)"""
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Проверяем, находимся ли мы в периоде сна для прогрева
            if not is_warmup_sleep_period(now):
                await asyncio.sleep(WARMUP_SCAN_INTERVAL_SECONDS)
                continue
            
            all_accounts = await get_running_accounts()
            accounts = [acc for acc in all_accounts if acc.get("mode") == "warmup"]

            for account in accounts:
                if account.get("mode") != "warmup":
                    continue

                # Проверяем, что основной процесс комментирования не запущен
                session_key = account["phone"]
                user_id = account["user_id"]
                key = make_session_key(user_id, session_key)
                
                if active_sessions.get(key):
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
                if account.get("warmup_joined_today", 0) >= WARMUP_CHANNELS_PER_DAY:
                    continue

                # Получаем следующий канал для добавления
                pending_channels = await get_warmup_pending(account["id"], limit=1, reset_if_empty=True)
                if not pending_channels:
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
                success = await join_channel(channel, account["id"], session_key, user_id, is_warmup=True)
                
                if not success:
                    # Если сессия истекла - переключаем в стандартный режим
                    await set_account_mode(account["id"], "standard", warmup_days=None)

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
            tomorrow_4am = now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)
            await db_update_warmup_schedule(account_id, next_join=tomorrow_4am)
            
            # Запускаем аккаунт
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Используются существующие каналы прогрева.')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
            await main_message(message)
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))
            return
        else:
            # Нет каналов в прогреве - запускаем в стандартном режиме
            await set_account_mode(account_id, "standard", warmup_days=None)
            await sync_warmup_channels(account_id, [])
            
            # Запускаем аккаунт
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, 'Аккаунт запущен в стандартном режиме (без прогрева).')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование')
            await main_message(message)
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))
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
            tomorrow_4am = now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)
            await db_update_warmup_schedule(account_id, next_join=tomorrow_4am)
            
            # Запускаем аккаунт
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Используются существующие каналы прогрева.')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
            await main_message(message)
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))
            return
        else:
            # Нет каналов в прогреве - запускаем в стандартном режиме
            await set_account_mode(account_id, "standard", warmup_days=None)
            await sync_warmup_channels(account_id, [])
            
            # Запускаем аккаунт
            key = make_session_key(message.from_user.id, session)
            active_sessions[key] = True
            active_account_ids[key] = account_id
            quiet_sessions_notified.discard(key)
            await asyncio.sleep(0.1)  # Пауза перед операцией с БД
            await mark_account_running(account_id)
            
            await state.clear()
            await bot.send_message(message.from_user.id, 'Аккаунт запущен в стандартном режиме (без прогрева).')
            await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование')
            await main_message(message)
            asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))
            return

    try:
        await sync_warmup_channels(account_id, warmup_channels)
        await set_account_mode(account_id, "warmup", warmup_days=WARMUP_DEFAULT_DAYS)
        # Планируем следующее вступление в период сна (4:00-6:00)
        now = datetime.now(timezone.utc)
        tomorrow_4am = now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)
        await db_update_warmup_schedule(account_id, next_join=tomorrow_4am)
    except Exception as e:
        await bot.send_message(log_channel, f"Ошибка при сохранении каналов прогрева для {session}: {e}")
        await bot.send_message(message.from_user.id, f"Ошибка при сохранении каналов прогрева: {e}")
        await state.clear()
        await main_message(message)
        return

    # Запускаем аккаунт
    key = make_session_key(message.from_user.id, session)
    active_sessions[key] = True
    active_account_ids[key] = account_id
    quiet_sessions_notified.discard(key)
    await mark_account_running(account_id)

    await state.clear()
    await bot.send_message(message.from_user.id, f'Аккаунт запущен в режиме прогрева. Запланировано {len(warmup_channels)} каналов для прогрева.')
    await bot.send_message(log_channel, f'Аккаунт {session} начал комментирование в режиме прогрева')
    await main_message(message)
    asyncio.create_task(safe_send_comments(message.from_user.id, session, account_id))


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


asyncio.run(main())
# asyncio.run(bot.run())

