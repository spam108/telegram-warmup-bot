#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки сохранения настроек с реальными переменными окружения
"""
import asyncio
import sys
import os

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем переменные окружения
os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@postgres:5432/commentbot"
os.environ["BOT_TOKEN"] = "8231470375:AAFcpq4Se_u1r8TdhzXTohjlFGI9jIUTbio"
os.environ["API_ID"] = "20047744"
os.environ["API_HASH"] = "09c81e1d266b98a8d82291abaa75bba7"
os.environ["PASSWORD"] = "853211"

from db import init_db, update_account_settings, get_account_by_id, get_accounts_for_user

async def test_settings_save():
    """Тестирует сохранение настроек аккаунта"""
    print("Инициализация базы данных...")
    try:
        await init_db()
        print("База данных инициализирована успешно")
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")
        return
    
    # Получаем список всех аккаунтов
    print("\nПолучаем список аккаунтов...")
    try:
        all_accounts = []
        for user_id in [291299155]:  # ID пользователя из сессий
            accounts = await get_accounts_for_user(user_id)
            all_accounts.extend(accounts)
        
        print(f"Найдено аккаунтов: {len(all_accounts)}")
        for acc in all_accounts:
            print(f"  - ID: {acc['id']}, Phone: {acc['phone']}, User: {acc['user_id']}")
        
        if not all_accounts:
            print("Аккаунты не найдены. Создаем тестовый аккаунт...")
            # Создаем тестовый аккаунт
            from db import ensure_account
            test_account = await ensure_account(291299155, "test_phone", "test_session")
            test_account_id = test_account["id"]
        else:
            test_account_id = all_accounts[0]["id"]
            
    except Exception as e:
        print(f"Ошибка получения аккаунтов: {e}")
        return
    
    # Тестовые данные
    test_settings = {
        "chance": 35,
        "system_prompt": "Тестовый промпт для проверки сохранения",
        "sleep_min": 12,
        "sleep_max": 28
    }
    
    print(f"\nТестируем сохранение настроек для аккаунта ID {test_account_id}")
    print(f"Настройки: {test_settings}")
    
    try:
        # Сохраняем настройки
        print("\nСохраняем настройки...")
        await update_account_settings(
            account_id=test_account_id,
            chance=test_settings["chance"],
            system_prompt=test_settings["system_prompt"],
            sleep_min=test_settings["sleep_min"],
            sleep_max=test_settings["sleep_max"]
        )
        print("Настройки успешно сохранены")
        
        # Проверяем, что настройки сохранились
        print("\nПроверяем сохраненные настройки...")
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
                print("\n🎉 Все настройки сохранены корректно!")
            else:
                print("\n💥 Обнаружены ошибки в сохранении настроек")
        else:
            print("❌ Аккаунт не найден в базе данных")
            
    except Exception as e:
        print(f"💥 Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_settings_save())
