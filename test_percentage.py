#!/usr/bin/env python3
"""
Тест логики процента комментирования
"""
import random

def test_percentage_logic(chance_percent, num_tests=1000):
    """
    Тестирует логику процента комментирования
    
    Args:
        chance_percent: Процент комментирования (например, 30 для 30%)
        num_tests: Количество тестов
    """
    print(f"Тестируем логику для {chance_percent}% комментирования")
    print(f"Количество тестов: {num_tests}")
    print("-" * 50)
    
    comment_count = 0
    skip_count = 0
    
    for i in range(num_tests):
        random_value = random.randint(1, 100)
        if random_value > chance_percent:
            skip_count += 1
        else:
            comment_count += 1
    
    actual_percentage = (comment_count / num_tests) * 100
    expected_percentage = chance_percent
    
    print(f"Ожидаемый процент: {expected_percentage}%")
    print(f"Фактический процент: {actual_percentage:.2f}%")
    print(f"Комментариев: {comment_count}")
    print(f"Пропущено: {skip_count}")
    print(f"Отклонение: {abs(actual_percentage - expected_percentage):.2f}%")
    
    # Проверяем, что отклонение не слишком большое (допустимо ±5%)
    if abs(actual_percentage - expected_percentage) <= 5:
        print("OK: ТЕСТ ПРОЙДЕН: Логика работает корректно")
    else:
        print("ERROR: ТЕСТ НЕ ПРОЙДЕН: Логика работает некорректно")
    
    print("=" * 50)
    return actual_percentage

if __name__ == "__main__":
    # Тестируем разные проценты
    test_cases = [10, 30, 50, 70, 90]
    
    for chance in test_cases:
        test_percentage_logic(chance, 1000)
        print()
