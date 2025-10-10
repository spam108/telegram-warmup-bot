import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def test_account_start():
    print("=== ТЕСТ ЗАПУСКА АККАУНТА ===")
    
    try:
        from db import init_db, get_accounts_for_user, mark_account_running
        
        await init_db()
        user_id = 291299155
        
        print("1. Получаем аккаунты пользователя...")
        accounts = await get_accounts_for_user(user_id)
        print(f"   Найдено аккаунтов: {len(accounts)}")
        
        for acc in accounts:
            print(f"   - {acc['phone']}: статус={acc['status']}")
        
        if accounts:
            test_account = accounts[0]
            print(f"2. Запускаем аккаунт {test_account['phone']}...")
            
            await mark_account_running(test_account['id'])
            print("✅ mark_account_running выполнен")
            
            # Проверяем обновленный статус
            updated_accounts = await get_accounts_for_user(user_id)
            for acc in updated_accounts:
                if acc['id'] == test_account['id']:
                    print(f"   Обновленный статус: {acc['status']}")
                    break
                    
            print("3. Проверяем файл сессии...")
            import os
            session_path = f"sessions/{user_id}/{test_account['phone']}.session"
            if os.path.exists(session_path):
                print(f"   ✅ Файл сессии существует: {session_path}")
            else:
                print(f"   ❌ Файл сессии не найден: {session_path}")
                
        else:
            print("❌ Нет аккаунтов для теста")
            
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_account_start())
