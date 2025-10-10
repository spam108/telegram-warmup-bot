def fix_mark_account_running():
    with open('/app/db.py', 'r') as f:
        content = f.read()
    
    # Находим и заменяем неполную функцию
    old_function = '''async def mark_account_running(account_id: int) -> None:
    async def _execute_update() -> None:
        await _execute(
            """
            UPDATE accounts
            SET status = 'running',
                last_started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_execute_update, max_retries=5)'''

    new_function = '''async def mark_account_running(account_id: int) -> None:
    async def _execute_update() -> None:
        await _execute(
            """
            UPDATE accounts
            SET status = 'running',
                last_started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        await _commit()

    if _is_sqlite():
        await _retry_db_operation(_execute_update, max_retries=5)
    else:
        await _execute_update()'''

    content = content.replace(old_function, new_function)
    
    with open('/app/db.py', 'w') as f:
        f.write(content)
    
    print("✅ mark_account_running fixed for PostgreSQL")

fix_mark_account_running()
