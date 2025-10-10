import re

# Читаем db.py
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Ищем функцию db_update_warmup_schedule и исправляем формат даты
# Проблема в том, что функция ожидает datetime объект, а получает строку из isoformat()

# Паттерн для поиска и замены использования isoformat в вызовах
patterns = [
    # Исправляем вызовы в main.py - заменяем next_join.isoformat() на next_join
    (r'(db_update_warmup_schedule\(.*?next_join=)([^)]+\.isoformat\(\))', r'\\1\\2'.replace('\\\\', '\\')),
]

fixed_content = content
for pattern, replacement in patterns:
    fixed_content = re.sub(pattern, replacement, fixed_content)

# Сохраняем исправления в main.py
if fixed_content != content:
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print("✅ Исправлены вызовы db_update_warmup_schedule в main.py")
else:
    print("❌ Не найдены проблемы в main.py")

# Теперь проверим саму функцию db_update_warmup_schedule в db.py
with open('db.py', 'r', encoding='utf-8') as f:
    db_content = f.read()

# Проверим что функция правильно обрабатывает datetime
if 'async def db_update_warmup_schedule' in db_content:
    print("✅ Функция db_update_warmup_schedule найдена в db.py")
    
# Проверим синтаксис
import py_compile
try:
    py_compile.compile('main.py', doraise=True)
    print("✅ Синтаксис main.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в main.py: {e}")

try:
    py_compile.compile('db.py', doraise=True)
    print("✅ Синтаксис db.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в db.py: {e}")
