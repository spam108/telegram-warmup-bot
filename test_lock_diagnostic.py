import asyncio
import logging
import time
from contextlib import AsyncExitStack

logging.basicConfig(level=logging.INFO)

async def test_lock_diagnostic():
    try:
        from db import init_db
        from main import get_session_lock, make_session_key, _release_session_lock
        
        await init_db()
        user_id = 291299155
        phone = "79639791823"
        
        print(f"=== ДИАГНОСТИКА БЛОКИРОВОК ДЛЯ {phone} ===")
        
        key = make_session_key(user_id, phone)
        print(f"1. Session key: {key}")
        
        # Пробуем получить блокировку с таймаутом
        print("2. Пытаемся получить блокировку...")
        lock = get_session_lock(key)
        
        start_time = time.time()
        try:
            async with asyncio.timeout(5.0):
                async with lock:
                    acquire_time = time.time() - start_time
                    print(f"✅ Блокировка получена за {acquire_time:.2f} секунд")
                    
                    # Держим блокировку 2 секунды
                    print("3. Держим блокировку 2 секунды...")
                    await asyncio.sleep(2)
                    print("✅ Блокировка отпущена")
                    return True
                    
        except asyncio.TimeoutError:
            print("❌ Таймаут при получении блокировки!")
            # Пробуем принудительно освободить
            print("4. Пробуем принудительно освободить блокировку...")
            _release_session_lock(key)
            return False
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

asyncio.run(test_lock_diagnostic())
