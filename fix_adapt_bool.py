def fix_adapt_bool():
    with open('/app/db.py', 'r') as f:
        content = f.read()
    
    # Добавляем функцию adapt_bool если ее нет
    if "def adapt_bool" not in content:
        # Находим где вставить функцию (после импортов)
        import_section_end = content.find("_UNSET = object()")
        if import_section_end == -1:
            import_section_end = content.find("_SQLITE_LOCK = asyncio.Lock()")
        
        if import_section_end != -1:
            # Вставляем функцию adapt_bool
            insert_point = content.find('\n', import_section_end) + 1
            adapt_bool_func = '''
def adapt_bool(value: bool) -> Any:
    """Адаптирует boolean значение для текущей СУБД"""
    if _is_postgres():
        return value  # True/False для PostgreSQL
    else:
        return 1 if value else 0  # 1/0 для SQLite
'''
            content = content[:insert_point] + adapt_bool_func + content[insert_point:]
            
            with open('/app/db.py', 'w') as f:
                f.write(content)
            print("✅ Функция adapt_bool добавлена")
        else:
            print("❌ Не найден место для вставки adapt_bool")
    else:
        print("✅ Функция adapt_bool уже существует")

fix_adapt_bool()
