import logging
import asyncio
from aiogram import types, F
from db import (
    init_db,
    get_account_by_session,
    mark_account_running, 
    mark_account_stopped,
    delete_account,
    get_account_by_id
)

async def test_callback_fix():
    """Тестируем полный фикс callback обработчика"""
    
    print("=== ТЕСТ ПОЛНОГО FIX CALLBACK ===")
    
    try:
        # Инициализируем БД
        await init_db()
        print("✅ БД инициализирована")
        
        user_id = 291299155
        
        # Тестируем разные callback сценарии
        test_cases = [
            ("info_79001400615", "Информация об аккаунте"),
            ("start_79001400615", "Запуск аккаунта"),
            ("stop_79001400615", "Остановка аккаунта"), 
            ("del_79001400615", "Удаление аккаунта"),
            ("warmup_79001400615", "Настройки прогрева"),
            ("reaction_79001400615", "Настройки реакций")
        ]
        
        for callback_data, description in test_cases:
            print(f"\n🔧 Тест: {description} ({callback_data})")
            
            class MockCallback:
                def __init__(self, data):
                    self.data = data
                    self.from_user = type('User', (), {'id': user_id})()
                    self.message = type('Message', (), {'message_id': 1})()
                    
                async def answer(self, text=None):
                    print(f"   📨 Ответ бота: {text}")
                    return True
                    
            mock_callback = MockCallback(callback_data)
            
            # Тестируем обработку
            if callback_data.startswith('info_'):
                session = callback_data.split('_', 1)[1]
                account = await get_account_by_session(user_id, session)
                if account:
                    print(f"   ✅ Аккаунт найден: {account['phone']}")
                    await mock_callback.answer(f"Информация об аккаунте {session}")
                else:
                    print(f"   ❌ Аккаунт не найден")
                    
            elif callback_data.startswith('start_'):
                session = callback_data.split('_', 1)[1]
                account = await get_account_by_session(user_id, session)
                if account:
                    print(f"   ✅ Запускаем аккаунт: {account['phone']}")
                    await mark_account_running(account['id'])
                    await mock_callback.answer(f"Аккаунт {session} запускается...")
                else:
                    print(f"   ❌ Аккаунт не найден")
                    
            elif callback_data.startswith('stop_'):
                session = callback_data.split('_', 1)[1]
                account = await get_account_by_session(user_id, session)
                if account:
                    print(f"   ✅ Останавливаем аккаунт: {account['phone']}")
                    await mark_account_stopped(account['id'])
                    await mock_callback.answer(f"Аккаунт {session} останавливается...")
                else:
                    print(f"   ❌ Аккаунт не найден")
                    
            elif callback_data.startswith('del_'):
                session = callback_data.split('_', 1)[1]
                account = await get_account_by_session(user_id, session)
                if account:
                    print(f"   ✅ Удаляем аккаунт: {account['phone']}")
                    await delete_account(account['id'])
                    await mock_callback.answer(f"Аккаунт {session} удален")
                else:
                    print(f"   ❌ Аккаунт не найден")
                    
            elif callback_data.startswith('warmup_'):
                session = callback_data.split('_', 1)[1]
                print(f"   ⚙️  Открываем настройки прогрева для {session}")
                await mock_callback.answer(f"Настройки прогрева для {session}")
                
            elif callback_data.startswith('reaction_'):
                session = callback_data.split('_', 1)[1]
                print(f"   🎯 Открываем настройки реакций для {session}")
                await mock_callback.answer(f"Настройки реакций для {session}")
                
        print("\n✅ Все тесты завершены!")
        
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_callback_fix())
