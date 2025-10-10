import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def test_check_account():
    try:
        from db import init_db
        from main import check_account
        
        await init_db()
        user_id = 291299155
        phone = "79639791823"
        
        print(f"Тестируем check_account для {phone}...")
        result = await check_account(user_id, phone)
        print(f"Результат check_account: {result}")
        
        if not result:
            print("❌ check_account вернул False - аккаунт не прошел проверку")
        else:
            print("✅ check_account вернул True - аккаунт готов к запуску")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_check_account())
