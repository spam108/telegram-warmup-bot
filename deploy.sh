#!/bin/bash
set -e

echo "🚀 Starting deployment..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found. Copy .env.example and configure variables."
    exit 1
fi

# Load environment
source .env

# Apply defaults if some variables are missing in .env
PROJECT_NAME=${PROJECT_NAME:-telegram_bot}
POSTGRES_DB=${POSTGRES_DB:-telegram_bot}
POSTGRES_USER=${POSTGRES_USER:-bot_user}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-bot_password}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

echo "📦 Project: $PROJECT_NAME"
echo "🗄️ Database: $POSTGRES_DB"

# Deploy
docker-compose down
docker-compose up -d --build

echo "✅ Deployment completed!"
echo "📊 Check logs: docker-compose logs -f"
