# Исправляем функцию set_user_authenticated в db.py
import re

# Читаем файл
with open('/app/db.py', 'r') as f:
    content = f.read()

# Ищем и исправляем функцию set_user_authenticated
old_code = '''async def set_user_authenticated(user_id: int, value: bool) -> None:
    await _execute(
        "UPDATE users SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (1 if value else 0, user_id),
    )
    await _commit()'''

new_code = '''async def set_user_authenticated(user_id: int, value: bool) -> None:
    await _execute(
        "UPDATE users SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (value, user_id),
    )
    await _commit()'''

# Заменяем код
content = content.replace(old_code, new_code)

# Записываем обратно
with open('/app/db.py', 'w') as f:
    f.write(content)

print("✅ Функция set_user_authenticated исправлена")
