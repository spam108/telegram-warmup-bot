# Инструкции по отладке проблемы сохранения настроек

## Проблема
Параметры аккаунта (sleep_min, sleep_max, chance, system_prompt) не сохраняются в базу данных при настройке через бот-интерфейс.

## Что уже сделано
1. ✅ Добавлено детальное логирование в функции `add_sleeps` и `add_channels` в `main.py`
2. ✅ Добавлено логирование в функцию `update_account_settings` в `db.py`
3. ✅ Протестирована логика парсинга и генерации SQL - работает корректно
4. ✅ Код обновлен на сервере

## Шаги для отладки на сервере

### 1. Запустите тест сохранения настроек
```bash
cd /path/to/your/bot
python server_test_settings.py
```

Этот тест проверит:
- Подключение к базе данных
- Список существующих аккаунтов
- Сохранение тестовых настроек
- Проверку сохраненных данных

### 2. Настройте аккаунт через бот
1. Запустите бот на сервере
2. Отправьте `/start` боту
3. Нажмите "Запустить" на любом аккаунте
4. Введите настройки:
   - Chance: например, 30
   - System prompt: например, "Тестовый промпт"
   - Sleep: например, 10-20

### 3. Проверьте логи в канале
В канале логирования должны появиться сообщения с DEBUG информацией:
- `DEBUG: add_sleeps called with message: 10-20`
- `DEBUG: sleeps saved to state: 10-20`
- `DEBUG: About to save settings - account_id=X, sleep_min=10, sleep_max=20, chance=30, system_promt=Тестовый промпт`
- `DEBUG: update_account_settings called with account_id=X, chance=30, sleep_min=10, sleep_max=20, system_prompt=Тестовый промпт`
- `DEBUG: Executing SQL update with 4 fields: ['chance = $1', 'system_prompt = $2', 'sleep_min = $3', 'sleep_max = $4']`
- `DEBUG: SQL update completed successfully for account_id=X`
- `DEBUG: Settings saved successfully for account_id=X`

### 4. Проверьте базу данных
```sql
SELECT id, phone, chance, system_prompt, sleep_min, sleep_max, updated_at 
FROM accounts 
WHERE user_id = 291299155 
ORDER BY updated_at DESC;
```

### 5. Возможные проблемы и решения

#### Если логи не появляются:
- Проверьте, что бот запущен с обновленным кодом
- Проверьте, что код из ветки `main` актуален на сервере

#### Если логи показывают, что данные не передаются:
- Проблема в логике обработки состояний бота
- Проверьте функцию `add_sleeps` в `main.py`

#### Если логи показывают, что данные передаются, но не сохраняются:
- Проблема в подключении к базе данных
- Проверьте переменные `DATABASE_URL`, `DATABASE_NAME`, `DATABASE_USER`
- Для PostgreSQL убедитесь, что пользователь имеет права на базу и сервис доступен

#### Если данные сохраняются, но не используются:
- Проблема в функции `send_comments`
- Проверьте, что аккаунт получает данные из БД

## Файлы для проверки
- `main.py` - функции `add_sleeps` и `add_channels`
- `db.py` - функция `update_account_settings`
- Логи в канале `-1003123025616`

## Ожидаемый результат
После настройки аккаунта через бот, в базе данных должны сохраниться:
- `chance` - процент комментирования
- `system_prompt` - системный промпт
- `sleep_min` - минимальная задержка
- `sleep_max` - максимальная задержка
