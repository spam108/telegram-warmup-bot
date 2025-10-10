with open('main.py', 'r') as f:
    content = f.read()

# 1. Удаляем ПЕРВЫЙ вызов _get_chat_available_quick_reactions и всю логику фильтрации
import re

# Заменяем блок с проверкой доступных реакций на простую логику
content = re.sub(
    r'working_reaction_emojis = list\\(reaction_emojis\\)\\s+allowed_reaction_emojis = await _get_chat_available_quick_reactions\\([^)]+\\)\\s+if allowed_reaction_emojis is not None:[^}]+}',
    '''working_reaction_emojis = list(reaction_emojis)
    # REMOVED: Pyrogram 2.0.106 removed get_available_reactions()''',
    content,
    flags=re.DOTALL
)

# 2. Удаляем ВТОРОЙ вызов _get_chat_available_quick_reactions
content = re.sub(
    r'refreshed_allowed = await _get_chat_available_quick_reactions\\([^)]+\\)',
    '# REMOVED: Pyrogram 2.0.106 removed get_available_reactions()',
    content
)

# 3. Удаляем саму функцию _get_chat_available_quick_reactions (если она есть)
content = re.sub(
    r'async def _get_chat_available_quick_reactions\\([^)]+\\):[^}]+}',
    '# REMOVED: Function using deleted Pyrogram method',
    content,
    flags=re.DOTALL
)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Проблемный код удален")
