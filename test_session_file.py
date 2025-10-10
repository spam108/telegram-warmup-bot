import asyncio
import logging
import os
from pyrogram import Client

logging.basicConfig(level=logging.DEBUG)

async def test_session_file():
    user_id = 291299155
    phone = "79639791823"
    session_path = f"/app/sessions/{user_id}/{phone}"
    
    print(f"=== ДИАГНОСТИКА ФАЙЛА СЕССИИ {phone} ===")
    
    # Проверяем существование файла
    session_file = f"{session_path}.session"
    print(f"1. Файл сессии: {session_file}")
    print(f"   Существует: {os.path.exists(session_file)}")
    
    if os.path.exists(session_file):
        print(f"   Размер: {os.path.getsize(session_file)} байт")
        print(f"   Права: {oct(os.stat(session_file).st_mode)}")
    else:
        print("❌ Файл сессии не существует!")
        return
    
    # Проверяем можем ли создать Client
    print("2. Пытаемся создать Telegram Client...")
    try:
        from main import API_ID, API_HASH
        print(f"   API_ID: {API_ID}")
        print(f"   API_HASH: {API_HASH}")
        
        # Пробуем создать клиент с таймаутом
        async with Client(session_path, API_ID, API_HASH) as app:
            print("✅ Client создан успешно")
            
            # Пробуем получить информацию о себе
            print("3. Пытаемся получить информацию о пользователе...")
            try:
                me = await asyncio.wait_for(app.get_me(), timeout=10.0)
                print(f"✅ User: {me.first_name} (@{me.username})")
                return True
            except asyncio.TimeoutError:
                print("❌ Таймаут при получении пользователя")
                return False
            except Exception as e:
                print(f"❌ Ошибка при получении пользователя: {e}")
                return False
                
    except Exception as e:
        print(f"❌ Ошибка при создании Client: {e}")
        import traceback
        traceback.print_exc()
        return False

asyncio.run(test_session_file())
