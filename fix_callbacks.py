import logging
from aiogram import types, F
from db import (
    get_account_by_session, 
    mark_account_running, 
    mark_account_stopped,
    delete_account
)

async def handle_account_callbacks(callback_query: types.CallbackQuery, call: str):
    """Обработчик callback запросов для кнопок аккаунтов"""
    
    user_id = callback_query.from_user.id
    
    # Обрабатываем префиксы аккаунтов
    if call.startswith('info_'):
        session = call.split('_', 1)[1]
        logging.info(f"Обработка info для аккаунта {session}, пользователь {user_id}")
        
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            await callback_query.answer("Аккаунт не найден в базе данных")
            return
            
        # TODO: Показать информацию об аккаунте
        await callback_query.answer(f"Информация об аккаунте {session}")
        
    elif call.startswith('start_'):
        session = call.split('_', 1)[1]
        logging.info(f"Обработка start для аккаунта {session}, пользователь {user_id}")
        
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            await callback_query.answer("Аккаунт не найден в базе данных")
            return
            
        # Запускаем аккаунт
        await mark_account_running(account_row["id"])
        await callback_query.answer(f"Аккаунт {session} запускается...")
        
    elif call.startswith('stop_'):
        session = call.split('_', 1)[1]
        logging.info(f"Обработка stop для аккаунта {session}, пользователь {user_id}")
        
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            await callback_query.answer("Аккаунт не найден в базе данных")
            return
            
        # Останавливаем аккаунт
        await mark_account_stopped(account_row["id"])
        await callback_query.answer(f"Аккаунт {session} останавливается...")
        
    elif call.startswith('del_'):
        session = call.split('_', 1)[1]
        logging.info(f"Обработка del для аккаунта {session}, пользователь {user_id}")
        
        account_row = await get_account_by_session(user_id, session)
        if not account_row:
            await callback_query.answer("Аккаунт не найден в базе данных")
            return
            
        # Удаляем аккаунт
        await delete_account(account_row["id"])
        await callback_query.answer(f"Аккаунт {session} удален")
        
    elif call.startswith('warmup_'):
        session = call.split('_', 1)[1]
        logging.info(f"Обработка warmup для аккаунта {session}, пользователь {user_id}")
        await callback_query.answer(f"Настройки прогрева для {session}")
        
    elif call.startswith('reaction_'):
        session = call.split('_', 1)[1]
        logging.info(f"Обработка reaction для аккаунта {session}, пользователь {user_id}")
        await callback_query.answer(f"Настройки реакций для {session}")

# Тестируем обработчик
async def test_fix():
    print("=== ТЕСТ ОБРАБОТЧИКА CALLBACK ===")
    
    test_cases = [
        "info_79001400615",
        "start_79001400615", 
        "stop_79001400615",
        "del_79001400615",
        "warmup_79001400615",
        "reaction_79001400615"
    ]
    
    for callback_data in test_cases:
        print(f"Тестируем: {callback_data}")
        
        class MockCallback:
            data = callback_data
            from_user = type('User', (), {'id': 291299155})()
            
            async def answer(self, text):
                print(f"  Ответ: {text}")
                
        mock_callback = MockCallback()
        await handle_account_callbacks(mock_callback, callback_data)

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_fix())
