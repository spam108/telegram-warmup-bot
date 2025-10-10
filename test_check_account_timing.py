import asyncio
import logging
import time
logging.basicConfig(level=logging.INFO)

async def test_check_account_timing():
    try:
        from db import init_db
        from main import check_account
        
        await init_db()
        user_id = 291299155
        phone = "79639791823"
        
        print(f"Тестируем время выполнения check_account для {phone}...")
        
        start_time = time.time()
        try:
            result = await asyncio.wait_for(
                check_account(user_id, phone),
                timeout=35.0
            )
            end_time = time.time()
            duration = end_time - start_time
            print(f"✅ check_account выполнен за {duration:.2f} секунд")
            print(f"Результат: {result}")
        except asyncio.TimeoutError:
            end_time = time.time()
            duration = end_time - start_time
            print(f"❌ check_account превысил таймаут 35 секунд")
            print(f"Выполнялся: {duration:.2f} секунд")
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            print(f"❌ Ошибка в check_account: {e}")
            print(f"Выполнялся: {duration:.2f} секунд")
            
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")

asyncio.run(test_check_account_timing())
