import os

with open('main.py', 'r') as f:
    content = f.read()

# Добавим обработчик для /Start после существующего CommandStart
if '@dp.message(CommandStart())' in content and '@dp.message(Command("Start"))' not in content:
    
    # Найдем где заканчивается функция start
    start_index = content.find('@dp.message(CommandStart())')
    if start_index != -1:
        # Найдем конец функции start
        func_end = content.find('async def start', start_index)
        if func_end != -1:
            # Найдем следующую функцию после start
            next_func = content.find('@dp.message', func_end)
            if next_func != -1:
                # Вставим новый обработчик перед следующей функцией
                new_handler = '''@dp.message(Command("Start"))
async def start_uppercase(message: types.Message, state: FSMContext):
    """Обработчик для /Start с заглавной буквы"""
    await ensure_user(message.from_user.id)
    if not await is_user_authenticated(message.from_user.id):
        await message.answer("Введите пароль для доступа:")
        await state.set_state(AuthState.waiting_for_password)
    else:
        await main_message(message)

'''
                content = content[:next_func] + new_handler + content[next_func:]

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Добавили обработчик для /Start с заглавной буквы")
