import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def test_password_logic():
    print("=== ТЕСТ ЛОГИКИ ПАРОЛЯ ===")
    
    try:
        # Имитируем логику проверки пароля из main.py
        correct_password = "853211"  # Пароль из .env
        test_password = "853211"     # Правильный пароль
        
        print(f"Проверяем пароль: '{test_password}'")
        print(f"Ожидаемый пароль: '{correct_password}'")
        
        if test_password == correct_password:
            print("✅ Пароль верный")
            
            # Имитируем вызов set_user_authenticated
            from db import init_db, set_user_authenticated
            await init_db()
            user_id = 291299155
            await set_user_authenticated(user_id, True)
            print("✅ Аутентификация установлена")
            
            # Проверяем что установилось
            from db import is_user_authenticated
            status = await is_user_authenticated(user_id)
            print(f"Статус аутентификации: {status}")
        else:
            print("❌ Пароль неверный")
            
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_password_logic())
