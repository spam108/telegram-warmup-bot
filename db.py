import aiosqlite
import json
from typing import List, Dict, Any, Optional

async def init_db():
    """Инициализация базы данных SQLite"""
    async with aiosqlite.connect('bot.db') as db:
        # Пользователи
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                is_authenticated BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Аккаунты
        await db.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                phone TEXT,
                session_path TEXT,
                status TEXT DEFAULT 'stopped',
                mode TEXT DEFAULT 'standard',
                channels TEXT,
                sleep_min INTEGER,
                sleep_max INTEGER,
                chance INTEGER,
                system_prompt TEXT,
                warmup_end_at TIMESTAMP,
                warmup_joined_today INTEGER DEFAULT 0,
                warmup_last_join_at TIMESTAMP,
                warmup_next_join_at TIMESTAMP,
                last_started_at TIMESTAMP,
                last_stopped_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Каналы для прогрева
        await db.execute('''
            CREATE TABLE IF NOT EXISTS warmup_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                channel TEXT,
                status TEXT DEFAULT 'pending',
                joined_at TIMESTAMP,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts (id)
            )
        ''')
        
        # Логи комментариев
        await db.execute('''
            CREATE TABLE IF NOT EXISTS comment_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                channel TEXT,
                message_id INTEGER,
                status TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts (id)
            )
        ''')
        
        await db.commit()
        print("✅ База данных SQLite инициализирована")

# ==================== ПОЛЬЗОВАТЕЛИ ====================
async def ensure_user(user_id: int):
    async with aiosqlite.connect('bot.db') as db:
        await db.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
        await db.commit()

