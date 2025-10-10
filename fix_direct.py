def fix_with_direct_sql():
    with open('/app/db.py', 'r') as f:
        content = f.read()
    
    # Заменяем на прямое использование asyncpg для этой функции
    new_function = '''async def set_user_authenticated(user_id: int, value: bool) -> None:
    if _is_postgres():
        pool = _require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE users SET is_authenticated = $1, updated_at = CURRENT_TIMESTAMP WHERE user_id = $2",
                value, user_id
            )
    else:
        await _execute(
            "UPDATE users SET is_authenticated = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (1 if value else 0, user_id),
        )
    await _commit()\n'''
    
    # Заменяем функцию
    import re
    content = re.sub(
        r'async def set_user_authenticated\(user_id: int, value: bool\) -> None:.*?await _commit\(\)',
        new_function,
        content,
        flags=re.DOTALL
    )
    
    with open('/app/db.py', 'w') as f:
        f.write(content)
    
    print("✅ set_user_authenticated fixed with direct SQL")

fix_with_direct_sql()
