import asyncio
import logging
from main import active_sessions, active_pyrogram_clients

logging.basicConfig(level=logging.INFO)

async def test_global_state():
    print("=== СОСТОЯНИЕ ГЛОБАЛЬНЫХ ПЕРЕМЕННЫХ ===")
    print(f"active_sessions: {len(active_sessions)} sessions")
    for key, value in list(active_sessions.items())[:3]:
        print(f"  {key}: {value}")
    
    print(f"active_pyrogram_clients: {len(active_pyrogram_clients)} clients")
    for key, value in list(active_pyrogram_clients.items())[:3]:
        print(f"  {key}: {type(value)}")
    
    print("✅ Диагностика завершена")

asyncio.run(test_global_state())
