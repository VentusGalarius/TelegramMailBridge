import httpx
import asyncio
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import dns.resolver

class CloudflareDNSManager:
    """Продвинутый менеджер DNS записей через Cloudflare API."""
    
    def __init__(self, config: Dict[str, Any], logger):
        self.config = config
        self.logger = logger
        self.api_token = config['api_token']
        self.zone_id = config['zone_id']
        self.domain = config['domain']
        self.base_url = f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        self.client = httpx.AsyncClient(
            headers=self.headers,
            timeout=30.0
        )
    
    async def create_mx_record(self, 
                              subdomain: str,
                              priority: int = 10,
                              target: str = "mail.example.com") -> bool:
        """Создание MX записи для поддомена."""
        try:
            record_name = f"{subdomain}.{self.domain}" if subdomain else self.domain
            
            data = {
                "type": "MX",
                "name": record_name,
                "content": target,
                "priority": priority,
                "ttl": 300,
                "proxied": False
            }
            
            response = await self.client.post(
                f"{self.base_url}/dns_records",
                json=data
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    self.logger.info(f"Создана MX запись для {record_name}")
                    return True
            
            self.logger.error(f"Ошибка создания MX: {response.text}")
            return False
            
        except Exception as e:
            self.logger.error(f"Исключение при создании MX: {e}")
            return False
    
    async def ensure_tmail_integration(self, 
                                      telegram_username: str) -> Dict[str, Any]:
        """Настройка DNS для интеграции t.me адреса с почтой."""
        try:
            # Проверка существующих записей
            existing_records = await self.get_dns_records()
            
            # Создание поддомена для Telegram
            subdomain = f"telegram.{telegram_username.replace('@', '')}"
            
            # Создание MX записей
            mx_servers = [
                {"priority": 10, "target": f"mx1.{self.domain}"},
                {"priority": 20, "target": f"mx2.{self.domain}"}
            ]
            
            mx_results = []
            for mx in mx_servers:
                success = await self.create_mx_record(
                    subdomain=subdomain,
                    priority=mx['priority'],
                    target=mx['target']
                )
                mx_results.append({
                    'server': mx['target'],
                    'priority': mx['priority'],
                    'success': success
                })
            
            # Создание TXT записи для верификации
            txt_success = await self.create_txt_record(
                subdomain=subdomain,
                content=f"telegram-mail-verify={telegram_username}"
            )
            
            # Создание CNAME для t.me (если требуется)
            cname_success = await self.create_cname_record(
                name=f"{telegram_username}.tmail",
                target=f"{subdomain}.{self.domain}"
            )
            
            return {
                'telegram_username': telegram_username,
                'subdomain': f"{subdomain}.{self.domain}",
                'mx_records': mx_results,
                'txt_record': {'success': txt_success},
                'cname_record': {'success': cname_success},
                'email_address': f"{telegram_username}@{self.domain}"
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка интеграции: {e}")
            return {'error': str(e)}
    
    async def create_txt_record(self, subdomain: str, content: str) -> bool:
        """Создание TXT записи."""
        try:
            record_name = f"{subdomain}.{self.domain}" if subdomain else self.domain
            
            data = {
                "type": "TXT",
                "name": record_name,
                "content": content,
                "ttl": 300,
                "proxied": False
            }
            
            response = await self.client.post(
                f"{self.base_url}/dns_records",
                json=data
            )
            
            return response.status_code == 200 and response.json().get('success')
            
        except Exception as e:
            self.logger.error(f"Ошибка создания TXT: {e}")
            return False
    
    async def create_cname_record(self, name: str, target: str) -> bool:
        """Создание CNAME записи."""
        try:
            data = {
                "type": "CNAME",
                "name": name,
                "content": target,
                "ttl": 300,
                "proxied": False
            }
            
            response = await self.client.post(
                f"{self.base_url}/dns_records",
                json=data
            )
            
            return response.status_code == 200 and response.json().get('success')
            
        except Exception as e:
            self.logger.error(f"Ошибка создания CNAME: {e}")
            return False
    
    async def get_dns_records(self, 
                             record_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Получение всех DNS записей зоны."""
        try:
            params = {}
            if record_type:
                params['type'] = record_type
            
            response = await self.client.get(
                f"{self.base_url}/dns_records",
                params=params
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return result.get('result', [])
            
            return []
            
        except Exception as e:
            self.logger.error(f"Ошибка получения DNS записей: {e}")
            return []
    
    async def verify_dns_configuration(self, domain: str) -> Dict[str, Any]:
        """Проверка DNS конфигурации домена."""
        try:
            resolver = dns.resolver.Resolver()
            
            # Проверка MX записей
            mx_records = []
            try:
                answers = resolver.resolve(domain, 'MX')
                for rdata in answers:
                    mx_records.append({
                        'priority': rdata.preference,
                        'host': str(rdata.exchange)
                    })
            except Exception as e:
                self.logger.warning(f"MX записи не найдены: {e}")
            
            # Проверка TXT записей
            txt_records = []
            try:
                answers = resolver.resolve(domain, 'TXT')
                for rdata in answers:
                    txt_records.append(str(rdata))
            except Exception as e:
                self.logger.warning(f"TXT записи не найдены: {e}")
            
            # Проверка A записей для MX хостов
            mx_validation = []
            for mx in mx_records:
                try:
                    a_answers = resolver.resolve(mx['host'], 'A')
                    mx_validation.append({
                        'mx': mx['host'],
                        'ips': [str(a) for a in a_answers],
                        'valid': True
                    })
                except Exception as e:
                    mx_validation.append({
                        'mx': mx['host'],
                        'error': str(e),
                        'valid': False
                    })
            
            return {
                'domain': domain,
                'mx_records': mx_records,
                'txt_records': txt_records,
                'mx_validation': mx_validation,
                'is_valid': len(mx_records) > 0
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка верификации DNS: {e}")
            return {'error': str(e), 'domain': domain}
    
    async def close(self):
        """Закрытие HTTP клиента."""
        await self.client.aclose()