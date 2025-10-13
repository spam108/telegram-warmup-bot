# 🚀 Deployment Guide for Telegram Comment Bot

This guide covers deploying the Telegram Comment Bot to a VPS server using both traditional and Docker methods.

## 📋 Prerequisites

- VPS with Ubuntu 20.04+ or similar Linux distribution
- Root access or sudo privileges
- Git installed
- At least 2GB RAM and 10GB storage

## 🔧 Method 1: Traditional Deployment (Recommended)

### Step 1: Connect to your VPS
```bash
ssh root@your-vps-ip
# or
ssh username@your-vps-ip
```

### Step 2: Run the deployment script
```bash
# Download and run the deployment script
curl -fsSL https://raw.githubusercontent.com/yourusername/telegram-comment-bot/main/deploy.sh | bash

# Or clone the repository first
git clone https://github.com/yourusername/telegram-comment-bot.git
cd telegram-comment-bot
chmod +x deploy.sh
./deploy.sh
```

### Step 3: Configure environment variables
```bash
nano .env
```

Fill in the following variables:
```env
BOT_TOKEN=your_bot_token_here
API_ID=your_api_id
API_HASH=your_api_hash
OPENAI_API_KEY=your_openai_key
PASSWORD=your_admin_password
DATABASE_NAME=pgbot1010
DATABASE_USER=pgbot1010_user
DATABASE_PASSWORD=pgbot1010_password
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_URL=postgresql://${DATABASE_USER}:${DATABASE_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}
DATABASE_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
DATABASE_BACKUP_DIR=/var/lib/telegram-warmup-bot/backups
LOG_CHANNEL_ID=your_log_channel_id
```

### Step 4: Start the bot
```bash
./manage.sh start
./manage.sh status
./manage.sh logs
```

## 🐳 Method 2: Docker Deployment

### Step 1: Install Docker and Docker Compose
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Logout and login again
```

### Step 2: Clone and configure
```bash
git clone https://github.com/yourusername/telegram-comment-bot.git
cd telegram-comment-bot
cp .env.example .env
nano .env  # Configure your variables
```

### Step 3: Deploy with Docker
```bash
./docker-deploy.sh
# or manually:
docker-compose up -d --build
```

### Step 4: Run commands inside the container
```bash
# open a shell inside the bot container
docker-compose exec bot /bin/bash

# from inside the container you can run maintenance utilities, e.g.
python server_test_settings.py
python fix_mode.py
```

### Step 5: Monitor
```bash
docker-compose logs -f
docker-compose ps
```

## 🛠️ Management Commands

### Traditional Deployment
```bash
./manage.sh start      # Start the bot
./manage.sh stop       # Stop the bot
./manage.sh restart    # Restart the bot
./manage.sh status     # Check status
./manage.sh logs       # View logs
./manage.sh update     # Update from Git
```

### Docker Deployment
```bash
docker-compose up -d           # Start all services
docker-compose down            # Stop all services
docker-compose restart bot     # Restart only the bot
docker-compose logs -f bot     # View bot logs
```

## 🔍 Monitoring and Troubleshooting

### Check bot status
```bash
# Traditional
sudo systemctl status CDXBOT0310

# Docker
docker-compose ps
```

### View logs
```bash
# Traditional
sudo journalctl -u CDXBOT0310 -f

# Docker
docker-compose logs -f
```

### Check database
```bash
# Traditional
sqlite3 data/CDXBOT0310.db ".tables"

# Docker
docker-compose exec bot sqlite3 data/CDXBOT0310.db ".tables"
```

## 🔒 Security Considerations

1. **Firewall**: Configure UFW to only allow necessary ports
   ```bash
   sudo ufw allow ssh
   sudo ufw allow 80
   sudo ufw allow 443
   sudo ufw enable
   ```

2. **Environment Variables**: Never commit `.env` file to Git
3. **Database**: Храните файл SQLite в каталоге с ограниченным доступом и выполняйте регулярные резервные копии
4. **Sessions**: Keep session files secure and backed up

## 📊 Performance Optimization

### For high-load scenarios:
1. Периодически запускайте `VACUUM`/`ANALYZE` для оптимизации базы SQLite
2. Use Redis for caching (optional)
3. Scale with multiple bot instances
4. Monitor resource usage

### Resource monitoring:
```bash
# CPU and memory usage
htop

# Disk usage
df -h

# Database size
du -h data/CDXBOT0310.db
```

## 🔄 Updates and Maintenance

### Update the bot:
```bash
# Traditional
./manage.sh update

# Docker
git pull
docker-compose up -d --build
```

### Backup data:
```bash
# Backup database
cp data/CDXBOT0310.db backups/CDXBOT0310_$(date +%Y%m%d).db

# Backup sessions
tar -czf sessions_backup_$(date +%Y%m%d).tar.gz sessions/
```

## 🆘 Common Issues

### Bot not starting:
1. Check `.env` file configuration
2. Verify database connection
3. Check logs for errors
4. Ensure all dependencies are installed

### Database connection errors:
1. Проверьте переменную `DATABASE_URL`
2. Убедитесь, что каталог для файла SQLite существует и доступен для записи
3. Проверьте логи на наличие ошибок блокировки или повреждения файла

### Session errors:
1. Check session file permissions
2. Verify session files are not corrupted
3. Re-authorize accounts if needed

## 📞 Support

If you encounter issues:
1. Check the logs first
2. Verify all environment variables
3. Ensure all dependencies are installed
4. Check the GitHub issues page

---

**Note**: Replace `yourusername/telegram-comment-bot` with your actual GitHub repository URL.
