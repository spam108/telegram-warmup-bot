@echo off
echo Обновляем сервер с исправлениями...

echo 1. Останавливаем контейнеры на сервере
ssh root@ap0678 "cd ~/telegram-bot && sudo docker-compose down"

echo 2. Обновляем код на сервере
ssh root@ap0678 "cd ~/telegram-bot && git fetch origin && git reset --hard origin/st.0"

echo 3. Пересобираем и запускаем контейнеры
ssh root@ap0678 "cd ~/telegram-bot && sudo docker-compose build --no-cache && sudo docker-compose up -d"

echo 4. Проверяем статус
ssh root@ap0678 "cd ~/telegram-bot && sudo docker-compose ps"

echo Обновление завершено!
pause
