# Инструкции для обновления сервера

## Проблема
Настройки аккаунта (sleep_min, sleep_max, chance) не сохраняются в базе данных при настройке через бота.

## Исправления
1. Исправлен обработчик `add_sleeps` - добавлен `else` блок для неверного формата
2. Исправлен обработчик `add_channels` - добавлено сохранение всех настроек в БД
3. Убрано дублирование вызовов `update_account_settings`

## Команды для обновления сервера

Выполните эти команды на сервере:

```bash
# 1. Переходим в директорию проекта
cd ~/telegram-bot

# 2. Останавливаем контейнеры
sudo docker-compose down

# 3. Обновляем код из репозитория
git fetch origin
git reset --hard origin/st.0

# 4. Пересобираем и запускаем контейнеры
sudo docker-compose build --no-cache
sudo docker-compose up -d

# 5. Проверяем статус
sudo docker-compose ps

# 6. Проверяем логи
sudo docker-compose logs bot --tail=20
```

## Проверка исправления

После обновления:

1. Зайдите в бота в Telegram
2. Нажмите "Добавить аккаунт" 
3. Введите номер телефона
4. Настройте аккаунт:
   - Шанс комментирования: 30
   - Системный промпт: любой текст
   - Задержки: 10-20
   - Каналы: введите каналы или "-"
   - Каналы прогрева: введите каналы или "-"

5. Проверьте в базе данных:
```bash
sudo docker-compose exec postgres psql -U bot_user -d telegram_bot \
  -c "SELECT id, phone, sleep_min, sleep_max, chance FROM accounts WHERE phone = 'YOUR_PHONE';"
```

Настройки должны быть сохранены (не NULL).

## Ожидаемый результат
- sleep_min: 10
- sleep_max: 20  
- chance: 30
- system_prompt: введенный текст
