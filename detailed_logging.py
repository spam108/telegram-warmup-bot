# Добавляем детальное логирование для отладки

with open('main.py', 'r') as f:
    content = f.read()

# Добавляем логирование в функцию add_sleeps
content = content.replace('''    session = (await state.get_data()).get("account")\n    channels = []''', '''    session = (await state.get_data()).get("account")\n    channels = []\n    \n    print(f"DEBUG: Получаем каналы для аккаунта {session}")''')

# Добавляем логирование количества найденных каналов
content = content.replace('''        # Используем унифицированное отображение каналов\n        channels_display = await format_channels_display(channels, "Аккаунт подписан на каналы", 10)''', '''        # Используем унифицированное отображение каналов\n        print(f"DEBUG: Найдено {len(channels)} каналов для аккаунта {session}")\n        channels_display = await format_channels_display(channels, "Аккаунт подписан на каналы", 10)''')

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Детальное логирование добавлено")
