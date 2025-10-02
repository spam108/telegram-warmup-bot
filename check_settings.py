import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from db import init_db, get_account_by_id

async def check_settings():
    try:
        await init_db()
        print("✅ База данных подключена")
        
        # Проверим аккаунт ID=1
        account = await get_account_by_id(1)
        if account:
            print(f"📱 Аккаунт: {account['phone']} (ID: {account['id']})")
            print(f"   Задержка: {account.get('sleep_min')}-{account.get('sleep_max')}")
            print(f"   Шанс: {account.get('chance')}%")
            
            prompt = account.get('system_prompt')
            if prompt:
                print(f"   Промпт: {prompt}")
            else:
                print(f"   Промпт: Не задан")
                
            print(f"   Режим: {account.get('mode', 'standard')}")
            print(f"   Статус: {account.get('status', 'stopped')}")
            
        else:
            print("❌ Аккаунт не найден")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")

asyncio.run(check_settings())
