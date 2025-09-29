# 🚀 Развертывание Telegram Comment Bot на VPS

## Быстрое развертывание (рекомендуется)

### Если curl не работает (ошибка 400):

Попробуйте альтернативные команды:

```bash
# Вариант 1: wget вместо curl
wget -O - https://raw.githubusercontent.com/spam108/telegram-warmup-bot/main/docker-deploy.sh | bash

# Вариант 2: Скачайте скрипт вручную
wget https://raw.githubusercontent.com/spam108/telegram-warmup-bot/main/docker-deploy.sh
chmod +x docker-deploy.sh
./docker-deploy.sh

# Вариант 3: Скачайте весь репозиторий как архив
wget https://github.com/spam108/telegram-warmup-bot/archive/refs/heads/main.zip
unzip main.zip
cd telegram-warmup-bot-main
chmod +x docker-deploy.sh
./docker-deploy.sh
```

### Если все способы через интернет не работают:

Используйте копирование файлов с локальной машины на сервер.

### Специальный случай: Размещение готового проекта

Если у вас уже есть директория с проектом и нужно только добавить .env файл:

```bash
# Предполагаем что проект уже размещен в ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Убедитесь что все необходимые файлы присутствуют
ls -la

# Создайте .env файл из шаблона
cp .env.example .env

# Отредактируйте .env файл с вашими токенами
nano .env

# Сделайте скрипты исполняемыми (если нужно)
chmod +x docker-deploy.sh check_docker.py

# Запустите развертывание
./docker-deploy.sh
```

**Необходимые файлы в проекте:**
- `main.py` - основной бот
- `docker-compose.yml` - конфигурация Docker
- `Dockerfile` - образ приложения
- `requirements.txt` - зависимости Python
- `db.py` - работа с базой данных
- `comment_engine.py` - генерация комментариев
- Все остальные файлы проекта

**Файлы которые НЕ нужны:**
- `.env` (создадите на сервере)
- `venv/` (создастся автоматически)
- `sessions/` (создастся автоматически)
- `*.log` (создадутся автоматически)

### Вариант 1: Через прямую загрузку (работает без GitHub)

```bash
# Создайте директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Установите unzip если отсутствует
sudo apt update && sudo apt install -y unzip

# Скачайте архив проекта
wget https://github.com/spam108/telegram-warmup-bot/archive/refs/heads/main.zip

# Распакуйте архив
unzip main.zip

# Перейдите в распакованную директорию
cd telegram-warmup-bot-main

# Скопируйте файлы в корневую директорию проекта
mv * ../ 2>/dev/null || true
mv .* ../ 2>/dev/null || true

# Вернитесь назад и удалите архив
cd ..
rmdir telegram-warmup-bot-main
rm main.zip

# Сделайте скрипты исполняемыми
chmod +x docker-deploy.sh check_docker.py

# Настройте переменные окружения
nano .env

# Запустите развертывание
./docker-deploy.sh
```

### Вариант 2: Локальное развертывание (текущий способ)

```bash
# Создайте директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Скопируйте файлы проекта (замените на ваш метод копирования)
# Например, через scp, rsync или git clone из вашего репозитория

# Сделайте скрипт исполняемым
chmod +x docker-deploy.sh

# Запустите развертывание
./docker-deploy.sh
```

### Вариант 3: Ручное развертывание

```bash
# Создайте директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Клонируйте репозиторий
git clone https://github.com/spam108/telegram-warmup-bot.git .

# Создайте необходимые директории
mkdir -p sessions logs

# Сделайте скрипты исполняемыми
chmod +x docker-deploy.sh check_docker.py

# Настройте переменные окружения
nano .env

# Запустите развертывание
./docker-deploy.sh
```

### Вариант 4: Развертывание через прямую загрузку

Если GitHub недоступен или curl не работает:

