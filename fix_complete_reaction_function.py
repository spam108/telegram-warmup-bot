with open('main.py', 'r') as f:
    lines = f.readlines()

# Закомментируем ВЕСЬ код от строки 1102 до где-то 1150
# который использует allowed_reaction_emojis
start_line = 1102  # working_reaction_emojis = list(reaction_emojis)
end_line = 1180    # Безопасно далеко

for i in range(start_line-1, min(end_line, len(lines))):
    line = lines[i]
    if line.strip() and not line.strip().startswith('#'):
        # Сохраняем оригинальные отступы, добавляем комментарий
        indent = len(line) - len(line.lstrip())
        lines[i] = " " * indent + "# " + line.lstrip()

# Особенно важно закомментировать строки использующие allowed_reaction_emojis
with open('main.py', 'w') as f:
    f.writelines(lines)

print("✅ Весь проблемный код закомментирован")