async def is_user_authenticated(user_id: int) -> bool:
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute("SELECT is_authenticated FROM users WHERE id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else False

async def set_user_authenticated(user_id: int, authenticated: bool):
    async with aiosqlite.connect('bot.db') as db:
        await db.execute("UPDATE users SET is_authenticated = ? WHERE id = ?", (authenticated, user_id))
        await db.commit()

# ==================== АККАУНТЫ ====================
async def ensure_account(user_id: int, phone: str, session_path: str):
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('''
            INSERT OR REPLACE INTO accounts (user_id, phone, session_path, updated_at) 
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, phone, session_path))
        await db.commit()

async def get_accounts_for_user(user_id: int) -> List[Dict]:
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,))
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) async for row in cursor]

async def get_account_by_session(user_id: int, phone: str) -> Optional[Dict]:
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute("SELECT * FROM accounts WHERE user_id = ? AND phone = ?", (user_id, phone))
        row = await cursor.fetchone()
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None

async def get_account_by_id(account_id: int) -> Optional[Dict]:
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = await cursor.fetchone()
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None

async def mark_account_running(account_id: int):
    async with aiosqlite.connect('bot.db') as db:
        await db.execute(
            "UPDATE accounts SET status = 'running', last_started_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,)
        )
        await db.commit()

async def mark_account_stopped(account_id: int):
    async with aiosqlite.connect('bot.db') as db:
        await db.execute(
            "UPDATE accounts SET status = 'stopped', last_stopped_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,)
        )
        await db.commit()

async def delete_account(user_id: int, phone: str):
    async with aiosqlite.connect('bot.db') as db:
        await db.execute("DELETE FROM accounts WHERE user_id = ? AND phone = ?", (user_id, phone))
        await db.commit()

async def get_running_accounts() -> List[Dict]:
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute("SELECT * FROM accounts WHERE status = 'running'")
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) async for row in cursor]

# ==================== НАСТРОЙКИ АККАУНТОВ ====================
async def update_account_settings(account_id: int, **kwargs):
    """Обновляет настройки аккаунта"""
    async with aiosqlite.connect('bot.db') as db:
        set_clause = []
        params = []
        
        for key, value in kwargs.items():
            if key == 'channels' and value is not None:
                set_clause.append("channels = ?")
                params.append(json.dumps(value) if value else '[]')
            elif value is not None:
                set_clause.append(f"{key} = ?")
                params.append(value)
        
        if set_clause:
            params.append(account_id)
            query = f"UPDATE accounts SET {', '.join(set_clause)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            await db.execute(query, params)
            await db.commit()

async def set_account_mode(account_id: int, mode: str, warmup_days: Optional[int] = None):
    """Устанавливает режим работы аккаунта"""
    async with aiosqlite.connect('bot.db') as db:
        if mode == "warmup" and warmup_days:
            from datetime import datetime, timedelta
            warmup_end = datetime.now() + timedelta(days=warmup_days)
            await db.execute(
                "UPDATE accounts SET mode = ?, warmup_end_at = ? WHERE id = ?",
                (mode, warmup_end.isoformat(), account_id)
            )
        else:
            await db.execute(
                "UPDATE accounts SET mode = ?, warmup_end_at = NULL WHERE id = ?",
                (mode, account_id)
            )
        await db.commit()

# ==================== ПРОГРЕВ КАНАЛОВ ====================
async def sync_warmup_channels(account_id: int, channels: List[str]):
    """Синхронизирует каналы для прогрева"""
    async with aiosqlite.connect('bot.db') as db:
        # Удаляем старые каналы
        await db.execute("DELETE FROM warmup_channels WHERE account_id = ?", (account_id,))
        
        # Добавляем новые
        for channel in channels:
            await db.execute(
                "INSERT INTO warmup_channels (account_id, channel) VALUES (?, ?)",
                (account_id, channel)
            )
        
        await db.commit()

async def get_warmup_pending(account_id: int, limit: int = 10, reset_if_empty: bool = False) -> List[Dict]:
    """Получает каналы ожидающие прогрева"""
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute('''
            SELECT * FROM warmup_channels 
            WHERE account_id = ? AND status = 'pending' 
            ORDER BY created_at 
            LIMIT ?
        ''', (account_id, limit))
        
        columns = [description[0] for description in cursor.description]
        results = [dict(zip(columns, row)) async for row in cursor]
        
        # Если нет каналов и нужно сбросить
        if not results and reset_if_empty:
            await db.execute('''
                UPDATE warmup_channels 
                SET status = 'pending', joined_at = NULL, error = NULL 
                WHERE account_id = ? AND status = 'error'
            ''', (account_id,))
            await db.commit()
            
            # Повторяем запрос
            cursor = await db.execute('''
                SELECT * FROM warmup_channels 
                WHERE account_id = ? AND status = 'pending' 
                ORDER BY created_at 
                LIMIT ?
            ''', (account_id, limit))
            results = [dict(zip(columns, row)) async for row in cursor]
        
        return results

async def get_warmup_queue_stats(account_id: int) -> Dict[str, int]:
    """Статистика по каналам прогрева"""
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute('''
            SELECT status, COUNT(*) as count 
            FROM warmup_channels 
            WHERE account_id = ? 
            GROUP BY status
        ''', (account_id,))
        
        stats = {'pending': 0, 'joined': 0, 'error': 0}
        async for row in cursor:
            stats[row[0]] = row[1]
        
        return stats

async def mark_warmup_channel_joined(account_id: int, channel: str):
    """Отмечает канал как присоединенный"""
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('''
            UPDATE warmup_channels 
            SET status = 'joined', joined_at = CURRENT_TIMESTAMP 
            WHERE account_id = ? AND channel = ?
        ''', (account_id, channel))
        await db.commit()

async def record_warmup_channel_error(account_id: int, channel: str, error: str):
    """Записывает ошибку присоединения к каналу"""
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('''
            UPDATE warmup_channels 
            SET status = 'error', error = ? 
            WHERE account_id = ? AND channel = ?
        ''', (error, account_id, channel))
        await db.commit()

async def increment_warmup_joined(account_id: int):
    """Увеличивает счетчик присоединений за сегодня"""
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('''
            UPDATE accounts 
            SET warmup_joined_today = warmup_joined_today + 1, 
                warmup_last_join_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (account_id,))
        await db.commit()

async def reset_warmup_daily_state(account_id: int):
    """Сбрасывает дневной счетчик прогрева"""
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('''
            UPDATE accounts 
            SET warmup_joined_today = 0 
            WHERE id = ?
        ''', (account_id,))
        await db.commit()

async def db_update_warmup_schedule(account_id: int, next_join: Any = None):
    """Обновляет расписание прогрева"""
    async with aiosqlite.connect('bot.db') as db:
        if next_join:
            await db.execute(
                "UPDATE accounts SET warmup_next_join_at = ? WHERE id = ?",
                (next_join.isoformat() if hasattr(next_join, 'isoformat') else next_join, account_id)
            )
        await db.commit()

# ==================== ЛОГИ КОММЕНТАРИЕВ ====================
async def add_comment_log(account_id: int, channel: str, message_id: int, status: str, error: str = None):
    """Добавляет лог комментария"""
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('''
            INSERT INTO comment_logs (account_id, channel, message_id, status, error)
            VALUES (?, ?, ?, ?, ?)
        ''', (account_id, channel, message_id, status, error))
        await db.commit()

# ==================== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ====================
async def _require_pool():
    """Заглушка для совместимости"""
    return None
