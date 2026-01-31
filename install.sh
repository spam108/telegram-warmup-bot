#!/bin/bash
# Установочный скрипт для чистого сервера
# Устанавливает Docker, Python, зависимости и настраивает бота

set -e

echo "🚀 Начинаем установку бота на чистый сервер..."

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
    echo "⚠️  Скрипт должен быть запущен от root или через sudo"
    exit 1
fi

# Определяем дистрибутив
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    VER=$VERSION_ID
else
    echo "❌ Не удалось определить дистрибутив Linux"
    exit 1
fi

echo "📦 Обнаружен дистрибутив: $OS $VER"

# Обновление системы
echo "🔄 Обновление системы..."
if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    apt-get update
    apt-get upgrade -y
    apt-get install -y curl wget git ca-certificates gnupg lsb-release
elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ] || [ "$OS" = "fedora" ]; then
    yum update -y
    yum install -y curl wget git
fi

# Установка Docker
echo "🐳 Установка Docker..."
if ! command -v docker &> /dev/null; then
    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        # Удаляем старые версии
        apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
        
        # Устанавливаем Docker
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
        rm get-docker.sh
        
        # Запускаем Docker
        systemctl start docker
        systemctl enable docker
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        yum install -y yum-utils
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        yum install -y docker-ce docker-ce-cli containerd.io
        systemctl start docker
        systemctl enable docker
    fi
else
    echo "✅ Docker уже установлен"
fi

# Установка Docker Compose
echo "🐳 Установка Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
    curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose
else
    echo "✅ Docker Compose уже установлен"
fi

# Установка Python 3.10+
echo "🐍 Проверка Python..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ -z "$PYTHON_VERSION" ] || [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo "📦 Установка Python 3.10+..."
    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        apt-get install -y software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
        apt-get install -y python3.10 python3.10-venv python3.10-dev python3-pip
        # Создаем симлинк если нужно
        if [ ! -f /usr/bin/python3 ]; then
            ln -s /usr/bin/python3.10 /usr/bin/python3
        fi
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        yum install -y python3 python3-pip python3-devel
    fi
else
    echo "✅ Python $PYTHON_VERSION уже установлен"
fi

# Установка системных зависимостей
echo "📦 Установка системных зависимостей..."
if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    apt-get install -y \
        build-essential \
        libpq-dev \
        postgresql-client \
        libffi-dev \
        libssl-dev \
        python3-dev \
        gcc \
        g++
elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
    yum groupinstall -y "Development Tools"
    yum install -y \
        postgresql-devel \
        libffi-devel \
        openssl-devel \
        python3-devel \
        gcc \
        gcc-c++
fi

# Проверка версий
echo ""
echo "📊 Проверка установленных версий:"
echo "   Docker: $(docker --version)"
echo "   Docker Compose: $(docker-compose --version)"
echo "   Python: $(python3 --version)"
echo "   pip: $(python3 -m pip --version 2>/dev/null || echo 'не установлен')"
echo ""

# Создание директории для проекта (если запускается не из директории проекта)
CURRENT_DIR=$(pwd)
if [ ! -f "$CURRENT_DIR/main.py" ]; then
    echo "⚠️  Скрипт запущен не из директории проекта"
    echo "📁 Текущая директория: $CURRENT_DIR"
    read -p "Продолжить установку системных компонентов? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
    echo "✅ Установка системных компонентов завершена!"
    echo "📝 Следующие шаги:"
    echo "   1. Перейдите в директорию проекта"
    echo "   2. Создайте .env файл из .env.example"
    echo "   3. Запустите: docker-compose up -d"
    exit 0
fi

# Установка Python зависимостей (если requirements.txt существует)
if [ -f "requirements.txt" ]; then
    echo "📦 Установка Python зависимостей..."
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    echo "✅ Python зависимости установлены"
else
    echo "⚠️  requirements.txt не найден, пропускаем установку Python зависимостей"
fi

# Проверка .env файла
if [ ! -f ".env" ]; then
    echo "⚠️  .env файл не найден!"
    if [ -f ".env.example" ]; then
        echo "📝 Создаю .env из .env.example..."
        cp .env.example .env
        echo "✅ .env файл создан. Пожалуйста, отредактируйте его и укажите правильные значения!"
    else
        echo "❌ .env.example также не найден. Создайте .env файл вручную."
    fi
else
    echo "✅ .env файл найден"
fi

# Проверка docker-compose.yml
if [ ! -f "docker-compose.yml" ]; then
    echo "⚠️  docker-compose.yml не найден!"
else
    echo "✅ docker-compose.yml найден"
fi

echo ""
echo "🎉 Установка завершена!"
echo ""
echo "📋 Следующие шаги:"
echo "   1. Отредактируйте .env файл и укажите правильные значения"
echo "   2. Запустите бота: docker-compose up -d"
echo "   3. Проверьте логи: docker-compose logs -f bot"
echo ""
echo "📝 Полезные команды:"
echo "   Остановить: docker-compose down"
echo "   Перезапустить: docker-compose restart"
echo "   Логи: docker-compose logs -f"
echo "   Статус: docker-compose ps"
echo ""
