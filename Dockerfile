# Используем официальный Python образ
FROM python:3.12-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаем директорию для сессий
RUN mkdir -p sessions

# Создаем пользователя для безопасности
RUN useradd -m -u 1000 botuser && \
    chown -R botuser:botuser /app
USER botuser

# Открываем порт (если понадобится для веб-интерфейса)
EXPOSE 8000

# Команда запуска
CMD ["python", "main.py"]


