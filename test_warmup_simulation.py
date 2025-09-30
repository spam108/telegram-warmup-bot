#!/usr/bin/env python3
"""
Симуляция тестирования логики прогрева аккаунтов
"""

import asyncio
import random
from datetime import datetime, timezone, timedelta, time
from typing import List, Dict, Any

# Копируем константы из main.py
WARMUP_CHANNELS_PER_DAY = 15
WARMUP_DELAY_SECONDS = 10 * 60  # 10 минут между добавлениями
WARMUP_SCAN_INTERVAL_SECONDS = 60  # Проверка каждую минуту
WARMUP_DEFAULT_DAYS = 7
WARMUP_SLEEP_START_HOUR = 4  # Начало периода сна (4:00)
WARMUP_SLEEP_END_HOUR = 6    # Конец периода сна (6:00)

def is_warmup_sleep_period(now: datetime | None = None) -> bool:
    """Проверяет, находимся ли мы в периоде сна для прогрева (04:00 - 06:00)"""
    now = now or datetime.now(timezone.utc)
    current_time = now.time()
    start = time(WARMUP_SLEEP_START_HOUR, 0)
    end = time(WARMUP_SLEEP_END_HOUR, 0)
    result = start <= current_time < end
    return result

class MockAccount:
    def __init__(self, account_id: int, phone: str, user_id: int, mode: str = "warmup"):
        self.id = account_id
        self.phone = phone
        self.user_id = user_id
        self.mode = mode
        self.warmup_end_at = datetime.now(timezone.utc) + timedelta(days=WARMUP_DEFAULT_DAYS)
        self.warmup_joined_today = 0
        self.warmup_last_join_at = None
        self.warmup_channels = []

class MockWarmupChannel:
    def __init__(self, channel: str, position: int, status: str = "pending"):
        self.channel = channel
        self.position = position
        self.status = status
        self.attempts = 0
        self.joined_at = None

