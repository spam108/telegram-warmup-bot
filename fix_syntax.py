with open('main.py', 'r') as f:
    content = f.read()

# Исправляем отсутствующую скобку
content = content.replace(
    '''allowed_reaction_emojis = await _get_chat_available_quick_reactions(
        client,
        chat_id_for_reactions,
    ''',
    '''allowed_reaction_emojis = await _get_chat_available_quick_reactions(
        client,
        chat_id_for_reactions,
    )'''
)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Синтаксис исправлен")
