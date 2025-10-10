with open('main.py', 'r') as f:
    lines = f.readlines()

# Полностью перезаписываем ВЕСЬ проблемный блок
new_lines = []
in_block = False
block_start = 1102  # working_reaction_emojis = list(reaction_emojis)
block_end = 1120    # Примерный конец блока

for i, line in enumerate(lines, 1):
    if i == block_start:
        new_lines.append("    working_reaction_emojis = list(reaction_emojis)\n")
        new_lines.append("    # REMOVED: Pyrogram 2.0.106 broke reaction filtering\n")
        in_block = True
    elif i > block_start and i <= block_end:
        if line.strip() and not line.strip().startswith('#'):
            new_lines.append("    # " + line.lstrip())
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)

with open('main.py', 'w') as f:
    f.writelines(new_lines)

print("✅ Весь проблемный блок исправлен")
