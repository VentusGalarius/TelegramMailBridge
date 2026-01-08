"""
DNS валидатор для проверки MX записей и доменной инфраструктуры.
"""
import dns.resolver
from typing import Optional, Dict, Any, List
from datetime import datetime

class DNSSystemValidator:
    """Валидатор DNS инфраструктуры."""
    
    def __init__(self, config: Dict[str, Any], logger):
        self.config = config
        self.logger = logger
        self.resolver = dns.resolver.Resolver()
        self.resolver.timeout = config.get('timeout', 10)
        self.resolver.lifetime = config.get('lifetime', 30)
    
    async def validate_domain_mx(self, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Комплексная проверка домена.
        
        Args:
            domain: Домен для проверки
            
        Returns:
            Детальный отчет о DNS конфигурации
        """
        target = domain or self.config.get('target_domain', 't.me')
        
        self.logger.info(f"Валидация домена: {target}")
        
        report = {
            'domain': target,
            'timestamp': datetime.utcnow().isoformat(),
            'mx_records': [],
            'txt_records': [],
            'a_records': [],
            'ns_records': [],
            'soa_record': None,
            'has_mx': False,
            'errors': []
        }
        
        try:
            # Проверка MX записей
            report['mx_records'] = await self._get_mx_records(target)
            report['has_mx'] = len(report['mx_records']) > 0
            
            # Дополнительные проверки
            report['txt_records'] = await self._get_txt_records(target)
            report['ns_records'] = await self._get_ns_records(target)
            report['soa_record'] = await self._get_soa_record(target)
            
            # Проверка A записей для MX хостов
            for mx in report['mx_records']:
                a_records = await self._get_a_records(mx['host'])
                report['a_records'].extend(a_records)
                mx['resolved'] = len(a_records) > 0
            
            # Проверка обратных записей (PTR)
            for a_record in report['a_records'][:3]:  # Ограничиваем количество
                ptr_records = await self._get_ptr_records(a_record['ip'])
                a_record['ptr'] = ptr_records
            
            self.logger.info(
                f"Валидация завершена: {target} - "
                f"MX: {len(report['mx_records'])}, "
                f"ошибок: {len(report['errors'])}"
            )
            
        except Exception as e:
            report['errors'].append(f"Критическая ошибка: {str(e)}")
            self.logger.error(f"Ошибка валидации домена {target}: {e}")
        
        return report
    
    async def _get_mx_records(self, domain: str) -> List[Dict[str, Any]]:
        """Получение MX записей домена."""
        try:
            answers = self.resolver.resolve(domain, 'MX')
            return [
                {
                    'priority': rdata.preference,
                    'host': str(rdata.exchange).rstrip('.'),
                    'ttl': answers.rrset.ttl if hasattr(answers, 'rrset') else None
                }
                for rdata in answers
            ]
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN) as e:
            self.logger.warning(f"MX записи не найдены для {domain}: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Ошибка получения MX для {domain}: {e}")
            raise
    
    async def _get_txt_records(self, domain: str) -> List[str]:
        """Получение TXT записей домена."""
        try:
            answers = self.resolver.resolve(domain, 'TXT')
            return [str(txt).strip('"') for txt in answers]
        except Exception:
            return []
    
    async def _get_a_records(self, hostname: str) -> List[Dict[str, Any]]:
        """Получение A записей хоста."""
        try:
            answers = self.resolver.resolve(hostname, 'A')
            return [{'host': hostname, 'ip': str(rdata)} for rdata in answers]
        except Exception:
            return []
    
    async def _get_ns_records(self, domain: str) -> List[str]:
        """Получение NS записей домена."""
        try:
            answers = self.resolver.resolve(domain, 'NS')
            return [str(ns).rstrip('.') for ns in answers]
        except Exception:
            return []
    
    async def _get_soa_record(self, domain: str) -> Optional[Dict[str, Any]]:
        """Получение SOA записи домена."""
        try:
            answers = self.resolver.resolve(domain, 'SOA')
            if answers:
                soa = answers[0]
                return {
                    'mname': str(soa.mname).rstrip('.'),
                    'rname': str(soa.rname).rstrip('.'),
                    'serial': soa.serial,
                    'refresh': soa.refresh,
                    'retry': soa.retry,
                    'expire': soa.expire,
                    'minimum': soa.minimum
                }
        except Exception:
            pass
        return None
    
    async def _get_ptr_records(self, ip: str) -> List[str]:
        """Получение PTR записей (обратный DNS)."""
        try:
            # Преобразование IP для PTR запроса
            if ':' in ip:  # IPv6
                ptr_query = '.'.join(reversed(ip.split(':'))) + '.ip6.arpa'
            else:  # IPv4
                ptr_query = '.'.join(reversed(ip.split('.'))) + '.in-addr.arpa'
            
            answers = self.resolver.resolve(ptr_query, 'PTR')
            return [str(ptr).rstrip('.') for ptr in answers]
        except Exception:
            return []