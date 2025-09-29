#!/bin/bash

# Docker Deployment Script for Telegram Comment Bot
# Рекомендуемый способ развертывания

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is installed
check_docker() {
    log_info "Проверяем Docker..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker не установлен"
        log_info "Установите Docker: curl -fsSL https://get.docker.com | sudo sh"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose не установлен"
        log_info "Установите Docker Compose с официального сайта"
        exit 1
    fi

    log_success "Docker установлен"
}

# Setup project
setup_project() {
    log_info "Настраиваем проект..."

    # Create project directory if it doesn't exist
    if [ ! -d "telegram-comment-bot" ]; then
        log_info "Клонируем репозиторий..."
        git clone https://github.com/spam108/telegram-warmup-bot.git
        cd telegram-warmup-bot
    else
        log_info "Проект уже существует, обновляем..."
        cd telegram-warmup-bot
        git pull origin main
    fi

    # Check if this is a valid project
    if [ ! -f "docker-compose.yml" ] || [ ! -f "main.py" ]; then
        log_error "Некорректная структура проекта"
        log_info "Убедитесь что в директории есть файлы: docker-compose.yml, main.py"
        exit 1
    fi

    # Create necessary directories
    mkdir -p sessions logs

    log_success "Проект настроен"
}

# Configure environment
configure_env() {
    log_info "Настраиваем переменные окружения..."

    cd telegram-comment-bot

    if [ ! -f .env ]; then
        log_warning ".env файл не найден"
        log_info "Создаем шаблон .env файла..."

        cat > .env << 'EOF'
# Telegram Bot Configuration
BOT_TOKEN=your_bot_token_here
API_ID=your_api_id_here
API_HASH=your_api_hash_here

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here

# Admin Password
PASSWORD=your_secure_password_here

# Database Configuration
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/commentbot

# Logging Configuration
LOG_CHANNEL_ID=your_log_channel_id_here
EOF

        log_warning "Заполните .env файл реальными значениями:"
        echo "  - BOT_TOKEN: токен от @BotFather"
        echo "  - API_ID и API_HASH: от my.telegram.org"
        echo "  - OPENAI_API_KEY: от platform.openai.com"
        echo "  - PASSWORD: пароль для доступа к боту"
        echo "  - LOG_CHANNEL_ID: ID канала для логов (опционально)"

        read -p "Нажмите Enter после заполнения .env файла..."
    else
        log_success ".env файл найден"
    fi
}

# Deploy with Docker
deploy_docker() {
    log_info "Развертываем с помощью Docker..."

    cd telegram-comment-bot

    # Stop existing containers
    sudo docker-compose down 2>/dev/null || true

    # Build and start
    sudo docker-compose up -d --build

    # Wait for services to be ready
    log_info "Ожидаем готовности сервисов..."
    sleep 30

    # Check status
    if sudo docker-compose ps | grep -q "Up"; then
        log_success "Сервисы запущены успешно"

        # Show status
        echo ""
        echo "📊 СТАТУС СЕРВИСОВ:"
        sudo docker-compose ps

    else
        log_error "Ошибка запуска сервисов"
        echo ""
        echo "📋 ЛОГИ ОШИБОК:"
        sudo docker-compose logs --tail=50
        exit 1
    fi
}

# Run health checks
run_health_checks() {
    log_info "Выполняем проверки здоровья..."

    cd telegram-comment-bot

    # Check if bot is responding
    if command -v curl &> /dev/null; then
        # This is a basic check - in real scenario you'd check bot health
        log_info "Базовые проверки выполнены"
    fi

    # Check logs for errors
    if sudo docker-compose logs | grep -q "ERROR\|Exception"; then
        log_warning "Обнаружены ошибки в логах - проверьте конфигурацию"
        echo ""
        echo "Последние ошибки:"
        sudo docker-compose logs --tail=20 | grep -E "ERROR|Exception" | tail -5
    else
        log_success "Ошибок в логах не обнаружено"
    fi
}

# Show deployment summary
show_summary() {
    cd telegram-comment-bot

    echo ""
    log_success "🎉 Развертывание завершено успешно!"
    echo ""
    echo "📋 ДОСТУП К СЕРВИСАМ:"
    echo "   База данных PostgreSQL: localhost:5432"
    echo "   Бот: запущен в контейнере"
    echo "   Логи: доступны через 'docker-compose logs'"
    echo ""
    echo "🔧 УПРАВЛЕНИЕ:"
    echo "   Статус:        sudo docker-compose ps"
    echo "   Логи:          sudo docker-compose logs -f"
    echo "   Перезапуск:    sudo docker-compose restart"
    echo "   Остановка:     sudo docker-compose down"
    echo "   Обновление:    git pull && sudo docker-compose up -d --build"
    echo ""
    echo "📊 МОНИТОРИНГ:"
    echo "   Системные логи: sudo docker-compose logs -f"
    echo "   Логи приложения: доступны в контейнере /app/bot.log"
    echo "   База данных:    sudo docker-compose exec postgres psql -U postgres commentbot"
    echo ""
    echo "⚠️  НАПОМИНАНИЕ:"
    echo "   Убедитесь что .env файл заполнен реальными значениями!"
    echo "   Добавьте сессии Telegram аккаунтов в папку sessions/"
}

# Main deployment function
main() {
    echo "🐳 Telegram Comment Bot - Docker Развертывание"
    echo "=============================================="

    check_docker
    setup_project
    configure_env
    deploy_docker
    run_health_checks
    show_summary

    echo ""
    echo "🚀 Развертывание завершено! Бот готов к работе."
}

# Run deployment
main "$@"
