# Telegram Comment Bot

Автоматический бот для комментирования в Telegram каналах с поддержкой режима прогрева и циркадных ритмов.

## Возможности

- 🤖 **Автоматическое комментирование** в Telegram каналах
- 🧠 **ИИ-генерация комментариев** через OpenAI API
- 🔄 **Режим прогрева** для новых аккаунтов (постепенное добавление каналов)
- ⏰ **Циркадные ритмы** (пауза комментирования с 00:30 до 08:30)
- 💾 **PostgreSQL база данных** для надежного хранения данных
- 🔐 **Безопасная авторизация** через пароль
- 📊 **Статистика и мониторинг** работы аккаунтов

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

Установите PostgreSQL и создайте базу данных:

```sql
CREATE DATABASE commentbot;
```

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
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/commentbot
```

### 6. Запуск бота

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
- **С 4:30 до 8:25 утра** добавляет каналы из очереди прогрева
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
sudo apt install python3 python3-pip postgresql postgresql-contrib git
```

2. **Настройка PostgreSQL**:
```bash
sudo -u postgres createdb commentbot
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'your_password';"
```

3. **Клонирование и настройка**:
```bash
git clone https://github.com/yourusername/telegram-comment-bot.git
cd telegram-comment-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

4. **Настройка systemd сервиса**:
```bash
sudo cp commentbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable commentbot
sudo systemctl start commentbot
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
sudo systemctl start commentbot
sudo systemctl stop commentbot
sudo systemctl restart commentbot
sudo systemctl status commentbot
sudo journalctl -u commentbot -f
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
   - Убедитесь, что PostgreSQL запущен

3. **"Account is in warmup mode"**
   - Используйте команду `/fixmode` для переключения в стандартный режим

4. **Ошибки авторизации Telegram**
   - Удалите файлы сессий и авторизуйтесь заново

### Логи и отладка

```bash
# Просмотр логов systemd
sudo journalctl -u commentbot -f

# Просмотр логов Docker
docker-compose logs -f
```

## Лицензия

MIT License

## Поддержка

Для вопросов и поддержки создайте issue в репозитории.