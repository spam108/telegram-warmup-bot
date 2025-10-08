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

echo "📦 Project: $PROJECT_NAME"
echo "🗄️ Database: $POSTGRES_DB"

# Deploy
docker-compose down
docker-compose up -d --build

echo "✅ Deployment completed!"
echo "📊 Check logs: docker-compose logs -f"
