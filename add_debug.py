import re

# Читаем db.py
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Добавляем отладку после объявления параметров функции
debug_code = '''
    # DEBUG: Check parameter types
    if next_join is not None:
        print(f"DEBUG db_update_warmup_schedule: next_join type: {type(next_join)}, value: {next_join}")
    if last_join is not None:
        print(f"DEBUG db_update_warmup_schedule: last_join type: {type(last_join)}, value: {last_join}")
'''

# Вставляем отладку после объявления params
pattern = r'(params: List\[Any\] = \[\])'
replacement = r'\\1' + debug_code

fixed_content = re.sub(pattern, replacement, content)

# Сохраняем
with open('db.py', 'w', encoding='utf-8') as f:
    f.write(fixed_content)

print("✅ Добавлена отладочная информация в db_update_warmup_schedule")

# Проверим синтаксис
import py_compile
try:
    py_compile.compile('db.py', doraise=True)
    print("✅ Синтаксис db.py корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса в db.py: {e}")
