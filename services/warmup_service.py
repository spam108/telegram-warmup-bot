import asyncio
import logging
from datetime import datetime


async def retry_on_lock(operation, max_retries=3, delay=1.0):
    """Повторяет операцию при блокировке БД"""
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                logging.warning(f"🔒 Блокировка БД, повтор {attempt + 1}/{max_retries}")
                await asyncio.sleep(delay * (attempt + 1))
            else:
                raise e
    return None


class WarmupService:
    def __init__(self):
        self.is_running = False
        self.accounts_stopped = False

    async def execute_warmup_cycle(self):
        """Выполняет цикл прогрева для всех аккаунтов с защитой от блокировок"""
        try:
            from db import (
                get_running_warmup_accounts,
                get_warmup_pending,
            )
            from main import join_channel

            # 1. Получаем аккаунты для прогрева
            accounts = await get_running_warmup_accounts()
            logging.info(f"🎯 Начинаем прогрев для {len(accounts)} аккаунтов")

            # 2. Для каждого аккаунта обрабатываем pending каналы С ПАУЗАМИ
            for i, account in enumerate(accounts):
                try:
                    # ПАУЗА между аккаунтами для снижения нагрузки на БД
                    if i > 0:
                        await asyncio.sleep(2)  # 2 секунды между аккаунтами

                    account_id = account.get("id")
                    phone = account.get("phone", "unknown")

                    if account_id is None:
                        logging.warning("⚠️ Пропускаем аккаунт без идентификатора")
                        continue

                    # ИСПОЛЬЗУЕМ ПОВТОРНЫЕ ПОПЫТКИ для получения каналов
                    pending_channels = await retry_on_lock(
                        lambda: get_warmup_pending(account_id, limit=1),
                        max_retries=2,
                        delay=0.5
                    )

                    if not pending_channels:
                        logging.debug(f"⏭️ Для {phone} нет каналов для прогрева")
                        continue

                    channel = pending_channels[0]
                    channel_name = channel.get("channel")

                    if not channel_name:
                        logging.warning(f"⚠️ Для {phone} не указан канал")
                        continue

                    session_key = account.get("phone")
                    user_id = account.get("user_id")

                    if not session_key or user_id is None:
                        logging.warning(f"⚠️ Для {phone} отсутствуют данные сессии")
                        continue

                    logging.info(f"📺 {phone} вступает в {channel_name}")

                    # ИСПОЛЬЗУЕМ ПОВТОРНЫЕ ПОПЫТКИ для вступления в канал
                    success, error_message = await retry_on_lock(
                        lambda: join_channel(
                            channel=channel_name,
                            account_id=account_id,
                            session_key=session_key,
                            user_id=user_id,
                            is_warmup=True,
                            acquire_lock=False,
                        ),
                        max_retries=2,
                        delay=1.0
                    )

                    if success:
                        logging.info(f"✅ {phone} успешно вступил в {channel_name}")
                    else:
                        logging.warning(f"⚠️ {phone} не смог вступить в {channel_name}: {error_message}")

                except Exception as e:
                    logging.error(f"❌ Ошибка прогрева {account.get('phone', 'unknown')}: {e}")

        except Exception as e:
            logging.error(f"💥 Критическая ошибка в цикле прогрева: {e}")

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

                await self.execute_warmup_cycle()

                logging.info("💤 Цикл прогрева завершен, ждем 1 час")
                await asyncio.sleep(3600)  # 1 час между циклами

            except Exception as e:
                logging.error(f"❌ Ошибка в сервисе прогрева: {e}")
                await asyncio.sleep(300)  # 5 минут при ошибке

    async def stop(self):
        """Остановка сервиса"""
        self.is_running = False
        logging.info("🛑 Сервис прогрева остановлен")
