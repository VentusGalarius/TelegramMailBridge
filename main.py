#!/usr/bin/env python3
"""
Основной модуль запуска Telegram Mail Bridge System.
Запуск: python main.py
"""

import asyncio
import sys
from pathlib import Path
import signal

# Добавление пути для импорта модулей
sys.path.insert(0, str(Path(__file__).parent))

from core.app_config import AppConfig
from core.telegram_client import EnhancedTelegramClient
from core.dns_validator import DNSSystemValidator
from core.cf_dns_manager import CloudflareDNSManager
from core.redis_storage import RedisStorage
from services.smtp_handler import AdvancedSMTPHandler
from services.telegram_handler import TelegramCommandHandler
from aiosmtpd.controller import Controller
import logging

class TelegramMailBridge:
    """Основной класс приложения."""
    
    def __init__(self, config_path: Path):
        # Конфигурация
        self.config = AppConfig.from_ini_file(config_path)
        
        # Логгирование
        self.logger = self._setup_logging()
        
        # Компоненты системы
        self.redis_storage = None
        self.tg_client = None
        self.dns_validator = None
        self.cf_manager = None
        self.smtp_handler = None
        self.tg_handler = None
        self.smtp_controller = None
        
        # Флаги состояния
        self.is_running = False
        
    def _setup_logging(self):
        """Настройка продвинутого логгирования."""
        logger = logging.getLogger("TelegramMailBridge")
        logger.setLevel(getattr(logging, self.config.logging.level))
        
        # Форматтер
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - '
            '[%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Файловый обработчик
        file_handler = logging.FileHandler(
            self.config.logging.file,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Консольный обработчик
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    async def initialize_components(self):
        """Инициализация всех компонентов системы."""
        self.logger.info("Инициализация компонентов...")
        
        try:
            # 1. Redis хранилище
            self.logger.info("Инициализация Redis...")
            self.redis_storage = RedisStorage(
                self.config.redis.dict(),
                self.logger
            )
            await self.redis_storage.connect()
            
            # 2. DNS валидатор (передаётся извне)
            self.logger.info("Инициализация DNS валидатора...")
            self.dns_validator = DNSSystemValidator(
                self.config.dns.dict(),
                self.logger
            )
            
            # 3. Cloudflare DNS менеджер
            if all([self.config.cloudflare.api_token,
                   self.config.cloudflare.zone_id,
                   self.config.cloudflare.domain]):
                self.logger.info("Инициализация Cloudflare DNS менеджера...")
                self.cf_manager = CloudflareDNSManager(
                    self.config.cloudflare.dict(),
                    self.logger
                )
            
            # 4. Telegram клиент
            self.logger.info("Инициализация Telegram клиента...")
            self.tg_client = EnhancedTelegramClient(
                self.config.telegram.dict(),
                self.logger
            )
            await self.tg_client.connect()
            
            # 5. SMTP обработчик
            self.logger.info("Инициализация SMTP обработчика...")
            self.smtp_handler = AdvancedSMTPHandler(
                telegram_client=self.tg_client.client,
                redis_storage=self.redis_storage,
                dns_validator=self.dns_validator,
                cf_manager=self.cf_manager,
                logger=self.logger
            )
            
            # 6. Контроллер SMTP сервера
            self.logger.info("Запуск SMTP сервера...")
            self.smtp_controller = Controller(
                self.smtp_handler,
                hostname=self.config.smtp.host,
                port=self.config.smtp.port
            )
            
            # Настройка аутентификации
            if self.config.smtp.auth_required:
                from aiosmtpd.smtp import AuthResult
                
                async def authenticator(server, session, envelope, mechanism, auth_data):
                    if (mechanism == 'PLAIN' and 
                        auth_data.login == self.config.smtp.auth_username.encode() and
                        auth_data.password == self.config.smtp.auth_password.encode()):
                        return AuthResult(success=True)
                    return AuthResult(success=False)
                
                self.smtp_controller.authenticator = authenticator
                self.smtp_controller.auth_required = True
            
            self.smtp_controller.start()
            
            # 7. Обработчик команд Telegram
            self.logger.info("Инициализация обработчика команд...")
            self.tg_handler = TelegramCommandHandler(
                telegram_client=self.tg_client.client,
                redis_storage=self.redis_storage,
                smtp_handler=self.smtp_handler,
                logger=self.logger
            )
            
            self.logger.info("Все компоненты инициализированы")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации: {e}", exc_info=True)
            await self.shutdown()
            return False
    
    async def run(self):
        """Основной цикл работы приложения."""
        try:
            self.logger.info("Запуск Telegram Mail Bridge System...")
            self.is_running = True
            
            # Инициализация
            if not await self.initialize_components():
                return
            
            # Проверка DNS
            self.logger.info("Проверка DNS конфигурации...")
            dns_report = await self.dns_validator.validate_domain_mx(
                self.config.dns.target_domain
            )
            
            if dns_report.get('has_mx'):
                self.logger.info("DNS конфигурация в порядке")
            else:
                self.logger.warning("DNS требует настройки!")
            
            # Информация о системе
            self.logger.info(f"SMTP сервер запущен на {self.config.smtp.host}:{self.config.smtp.port}")
            self.logger.info(f"Telegram сессия: {self.config.telegram.session_name}")
            self.logger.info(f"Redis хранилище: {self.config.redis.host}:{self.config.redis.port}")
            
            if self.cf_manager:
                self.logger.info(f"Cloudflare DNS: {self.config.cloudflare.domain}")
            
            # Основной цикл
            self.logger.info("Система готова к работе")
            
            # Ожидание сигналов завершения
            loop = asyncio.get_event_loop()
            stop_event = asyncio.Event()
            
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig, 
                    lambda: stop_event.set()
                )
            
            await stop_event.wait()
            self.logger.info("Получен сигнал завершения")
            
        except KeyboardInterrupt:
            self.logger.info("Прервано пользователем")
        except Exception as e:
            self.logger.error(f"Критическая ошибка: {e}", exc_info=True)
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        """Корректное завершение работы всех компонентов."""
        if not self.is_running:
            return
        
        self.logger.info("Завершение работы...")
        self.is_running = False
        
        shutdown_tasks = []
        
        # Остановка SMTP сервера
        if self.smtp_controller:
            self.logger.info("Остановка SMTP сервера...")
            self.smtp_controller.stop()
        
        # Отключение Telegram
        if self.tg_client:
            self.logger.info("Отключение от Telegram...")
            await self.tg_client.disconnect()
        
        # Закрытие Cloudflare клиента
        if self.cf_manager:
            self.logger.info("Закрытие Cloudflare соединения...")
            await self.cf_manager.close()
        
        # Отключение Redis
        if self.redis_storage:
            self.logger.info("Отключение от Redis...")
            await self.redis_storage.disconnect()
        
        # Ожидание завершения всех задач
        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        
        self.logger.info("Все компоненты остановлены. Выход.")

def main():
    """Точка входа в приложение."""
    print("=" * 60)
    print("Telegram Mail Bridge System")
    print("Полная реализация почтовой системы через Telegram")
    print("=" * 60)
    
    # Проверка конфигурации
    config_path = Path("config.ini")
    if not config_path.exists():
        print(f"\n❌ Конфигурационный файл не найден: {config_path}")
        print("\nСоздайте файл config.ini (шаблон в README)")
        sys.exit(1)
    
    try:
        # Создание и запуск приложения
        app = TelegramMailBridge(config_path)
        
        # Запуск асинхронного event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(app.run())
        finally:
            loop.close()
            
    except Exception as e:
        print(f"\n❌ Фатальная ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()