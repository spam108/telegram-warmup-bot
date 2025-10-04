#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки логики сохранения настроек без БД
"""
import sys
import os

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_sleep_parsing():
    """Тестирует парсинг строки sleep"""
    print("Тестируем парсинг строки sleep...")
    
    # Тестовые случаи
    test_cases = [
        ("10-20", (10, 20)),
        ("5-30", (5, 30)),
        ("1-1", (1, 1)),
        ("invalid", None),
        ("10", None),
        ("10-", None),
        ("-20", None),
        ("", None),
    ]
    
    for input_str, expected in test_cases:
        result = None
        if '-' in input_str:
            try:
                sleeps = input_str.split('-')
                if len(sleeps) == 2 and all(sleep.isdigit() for sleep in sleeps):
                    sleep_min = int(sleeps[0])
                    sleep_max = int(sleeps[1])
                    result = (sleep_min, sleep_max)
            except Exception:
                pass
        
        if result == expected:
            print(f"OK: '{input_str}' -> {result}")
        else:
            print(f"ОШИБКА: '{input_str}' -> {result}, ожидалось {expected}")

def test_state_data_processing():
    """Тестирует обработку данных состояния"""
    print("\nТестируем обработку данных состояния...")
    
    # Симулируем данные состояния
    state_data = {
        "sleeps": "15-25",
        "systempromt": "Тестовый промпт",
        "chance": 30
    }
    
    # Извлекаем данные как в main.py
    sleeps = state_data.get("sleeps")
    system_promt = state_data.get("systempromt")
    chance = state_data.get("chance")
    
    print(f"Исходные данные: {state_data}")
    print(f"Извлеченные данные: sleeps={sleeps}, system_promt={system_promt}, chance={chance}")
    
    # Парсим sleeps
    sleep_min, sleep_max = None, None
    if sleeps and '-' in sleeps:
        try:
            sleep_parts = sleeps.split('-')
            if len(sleep_parts) == 2:
                sleep_min = int(sleep_parts[0])
                sleep_max = int(sleep_parts[1])
        except ValueError:
            pass
    
    print(f"Результат парсинга: sleep_min={sleep_min}, sleep_max={sleep_max}")
    
    # Проверяем результат
    expected_sleep_min, expected_sleep_max = 15, 25
    if sleep_min == expected_sleep_min and sleep_max == expected_sleep_max:
        print("OK: Парсинг sleep прошел успешно")
    else:
        print(f"ОШИБКА: Ожидалось sleep_min={expected_sleep_min}, sleep_max={expected_sleep_max}")

def test_sql_generation():
    """Тестирует генерацию SQL запроса"""
    print("\nТестируем генерацию SQL запроса...")
    
    # Симулируем параметры как в update_account_settings
    account_id = 1
    chance = 30
    system_prompt = "Тестовый промпт"
    sleep_min = 15
    sleep_max = 25
    channels = ["@test1", "@test2"]
    reaction_chance = 40
    reaction_sleep_min = 5
    reaction_sleep_max = 12
    reaction_emojis = ["🔥", "👍"]

    # Генерируем SQL как в функции
    updates = []
    values = []

    if chance is not None:
        updates.append("chance = $%d" % (len(values) + 1))
        values.append(chance)
    if system_prompt is not None:
        updates.append("system_prompt = $%d" % (len(values) + 1))
        values.append(system_prompt)
    if sleep_min is not None:
        updates.append("sleep_min = $%d" % (len(values) + 1))
        values.append(sleep_min)
    if sleep_max is not None:
        updates.append("sleep_max = $%d" % (len(values) + 1))
        values.append(sleep_max)
    if reaction_chance is not None:
        updates.append("reaction_chance = $%d" % (len(values) + 1))
        values.append(reaction_chance)
    if reaction_sleep_min is not None:
        updates.append("reaction_sleep_min = $%d" % (len(values) + 1))
        values.append(reaction_sleep_min)
    if reaction_sleep_max is not None:
        updates.append("reaction_sleep_max = $%d" % (len(values) + 1))
        values.append(reaction_sleep_max)
    if reaction_emojis is not None:
        updates.append("reaction_emojis = $%d" % (len(values) + 1))
        values.append(reaction_emojis)
    if channels is not None:
        updates.append("channels = $%d" % (len(values) + 1))
        values.append(channels)

    values.append(account_id)
    assignments = ", ".join(updates)
    
    query = f"""
        UPDATE accounts
        SET {assignments}, updated_at = NOW()
        WHERE id = ${len(values)}
    """
    
    print(f"SQL запрос: {query}")
    print(f"Параметры: {values}")

    # Проверяем, что все поля включены
    expected_fields = [
        "chance",
        "system_prompt",
        "sleep_min",
        "sleep_max",
        "reaction_chance",
        "reaction_sleep_min",
        "reaction_sleep_max",
        "reaction_emojis",
        "channels",
    ]
    for field in expected_fields:
        if field in query:
            print(f"OK: Поле {field} найдено в запросе")
        else:
            print(f"ОШИБКА: Поле {field} не найдено в запросе")

if __name__ == "__main__":
    test_sleep_parsing()
    test_state_data_processing()
    test_sql_generation()
    print("\nТестирование завершено!")
