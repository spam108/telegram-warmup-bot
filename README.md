# Telegram Comment Bot

Автоматический бот для комментирования в Telegram каналах с поддержкой режима прогрева и циркадных ритмов.

## Возможности

- 🤖 **Автоматическое комментирование** в Telegram каналах
- 🧠 **ИИ-генерация комментариев** через OpenAI API
- 🔄 **Режим прогрева** для новых аккаунтов (постепенное добавление каналов)
- ⏰ **Циркадные ритмы** (настраиваемая пауза комментирования через переменные окружения)
- 💾 **PostgreSQL база данных** для надежного хранения данных и параллельной работы
- 🔐 **Безопасная авторизация** через пароль
- 📊 **Статистика и мониторинг** работы аккаунтов

## Быстрый старт

1. Клонируйте репозиторий
2. Скопируйте `.env.example` в `.env`
3. Настройте переменные в `.env`:
   - `PROJECT_NAME` - уникальное имя проекта (определяет имена контейнеров)
   - `POSTGRES_*` - настройки базы данных
   - `BOT_TOKEN`, `API_ID`, `API_HASH` - данные Telegram API
4. Запустите: `docker-compose up -d --build`

## Масштабирование

Для запуска multiple instances:
- Создайте отдельные папки для каждого инстанса
- В каждой папке настройте свой `.env` с уникальным `PROJECT_NAME`
- Запускайте независимо в каждой папке


## Установка

### 1. Клонирование репозитория

```bash
git clone <repository-url>
cd telegram-comment-bot
```

### 2. Создание виртуального окружения

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4. Настройка базы данных

Бот хранит данные в PostgreSQL. При использовании `docker-compose` база разворачивается автоматически (см. `docker-compose.yml`). Для локального запуска установите PostgreSQL 15+ и создайте базу с пользователем, после чего укажите строку подключения через переменную `DATABASE_URL`.

### 5. Настройка переменных окружения

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
BOT_TOKEN=your_bot_token_here
API_ID=your_api_id_here
API_HASH=your_api_hash_here
OPENAI_API_KEY=your_openai_api_key_here
PASSWORD=your_secure_password_here
DATABASE_URL=postgresql+asyncpg://warmup:warmup@localhost:5432/warmup

# Настройки времени теперь в файле schedule.json
# Отредактируйте schedule.json под ваши нужды
```

### Подробные логи прогрева

По умолчанию бот отправляет в лог-канал только критичные сообщения о режиме прогрева.
Для включения расширенной диагностики предусмотрены переменные окружения:

- `WARMUP_VERBOSE_LOGS=1` — повышает уровень логирования до `DEBUG`, чтобы видеть детальные сообщения в консоли/файле.
- `WARMUP_VERBOSE_NOTIFICATIONS=1` — добавляет в Telegram единое агрегированное сообщение за итерацию с подробным статусом прогрева (количество аккаунтов, очередь каналов, ежедневные лимиты и т. д.).

Если переменные не заданы, бот продолжит слать только предупреждения и ошибки, сохраняя основной функционал без изменений.

### 6. Настройка расписания

Отредактируйте `schedule.json` под ваши нужды:
```json
{
  "quiet_period": {
    "start_hour": 8,
    "start_minute": 0,
    "end_hour": 20,
    "end_minute": 0,
    "description": "Циркадный ритм - бот не комментирует в это время (UTC)"
  },
  "warmup_period": {
    "start_hour": 12,
    "start_minute": 0,
    "end_hour": 19,
    "end_minute": 0,
    "description": "Период прогрева - бот вступает в каналы в это время (UTC)"
  },
  "warmup_settings": {
    "channels_per_day": 15,
    "delay_minutes": 7,
    "default_days": 7,
    "description": "Настройки режима прогрева"
  }
}
```

### 7. Запуск бота

```bash
python main.py
```

## Использование

### Основные команды

- `/start` - Запуск бота
- `/addsession` - Добавление нового аккаунта
- `/fixmode` - Переключение аккаунтов из режима прогрева в стандартный

### Режимы работы

#### Стандартный режим
- Обычное комментирование в каналах
- Настраиваемые задержки между комментариями
- Шанс комментирования (%)

#### Режим прогрева
- **Автоматически активируется** для новых аккаунтов
- **Днем работает как стандартный режим** (комментирует)
- **Настраиваемое время** добавления каналов из очереди прогрева (через переменные окружения)
- **Не более 15 каналов в день**
- **Длительность: 7 дней** (настраивается)

### Настройка аккаунта

1. **Добавление сессии**: Отправьте номер телефона
2. **Настройка параметров**:
   - Шанс комментирования (1-100%)
   - Системный промпт для ИИ
   - Задержка между комментариями (мин-макс секунды)
3. **Выбор каналов**:
   - Каналы для немедленной подписки
   - Каналы для прогрева (добавляются постепенно)

## Архитектура

### Основные компоненты

- `main.py` - Основная логика бота и обработчики команд
- `db.py` - Работа с базой данных PostgreSQL
- `comment_engine.py` - Генерация комментариев через OpenAI
- `requirements.txt` - Зависимости Python

### База данных

#### Таблицы:
- `users` - Пользователи бота
- `accounts` - Telegram аккаунты
- `comment_logs` - Логи комментариев
- `warmup_channels` - Каналы для прогрева

#### Ключевые поля аккаунтов:
- `mode` - Режим работы (standard/warmup)
- `warmup_end_at` - Окончание режима прогрева
- `warmup_joined_today` - Количество добавленных каналов сегодня
- `channels` - Активные каналы для комментирования
- `warmup_channels` - Каналы в очереди прогрева

## 🚀 Развертывание на VPS

### Быстрое развертывание (рекомендуется)

```bash
# Скачайте и запустите скрипт автоматического развертывания
curl -fsSL https://raw.githubusercontent.com/yourusername/telegram-comment-bot/main/deploy.sh | bash
```

### Docker развертывание

```bash
# Клонируйте репозиторий
git clone https://github.com/yourusername/telegram-comment-bot.git
cd telegram-comment-bot

