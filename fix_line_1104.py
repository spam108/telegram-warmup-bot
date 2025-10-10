with open('main.py', 'r') as f:
    lines = f.readlines()

# Исправляем конкретно строки 1102-1107
# БЫЛО:
# 1102:    working_reaction_emojis = list(reaction_emojis)
# 1103:    allowed_reaction_emojis = await _get_chat_available_quick_reactions(
# 1104:        client,
# 1105:        chat_id_for_reactions,
# 1106:    )
# 1107:    if allowed_reaction_emojis is not None:

# СТАНОВИТСЯ:
lines[1102] = "    working_reaction_emojis = list(reaction_emojis)\n"
lines[1103] = "    # REMOVED: _get_chat_available_quick_reactions - Pyrogram 2.0.106\n"
lines[1104] = "    # REMOVED: client,\n" 
lines[1105] = "    # REMOVED: chat_id_for_reactions,\n"
lines[1106] = "    # REMOVED: )\n"
lines[1107] = "    # REMOVED: if allowed_reaction_emojis is not None:\n"

with open('main.py', 'w') as f:
    f.writelines(lines)

print("✅ Строки 1103-1107 закомментированы")
