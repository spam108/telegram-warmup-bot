import asyncio
import logging
from datetime import datetime


class WarmupService:
    def __init__(self):
        self.is_running = False
        self.accounts_stopped = False

    async def execute_warmup_cycle(self):
        """Выполняет цикл прогрева для всех аккаунтов"""
        try:
            from db import (
                get_running_warmup_accounts,
                get_warmup_pending,
                mark_warmup_channel_joined,
            )

            # 1. Получаем аккаунты для прогрева
            accounts = await get_running_warmup_accounts()
            logging.info(f"🎯 Начинаем прогрев для {len(accounts)} аккаунтов")

            # 2. Для каждого аккаунта обрабатываем pending каналы
            for account in accounts:
                try:
                    account_id = account.get("id")
                    phone = account.get("phone", "unknown")

                    if account_id is None:
                        logging.warning(
                            "⚠️ Пропускаем аккаунт без идентификатора при прогреве"
                        )
                        continue

                    pending_channels = await get_warmup_pending(account_id, limit=1)
                    if not pending_channels:
                        logging.debug(
                            f"⏭️ Для аккаунта {phone} нет каналов для прогрева"
                        )
                        continue

                    channel = pending_channels[0]
                    channel_name = channel.get("channel")
                    logging.info(
                        f"📺 Аккаунт {phone} вступает в {channel_name}"
                    )

                    # 3. Временная заглушка - всегда успешное вступление
                    success = True

                    if success and channel_name:
                        # 4. Обновляем БД при успешном вступлении
                        await mark_warmup_channel_joined(account_id, channel_name)
                        logging.info(
                            f"✅ {phone} успешно вступил в {channel_name}"
                        )
                    elif not channel_name:
                        logging.warning(
                            f"⚠️ Для аккаунта {phone} не указан канал для вступления"
                        )
                    else:
                        logging.warning(
                            f"⚠️ {phone} не смог вступить в {channel_name}"
                        )

                except Exception as e:
                    logging.error(
                        f"❌ Ошибка прогрева аккаунта {account.get('phone', 'unknown')}: {e}"
                    )

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
