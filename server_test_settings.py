#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки сохранения настроек на сервере
"""
import asyncio
import sys
import os

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DATABASE_URL", "sqlite:///commentbot.db")

from db import (
    close_db,
    ensure_account,
    ensure_user,
    get_account_by_id,
    get_accounts_for_user,
    init_db,
    update_account_settings,
)


DEFAULT_USER_ID = 291299155
DEFAULT_PHONE = "+79999999999"
DEFAULT_SESSION_PATH = "sessions/test.session"

async def test_settings_save():
    """Тестирует сохранение настроек аккаунта"""
    print("Инициализация базы данных...")
    try:
        await init_db()
        print("База данных инициализирована успешно")
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")
        return False

    result = False

    try:
        # Получаем список всех аккаунтов
        print("\nПолучаем список аккаунтов...")
        all_accounts = []
        for user_id in [DEFAULT_USER_ID]:  # ID пользователя из сессий
            accounts = await get_accounts_for_user(user_id)
            all_accounts.extend(accounts)

        print(f"Найдено аккаунтов: {len(all_accounts)}")
        for acc in all_accounts:
            print(f"  - ID: {acc['id']}, Phone: {acc['phone']}, User: {acc['user_id']}")
            print(
                f"    Текущие настройки: chance={acc.get('chance')}, "
                f"sleep_min={acc.get('sleep_min')}, sleep_max={acc.get('sleep_max')}"
            )

        if not all_accounts:
            print("Аккаунты не найдены! Создаём тестовый аккаунт...")
            await ensure_user(DEFAULT_USER_ID)

            session_dir = os.path.dirname(DEFAULT_SESSION_PATH)
            if session_dir and not os.path.exists(session_dir):
                os.makedirs(session_dir, exist_ok=True)

            account = await ensure_account(
                DEFAULT_USER_ID,
                DEFAULT_PHONE,
                DEFAULT_SESSION_PATH,
            )
            print(
                "Создан аккаунт:\n"
                f"  - ID: {account['id']}, Phone: {account['phone']}, User: {account['user_id']}"
            )
            all_accounts = [account]

        # Используем первый найденный аккаунт
        test_account_id = all_accounts[0]["id"]
        test_phone = all_accounts[0]["phone"]

        # Тестовые данные
        test_settings = {
            "chance": 45,
            "system_prompt": f"Тестовый промпт для {test_phone}",
            "sleep_min": 8,
            "sleep_max": 18,
        }

        print(
            f"\nТестируем сохранение настроек для аккаунта ID {test_account_id} "
            f"(Phone: {test_phone})"
        )
        print(f"Настройки: {test_settings}")

        # Сохраняем настройки
        print("\nСохраняем настройки...")
        await update_account_settings(
            account_id=test_account_id,
            chance=test_settings["chance"],
            system_prompt=test_settings["system_prompt"],
            sleep_min=test_settings["sleep_min"],
            sleep_max=test_settings["sleep_max"],
        )
        print("Настройки успешно сохранены")

        # Проверяем, что настройки сохранились
        print("\nПроверяем сохраненные настройки...")
        account = await get_account_by_id(test_account_id)
        if account:
            print("Полученные данные из БД:")
            print(f"  - chance: {account.get('chance')}")
            print(f"  - system_prompt: {account.get('system_prompt')}")
            print(f"  - sleep_min: {account.get('sleep_min')}")
            print(f"  - sleep_max: {account.get('sleep_max')}")

            # Проверяем соответствие
            success = True
            for key, expected_value in test_settings.items():
                actual_value = account.get(key)
                if actual_value != expected_value:
                    print(
                        f"ОШИБКА: {key} = {actual_value}, ожидалось {expected_value}"
                    )
                    success = False
                else:
                    print(f"OK: {key} = {actual_value}")

            if success:
                print("\n🎉 Все настройки сохранены корректно!")
                result = True
            else:
                print("\n💥 Обнаружены ошибки в сохранении настроек")
        else:
            print("❌ Аккаунт не найден в базе данных")

    except Exception as e:
        print(f"💥 Ошибка при тестировании: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await close_db()

    return result

if __name__ == "__main__":
    result = asyncio.run(test_settings_save())
    if result:
        print("\n✅ Тест прошел успешно!")
    else:
        print("\n❌ Тест не прошел!")
