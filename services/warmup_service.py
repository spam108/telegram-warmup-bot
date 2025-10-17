import asyncio
import logging
from datetime import datetime


class WarmupService:
    def __init__(self):
        self.is_running = False
        self.accounts_stopped = False

    async def stop_all_accounts(self):
        """Временно останавливает все аккаунты (устанавливает флаг)"""
        self.accounts_stopped = True
        logging.info("🛑 Все аккаунты остановлены для прогрева")

    async def start_all_accounts(self):
        """Запускает аккаунты обратно"""
        self.accounts_stopped = False
        logging.info("🟢 Все аккаунты запущены после прогрева")

    async def can_account_operate(self, account_id):
        """Проверяет может ли аккаунт работать (не в режиме прогрева)"""
        return not self.accounts_stopped

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

            except Exception as e:
                logging.error(f"❌ Ошибка в сервисе прогрева: {e}")
                await asyncio.sleep(30)

    async def stop(self):
        """Остановка сервиса"""
        self.is_running = False
        logging.info("🛑 Сервис прогрева остановлен")
