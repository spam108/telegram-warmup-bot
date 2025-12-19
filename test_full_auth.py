import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def test_full_auth():
    print("=== ТЕСТ ПОЛНОГО ЦИКЛА АВТОРИЗАЦИИ ===")
    
    try:
        from db import init_db, set_user_authenticated, is_user_authenticated
        
        await init_db()
        print("✅ БД инициализирована")
        
        user_id = 291299155
        
        # Тестируем установку аутентификации
        print("1. Устанавливаем is_authenticated = True...")
        await set_user_authenticated(user_id, True)
        
        # Проверяем что установилось
        print("2. Проверяем статус...")
        auth_status = await is_user_authenticated(user_id)
        print(f"   is_user_authenticated = {auth_status}")
        
        # Проверяем в базе напрямую
        print("3. Проверяем в базе данных...")
        from db import _fetchone
        row = await _fetchone("SELECT is_authenticated FROM users WHERE user_id = ?", (user_id,))
        if row:
            db_status = row["is_authenticated"]
            print(f"   В базе: is_authenticated = {db_status} (тип: {type(db_status)})")
        else:
            print("   ❌ Пользователь не найден в базе")
        
        print("=== ТЕСТ ЗАВЕРШЕН ===")
        
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_full_auth())