# Настройте переменные окружения
cp .env.example .env
nano .env

# Запустите через Docker
docker-compose up -d --build

# Просмотр логов
docker-compose logs -f
```

### Ручное развертывание

1. **Установка зависимостей**:
```bash
sudo apt update
sudo apt install python3 python3-pip git
```

2. **Клонирование и настройка**:
```bash
git clone https://github.com/yourusername/telegram-comment-bot.git
cd telegram-comment-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Настройка systemd сервиса**:
```bash
sudo cp CDXBOT0310.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable CDXBOT0310
sudo systemctl start CDXBOT0310
```

### Управление ботом

```bash
# Использование скрипта управления (если доступен)
./manage.sh start      # Запуск
./manage.sh stop       # Остановка
./manage.sh restart    # Перезапуск
./manage.sh status     # Статус
./manage.sh logs       # Логи
./manage.sh update     # Обновление

# Или через systemd
sudo systemctl start CDXBOT0310
sudo systemctl stop CDXBOT0310
sudo systemctl restart CDXBOT0310
sudo systemctl status CDXBOT0310
sudo journalctl -u CDXBOT0310 -f
```

📖 **Подробное руководство по развертыванию**: [DEPLOYMENT.md](DEPLOYMENT.md)

## Мониторинг

### Логи
- Все действия логируются в консоль
- Ошибки записываются в базу данных
- Статистика доступна через бота

### Статистика аккаунтов
- Количество активных каналов
- Очередь прогрева
- Количество добавленных каналов в прогреве
- Количество ошибок

## Безопасность

- 🔐 **Парольная защита** для доступа к боту
- 🔒 **Безопасное хранение** сессий Telegram
- 🛡️ **Валидация входных данных**
- ⚡ **Ограничения скорости** для избежания блокировок

## Устранение неполадок

### Частые проблемы

1. **"TgCrypto is missing!"**
   - Установите: `pip install TgCrypto`

2. **Ошибки подключения к БД**
   - Проверьте `DATABASE_URL` в `.env`
   - Убедитесь, что строка подключения корректна и PostgreSQL доступен (хост, порт, логин и пароль верные)

3. **"Account is in warmup mode"**
   - Используйте команду `/fixmode` для переключения в стандартный режим

4. **Ошибки авторизации Telegram**
   - Удалите файлы сессий и авторизуйтесь заново

### Логи и отладка

```bash
# Просмотр логов systemd
sudo journalctl -u CDXBOT0310 -f

# Просмотр логов Docker
docker-compose logs -f
```

## Лицензия

MIT License

## Поддержка

Для вопросов и поддержки создайте issue в репозитории.
