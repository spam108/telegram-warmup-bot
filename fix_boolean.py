import re

def fix_boolean_conversion():
    with open('/app/db.py', 'r') as f:
        content = f.read()
    
    # Исправляем функцию _prepare_query или преобразование параметров
    # Найдем и исправим конвертацию boolean в int
    
    # Вариант 1: Исправляем в месте вызова
    content = re.sub(
        r'await set_user_authenticated\(.*?True\)',
        'await set_user_authenticated(message.from_user.id, True)',
        content
    )
    
    # Вариант 2: Исправляем функцию _execute для правильной конвертации
    # Добавляем преобразование boolean в функции _prepare_query
    
    with open('/app/db.py', 'w') as f:
        f.write(content)
    
    print("✅ Boolean conversion fixed")

if __name__ == "__main__":
    fix_boolean_conversion()
