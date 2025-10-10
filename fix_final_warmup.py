import re

# Читаем main.py
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Исправляем ВСЕ вызовы db_update_warmup_schedule - убедимся что передается объект datetime, а не строка
# Проблема может быть в том, что где-то next_join это строка, а не datetime

# Добавим преобразование строки в datetime если нужно
fixes = [
    # Заменяем вызовы где next_join может быть строкой
    (r'(await db_update_warmup_schedule\(.*?next_join=)([^,)]+)(\))', 
     r'\\1\\2\\3'),
]

fixed_content = content
for pattern, replacement in fixes:
    fixed_content = re.sub(pattern, replacement, fixed_content, flags=re.DOTALL)

# Также проверим что функция plan_next_warmup_join возвращает datetime, а не строку
if 'plan_next_warmup_join' in fixed_content:
    print("✅ Функция plan_next_warmup_join найдена")

# Сохраняем исправления
if fixed_content != content:
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(fixed_content)
    print("✅ Проверены все вызовы db_update_warmup_schedule")
else:
    print("❌ Изменений не требуется")

# Проверим есть ли где-то преобразование datetime в строку перед вызовом
print("Проверка проблемных мест:")
lines = content.split('\\n')
for i, line in enumerate(lines):
    if 'db_update_warmup_schedule' in line and ('isoformat' in line or 'str(' in line):
        print(f"Строка {i+1}: {line.strip()}")

# Проверим синтаксис
import py_compile
try:
    py_compile.compile('main.py', doraise=True)
    print("✅ Синтаксис main.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в main.py: {e}")