class WarmupSimulator:
    def __init__(self):
        self.accounts = []
        self.warmup_channels = []
        self.logs = []
        
    def add_account(self, account_id: int, phone: str, user_id: int):
        """Добавляет аккаунт для тестирования"""
        account = MockAccount(account_id, phone, user_id)
        self.accounts.append(account)
        self.log(f"[OK] Добавлен аккаунт {phone} (ID: {account_id})")
        
    def add_warmup_channels(self, account_id: int, channels: List[str]):
        """Добавляет каналы для прогрева"""
        account = next((acc for acc in self.accounts if acc.id == account_id), None)
        if not account:
            self.log(f"[ERROR] Аккаунт {account_id} не найден")
            return
            
        for i, channel in enumerate(channels, 1):
            warmup_channel = MockWarmupChannel(channel, i)
            self.warmup_channels.append(warmup_channel)
            account.warmup_channels.append(warmup_channel)
            
        self.log(f"[OK] Добавлено {len(channels)} каналов для прогрева аккаунта {account.phone}")
        
    def log(self, message: str):
        """Логирует сообщение с временной меткой"""
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(log_message)
        self.logs.append(log_message)
        
    def simulate_warmup_process(self, test_time: datetime = None):
        """Симулирует процесс прогрева для заданного времени"""
        now = test_time or datetime.now(timezone.utc)
        self.log(f"Симуляция времени: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Проверяем, находимся ли в периоде прогрева
        if not is_warmup_sleep_period(now):
            self.log(f"Вне периода прогрева (04:00-06:00). Текущее время: {now.time()}")
            return False
            
        self.log(f"[OK] В периоде прогрева! Начинаем обработку...")
        
        # Получаем аккаунты в режиме прогрева
        warmup_accounts = [acc for acc in self.accounts if acc.mode == "warmup"]
        self.log(f"Найдено {len(warmup_accounts)} аккаунтов в режиме прогрева")
        
        for account in warmup_accounts:
            self.log(f"Обрабатываем аккаунт {account.phone}")
            
            # ПРИМЕЧАНИЕ: В реальном коде убрано блокирующее условие active_sessions
            # Прогрев теперь работает ПАРАЛЛЕЛЬНО с комментированием
            
            # Проверяем, не истек ли период прогрева
            if account.warmup_end_at <= now:
                self.log(f"Период прогрева истек для аккаунта {account.phone}")
                account.mode = "standard"
                continue
                
            # Сбрасываем дневной счетчик если новый день
            if (account.warmup_last_join_at and 
                account.warmup_last_join_at.date() < now.date()):
                account.warmup_joined_today = 0
                self.log(f"Сброшен дневной счетчик для {account.phone}")
                
            # Проверяем дневной лимит
            if account.warmup_joined_today >= WARMUP_CHANNELS_PER_DAY:
                self.log(f"Достигнут дневной лимит для {account.phone} ({account.warmup_joined_today}/{WARMUP_CHANNELS_PER_DAY})")
                continue
                
            # Получаем следующий канал для добавления
            pending_channels = [ch for ch in account.warmup_channels if ch.status == "pending"]
            if not pending_channels:
                self.log(f"Нет каналов в очереди для {account.phone}")
                continue
                
            # Берем первый канал из очереди
            channel = pending_channels[0]
            self.log(f"Пытаемся вступить в канал: {channel.channel}")
            
            # Симулируем результат вступления
            success = self.simulate_join_channel(channel)
            
            if success:
                channel.status = "joined"
                channel.joined_at = now
                account.warmup_joined_today += 1
                account.warmup_last_join_at = now
                self.log(f"[OK] Успешно вступили в {channel.channel}")
            else:
                channel.attempts += 1
                self.log(f"[ERROR] Ошибка при вступлении в {channel.channel} (попытка {channel.attempts})")
                
        return True
        
    def simulate_join_channel(self, channel: MockWarmupChannel) -> bool:
        """Симулирует попытку вступления в канал с различными исходами"""
        # 60% успеха, 40% ошибок (более реалистично)
        if random.random() < 0.6:
            return True
            
        # Симулируем различные типы ошибок
        error_types = [
            "PRIVATE_CHANNEL",
            "FLOOD_WAIT", 
            "AUTH_ERROR",
            "CHANNEL_ERROR",
            "USERNAME_NOT_OCCUPIED",
            "INVITE_HASH_INVALID"
        ]
        
        error = random.choice(error_types)
        self.log(f"[WARNING] Ошибка {error} для канала {channel.channel}")
        return False
        
    def print_statistics(self):
        """Выводит статистику симуляции"""
        print("\n" + "="*60)
        print("СТАТИСТИКА СИМУЛЯЦИИ ПРОГРЕВА")
        print("="*60)
        
        total_joined = 0
        total_errors = 0
        
        for account in self.accounts:
            joined_count = len([ch for ch in account.warmup_channels if ch.status == "joined"])
            pending_count = len([ch for ch in account.warmup_channels if ch.status == "pending"])
            error_count = len([ch for ch in account.warmup_channels if ch.attempts > 0])
            
            total_joined += joined_count
            total_errors += error_count
            
            print(f"Аккаунт {account.phone}:")
            print(f"  - Режим: {account.mode}")
            print(f"  - Вступил в каналов: {joined_count}")
            print(f"  - В очереди: {pending_count}")
            print(f"  - Ошибок: {error_count}")
            print(f"  - Сегодня вступил: {account.warmup_joined_today}")
            print()
            
        print("ОБЩАЯ СТАТИСТИКА:")
        print(f"  - Всего каналов вступили: {total_joined}")
        print(f"  - Всего ошибок: {total_errors}")
        print(f"  - Процент успеха: {(total_joined / (total_joined + total_errors) * 100):.1f}%" if (total_joined + total_errors) > 0 else "0%")
        print()

def main():
    """Основная функция симуляции"""
    print("СИМУЛЯЦИЯ ТЕСТИРОВАНИЯ ЛОГИКИ ПРОГРЕВА")
    print("="*60)
    
    simulator = WarmupSimulator()
    
    # Добавляем тестовые аккаунты
    simulator.add_account(1, "79639791823", 291299155)
    simulator.add_account(2, "79060419825", 291299155)
    
    # Добавляем каналы для прогрева (больше для тестирования лимитов)
    test_channels_1 = [
        "@test_channel_1", "@test_channel_2", "@test_channel_3", "@test_channel_4", "@test_channel_5",
        "@test_channel_6", "@test_channel_7", "@test_channel_8", "@test_channel_9", "@test_channel_10",
        "@test_channel_11", "@test_channel_12", "@test_channel_13", "@test_channel_14", "@test_channel_15",
        "@test_channel_16", "@test_channel_17", "@test_channel_18", "@test_channel_19", "@test_channel_20"
    ]
    
    test_channels_2 = [
        "@warmup_channel_1", "@warmup_channel_2", "@warmup_channel_3", "@warmup_channel_4", "@warmup_channel_5",
        "@warmup_channel_6", "@warmup_channel_7", "@warmup_channel_8", "@warmup_channel_9", "@warmup_channel_10"
    ]
    
    simulator.add_warmup_channels(1, test_channels_1)
    simulator.add_warmup_channels(2, test_channels_2)
    
    print("\nТЕСТИРУЕМ РАЗНЫЕ ВРЕМЕНА:")
    print("-" * 40)
    
    # Тест 1: Вне периода прогрева
    test_time_1 = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0)
    simulator.simulate_warmup_process(test_time_1)
    
    print("\n" + "-" * 40)
    
    # Тест 2-6: Несколько циклов в периоде прогрева (каждые 10 минут)
    base_time = datetime.now(timezone.utc).replace(hour=4, minute=0, second=0)
    
    for i in range(5):  # 5 циклов по 10 минут
        test_time = base_time + timedelta(minutes=i * 10)
        print(f"\n--- ЦИКЛ {i+1}: {test_time.strftime('%H:%M')} ---")
        simulator.simulate_warmup_process(test_time)
        
        # Симулируем задержку между циклами
        if i < 4:  # Не ждем после последнего цикла
            print(f"[INFO] Ожидание 10 минут до следующего цикла...")
    
    print("\n" + "-" * 40)
    
    # Тест 7: Проверяем дневной лимит (после 15 каналов)
    test_time_limit = base_time + timedelta(minutes=60)  # Через час
    print(f"\n--- ПРОВЕРКА ЛИМИТА: {test_time_limit.strftime('%H:%M')} ---")
    simulator.simulate_warmup_process(test_time_limit)
    
    # Выводим финальную статистику
    simulator.print_statistics()

if __name__ == "__main__":
    main()
