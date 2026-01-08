import pickle
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import aioredis
from email import message_from_bytes
from email.policy import default

class RedisStorage:
    """Продвинутое хранилище для полных писем в Redis с сериализацией и TTL."""
    
    def __init__(self, config: Dict[str, Any], logger):
        self.config = config
        self.logger = logger
        self.redis: Optional[aioredis.Redis] = None
        self.message_ttl = int(config.get('message_ttl', 604800))
        
    async def connect(self):
        """Установка соединения с Redis."""
        try:
            self.redis = await aioredis.from_url(
                f"redis://{self.config['host']}:{self.config['port']}",
                password=self.config.get('password') or None,
                db=int(self.config.get('db', 0)),
                decode_responses=False,
                encoding='utf-8'
            )
            await self.redis.ping()
            self.logger.info("Успешное подключение к Redis")
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Redis: {e}")
            raise
    
    async def store_email(self, 
                         email_id: str, 
                         raw_email: bytes,
                         metadata: Dict[str, Any]) -> bool:
        """Сохранение полного письма и метаданных в Redis."""
        try:
            pipeline = self.redis.pipeline()
            
            # Сериализация письма с помощью pickle
            serialized_email = pickle.dumps(raw_email)
            
            # Основное хранилище письма
            pipeline.setex(
                f"email:raw:{email_id}",
                self.message_ttl,
                serialized_email
            )
            
            # Метаданные в JSON
            pipeline.setex(
                f"email:meta:{email_id}",
                self.message_ttl,
                json.dumps(metadata, ensure_ascii=False)
            )
            
            # Индексация по получателю
            recipient = metadata.get('recipient_domain', 'unknown')
            pipeline.sadd(f"index:domain:{recipient}", email_id)
            
            # Индексация по времени
            timestamp = datetime.utcnow().isoformat()
            pipeline.zadd("index:timestamp", {email_id: datetime.utcnow().timestamp()})
            
            await pipeline.execute()
            
            # Автоматическая очистка старых индексов
            await self._cleanup_old_indexes()
            
            self.logger.debug(f"Письмо {email_id} сохранено в Redis")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка сохранения письма {email_id}: {e}")
            return False
    
    async def retrieve_email(self, email_id: str) -> Optional[Dict[str, Any]]:
        """Полное извлечение письма и метаданных."""
        try:
            pipeline = self.redis.pipeline()
            pipeline.get(f"email:raw:{email_id}")
            pipeline.get(f"email:meta:{email_id}")
            results = await pipeline.execute()
            
            raw_email, meta_json = results
            
            if not raw_email or not meta_json:
                return None
            
            # Десериализация письма
            email_bytes = pickle.loads(raw_email)
            metadata = json.loads(meta_json)
            
            # Парсинг письма
            email_msg = message_from_bytes(email_bytes, policy=default)
            
            return {
                'raw': email_bytes,
                'message': email_msg,
                'metadata': metadata,
                'parsed': self._parse_email_structure(email_msg)
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка извлечения письма {email_id}: {e}")
            return None
    
    async def search_emails(self, 
                           domain: Optional[str] = None,
                           limit: int = 50,
                           offset: int = 0) -> List[Dict[str, Any]]:
        """Поиск писем по домену получателя."""
        try:
            if domain:
                email_ids = await self.redis.smembers(f"index:domain:{domain}")
            else:
                # Получаем последние письма по времени
                email_ids = await self.redis.zrevrange(
                    "index:timestamp", 
                    offset, 
                    offset + limit - 1
                )
            
            emails = []
            for email_id in list(email_ids)[:limit]:
                email_data = await self.retrieve_email(email_id.decode() 
                                                      if isinstance(email_id, bytes) 
                                                      else email_id)
                if email_data:
                    emails.append(email_data)
            
            return emails
            
        except Exception as e:
            self.logger.error(f"Ошибка поиска писем: {e}")
            return []
    
    async def _cleanup_old_indexes(self):
        """Очистка устаревших индексов."""
        try:
            cutoff = datetime.utcnow().timestamp() - self.message_ttl
            await self.redis.zremrangebyscore("index:timestamp", 0, cutoff)
        except Exception as e:
            self.logger.warning(f"Ошибка очистки индексов: {e}")
    
    def _parse_email_structure(self, email_msg) -> Dict[str, Any]:
        """Детальный парсинг структуры письма."""
        structure = {
            'headers': dict(email_msg.items()),
            'parts': [],
            'attachments': [],
            'body': {
                'plain': None,
                'html': None
            }
        }
        
        if email_msg.is_multipart():
            for part in email_msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                part_info = {
                    'content_type': content_type,
                    'content_disposition': content_disposition,
                    'size': len(part.get_payload(decode=True) or b'')
                }
                
                if "attachment" in content_disposition:
                    structure['attachments'].append(part_info)
                elif content_type == "text/plain":
                    structure['body']['plain'] = part.get_payload(decode=True)
                elif content_type == "text/html":
                    structure['body']['html'] = part.get_payload(decode=True)
                else:
                    structure['parts'].append(part_info)
        else:
            content_type = email_msg.get_content_type()
            if content_type == "text/plain":
                structure['body']['plain'] = email_msg.get_payload(decode=True)
            elif content_type == "text/html":
                structure['body']['html'] = email_msg.get_payload(decode=True)
        
        return structure
    
    async def disconnect(self):
        """Корректное отключение от Redis."""
        if self.redis:
            await self.redis.close()
            self.logger.info("Отключено от Redis")