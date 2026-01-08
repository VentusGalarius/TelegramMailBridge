import asyncio
from email import message_from_bytes
from email.policy import default
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Message as SmtpMessageHandler
from aiosmtpd.smtp import AuthResult, LoginPassword, SMTP as SMTPServer
import uuid
from datetime import datetime

class AdvancedSMTPHandler(SmtpMessageHandler):
    """Продвинутый обработчик SMTP с интеграцией Redis и Telegram."""
    
    def __init__(self, 
                 telegram_client,
                 redis_storage,
                 dns_validator,
                 cf_manager,
                 logger,
                 target_mapping: Dict[str, str] = None):
        super().__init__()
        self.tg_client = telegram_client
        self.redis_storage = redis_storage
        self.dns_validator = dns_validator
        self.cf_manager = cf_manager
        self.logger = logger
        self.target_mapping = target_mapping or {}
        self.message_counter = 0
        
        # Настройка получателей по умолчанию
        self.default_recipients = {
            'me': 'self',  # Сохранённые сообщения
            'channel': None,  # ID канала (настраивается через команду)
            'group': None     # ID группы (настраивается через команду)
        }
    
    async def handle_message(self, message):
        """Обработка входящего SMTP сообщения."""
        self.message_counter += 1
        msg_id = f"msg_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        
        try:
            # Парсинг письма
            raw_email = bytes(message)
            email_msg = message_from_bytes(raw_email, policy=default)
            
            # Извлечение метаданных
            metadata = self._extract_metadata(email_msg, msg_id)
            
            # Проверка DNS домена получателя
            dns_report = await self._validate_recipient_dns(metadata['recipient_domain'])
            metadata['dns_report'] = dns_report
            
            # Сохранение полного письма в Redis
            await self.redis_storage.store_email(msg_id, raw_email, metadata)
            
            # Формирование и отправка уведомления в Telegram
            await self._send_telegram_notification(msg_id, email_msg, metadata)
            
            # Интеграция с Cloudflare DNS при необходимости
            await self._handle_dns_integration(metadata)
            
            self.logger.info(f"[{msg_id}] Письмо обработано: {metadata['subject']}")
            return f"250 Message {msg_id} accepted"
            
        except Exception as e:
            self.logger.error(f"[{msg_id}] Ошибка обработки: {e}", exc_info=True)
            return f"451 Temporary processing error: {str(e)}"
    
    def _extract_metadata(self, email_msg, msg_id: str) -> Dict[str, Any]:
        """Извлечение метаданных из письма."""
        return {
            'message_id': msg_id,
            'from': email_msg.get('From', ''),
            'to': email_msg.get('To', ''),
            'cc': email_msg.get('Cc', ''),
            'bcc': email_msg.get('Bcc', ''),
            'subject': email_msg.get('Subject', ''),
            'date': email_msg.get('Date', ''),
            'recipient_domain': self._extract_domain(email_msg.get('To', '')),
            'received_at': datetime.utcnow().isoformat(),
            'headers': dict(email_msg.items())
        }
    
    def _extract_domain(self, email_address: str) -> str:
        """Извлечение домена из email адреса."""
        if '@' in email_address:
            return email_address.split('@')[1].strip().lower()
        return 'unknown'
    
    async def _validate_recipient_dns(self, domain: str) -> Dict[str, Any]:
        """Валидация DNS домена получателя."""
        if self.dns_validator:
            try:
                return await self.dns_validator.validate_domain_mx(domain)
            except Exception as e:
                self.logger.warning(f"Ошибка DNS валидации: {e}")
        
        return {'domain': domain, 'error': 'Validator not available'}
    
    async def _send_telegram_notification(self, 
                                         msg_id: str,
                                         email_msg,
                                         metadata: Dict[str, Any]):
        """Отправка уведомления в выбранный чат/канал."""
        try:
            # Определение получателя
            recipient_type, recipient_id = self._determine_recipient(metadata)
            
            if not recipient_id:
                self.logger.warning(f"[{msg_id}] Не указан получатель")
                return
            
            # Форматирование сообщения
            formatted_msg = self._format_notification(msg_id, email_msg, metadata)
            
            # Отправка в Telegram
            if recipient_type == 'me':
                await self.tg_client.send_message("me", formatted_msg)
            else:
                await self.tg_client.send_message(
                    int(recipient_id),
                    formatted_msg
                )
            
            self.logger.debug(f"[{msg_id}] Уведомление отправлено в {recipient_type}:{recipient_id}")
            
        except Exception as e:
            self.logger.error(f"[{msg_id}] Ошибка отправки в Telegram: {e}")
    
    def _determine_recipient(self, metadata: Dict[str, Any]) -> tuple:
        """Определение получателя на основе настроек и команд."""
        # Проверка маппинга доменов
        domain = metadata['recipient_domain']
        if domain in self.target_mapping:
            return 'custom', self.target_mapping[domain]
        
        # Использование настроек по умолчанию
        if self.default_recipients['channel']:
            return 'channel', self.default_recipients['channel']
        elif self.default_recipients['group']:
            return 'group', self.default_recipients['group']
        else:
            return 'me', 'self'
    
    def _format_notification(self, 
                            msg_id: str,
                            email_msg,
                            metadata: Dict[str, Any]) -> str:
        """Форматирование уведомления для Telegram."""
        lines = [
            f"📧 **Новое письмо #{self.message_counter}**",
            f"`{msg_id}`",
            f"",
            f"**От:** `{metadata['from']}`",
            f"**Кому:** `{metadata['to']}`",
            f"**Тема:** {metadata['subject']}",
            f"**Дата:** {metadata['date']}",
            f""
        ]
        
        # DNS информация
        dns_report = metadata.get('dns_report', {})
        if dns_report.get('mx_records'):
            mx_status = "✅" if dns_report.get('has_mx') else "⚠️"
            lines.append(f"**DNS:** {mx_status} {len(dns_report['mx_records'])} MX записей")
        
        # Тело письма (первые 200 символов)
        body_preview = self._extract_body_preview(email_msg)
        if body_preview:
            lines.append(f"")
            lines.append(f"**Превью:**")
            lines.append(f"```")
            lines.append(f"{body_preview[:200]}...")
            lines.append(f"```")
        
        # Команды для управления
        lines.append(f"")
        lines.append(f"**Команды:**")
        lines.append(f"• `/view {msg_id}` - Просмотр полного письма")
        lines.append(f"• `/source {msg_id}` - Исходный код письма")
        lines.append(f"• `/set_target {msg_id} [me/channel/group/id]` - Изменить получателя")
        
        return "\n".join(lines)
    
    def _extract_body_preview(self, email_msg) -> str:
        """Извлечение текста письма для превью."""
        if email_msg.is_multipart():
            for part in email_msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        return part.get_content()[:500]
                    except:
                        pass
        else:
            if email_msg.get_content_type() == "text/plain":
                return email_msg.get_content()[:500]
        return ""
    
    async def _handle_dns_integration(self, metadata: Dict[str, Any]):
        """Обработка интеграции с Cloudflare DNS."""
        try:
            domain = metadata['recipient_domain']
            
            # Если домен связан с t.me
            if domain.endswith('.t.me') or 't.me' in domain:
                telegram_username = domain.replace('.t.me', '').replace('@', '')
                
                # Настройка DNS через Cloudflare
                result = await self.cf_manager.ensure_tmail_integration(
                    telegram_username
                )
                
                if 'error' not in result:
                    self.logger.info(f"DNS интеграция настроена для {telegram_username}")
                else:
                    self.logger.warning(f"Ошибка DNS интеграции: {result['error']}")
                    
        except Exception as e:
            self.logger.error(f"Ошибка DNS интеграции: {e}")