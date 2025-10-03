#!/bin/bash
# Автоматическое исправление отступов

# Создаем backup
cp main.py main.py.backup2

# Используем sed для точной замены
sed -i '/async for dialog in app.get_dialogs():/{
    n
    n
    n
    n
    n
    i\                try:
    a\                except Exception:
    a\                    # Пропускаем каналы с ошибками
    a\                    continue
}' main.py

echo "✅ Патч применен"
