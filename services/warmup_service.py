import asyncio
import logging
from datetime import datetime


class WarmupService:
    def __init__(self):
        self.is_running = False
        self.accounts_stopped = False

    async def run(self):
        """Основной цикл сервиса"""
        self.is_running = True
        logging.info("🚀 Сервис прогрева запущен")

        while self.is_running:
            try:
                logging.info("🔄 Начинаем цикл прогрева...")

                # Здесь будет логика прогрева
                await asyncio.sleep(10)  # Временная заглушка

                logging.info("💤 Цикл завершен, ждем 1 минуту (для теста)")
                await asyncio.sleep(60)  # 1 минута для теста

            except asyncio.CancelledError:
                logging.info("🛑 Сервис прогрева отменен")
                raise
            except Exception as e:
                logging.error(f"❌ Ошибка в сервисе прогрева: {e}")
                await asyncio.sleep(30)

    async def stop(self):
        """Остановка сервиса"""
        self.is_running = False
        logging.info("🛑 Сервис прогрева остановлен")
