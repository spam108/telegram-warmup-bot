import re

# Читаем db.py
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Ищем функцию db_update_warmup_schedule и исправляем использование isoformat()
pattern = r'(params\.append\()next_join\.isoformat\(\)'

def replacement(match):
    return match.group(1) + 'next_join'

fixed_content = re.sub(pattern, replacement, content)

# Сохраняем исправления
if fixed_content != content:
    with open('db.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print("✅ Исправлена функция db_update_warmup_schedule в db.py")
    
    # Покажем исправленную функцию
    print("Исправленная функция:")
    lines = fixed_content.split('\n')
    for i, line in enumerate(lines):
        if 'async def db_update_warmup_schedule' in line:
            for j in range(i, min(i+30, len(lines))):
                print(f"{j+1}: {lines[j]}")
            break
else:
    print("❌ Не удалось исправить функцию db_update_warmup_schedule")

# Проверим синтаксис
import py_compile
try:
    py_compile.compile('db.py', doraise=True)
    print("✅ Синтаксис db.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в db.py: {e}")
