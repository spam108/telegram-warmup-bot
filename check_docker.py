#!/usr/bin/env python3
"""Проверка Docker конфигурации перед развертыванием"""

import os
import sys
from pathlib import Path

def check_docker_files():
    """Проверка наличия необходимых Docker файлов"""
    print("🔍 Проверяем Docker файлы...")

    required_files = [
        'Dockerfile',
        'docker-compose.yml',
        '.dockerignore'
    ]

    missing_files = []
    for file in required_files:
        if os.path.exists(file):
            print(f"✅ {file}")
        else:
            print(f"❌ {file} отсутствует")
            missing_files.append(file)

    if missing_files:
        print(f"\n❌ Отсутствуют файлы: {', '.join(missing_files)}")
        return False

    return True

def check_dockerfile():
    """Проверка Dockerfile"""
    print("\n🔍 Проверяем Dockerfile...")

    with open('Dockerfile', 'r') as f:
        content = f.read()

    checks = [
        ('FROM python:3.12-slim', 'Используется Python 3.12'),
        ('libpq-dev', 'Установлены зависимости PostgreSQL'),
        ('USER botuser', 'Создан непривилегированный пользователь'),
        ('CMD ["python", "main.py"]', 'Правильная команда запуска')
    ]

    for check, description in checks:
        if check in content:
            print(f"✅ {description}")
        else:
            print(f"⚠️ {description} - не найден паттерн '{check}'")

    return True

def check_docker_compose():
    """Проверка docker-compose.yml"""
    print("\n🔍 Проверяем docker-compose.yml...")

    with open('docker-compose.yml', 'r') as f:
        content = f.read()

    checks = [
        ('depends_on:', 'Настроены зависимости между сервисами'),
        ('postgres:15', 'Используется PostgreSQL 15'),
        ('healthcheck:', 'Настроены healthcheck\'и'),
        ('volumes:', 'Настроены volumes'),
        ('restart: unless-stopped', 'Настроен автозапуск')
    ]

    for check, description in checks:
        if check in content:
            print(f"✅ {description}")
        else:
            print(f"⚠️ {description} - не найден паттерн '{check}'")

    return True

def check_env_template():
    """Проверка наличия шаблона .env"""
    print("\n🔍 Проверяем переменные окружения...")

    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            content = f.read()

        required_vars = [
            'BOT_TOKEN=',
            'API_ID=',
            'API_HASH=',
            'OPENAI_API_KEY=',
            'DATABASE_URL=',
            'PASSWORD='
        ]

        found_vars = 0
        for var in required_vars:
            if var in content:
                found_vars += 1

        print(f"✅ Найдено {found_vars} из {len(required_vars)} необходимых переменных")

        if found_vars < len(required_vars):
            print("⚠️ Некоторые переменные могут отсутствовать")
        else:
            print("✅ Все необходимые переменные присутствуют")
    else:
        print("❌ .env файл не найден")
        return False

    return True

def main():
    """Основная функция проверки"""
    print("🚀 Проверка Docker конфигурации Telegram Comment Bot\n")

    tests = [
        ("Docker файлы", check_docker_files),
        ("Dockerfile", check_dockerfile),
        ("Docker Compose", check_docker_compose),
        ("Переменные окружения", check_env_template),
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
    print("📊 РЕЗУЛЬТАТЫ ПРОВЕРКИ")
    print('='*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{test_name"30"} {status}")

    print(f"\nОбщий результат: {passed}/{total} тестов пройдено")

    if passed == total:
        print("\n🎉 Docker конфигурация готова к развертыванию!")
        print("💡 Следующие шаги:")
        print("   1. Настроить реальные значения в .env")
        print("   2. Выполнить: docker-compose up -d --build")
        print("   3. Проверить логи: docker-compose logs -f")
        return True
    else:
        print(f"\n⚠️ Найдены проблемы в конфигурации ({total-passed} из {total}).")
        print("🔧 Необходимо исправить ошибки перед развертыванием.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