```bash
# Создайте директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Установите unzip если отсутствует
sudo apt update && sudo apt install -y unzip

# Скачайте архив проекта
wget https://github.com/spam108/telegram-warmup-bot/archive/refs/heads/main.zip

# Распакуйте архив
unzip main.zip

# Перейдите в распакованную директорию
cd telegram-warmup-bot-main

# Скопируйте файлы в корневую директорию проекта
mv * ../ 2>/dev/null || true
mv .* ../ 2>/dev/null || true

# Вернитесь назад и удалите архив
cd ..
rmdir telegram-warmup-bot-main
rm main.zip

# Сделайте скрипты исполняемыми
chmod +x docker-deploy.sh check_docker.py

# Настройте переменные окружения
nano .env

# Запустите развертывание
./docker-deploy.sh
```

### Вариант 5: Через scp/rsync (с вашей локальной машины)

```bash
# На вашей локальной машине создайте архив проекта:
cd ~/git\ cursor/clonetest
zip -r telegram-comment-bot.zip . -x "*.git*" "venv*" "*.pyc" "__pycache__/*" "*.log" ".env"

# На сервере:
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Скопируйте архив с локальной машины (замените на ваш IP/домен)
scp ваш_локальный_пользователь@ваш_ip:~/telegram-comment-bot.zip .

# Распакуйте архив
unzip telegram-comment-bot.zip
rm telegram-comment-bot.zip

# Сделайте скрипты исполняемыми
chmod +x docker-deploy.sh check_docker.py

# Настройте переменные окружения
nano .env

# Запустите развертывание
./docker-deploy.sh
```

## Требования к серверу

### Минимальные характеристики
- **ОС**: Ubuntu 20.04+ / Debian 10+ / CentOS 8+
- **Память**: 2GB RAM (рекомендуется 4GB+)
- **Диск**: 10GB свободного места
- **CPU**: 1 ядро (рекомендуется 2+ ядра)

### Рекомендуемые VPS провайдеры
- **DigitalOcean** (Droplets от $12/мес)
- **Linode** (Nanode от $5/мес)
- **Vultr** (Cloud Compute от $6/мес)
- **Hetzner** (CX11 от €3.29/мес)

## Шаг 1: Подготовка сервера

### Автоматическая подготовка
```bash
# Обновляем систему
sudo apt update && sudo apt upgrade -y

# Устанавливаем необходимые пакеты
sudo apt install -y curl wget git htop nano ufw

# Настраиваем firewall (опционально)
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw --force enable
```

### Установка Docker (автоматически)
```bash
# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Установка Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Перезагрузка для применения изменений
sudo reboot
```

## Диагностика проблем с загрузкой

### Если curl возвращает ошибку 400/22:

1. **Проверьте URL:**
```bash
# Тестируем доступность
curl -I https://raw.githubusercontent.com/spam108/telegram-warmup-bot/main/docker-deploy.sh
```

2. **Попробуйте альтернативные инструменты:**
```bash
# wget вместо curl
wget -O deploy.sh https://raw.githubusercontent.com/spam108/telegram-warmup-bot/main/docker-deploy.sh
chmod +x deploy.sh
./deploy.sh

# Или скачайте архив всего репозитория
wget https://github.com/spam108/telegram-warmup-bot/archive/refs/heads/main.zip
unzip main.zip
cd telegram-warmup-bot-main
./docker-deploy.sh
```

3. **Проверьте сетевые настройки:**
```bash
# Проверка DNS
nslookup github.com

# Проверка доступности портов
telnet github.com 443

# Проверка firewall
sudo ufw status
```

### Если ничего не работает через интернет:

Используйте **локальное копирование файлов** с вашей машины на сервер.

## 🚀 РЕШЕНИЕ ПРОБЛЕМЫ С GITHUB

Поскольку репозиторий может быть недоступен или приватным, используйте **локальное копирование файлов**:

### Шаг 1: Подготовка архива на локальной машине

