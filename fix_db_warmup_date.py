import re

# Читаем db.py
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Ищем использование isoformat() в контексте created_at
pattern = r'(\bcreated_at\s*=\s*)([^,)]+\.isoformat\(\))'

def replacement(match):
    return match.group(1) + "datetime.now()"

fixed_content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

# Сохраняем исправления
if fixed_content != content:
    with open('db.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print("✅ Исправлен формат даты в db.py")
    
    # Покажем что исправили
    print("Исправленные места:")
    for line in fixed_content.split('\n'):
        if 'created_at' in line and 'datetime.now()' in line:
            print(f"  {line.strip()}")
else:
    print("❌ Не найдены проблемы с датами в db.py")

# Проверим синтаксис
import py_compile
try:
    py_compile.compile('db.py', doraise=True)
    print("✅ Синтаксис db.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в db.py: {e}")

try:
    py_compile.compile('main.py', doraise=True)
    print("✅ Синтаксис main.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в main.py: {e}")
