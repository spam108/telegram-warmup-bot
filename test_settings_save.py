#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки сохранения настроек аккаунта
"""
import asyncio
import sys
import os

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DATABASE_URL", "sqlite:///commentbot.db")

from db import init_db, update_account_settings, get_account_by_id

async def test_settings_save():
    """Тестирует сохранение настроек аккаунта"""
    print("Инициализация базы данных...")
    await init_db()
    
    # Тестовые данные
    test_account_id = 1  # Предполагаем, что аккаунт с ID 1 существует
    test_settings = {
        "chance": 30,
        "system_prompt": "Тестовый промпт для проверки",
        "sleep_min": 15,
        "sleep_max": 25
    }
    
    print(f"Тестируем сохранение настроек для аккаунта ID {test_account_id}")
    print(f"Настройки: {test_settings}")
    
    try:
        # Сохраняем настройки
        await update_account_settings(
            account_id=test_account_id,
            chance=test_settings["chance"],
            system_prompt=test_settings["system_prompt"],
            sleep_min=test_settings["sleep_min"],
            sleep_max=test_settings["sleep_max"]
        )
        print("Настройки успешно сохранены")
        
        # Проверяем, что настройки сохранились
        account = await get_account_by_id(test_account_id)
        if account:
            print(f"Полученные данные из БД:")
            print(f"  - chance: {account.get('chance')}")
            print(f"  - system_prompt: {account.get('system_prompt')}")
            print(f"  - sleep_min: {account.get('sleep_min')}")
            print(f"  - sleep_max: {account.get('sleep_max')}")
            
            # Проверяем соответствие
            success = True
            for key, expected_value in test_settings.items():
                actual_value = account.get(key)
                if actual_value != expected_value:
                    print(f"ОШИБКА: {key} = {actual_value}, ожидалось {expected_value}")
                    success = False
                else:
                    print(f"OK: {key} = {actual_value}")
            
            if success:
                print("Все настройки сохранены корректно!")
            else:
                print("Обнаружены ошибки в сохранении настроек")
        else:
            print("Аккаунт не найден в базе данных")
            
    except Exception as e:
        print(f"Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_settings_save())