# Полностью очищаем проблемный блок

with open('main.py', 'r') as f:
    lines = f.readlines()

# Находим блок который нужно очистить
start_index = -1
end_index = -1

for i in range(len(lines)):
    if 'async with app:' in lines[i] and i > 690:
        start_index = i
        # Ищем конец блока (пустую строку или следующую функцию)
        for j in range(i + 1, min(i + 20, len(lines))):
            if lines[j].strip() == '' or 'async def' in lines[j]:
                end_index = j
                break
        break

if start_index != -1 and end_index != -1:
    print(f"Найден блок с {start_index+1} по {end_index+1}")
    
    # Показываем что будем удалять
    print("Удаляемые строки:")
    for j in range(start_index, end_index):
        print(f"{j+1}: {lines[j].rstrip()}")
    
    # Заменяем блок на чистую версию
    new_block = [
        '        async with app:\n',
        '            # Получение диалогов отключено из-за ChannelPrivate ошибок\n',
        '            pass\n'
    ]
    
    # Удаляем старые строки и вставляем новые
    lines[start_index:end_index] = new_block
    
    with open('main.py', 'w') as f:
        f.writelines(lines)
    
    print("✅ Блок очищен")
else:
    print("❌ Блок не найден")
