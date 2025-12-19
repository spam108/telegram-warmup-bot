#!/bin/bash
# Quick deployment script for Telegram Comment Bot
# Usage: ./quick_deploy.sh

set -e

echo "🚀 Quick Deployment Script for Telegram Comment Bot"
echo "=================================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env from env.example..."
    if [ -f env.example ]; then
        cp env.example .env
        echo "✅ .env file created. Please edit it with your configuration."
        echo "⚠️  Required variables:"
        echo "   - BOT_TOKEN"
        echo "   - API_ID"
        echo "   - API_HASH"
        echo "   - DATABASE_URL (or DB_* variables)"
        echo "   - OPENAI_API_KEY"
        echo "   - PASSWORD"
        exit 1
    else
        echo "❌ env.example not found. Please create .env manually."
        exit 1
    fi
fi

# Load environment variables
source .env

# Check required variables
REQUIRED_VARS=("BOT_TOKEN" "API_ID" "API_HASH")
MISSING_VARS=()

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
    echo "❌ Missing required environment variables: ${MISSING_VARS[*]}"
    echo "Please edit .env file and set all required variables."
    exit 1
fi

# Check Python version
echo "🐍 Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "   Python version: $PYTHON_VERSION"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "📥 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p sessions logs data

# Initialize database
echo "🗄️  Initializing database..."
if [ -n "$DATABASE_URL" ]; then
    echo "   Using DATABASE_URL: ${DATABASE_URL%%:*}"
    python3 -c "
import asyncio
import os
from db import init_db, ensure_warmup_settings

async def setup():
    await init_db()
    await ensure_warmup_settings(
        channels_per_day=15,
        delay_minutes=7,
        join_start_hour=9,
        join_start_minute=0,
        join_end_hour=21,
        join_end_minute=0,
    )
    print('✅ Database initialized')

asyncio.run(setup())
" || echo "⚠️  Database initialization failed. Please check your DATABASE_URL."
else
    echo "⚠️  DATABASE_URL not set. Please configure database connection."
fi

echo ""
echo "✅ Deployment setup complete!"
echo ""
echo "📋 Next steps:"
echo "   1. Review and edit .env file if needed"
echo "   2. Run the bot: python3 main.py"
echo "   3. Or use systemd service: sudo systemctl start CDXBOT0310"
echo ""
echo "📖 For more information, see DEPLOYMENT.md"

