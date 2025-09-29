# 🚀 Деплой ветки st.0 на сервер

## ✅ **Ветка st.0 готова к деплою!**

Все изменения отправлены в GitHub: https://github.com/spam108/telegram-warmup-bot/tree/st.0

## 🎯 **КОМАНДЫ ДЛЯ СЕРВЕРА:**

### **Вариант 1: Обновление существующего проекта**

```bash
# Перейдите в директорию проекта
cd ~/telegram-comment-bot

# Обновите код с ветки st.0
git fetch origin
git checkout st.0
git pull origin st.0

# Остановите старые контейнеры
sudo docker-compose down

# Запустите бота
sudo docker-compose up -d

# Проверьте статус
sudo docker-compose ps
sudo docker-compose logs --tail=10
```

### **Вариант 2: Новое развертывание с ветки st.0**

```bash
# Скачайте и запустите скрипт развертывания
wget -O - https://raw.githubusercontent.com/spam108/telegram-warmup-bot/st.0/deploy.sh | bash
```

### **Вариант 3: Ручное клонирование**

```bash
# Создайте директорию
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

# Клонируйте репозиторий
git clone https://github.com/spam108/telegram-warmup-bot.git .

# Переключитесь на ветку st.0
git checkout st.0

# Создайте .env файл
cp .env.example .env
nano .env  # Заполните токены

# Запустите развертывание
chmod +x deploy.sh
./deploy.sh
```

## 🔧 **Проверка работы:**

```bash
# Статус контейнеров
sudo docker-compose ps

# Логи бота
sudo docker-compose logs commentbot --tail=20

# Логи базы данных
sudo docker-compose logs postgres --tail=10

# Проверка базы данных
sudo docker-compose exec postgres psql -U postgres -d commentbot -c "SELECT COUNT(*) FROM accounts;"
```

## 📋 **Что включено в ветку st.0:**

- ✅ Исправлена логика режимов аккаунтов
- ✅ Улучшенное логирование с эмодзи
- ✅ Оптимизированные индексы базы данных
- ✅ Исправлены права доступа в Docker
- ✅ Обновленная документация
- ✅ Готовые скрипты развертывания

## 🚨 **Если что-то не работает:**

```bash
# Перезапустите все контейнеры
sudo docker-compose restart

# Посмотрите подробные логи
sudo docker-compose logs --tail=50

# Проверьте конфигурацию
cat .env
```

**Готово к деплою! Выполните команды на сервере.** 🚀