```bash
# Перейдите в директорию проекта
cd ~/git\ cursor/clonetest

# Создайте архив без ненужных файлов
zip -r telegram-comment-bot.zip . \
    -x "*.git*" \
    "venv*" \
    "*.pyc" \
    "__pycache__/*" \
    "*.log" \
    ".env" \
    "*.tmp" \
    ".DS_Store" \
    "*.zip" \
    "*.tar.gz"

echo "Архив создан: $(ls -lh telegram-comment-bot.zip)"
```

### Шаг 2: Загрузка на сервер

```bash
# Скопируйте архив на сервер (замените на ваш сервер)
scp telegram-comment-bot.zip root@ваш_сервер_ip:~/

# Альтернатива: если используется другой пользователь
scp telegram-comment-bot.zip ваш_пользователь@ваш_сервер_ip:~/
```

### Шаг 3: Распаковка и запуск на сервере

```bash
# Распакуйте архив
unzip ~/telegram-comment-bot.zip
rm ~/telegram-comment-bot.zip

# Сделайте скрипты исполняемыми
chmod +x docker-deploy.sh check_docker.py

# Создайте .env файл из шаблона
cp .env.example .env

# Отредактируйте .env файл с вашими токенами
nano .env

# Запустите развертывание
./docker-deploy.sh
```

### Шаг 4: Проверка

```bash
# Проверьте статус
sudo docker-compose ps

# Посмотрите логи
sudo docker-compose logs -f

# Проверьте что база данных работает
sudo docker-compose exec postgres psql -U postgres -d commentbot -c "\dt"
```

## 📋 Если сервер недоступен по SSH

Используйте **SFTP (File Transfer Protocol)** для загрузки файлов через файловый менеджер:

1. **Подключитесь к серверу через SFTP** (используйте FileZilla, WinSCP)
2. **Создайте директорию** `telegram-comment-bot`
3. **Загрузите файлы проекта** (кроме .env)
4. **Подключитесь по SSH** и выполните команды выше

## ✅ Проверка успешного развертывания

После развертывания убедитесь что:

```bash
# 1. Сервисы запущены
sudo docker-compose ps

# 2. База данных доступна
sudo docker-compose exec postgres psql -U postgres commentbot -c "SELECT 1;"

# 3. Логи не содержат критических ошибок
sudo docker-compose logs --tail=20

# 4. Бот отвечает в Telegram
# (проверьте что токены в .env файле заполнены правильно)
```

## 🔧 Если возникли проблемы

### Проверка сетевого доступа
```bash
# Тестирование подключения к GitHub
curl -I https://github.com

# Тестирование DNS
nslookup github.com

# Тестирование портов
telnet github.com 443
```

### Альтернативные источники файлов
Если GitHub недоступен, файлы проекта можно найти:
- В исходной директории проекта на вашей машине
- В резервных копиях
- В системе контроля версий

---

🎉 **После успешного развертывания бот будет готов к работе!**

## Шаг 2: Развертывание бота

### Вариант A: Автоматическое развертывание (рекомендуется)

```bash
# Создаем директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Клонируем репозиторий
git clone https://github.com/yourusername/telegram-comment-bot.git .
```

### Вариант B: Ручное развертывание

```bash
# Создаем директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Клонируем репозиторий
git clone https://github.com/yourusername/telegram-comment-bot.git .

# Создаем необходимые директории
mkdir -p sessions logs

# Настраиваем права доступа
chmod +x docker-deploy.sh check_docker.py
```

## Шаг 3: Конфигурация

### Настройка переменных окружения

```bash
cd ~/telegram-comment-bot

# Создаем .env файл из шаблона
cp .env.example .env 2>/dev/null || echo "Создаем .env файл..."

# Редактируем конфигурацию
nano .env
```

**Обязательные настройки в .env:**

