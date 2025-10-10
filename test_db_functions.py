import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def test_db_functions():
    print("=== ТЕСТ ФУНКЦИЙ БАЗЫ ДАННЫХ ===")
    
    try:
        from db import init_db, _is_postgres, adapt_bool
        
        print("1. Инициализация БД...")
        await init_db()
        print("✅ БД инициализирована")
        
        print("2. Проверка _is_postgres()...")
        is_pg = _is_postgres()
        print(f"   _is_postgres() = {is_pg}")
        
        print("3. Проверка adapt_bool()...")
        true_result = adapt_bool(True)
        false_result = adapt_bool(False)
        print(f"   adapt_bool(True) = {true_result} (type: {type(true_result)})")
        print(f"   adapt_bool(False) = {false_result} (type: {type(false_result)})")
        
        print("4. Проверка set_user_authenticated...")
        from db import set_user_authenticated
        user_id = 291299155
        await set_user_authenticated(user_id, True)
        print("✅ set_user_authenticated выполнен")
        
        print("=== ВСЕ ТЕСТЫ ПРОЙДЕНЫ ===")
        
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_db_functions())
