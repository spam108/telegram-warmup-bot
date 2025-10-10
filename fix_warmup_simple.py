# Простое исправление - проверим что plan_next_warmup_join возвращает datetime
import re

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Найдем функцию plan_next_warmup_join
if 'def plan_next_warmup_join' in content:
    print("✅ Функция plan_next_warmup_join найдена")
    
    # Проверим что она возвращает datetime
    lines = content.split('\\n')
    for i, line in enumerate(lines):
        if 'def plan_next_warmup_join' in line:
            print(f"Функция начинается на строке {i+1}")
            # Покажем несколько строк после определения
            for j in range(i, min(i+10, len(lines))):
                print(f"{j+1}: {lines[j]}")
            break

print("\\nПроверка завершена. Нужно вручную проверить что plan_next_warmup_join возвращает datetime объект.")