```env
# Telegram Bot API
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890

# OpenAI API
OPENAI_API_KEY=sk-your-openai-api-key-here

# Админ-пароль для доступа к боту
PASSWORD=your_secure_password_123

# База данных (используется по умолчанию)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/commentbot

# Лог канал (опционально)
LOG_CHANNEL_ID=-1001234567890
```

### Получение токенов

#### Telegram Bot Token
1. Создайте бота через [@BotFather](https://t.me/botfather)
2. Выполните команды:
   ```
   /newbot
   [Название бота]
   [username_бота]
   ```
3. Скопируйте полученный токен

#### Telegram API Credentials
1. Перейдите на [my.telegram.org](https://my.telegram.org)
2. Войдите в аккаунт
3. Перейдите в "API development tools"
4. Создайте приложение и получите API ID и API Hash

#### OpenAI API Key
1. Зарегистрируйтесь на [platform.openai.com](https://platform.openai.com)
2. Перейдите в API Keys
3. Создайте новый ключ

## Шаг 4: Запуск

### Запуск через Docker (рекомендуется)

```bash
cd ~/telegram-comment-bot

# Запуск в фоновом режиме
sudo docker-compose up -d --build

# Проверка статуса
sudo docker-compose ps

# Просмотр логов
sudo docker-compose logs -f
```

### Альтернативный запуск (если Docker не работает)

```bash
# Установка зависимостей
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Создание базы данных
sudo -u postgres createdb commentbot

# Запуск бота
python main.py
```

## Шаг 5: Верификация и мониторинг

### Проверка работоспособности

```bash
# Статус сервисов
sudo docker-compose ps

# Логи в реальном времени
sudo docker-compose logs -f

# Проверка базы данных
sudo docker-compose exec postgres psql -U postgres commentbot -c "\dt"

# Проверка логов приложения
sudo docker-compose exec bot tail -f /app/bot.log
```

### Мониторинг системы

```bash
# Использование ресурсов
sudo docker stats

# Системные логи
sudo journalctl -u telegram-comment-bot -f

# Логи бота
sudo docker-compose logs --tail=100 bot
```

## Шаг 6: Управление и обслуживание

### Ежедневные команды управления

```bash
cd ~/telegram-comment-bot

# Статус
sudo docker-compose ps

# Логи
sudo docker-compose logs -f

# Перезапуск
sudo docker-compose restart

# Остановка
sudo docker-compose down

# Обновление кода
git pull
sudo docker-compose down
sudo docker-compose up -d --build
```

### Мониторинг производительности

```bash
# Использование ресурсов
sudo docker stats

# Проверка базы данных
sudo docker-compose exec postgres psql -U postgres commentbot -c "
SELECT
    schemaname,
    tablename,
    attname,
    n_distinct,
    most_common_vals,
    most_common_freqs
FROM pg_stats
WHERE tablename IN ('accounts', 'comment_logs', 'warmup_channels');
"

# Проверка индексов
sudo docker-compose exec postgres psql -U postgres commentbot -c "\di"
```

### Резервное копирование

```bash
# Создаем директорию для бэкапов
mkdir -p ~/backups

# Сохраняем базу данных
sudo docker-compose exec postgres pg_dump -U postgres commentbot > ~/backups/commentbot_$(date +%Y%m%d_%H%M%S).sql

# Сохраняем сессии
cp -r ~/telegram-comment-bot/sessions ~/backups/sessions_$(date +%Y%m%d_%H%M%S)

# Список бэкапов
ls -la ~/backups/
```

## Шаг 7: Устранение проблем

### Распространенные проблемы

#### База данных не стартует
```bash
# Проверить логи PostgreSQL
sudo docker-compose logs postgres

# Перезапустить базу данных
sudo docker-compose restart postgres

# Проверить подключение
sudo docker-compose exec postgres psql -U postgres -d commentbot -c "SELECT 1;"
```

#### Бот не отвечает
```bash
# Проверить статус бота
sudo docker-compose ps

# Посмотреть логи бота
sudo docker-compose logs bot

# Проверить переменные окружения
sudo docker-compose exec bot env | grep -E "(BOT_TOKEN|API_ID|DATABASE_URL)"

# Перезапустить бота
sudo docker-compose restart bot
```

#### Проблемы с памятью
```bash
# Проверить использование памяти
sudo docker stats

# Остановить ненужные контейнеры
sudo docker system prune -a

# Увеличить лимиты в docker-compose.yml
nano docker-compose.yml  # Изменить memory limits
sudo docker-compose up -d --build
```

#### Проблемы с сессиями Telegram
```bash
# Проверить наличие сессий
ls -la ~/telegram-comment-bot/sessions/

# Проверить права доступа
chmod -R 755 ~/telegram-comment-bot/sessions/

# Перезапустить бота
sudo docker-compose restart
```

### Полезные команды диагностики

```bash
# Проверка сетевых подключений
sudo docker-compose exec bot netstat -tuln

# Проверка процессов
sudo docker-compose exec bot ps aux

# Проверка дискового пространства
df -h

# Проверка памяти
free -h

# Проверка запущенных контейнеров
sudo docker ps -a
```

## Шаг 8: Масштабирование

### Добавление аккаунтов
1. Создайте дополнительные сессии Telegram в папке `sessions/`
2. База данных автоматически масштабируется благодаря индексам
3. Мониторьте использование ресурсов

### Производительность
- **3-5 аккаунтов**: текущая конфигурация оптимальна
- **5-10 аккаунтов**: рассмотрите увеличение ресурсов сервера
- **10+ аккаунтов**: распределите на несколько серверов или используйте очередь задач

## Безопасность

### Рекомендации по безопасности
- ✅ Используйте сильные пароли для всех сервисов
- ✅ Регулярно обновляйте систему и зависимости
- ✅ Настройте firewall (ufw)
- ✅ Используйте VPN для доступа к серверу
- ✅ Мониторьте логи на подозрительную активность
- ✅ Создавайте регулярные резервные копии

### Проверка безопасности
```bash
# Проверка открытых портов
sudo netstat -tuln

# Проверка процессов
sudo ps aux | grep -E "(python|docker)"

# Проверка логов на ошибки безопасности
sudo docker-compose logs | grep -i "error\|exception\|failed"
```

## Поддержка и мониторинг

### Мониторинг в реальном времени
```bash
# Основной мониторинг
sudo docker-compose logs -f

# Системный мониторинг
htop

# Мониторинг диска
sudo docker system df

# Мониторинг сети
sudo docker-compose exec bot iftop -i eth0
```

### Автоматическое резервное копирование
```bash
# Создайте скрипт резервного копирования
cat > ~/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=~/backups
mkdir -p $BACKUP_DIR

# База данных
sudo docker-compose exec postgres pg_dump -U postgres commentbot > $BACKUP_DIR/db_$(date +%Y%m%d_%H%M%S).sql

# Сессии
cp -r ~/telegram-comment-bot/sessions $BACKUP_DIR/

# Логи
cp ~/telegram-comment-bot/bot.log $BACKUP_DIR/ 2>/dev/null || true

# Удаляем старые бэкапы (старше 7 дней)
find $BACKUP_DIR -type f -name "*.sql" -mtime +7 -delete
find $BACKUP_DIR -type d -name "sessions_*" -mtime +7 -exec rm -rf {} + 2>/dev/null || true

echo "Backup completed: $(date)"
EOF

chmod +x ~/backup.sh

# Добавьте в cron для ежедневного выполнения
crontab -e
# Добавьте строку: 0 2 * * * ~/backup.sh
```

---

🎉 **Поздравляем! Ваш Telegram Comment Bot развернут и готов к работе!**

Для получения дополнительной помощи обратитесь к документации в репозитории или создайте issue.
