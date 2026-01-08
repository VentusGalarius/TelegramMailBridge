"""
Конфигурация приложения с использованием Pydantic.
"""
from pydantic import BaseSettings, Field, validator
from pydantic.generics import GenericModel
from typing import Optional, Dict, Any
from pathlib import Path
from configparser import ConfigParser

class TelegramConfig(BaseSettings):
    """Конфигурация Telegram."""
    api_id: int
    api_hash: str
    phone_number: str
    session_name: str = "telegram_mail_bridge"
    test_mode: bool = True
    
class SMTPConfig(BaseSettings):
    """Конфигурация SMTP сервера."""
    host: str = "0.0.0.0"
    port: int = 1025
    auth_required: bool = True
    auth_username: str = "telegram_user"
    auth_password: str = "complex_password_123!"
    tls_enabled: bool = False
    tls_certfile: Optional[str] = None
    tls_keyfile: Optional[str] = None

class RedisConfig(BaseSettings):
    """Конфигурация Redis."""
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    db: int = 0
    decode_responses: bool = False
    message_ttl: int = 604800  # 7 дней

class DNSConfig(BaseSettings):
    """Конфигурация DNS."""
    target_domain: str = "t.me"
    timeout: int = 10
    lifetime: int = 30

class CloudflareConfig(BaseSettings):
    """Конфигурация Cloudflare."""
    api_token: Optional[str] = None
    zone_id: Optional[str] = None
    domain: Optional[str] = None

class LoggingConfig(BaseSettings):
    """Конфигурация логгирования."""
    level: str = "INFO"
    file: str = "mail_bridge.log"

class AppConfig:
    """Основная конфигурация приложения."""
    
    def __init__(self):
        self.telegram = TelegramConfig()
        self.smtp = SMTPConfig()
        self.redis = RedisConfig()
        self.dns = DNSConfig()
        self.cloudflare = CloudflareConfig()
        self.logging = LoggingConfig()
    
    @classmethod
    def from_ini_file(cls, path: Path) -> "AppConfig":
        """Загрузка конфигурации из INI файла."""
        config = cls()
        parser = ConfigParser()
        parser.read(path)
        
        # Загрузка секций
        if 'TELEGRAM' in parser:
            config.telegram = TelegramConfig(
                **dict(parser['TELEGRAM'])
            )
        
        if 'SMTP' in parser:
            config.smtp = SMTPConfig(
                **dict(parser['SMTP'])
            )
        
        if 'REDIS' in parser:
            config.redis = RedisConfig(
                **dict(parser['REDIS'])
            )
        
        if 'CLOUDFLARE' in parser:
            config.cloudflare = CloudflareConfig(
                **dict(parser['CLOUDFLARE'])
            )
        
        return config
    
    def dict(self) -> Dict[str, Any]:
        """Преобразование в словарь."""
        return {
            'telegram': self.telegram.dict(),
            'smtp': self.smtp.dict(),
            'redis': self.redis.dict(),
            'dns': self.dns.dict(),
            'cloudflare': self.cloudflare.dict(),
            'logging': self.logging.dict()
        }