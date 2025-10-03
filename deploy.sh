#!/bin/bash

# Скрипт для развертывания Telegram Comment Bot на VPS
# Использование: ./deploy.sh

set -e

echo "🚀 Начинаем развертывание Telegram Comment Bot..."

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверяем, что скрипт запущен от root или с sudo
if [[ $EUID -eq 0 ]]; then
    error "Не запускайте этот скрипт от root! Используйте обычного пользователя с sudo правами."
    exit 1
fi

# Проверяем наличие sudo
if ! command -v sudo &> /dev/null; then
    error "sudo не найден. Установите sudo или запустите от root."
    exit 1
fi

log "Обновляем систему..."
sudo apt update && sudo apt upgrade -y

log "Устанавливаем необходимые пакеты..."
sudo apt install -y curl wget git python3 python3-pip python3-venv sqlite3

log "Устанавливаем Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
    warn "Docker установлен. Перезайдите в систему для применения изменений группы."
fi

log "Устанавливаем Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

log "Создаем директорию для проекта..."
mkdir -p ~/telegram-comment-bot
cd ~/telegram-comment-bot

log "Клонируем репозиторий (замените URL на ваш)..."
if [ ! -d ".git" ]; then
    read -p "Введите URL вашего Git репозитория: " REPO_URL
    git clone $REPO_URL .
else
    log "Репозиторий уже существует, обновляем..."
    git pull
fi

log "Создаем .env файл..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn "Создан файл .env из шаблона. Отредактируйте его перед запуском!"
    echo "Не забудьте заполнить:"
    echo "- BOT_TOKEN"
    echo "- API_ID и API_HASH"
    echo "- OPENAI_API_KEY"
    echo "- PASSWORD"
    echo "- DATABASE_URL (если хотите изменить путь к файлу SQLite)"
else
    log ".env файл уже существует"
fi

log "Создаем директории для данных..."
mkdir -p sessions logs data

log "Настраиваем systemd сервис..."
sudo tee /etc/systemd/system/CDXBOT0310.service > /dev/null <<EOF
[Unit]
Description=Telegram Comment Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin
ExecStart=$(pwd)/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

log "Создаем виртуальное окружение Python..."
python3 -m venv venv
source venv/bin/activate

log "Устанавливаем зависимости..."
pip install --upgrade pip
pip install -r requirements.txt

log "Перезагружаем systemd..."
sudo systemctl daemon-reload

log "Включаем автозапуск сервиса..."
sudo systemctl enable CDXBOT0310

log "Создаем скрипт управления..."
cat > manage.sh << 'EOF'
#!/bin/bash

case "$1" in
    start)
        echo "Запускаем бота..."
        sudo systemctl start CDXBOT0310
        ;;
    stop)
        echo "Останавливаем бота..."
        sudo systemctl stop CDXBOT0310
        ;;
    restart)
        echo "Перезапускаем бота..."
        sudo systemctl restart CDXBOT0310
        ;;
    status)
        sudo systemctl status CDXBOT0310
        ;;
    logs)
        sudo journalctl -u CDXBOT0310 -f
        ;;
    update)
        echo "Обновляем код..."
        git pull
        source venv/bin/activate
        pip install -r requirements.txt
        sudo systemctl restart CDXBOT0310
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|logs|update}"
        exit 1
        ;;
esac
EOF

chmod +x manage.sh

log "Создаем скрипт для Docker развертывания..."
cat > docker-deploy.sh << 'EOF'
#!/bin/bash

echo "🐳 Развертывание через Docker..."

# Останавливаем существующие контейнеры
docker-compose down

# Собираем и запускаем
docker-compose up -d --build

echo "✅ Развертывание завершено!"
echo "📊 Просмотр логов: docker-compose logs -f"
echo "🛑 Остановка: docker-compose down"
EOF

chmod +x docker-deploy.sh

log "✅ Развертывание завершено!"
echo ""
echo "📋 Следующие шаги:"
echo "1. Отредактируйте файл .env с вашими настройками"
echo "2. Запустите бота: ./manage.sh start"
echo "3. Проверьте статус: ./manage.sh status"
echo "4. Просмотр логов: ./manage.sh logs"
echo ""
echo "🔧 Управление:"
echo "- ./manage.sh start    - Запуск"
echo "- ./manage.sh stop     - Остановка"
echo "- ./manage.sh restart  - Перезапуск"
echo "- ./manage.sh status   - Статус"
echo "- ./manage.sh logs     - Логи"
echo "- ./manage.sh update   - Обновление"
echo ""
echo "🐳 Для Docker развертывания: ./docker-deploy.sh"
echo ""
warn "Не забудьте отредактировать .env файл перед первым запуском!"


