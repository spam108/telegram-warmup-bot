# 🚀 Docker Развертывание Telegram Comment Bot

## Быстрый запуск

```bash
# 1. Настроить переменные окружения
cp .env.example .env
nano .env  # Заполнить реальные значения

# 2. Запустить в фоновом режиме
docker-compose up -d --build

# 3. Проверить статус
docker-compose ps

# 4. Посмотреть логи
docker-compose logs -f
```

## Структура проекта

```
telegram-comment-bot/
├── main.py              # Основной бот
├── db.py               # Работа с базой данных
├── comment_engine.py   # Генерация комментариев через OpenAI
├── requirements.txt    # Зависимости Python
├── Dockerfile         # Образ приложения
├── docker-compose.yml # Оркестрация сервисов
├── .dockerignore      # Исключения для Docker
└── sessions/          # Сессии Telegram аккаунтов (volume)
```

## Конфигурация

### Обязательные переменные окружения (.env)

```env
# Telegram Bot API
BOT_TOKEN=your_bot_token_here
API_ID=your_api_id
API_HASH=your_api_hash

# OpenAI API
OPENAI_API_KEY=your_openai_key

# Админ-пароль для доступа к боту
PASSWORD=your_secure_password

# База данных
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/commentbot

# Лог канал (опционально)
LOG_CHANNEL_ID=-1001234567890
```

### Ресурсные ограничения

- **База данных**: PostgreSQL 15 с оптимизированными настройками
- **Бот**: 2-4GB RAM, 0.5-1.0 CPU
- **Volumes**: Сессии и логи сохраняются локально

## Управление

```bash
# Запуск
docker-compose up -d

# Остановка
docker-compose down

# Перезапуск
docker-compose restart

# Обновление
docker-compose down
git pull
docker-compose up -d --build

# Логи в реальном времени
docker-compose logs -f

# Логи конкретного сервиса
docker-compose logs -f bot
docker-compose logs -f postgres

# Статус сервисов
docker-compose ps

# Очистка (удалить volumes тоже)
docker-compose down -v
```

## Мониторинг

### Логи
- Основные логи: `docker-compose logs -f`
- Логи приложения: `/app/bot.log` внутри контейнера
- Логи ошибок: `/app/bot_error.txt`

### Healthcheck
- PostgreSQL: проверка доступности каждые 30 секунд
- Бот: базовая проверка работоспособности каждые 60 секунд

### Статус системы
Бот автоматически логирует статистику каждые 30 минут:
- Количество активных аккаунтов
- Распределение по режимам (прогрев/стандартный)
- Статистика каналов прогрева

## Устранение проблем

### База данных не стартует
```bash
# Проверить логи PostgreSQL
docker-compose logs postgres

# Перезапустить только базу данных
docker-compose restart postgres
```

### Бот не стартует
```bash
# Проверить переменные окружения
docker-compose exec bot env | grep -E "(BOT_TOKEN|API_ID|DATABASE_URL)"

# Проверить логи бота
docker-compose logs bot

# Зайти в контейнер для диагностики
docker-compose exec bot bash
```

### Проблемы с сессиями аккаунтов
- Убедиться что volume `./sessions:/app/sessions` настроен правильно
- Проверить права доступа к папке sessions

## Производительность

### Оптимизации базы данных
- Индексы на ключевые поля (user_id, status, mode)
- Connection pool (1-5 соединений)
- Оптимизированные настройки PostgreSQL

### Масштабирование
- Рекомендуется 3-5 аккаунтов на один контейнер
- Для большего количества - несколько экземпляров бота
- Балансировка нагрузки через внешний прокси

## Безопасность

- ✅ Непривилегированный пользователь в контейнере
- ✅ Volumes монтируются только для необходимых данных
- ✅ Переменные окружения не логируются
- ✅ Ресурсные ограничения предотвращают DoS

## Резервное копирование

```bash
# Сохранить базу данных
docker-compose exec postgres pg_dump -U postgres commentbot > backup.sql

# Сохранить сессии
cp -r sessions sessions_backup_$(date +%Y%m%d_%H%M%S)

# Восстановить базу данных
docker-compose exec -T postgres psql -U postgres commentbot < backup.sql
```
