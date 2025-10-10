import re

# Читаем db.py
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Ищем функцию db_update_warmup_schedule и исправляем ВСЕ использования isoformat()
old_function = """
    if next_join is not None:
        updates.append("warmup_next_join_at = ?")
        params.append(next_join)

    if last_join is not None:
        iso = last_join.isoformat()
        updates.append("warmup_last_join_at = ?")
        params.append(iso)
        updates.append("warmup_last_join = ?")
        params.append(last_join.date().isoformat())
"""

new_function = """
    if next_join is not None:
        updates.append("warmup_next_join_at = ?")
        params.append(next_join)

    if last_join is not None:
        updates.append("warmup_last_join_at = ?")
        params.append(last_join)
        updates.append("warmup_last_join = ?")
        params.append(last_join.date())
"""

# Заменяем старую функцию на новую
fixed_content = content.replace(old_function, new_function)

# Сохраняем исправления
if fixed_content != content:
    with open('db.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print("✅ Полностью исправлена функция db_update_warmup_schedule в db.py")
    
    # Покажем исправленную функцию
    print("Исправленная функция:")
    lines = fixed_content.split('\\n')
    for i, line in enumerate(lines):
        if 'async def db_update_warmup_schedule' in line:
            for j in range(i, min(i+35, len(lines))):
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
