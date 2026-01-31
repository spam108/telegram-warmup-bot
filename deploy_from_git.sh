#!/bin/bash
# Скрипт для развертывания ботов из git на сервере
# Использование: ./deploy_from_git.sh [od1|od1_1bot|both]

set -e

GIT_REPO="https://github.com/spam108/telegram-warmup-bot.git"
BRANCH="q_od1"
OD1_DIR="$HOME/od1"
OD1_1BOT_DIR="$HOME/od1_1bot"

deploy_project() {
    local project_dir=$1
    local project_name=$2
    
    echo "🚀 Развертывание $project_name..."
    
    if [ ! -d "$project_dir" ]; then
        echo "📦 Клонирование репозитория в $project_dir..."
        git clone -b $BRANCH $GIT_REPO "$project_dir"
    else
        echo "🔄 Обновление кода из git..."
        cd "$project_dir"
        git fetch origin
        
        # Сохраняем локальные изменения если есть
        if ! git diff-index --quiet HEAD --; then
            echo "⚠️  Обнаружены локальные изменения, сохраняем их..."
            git stash save "Local changes before deploy $(date +%Y%m%d_%H%M%S)"
        fi
        
        # Переключаемся на нужную ветку
        git checkout $BRANCH 2>/dev/null || git checkout -b $BRANCH origin/$BRANCH
        
        # Обновляем код
        git pull origin $BRANCH
        
        # Восстанавливаем сохраненные изменения (если нужно)
        if git stash list | grep -q "before deploy"; then
            echo "💡 Восстановлены локальные изменения из stash (если нужно, проверьте git stash list)"
        fi
    fi
    
    echo "📝 Проверка .env файла..."
    if [ ! -f "$project_dir/.env" ]; then
        echo "⚠️  .env файл не найден! Создайте его перед запуском."
        if [ -f "$project_dir/env.example" ]; then
            echo "   Используйте: cp $project_dir/env.example $project_dir/.env"
        fi
        return 1
    fi
    
    echo "🐳 Остановка старых контейнеров..."
    cd "$project_dir"
    docker-compose down 2>/dev/null || true
    
    echo "🔨 Сборка и запуск контейнеров..."
    docker-compose up -d --build
    
    echo "✅ $project_name развернут!"
    echo "📊 Проверка статуса:"
    docker-compose ps
    
    echo ""
}

# Определяем что развертывать
DEPLOY_TARGET=${1:-both}

case $DEPLOY_TARGET in
    od1)
        deploy_project "$OD1_DIR" "od1"
        ;;
    od1_1bot)
        deploy_project "$OD1_1BOT_DIR" "od1_1bot"
        ;;
    both|*)
        deploy_project "$OD1_DIR" "od1"
        echo ""
        deploy_project "$OD1_1BOT_DIR" "od1_1bot"
        ;;
esac

echo "🎉 Развертывание завершено!"
echo ""
echo "📋 Полезные команды:"
echo "   Логи od1:    cd $OD1_DIR && docker-compose logs -f bot"
echo "   Логи od1_1bot:  cd $OD1_1BOT_DIR && docker-compose logs -f bot"
echo "   Статус:      docker ps | grep od1"
