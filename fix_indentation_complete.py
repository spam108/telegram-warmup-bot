with open('main.py', 'r') as f:
    lines = f.readlines()

# Закомментируем ВЕСЬ проблемный блок (строки 1109-1120)
for i in range(1108, 1121):  # Индексы с 0, поэтому 1108 = строка 1109
    if i < len(lines) and lines[i].strip() and not lines[i].strip().startswith('#'):
        # Добавляем отступ и комментарий
        lines[i] = "    # " + lines[i]

with open('main.py', 'w') as f:
    f.writelines(lines)

print("✅ Весь проблемный блок закомментирован")
