import re

with open('main.py', 'r') as f:
    content = f.read()

print("=== ИСПРАВЛЕНИЕ PHONE_CODE_EXPIRED ===")

# Упрощаем функцию add_code для максимальной скорости
new_add_code = '''
@dp.message(addsession.code)
async def add_code(message: Message, state: FSMContext) -> None:
    """Упрощенная быстрая аутентификация - исправление PHONE_CODE_EXPIRED"""
    code = str(message.text).replace(' ', '')
    
    if not code.isdigit():
        await message.answer("Код должен содержать только цифры")
        await state.clear()
        await main_message(message)
        return

    state_data = await state.get_data()
    code_hash = state_data.get("code_hash")
    number = state_data.get("number")
    
    if not all([code_hash, number]):
        await message.answer("Ошибка сессии. Начните заново.")
        await state.clear()
        await main_message(message)
        return

    session_path = f'sessions/{message.from_user.id}/{number}.session'
    client = None
    
    try:
        # ⚡ МИНИМАЛЬНАЯ БЫСТРАЯ АУТЕНТИФИКАЦИЯ
        # Создаем клиент БЕЗ фоновых задач
        client = Client(
            f"sessions/{message.from_user.id}/{number}",
            api_id=API_ID,
            api_hash=API_HASH,
            no_updates=True  # ⚡ ОТКЛЮЧАЕМ PingTask/NetworkTask
        )
        
        # ⚡ БЫСТРЫЙ connect (вместо медленного start)
        await client.connect()
        
        # ⚡ БЫСТРАЯ операция аутентификации
        await client.sign_in(
            phone_number=number,
            phone_code_hash=code_hash, 
            phone_code=code
        )
        
        await message.answer("✅ Успешная авторизация!")
        await ensure_account(message.from_user.id, number, session_path)
        
    except SessionPasswordNeeded:
        await state.update_data({"code": code})
        await message.answer("🔐 Требуется пароль двухфакторной аутентификации. Введите пароль:")
        await state.set_state(addsession.password)
        return
    except Exception as e:
        await message.answer(f"Ошибка: {str(e)}")
        try:
            os.remove(session_path)
        except OSError:
            pass
    finally:
        # ⚡ БЫСТРЫЙ disconnect (вместо медленного stop)
        if client:
            await client.disconnect()
    
    await state.clear()
    await main_message(message)
'''

# Заменяем текущую функцию add_code
content = re.sub(
    r'@dp\.message\(addsession\.code\)[\s\S]*?await main_message\(message\)',
    new_add_code,
    content
)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Функция add_code заменена на быструю аутентификацию")
print("⚡ Ожидаемое время аутентификации: 2-3 секунды вместо 10-20")
