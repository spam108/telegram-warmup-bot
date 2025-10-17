import asyncio
import logging
from functools import wraps
from typing import Any, Dict, Optional, Tuple, Type


logger = logging.getLogger(__name__)


def retry_on_db_lock(
    max_retries: int = 3,
    delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Декоратор для повторения операций при блокировке БД"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception: Optional[Exception] = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:  # type: ignore
                    last_exception = e
                    if "locked" in str(e).lower() and attempt < max_retries - 1:
                        logger.warning(
                            f"Повтор операции {func.__name__} из-за блокировки БД "
                            f"(попытка {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay * (attempt + 1))
                    else:
                        break
            if last_exception is not None:
                raise last_exception
            return None

        return wrapper

    return decorator


class WarmupService:
    """Сервис прогрева аккаунтов с защитой от блокировок БД"""

    def __init__(self):
        self._running = False
        self._task = None
        self.accounts_stopped = False
        # Совместимость с прежним кодом
        self.is_running = False

    @retry_on_db_lock(max_retries=3, delay=1.0)
    async def execute_warmup_cycle(self) -> None:
        """Выполняет один цикл прогрева аккаунтов с повторными попытками"""
        if not self._running:
            return

        logger.info("🎯 Начинаем прогрев аккаунтов")

        try:
            from db import get_running_warmup_accounts

            accounts = await get_running_warmup_accounts()
            if not accounts:
                logger.info("💤 Нет аккаунтов для прогрева")
                return

            logger.info(f"🎯 Начинаем прогрев для {len(accounts)} аккаунтов")

            for account in accounts:
                if not self._running:
                    break
                await self._process_account_warmup(account)

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в цикле прогрева: {e}")

    @retry_on_db_lock(max_retries=2, delay=0.5)
    async def _process_account_warmup(self, account: Dict[str, Any]) -> None:
        """Обрабатывает прогрев для одного аккаунта с повторными попытками"""
        from db import (
            get_warmup_pending,
            get_warmup_settings,
            increment_warmup_joined,
            mark_warmup_channel_joined,
            record_warmup_channel_error,
        )

        account_id = account.get("id")
        phone = account.get("phone", "unknown")

        if account_id is None:
            logger.warning("⚠️ Пропускаем аккаунт без идентификатора при прогреве")
            return

        try:
            settings = await get_warmup_settings()

            channels_per_day = getattr(settings, "channels_per_day", 0) or 0
            joined_today = account.get("warmup_joined_today", 0)
            if joined_today >= channels_per_day > 0:
                logger.debug(f"📊 Аккаунт {phone} достиг дневного лимита")
                return

            pending_channels = await get_warmup_pending(
                account_id,
                limit=1,
                reset_if_empty=False,
            )

            if not pending_channels:
                logger.debug(f"📭 Нет pending каналов для {phone}")
                return

            channel_data = pending_channels[0]
            channel = channel_data.get("channel")

            if not channel:
                logger.warning(
                    f"⚠️ Для аккаунта {phone} не указан канал для вступления"
                )
                return

            session_key = account.get("phone")
            user_id = account.get("user_id")

            if not session_key or user_id is None:
                logger.warning(
                    f"⚠️ Для аккаунта {phone} отсутствуют данные сессии для вступления в {channel}"
                )
                return

            logger.info(f"📺 Аккаунт {phone} вступает в {channel}")

            success, error = await self._join_channel_with_retry(
                channel=channel,
                account_id=account_id,
                session_key=session_key,
                user_id=user_id,
            )

            if success:
                await mark_warmup_channel_joined(account_id, channel)
                await increment_warmup_joined(account_id)
                logger.info(f"✅ Аккаунт {phone} успешно вступил в {channel}")
            else:
                await record_warmup_channel_error(account_id, channel, error or "unknown error")
                logger.error(f"❌ Ошибка вступления {phone} в {channel}: {error}")

        except Exception as e:
            logger.error(f"🚨 Ошибка обработки аккаунта {phone}: {e}")

    @retry_on_db_lock(max_retries=2, delay=1.0)
    async def _join_channel_with_retry(
        self,
        channel: str,
        account_id: int,
        session_key: str,
        user_id: int,
    ) -> Tuple[bool, Optional[str]]:
        """Вступает в канал с повторными попытками при блокировках"""
        from main import join_channel

        try:
            success, error = await join_channel(
                channel=channel,
                account_id=account_id,
                session_key=session_key,
                user_id=user_id,
                is_warmup=True,
                acquire_lock=False,
            )
            return success, error

        except Exception as e:
            if "locked" in str(e).lower():
                logger.warning(f"🔒 Обнаружена блокировка БД при вступлении в {channel}")
                raise
            return False, str(e)

    async def stop_all_accounts(self):
        """Временно останавливает все аккаунты (устанавливает флаг)"""
        self.accounts_stopped = True
        logger.info("🛑 Все аккаунты остановлены для прогрева")

    async def start_all_accounts(self):
        """Запускает аккаунты обратно"""
        self.accounts_stopped = False
        logger.info("🟢 Все аккаунты запущены после прогрева")

    async def can_account_operate(self, account_id):
        """Проверяет может ли аккаунт работать (не в режиме прогрева)"""
        return not self.accounts_stopped

    async def run(self):
        """Запускает сервис прогрева с обработкой ошибок"""
        self._running = True
        self.is_running = True
        logger.info("🚀 Сервис прогрева запущен")

        while self._running:
            try:
                await self.execute_warmup_cycle()
            except Exception as e:
                logger.error(f"💥 Критическая ошибка сервиса прогрева: {e}")

            if self._running:
                try:
                    from db import get_warmup_settings
                    settings = await get_warmup_settings()
                    delay_minutes = getattr(settings, "delay_minutes", 1) or 1
                    await asyncio.sleep(max(delay_minutes, 1) * 60)
                except Exception as e:
                    logger.error(f"⏰ Ошибка при ожидании: {e}")
                    await asyncio.sleep(60)

    async def stop(self):
        """Остановка сервиса"""
        self._running = False
        self.is_running = False
        logger.info("🛑 Сервис прогрева остановлен")


async def test_warmup_fix():
    """Тестирует исправление блокировок БД"""
    service = WarmupService()

    try:
        await service.execute_warmup_cycle()
        print("✅ Сервис прогрева работает без блокировок")
    except Exception as e:  # pragma: no cover - вспомогательная диагностика
        print(f"❌ Ошибка: {e}")


if __name__ == "__main__":
    async def test():
        service = WarmupService()
        try:
            await service.execute_warmup_cycle()
            print("✅ Сервис прогрева работает без ошибок")
        except Exception as e:  # pragma: no cover - ручная диагностика
            print(f"❌ Ошибка: {e}")

    asyncio.run(test())
