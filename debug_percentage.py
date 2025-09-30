#!/usr/bin/env python3
"""
Отладочный тест для проверки логики процента комментирования
"""
import random

def test_specific_case():
    """Тестирует конкретный случай: 30% комментирования"""
    chance = 30
    num_tests = 17  # Как в реальном случае
    
    print(f"Тестируем конкретный случай: {chance}% комментирования, {num_tests} сообщений")
    print("=" * 60)
    
    comment_count = 0
    skip_count = 0
    
    for i in range(num_tests):
        random_value = random.randint(1, 100)
        will_comment = random_value <= chance
        
        if will_comment:
            comment_count += 1
            status = "КОММЕНТИРУЕТ"
        else:
            skip_count += 1
            status = "ПРОПУСКАЕТ"
        
        print(f"Сообщение {i+1:2d}: выпало={random_value:2d}, {status}")
    
    actual_percentage = (comment_count / num_tests) * 100
    expected_percentage = chance
    
    print("=" * 60)
    print(f"Ожидаемый процент: {expected_percentage}%")
    print(f"Фактический процент: {actual_percentage:.1f}%")
    print(f"Комментариев: {comment_count}")
    print(f"Пропущено: {skip_count}")
    print(f"Отклонение: {abs(actual_percentage - expected_percentage):.1f}%")
    
    if abs(actual_percentage - expected_percentage) <= 10:  # Допускаем ±10% для малых выборок
        print("✅ РЕЗУЛЬТАТ: Логика работает корректно")
    else:
        print("❌ РЕЗУЛЬТАТ: Логика работает некорректно")
    
    return comment_count, skip_count

if __name__ == "__main__":
    # Запускаем тест несколько раз
    for test_num in range(5):
        print(f"\nТЕСТ #{test_num + 1}")
        comment_count, skip_count = test_specific_case()
        print()
