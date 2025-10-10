# Читаем db.py
with open('db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Находим функцию db_update_warmup_schedule и исправляем ее
old_function = '''async def db_update_warmup_schedule(
    account_id: int,
    *,
    next_join: Optional[datetime] = None,
    last_join: Optional[datetime] = None,
) -> None:
    updates: List[str] = []
    params: List[Any] = []

    if next_join is not None:
        updates.append("warmup_next_join_at = ?")
        params.append(next_join.isoformat())

    if last_join is not None:
        iso = last_join.isoformat()
        updates.append("warmup_last_join_at = ?")
        params.append(iso)
        updates.append("warmup_last_join = ?")
        params.append(last_join.date().isoformat())'''

new_function = '''async def db_update_warmup_schedule(
    account_id: int,
    *,
    next_join: Optional[datetime] = None,
    last_join: Optional[datetime] = None,
) -> None:
    updates: List[str] = []
    params: List[Any] = []

    if next_join is not None:
        updates.append("warmup_next_join_at = ?")
        params.append(next_join)

    if last_join is not None:
        updates.append("warmup_last_join_at = ?")
        params.append(last_join)
        updates.append("warmup_last_join = ?")
        params.append(last_join.date())'''

# Заменяем
if old_function in content:
    content = content.replace(old_function, new_function)
    with open('db.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ Функция db_update_warmup_schedule исправлена")
else:
    print("❌ Не удалось найти функцию для исправления")

# Проверим синтаксис
import py_compile
try:
    py_compile.compile('db.py', doraise=True)
    print("✅ Синтаксис корректен")
except Exception as e:
    print(f"❌ Ошибка синтаксиса: {e}")
