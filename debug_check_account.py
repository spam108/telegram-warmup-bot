import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

async def debug_check_account():
    try:
        from db import init_db
        from main import check_account, make_session_key, get_session_lock, active_pyrogram_clients, active_sessions
        
        await init_db()
        user_id = 291299155
        phone = "79639791823"
        
        print(f"=== ДЕБАГ check_account для {phone} ===")
        
        key = make_session_key(user_id, phone)
        print(f"1. Session key: {key}")
        
        print(f"2. active_pyrogram_clients: {active_pyrogram_clients.get(key)}")
        print(f"3. active_sessions: {active_sessions.get(key)}")
        
        lock = get_session_lock(key)
        print(f"4. Lock: {lock}")
        
        print("5. Calling check_account...")
        result = await check_account(user_id, phone)
        print(f"6. Result: {result}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(debug_check_account())
