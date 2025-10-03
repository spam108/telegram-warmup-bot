"""
Фикс для ошибки ChannelPrivate в Pyrogram
"""
import logging
from pyrogram.errors import ChannelPrivate

def apply_channel_fix():
    """Применяет патч для обработки ChannelPrivate ошибок"""
    
    try:
        from pyrogram import Client
        
        original_get_dialogs = Client.get_dialogs
        
        async def safe_get_dialogs(self, *args, **kwargs):
            try:
                async for dialog in original_get_dialogs(self, *args, **kwargs):
                    yield dialog
            except ChannelPrivate as e:
                logging.warning(f"Пропускаем приватный канал: {e}")
                return
            except Exception as e:
                logging.warning(f"Ошибка в get_dialogs: {e}")
                return
        
        Client.get_dialogs = safe_get_dialogs
        logging.info("✅ ChannelPrivate fix applied")
        
    except Exception as e:
        logging.error(f"Error applying channel fix: {e}")

# Автоматически применяем фикс при импорте
apply_channel_fix()
