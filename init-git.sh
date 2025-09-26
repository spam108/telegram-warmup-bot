#!/bin/bash

# Скрипт для инициализации Git репозитория
# Использование: ./init-git.sh

echo "🔧 Инициализация Git репозитория..."

# Инициализируем Git репозиторий
git init

# Добавляем все файлы
git add .

# Создаем первый коммит
git commit -m "Initial commit: Telegram Comment Bot with warmup mode and PostgreSQL support"

echo "✅ Git репозиторий инициализирован!"
echo ""
echo "📋 Следующие шаги:"
echo "1. Создайте репозиторий на GitHub/GitLab"
echo "2. Добавьте remote: git remote add origin <your-repo-url>"
echo "3. Отправьте код: git push -u origin main"
echo ""
echo "🔗 Для публикации на VPS:"
echo "1. Скопируйте deploy.sh на сервер"
echo "2. Запустите: ./deploy.sh"
echo "3. Отредактируйте .env файл"
echo "4. Запустите бота: ./manage.sh start"


