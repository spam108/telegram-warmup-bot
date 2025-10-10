# Читаем main.py
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Находим место перед вызовом db_update_warmup_schedule и добавляем отладку
import re
pattern = r'(next_join = plan_next_warmup_join\(datetime\.now\(timezone\.utc\), warmup_settings\))'
replacement = r'\\1\\n    print(f"DEBUG: Before db_update_warmup_schedule - next_join type: {{type(next_join)}}")\\n    print(f"DEBUG: Before db_update_warmup_schedule - next_join value: {{next_join}}")'

fixed_content = re.sub(pattern, replacement, content)

# Сохраняем
with open('main.py', 'w', encoding='utf-8') as f:
    f.write(fixed_content)

print("✅ Добавлена отладочная информация")

# Проверим синтаксис
import py_compile
try:
    py_compile.compile('main.py', doraise=True)
    print("✅ Синтаксис корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса: {e}")
