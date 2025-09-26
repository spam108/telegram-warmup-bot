# Скрипт для инициализации Git репозитория в Windows PowerShell
# Использование: .\init-git.ps1

Write-Host "🔧 Инициализация Git репозитория..." -ForegroundColor Green

# Инициализируем Git репозиторий
git init

# Добавляем все файлы
git add .

# Создаем первый коммит
git commit -m "Initial commit: Telegram Comment Bot with warmup mode and PostgreSQL support"

Write-Host "✅ Git репозиторий инициализирован!" -ForegroundColor Green
Write-Host ""
Write-Host "📋 Следующие шаги:" -ForegroundColor Yellow
Write-Host "1. Создайте репозиторий на GitHub/GitLab"
Write-Host "2. Добавьте remote: git remote add origin <your-repo-url>"
Write-Host "3. Отправьте код: git push -u origin main"
Write-Host ""
Write-Host "🔗 Для публикации на VPS:" -ForegroundColor Cyan
Write-Host "1. Скопируйте deploy.sh на сервер"
Write-Host "2. Запустите: ./deploy.sh"
Write-Host "3. Отредактируйте .env файл"
Write-Host "4. Запустите бота: ./manage.sh start"
Write-Host ""
Write-Host "💡 Пример команд для GitHub:" -ForegroundColor Magenta
Write-Host "git remote add origin https://github.com/yourusername/telegram-comment-bot.git"
Write-Host "git branch -M main"
Write-Host "git push -u origin main"


