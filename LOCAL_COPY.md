# 📋 Локальное копирование файлов на сервер

Если curl/wget не работают или доступ к GitHub ограничен, используйте копирование файлов с локальной машины.

## Способ 1: Через scp (SSH Copy)

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
    ".DS_Store"

# Проверьте размер архива
ls -lh telegram-comment-bot.zip
```

### Шаг 2: Копирование на сервер

```bash
# Скопируйте архив на сервер (замените на ваш сервер)
scp telegram-comment-bot.zip root@ваш_сервер_ip:~/

# Альтернатива: если у вас другой пользователь
scp telegram-comment-bot.zip ваш_пользователь@ваш_сервер_ip:~/
```

### Шаг 3: Распаковка на сервере

```bash
# Подключитесь к серверу
ssh root@ваш_сервер_ip

# Создайте директорию проекта
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Установите unzip если отсутствует
sudo apt update && sudo apt install -y unzip

# Распакуйте архив
unzip ~/telegram-comment-bot.zip

# Скопируйте файлы в проектную директорию
mv telegram-comment-bot/* . 2>/dev/null || true
mv telegram-comment-bot/.* . 2>/dev/null || true
rmdir telegram-comment-bot
rm ~/telegram-comment-bot.zip

# Сделайте скрипты исполняемыми
chmod +x deploy.sh check_docker.py

# Проверьте файлы
ls -la

# Создайте .env файл
cp .env.example .env
nano .env  # Заполните токены

# Запустите развертывание
./deploy.sh
```

## Способ 2: Через rsync (для синхронизации)

### Синхронизация файлов

```bash
# С локальной машины
rsync -avz --exclude='*.git*' \
          --exclude='venv*' \
          --exclude='*.pyc' \
          --exclude='__pycache__' \
          --exclude='*.log' \
          --exclude='.env' \
          ~/git\ cursor/clonetest/ \
          root@ваш_сервер_ip:~/telegram-comment-bot/

# На сервере
ssh root@ваш_сервер_ip
cd ~/telegram-comment-bot
chmod +x deploy.sh check_docker.py
cp .env.example .env
nano .env  # Заполните токены
./deploy.sh
```

## Способ 3: Через файловый менеджер (SFTP)

1. **Подключитесь к серверу через SFTP** (FileZilla, WinSCP и т.д.)
2. **Создайте директорию** `telegram-comment-bot`
3. **Загрузите файлы проекта** (кроме .env файла)
4. **Подключитесь по SSH** и выполните команды:
   ```bash
   cd ~/telegram-comment-bot
   chmod +x deploy.sh check_docker.py
   cp .env.example .env
   nano .env  # Заполните токены
   ./deploy.sh
   ```

## Проверка успешного копирования

```bash
# Проверьте наличие основных файлов
ls -la ~/telegram-comment-bot/

# Должны присутствовать:
# - main.py
# - docker-compose.yml
# - Dockerfile
# - requirements.txt
# - db.py
# - comment_engine.py
# - И другие файлы проекта

# Проверьте размер проекта
du -sh ~/telegram-comment-bot/
```

## Если возникли проблемы

### Проверка прав доступа
```bash
# Убедитесь что файлы имеют правильные права
find ~/telegram-comment-bot/ -name "*.sh" -exec chmod +x {} \;
find ~/telegram-comment-bot/ -name "*.py" -exec chmod +x {} \;
```

### Проверка сетевого доступа
```bash
# Тестирование SSH подключения
ssh -v root@ваш_сервер_ip

# Тестирование доступности портов
telnet ваш_сервер_ip 22
```

### Логи ошибок
```bash
# Если копирование прервалось, проверьте логи
tail -f /var/log/auth.log  # SSH логи
tail -f /var/log/syslog     # Системные логи
```

---

✅ **После копирования файлов следуйте стандартной процедуре развертывания!**
