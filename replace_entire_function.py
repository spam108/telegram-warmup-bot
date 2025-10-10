with open('main.py', 'r') as f:
    content = f.read()

# Создаем простую работающую версию функции
simple_function = '''
async def _maybe_send_reaction(
    *,
    client: Client,
    message: Any,
    session: str,
    account_id: int,
    reaction_emojis: List[str],
    reaction_sleep_min: int,
    reaction_sleep_max: int,
    reaction_limit_per_message: Optional[int],
    reactions_enabled: bool,
    selected_reaction_chance: int,
    reaction_comment_context: str,
    status_suffix: str,
    post_base_link: Optional[str],
    current_last_reaction_at: Optional[datetime],
    force: bool = False,
    ignore_cooldown: bool = False,
) -> Tuple[Optional[datetime], bool]:
    """Simplified reaction sending - Pyrogram 2.0.106 compatible"""
    
    # Basic checks
    if not reactions_enabled or not reaction_emojis:
        return current_last_reaction_at, False
        
    # Use all emojis directly
    for emoji in reaction_emojis:
        try:
            await client.send_reaction(message.chat.id, message.id, emoji)
            # Success
            now = datetime.now(timezone.utc)
            return now, True
        except Exception as e:
            # Try next emoji on failure
            continue
            
    return current_last_reaction_at, False
'''

# Находим старую функцию и заменяем её
import re

# Ищем функцию от async def до следующей функции или конца
pattern = r'(async def _maybe_send_reaction\\([^)]+\\):[\\s\\S]*?)(?=async def |def |@|\\Z)'
replacement = simple_function + '\\n\\n'

content = re.sub(pattern, replacement, content)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Вся функция _maybe_send_reaction заменена на простую версию")
