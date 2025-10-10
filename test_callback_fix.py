import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def test_callback_handlers():
    print("=== ТЕСТ CALLBACK ОБРАБОТЧИКОВ ===")
    
    try:
        # Импортируем необходимые модули
        from main import dp, bot
        from aiogram import types
        
        print("1. Проверяем зарегистрированные обработчики...")
        
        # Проверим какие обработчики зарегистрированы
        handlers = dp.message.handlers + dp.callback_query.handlers
        print(f"Всего обработчиков: {len(handlers)}")
        
        callback_handlers = [h for h in handlers if hasattr(h, 'filters')]
        print(f"Обработчиков callback: {len(callback_handlers)}")
        
        for handler in callback_handlers[:5]:  # Покажем первые 5
            print(f"  Обработчик: {handler}")
            
        print("2. Тестируем обработку callback данных...")
        
        # Создаем mock callback
        class MockCallback:
            def __init__(self, data):
                self.data = data
                self.from_user = type('User', (), {'id': 291299155})()
                self.message = type('Message', (), {'message_id': 1})()
                
            async def answer(self, text=None):
                print(f"    Callback answer: {text}")
                
        # Тестируем разные callback данные
        test_callbacks = [
            "start_79001400615",
            "stop_79001400615", 
            "info_79001400615",
            "del_79001400615",
            "warmup_79001400615",
            "reaction_79001400615"
        ]
        
        for callback_data in test_callbacks:
            print(f"  Тестируем callback: {callback_data}")
            mock_callback = MockCallback(callback_data)
            
            # Попробуем найти подходящий обработчик
            matched = False
            for handler in callback_handlers:
                try:
                    # Проверим фильтры обработчика
                    for filter in handler.filters:
                        if await filter(mock_callback):
                            matched = True
                            print(f"    ✅ Найден обработчик для: {callback_data}")
                            break
                    if matched:
                        break
                except Exception as e:
                    print(f"    ❌ Ошибка в фильтре: {e}")
            
            if not matched:
                print(f"    ❌ Не найден обработчик для: {callback_data}")
                
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_callback_handlers())
