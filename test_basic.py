#!/usr/bin/env python3
"""Базовый тест работоспособности кода без запуска сервера"""

import sys
import os

def test_imports():
    """Тест импортов"""
    print("🔍 Тестируем импорты...")

    try:
        import main
        print("✅ main.py импортируется без ошибок")
    except ImportError as e:
        print(f"❌ Ошибка импорта main.py: {e}")
        return False
    except Exception as e:
        print(f"❌ Другая ошибка в main.py: {e}")
        return False

    # Критические импорты
    critical_imports = [
        'aiogram', 'pyrogram', 'asyncpg', 'openai', 'dotenv'
    ]

    for module in critical_imports:
        try:
            __import__(module)
            print(f"✅ {module} доступен")
        except ImportError:
            print(f"❌ {module} не установлен")
            return False

    return True

def test_syntax():
    """Тест синтаксиса основных файлов"""
    print("\n🔍 Тестируем синтаксис...")

    files_to_check = ['main.py', 'db.py', 'comment_engine.py']

    for file in files_to_check:
        if os.path.exists(file):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    content = f.read()
                compile(content, file, 'exec')
                print(f"✅ {file} - синтаксис корректен")
            except SyntaxError as e:
                print(f"❌ {file} - синтаксическая ошибка: {e}")
                return False
            except Exception as e:
                print(f"❌ {file} - ошибка чтения: {e}")
                return False
        else:
            print(f"⚠️ {file} не найден")

    return True

def test_env_file():
    """Тест .env файла"""
    print("\n🔍 Тестируем .env файл...")

    if os.path.exists('.env'):
        with open('.env', 'r', encoding='utf-8') as f:
            content = f.read()
            lines = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]

        required_vars = ['BOT_TOKEN', 'API_ID', 'API_HASH', 'OPENAI_API_KEY', 'DATABASE_URL']
        found_vars = []

        for line in lines:
            if '=' in line:
                var_name = line.split('=')[0].strip()
                if var_name in required_vars:
                    found_vars.append(var_name)

        missing_vars = [var for var in required_vars if var not in found_vars]

        if missing_vars:
            print(f"⚠️ Отсутствуют переменные в .env: {', '.join(missing_vars)}")
        else:
            print("✅ Все необходимые переменные найдены в .env")

        return len(missing_vars) == 0
    else:
        print("❌ .env файл не найден")
        return False

def main():
    """Основная функция тестирования"""
    print("🚀 Начинаем базовое тестирование Telegram Comment Bot\n")

    tests = [
        ("Импорты модулей", test_imports),
        ("Синтаксис файлов", test_syntax),
        ("Конфигурация .env", test_env_file),
    ]

    results = []

    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Тест: {test_name}")
        print('='*50)

        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Критическая ошибка в тесте '{test_name}': {e}")
            results.append((test_name, False))

    print(f"\n{'='*60}")
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print('='*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{test_name"30"} {status}")

    print(f"\nОбщий результат: {passed}/{total} тестов пройдено")

    if passed == total:
        print("\n🎉 Базовое тестирование успешно! Код готов к запуску.")
        print("💡 Следующие шаги:")
        print("   1. Настроить реальные значения в .env")
        print("   2. Создать базу данных PostgreSQL")
        print("   3. Добавить сессии Telegram аккаунтов")
        print("   4. Запустить бота командой: python main.py")
        return True
    else:
        print(f"\n⚠️ Найдены проблемы ({total-passed} из {total}).")
        print("🔧 Необходимо исправить ошибки перед запуском.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
