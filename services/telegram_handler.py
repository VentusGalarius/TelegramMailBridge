from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import json
import base64
from typing import Optional, Dict, Any

class TelegramCommandHandler:
    """Обработчик команд Telegram с выбором получателя и управлением письмами."""
    
    def __init__(self, 
                 telegram_client: Client,
                 redis_storage,
                 smtp_handler,
                 logger):
        self.client = telegram_client
        self.redis_storage = redis_storage
        self.smtp_handler = smtp_handler
        self.logger = logger
        
        # Регистрация обработчиков
        self._register_handlers()
    
    def _register_handlers(self):
        """Регистрация всех обработчиков команд."""
        
        @self.client.on_message(filters.command("start"))
        async def start_command(client: Client, message: Message):
            """Обработчик команды /start."""
            help_text = """
            📧 **Telegram Mail Bridge System**
            
            **Доступные команды:**
            
            📨 **Управление получателями:**
            `/set_target me` - Отправлять себе (Saved Messages)
            `/set_target channel <ID>` - Указать канал
            `/set_target group <ID>` - Указать группу
            `/set_target custom <domain>=<ID>` - Настройка домена
            
            📂 **Работа с письмами:**
            `/view <message_id>` - Просмотр письма
            `/source <message_id>` - Исходный код письма
            `/search [domain]` - Поиск писем по домену
            `/list [limit]` - Список последних писем
            
            🌐 **Управление DNS:**
            `/dns_setup <telegram_username>` - Настройка DNS для t.me
            `/dns_check <domain>` - Проверка DNS конфигурации
            `/dns_records [type]` - Просмотр DNS записей
            
            ⚙️ **Система:**
            `/status` - Статус системы
            `/config` - Текущая конфигурация
            """
            
            await message.reply_text(
                help_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📚 Документация", url="https://core.telegram.org/"),
                    InlineKeyboardButton("🛠 Настройки", callback_data="settings")
                ]])
            )
        
        @self.client.on_message(filters.command("set_target"))
        async def set_target_command(client: Client, message: Message):
            """Настройка получателя уведомлений."""
            try:
                args = message.text.split()[1:]
                
                if not args:
                    await message.reply_text(
                        "❌ Укажите тип получателя:\n"
                        "`/set_target me` - себе\n"
                        "`/set_target channel <ID>` - канал\n"
                        "`/set_target group <ID>` - группа\n"
                        "`/set_target custom <domain>=<ID>` - домен"
                    )
                    return
                
                target_type = args[0].lower()
                
                if target_type == 'me':
                    self.smtp_handler.default_recipients.update({
                        'me': 'self',
                        'channel': None,
                        'group': None
                    })
                    await message.reply_text("✅ Уведомления будут отправляться вам в Saved Messages")
                
                elif target_type in ['channel', 'group']:
                    if len(args) < 2:
                        await message.reply_text(f"❌ Укажите ID {target_type}")
                        return
                    
                    target_id = args[1]
                    try:
                        # Проверка доступности чата
                        chat = await client.get_chat(int(target_id))
                        
                        self.smtp_handler.default_recipients.update({
                            'me': None,
                            target_type: target_id,
                            'group' if target_type == 'channel' else 'channel': None
                        })
                        
                        await message.reply_text(
                            f"✅ Уведомления будут отправляться в {target_type}: {chat.title}"
                        )
                        
                    except Exception as e:
                        await message.reply_text(f"❌ Ошибка: {e}")
                
                elif target_type == 'custom':
                    if len(args) < 2 or '=' not in args[1]:
                        await message.reply_text("❌ Формат: `/set_target custom domain=ID`")
                        return
                    
                    domain, chat_id = args[1].split('=', 1)
                    self.smtp_handler.target_mapping[domain] = chat_id
                    
                    await message.reply_text(
                        f"✅ Письма для `{domain}` будут отправляться в `{chat_id}`"
                    )
                
                else:
                    await message.reply_text("❌ Неизвестный тип получателя")
                    
            except Exception as e:
                await message.reply_text(f"❌ Ошибка: {e}")
        
        @self.client.on_message(filters.command("view"))
        async def view_email_command(client: Client, message: Message):
            """Просмотр полного письма из Redis."""
            try:
                args = message.text.split()[1:]
                
                if not args:
                    await message.reply_text("❌ Укажите ID письма: `/view msg_...`")
                    return
                
                msg_id = args[0]
                email_data = await self.redis_storage.retrieve_email(msg_id)
                
                if not email_data:
                    await message.reply_text("❌ Письмо не найдено")
                    return
                
                metadata = email_data['metadata']
                email_msg = email_data['message']
                
                # Форматирование детального просмотра
                response = [
                    f"📄 **Письмо:** `{msg_id}`",
                    f"",
                    f"**От:** {metadata['from']}",
                    f"**Кому:** {metadata['to']}",
                    f"**Тема:** {metadata['subject']}",
                    f"**Дата:** {metadata['date']}",
                    f"**Получено:** {metadata['received_at']}",
                    f""
                ]
                
                # Тело письма
                parsed = email_data.get('parsed', {})
                if parsed.get('body', {}).get('plain'):
                    body = parsed['body']['plain']
                    if isinstance(body, bytes):
                        body = body.decode('utf-8', errors='ignore')
                    
                    response.append("**Текст письма:**")
                    response.append("```")
                    response.append(body[:1500] + ("..." if len(body) > 1500 else ""))
                    response.append("```")
                
                # Вложения
                attachments = parsed.get('attachments', [])
                if attachments:
                    response.append(f"")
                    response.append(f"**Вложения:** {len(attachments)}")
                    for att in attachments[:5]:
                        response.append(f"• {att['content_type']} ({att['size']} bytes)")
                
                await message.reply_text(
                    "\n".join(response),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "📎 Исходник", 
                            callback_data=f"source_{msg_id}"
                        ),
                        InlineKeyboardButton(
                            "🗑 Удалить", 
                            callback_data=f"delete_{msg_id}"
                        )
                    ]])
                )
                
            except Exception as e:
                self.logger.error(f"Ошибка команды /view: {e}")
                await message.reply_text(f"❌ Ошибка: {e}")
        
        @self.client.on_message(filters.command(["search", "list"]))
        async def search_emails_command(client: Client, message: Message):
            """Поиск и список писем."""
            try:
                args = message.text.split()[1:]
                command = message.command[0]
                
                if command == "search":
                    domain = args[0] if args else None
                    emails = await self.redis_storage.search_emails(domain=domain, limit=20)
                    title = f"Поиск по домену: {domain}" if domain else "Все письма"
                else:  # list
                    limit = int(args[0]) if args and args[0].isdigit() else 10
                    emails = await self.redis_storage.search_emails(limit=limit)
                    title = f"Последние {limit} писем"
                
                if not emails:
                    await message.reply_text("📭 Писем не найдено")
                    return
                
                response = [f"📂 **{title}**", ""]
                
                for i, email_data in enumerate(emails[:15], 1):
                    metadata = email_data['metadata']
                    response.append(
                        f"{i}. `{metadata['message_id']}` - "
                        f"**{metadata['subject'][:50]}**\n"
                        f"   📨 {metadata['from'][:30]} → {metadata['to'][:30]}\n"
                        f"   🕐 {metadata['received_at']}"
                    )
                
                await message.reply_text(
                    "\n".join(response),
                    parse_mode=ParseMode.MARKDOWN
                )
                
            except Exception as e:
                self.logger.error(f"Ошибка команды /search: {e}")
                await message.reply_text(f"❌ Ошибка: {e}")
        
        @self.client.on_message(filters.command("dns_setup"))
        async def dns_setup_command(client: Client, message: Message):
            """Настройка DNS для интеграции t.me."""
            try:
                args = message.text.split()[1:]
                
                if not args:
                    await message.reply_text("❌ Укажите Telegram username: `/dns_setup @username`")
                    return
                
                username = args[0].replace('@', '')
                
                await message.reply_text(
                    f"🔄 Настраиваю DNS для `{username}`...\n"
                    f"Это может занять до 5 минут."
                )
                
                # Используем Cloudflare менеджер из SMTP обработчика
                if hasattr(self.smtp_handler, 'cf_manager'):
                    result = await self.smtp_handler.cf_manager.ensure_tmail_integration(username)
                    
                    if 'error' not in result:
                        response = [
                            f"✅ **DNS настройка завершена**",
                            f"",
                            f"**Telegram:** @{username}",
                            f"**Почта:** {result.get('email_address', 'N/A')}",
                            f"**Поддомен:** {result.get('subdomain', 'N/A')}",
                            f""
                        ]
                        
                        if result.get('mx_records'):
                            response.append("**MX записи:**")
                            for mx in result['mx_records']:
                                status = "✅" if mx['success'] else "❌"
                                response.append(f"{status} {mx['server']} (приоритет {mx['priority']})")
                        
                        await message.reply_text("\n".join(response))
                    else:
                        await message.reply_text(f"❌ Ошибка: {result['error']}")
                else:
                    await message.reply_text("❌ Cloudflare менеджер не настроен")
                    
            except Exception as e:
                self.logger.error(f"Ошибка команды /dns_setup: {e}")
                await message.reply_text(f"❌ Ошибка: {e}")
        
        @self.client.on_message(filters.command("status"))
        async def status_command(client: Client, message: Message):
            """Получение статуса системы."""
            try:
                # Статистика Redis
                email_count = len(await self.redis_storage.search_emails(limit=1000))
                
                # Настройки получателей
                targets = self.smtp_handler.default_recipients
                active_target = None
                for key, value in targets.items():
                    if value:
                        active_target = f"{key}: {value}"
                        break
                
                status_text = [
                    "🟢 **Система работает**",
                    "",
                    f"**Хранилище:** {email_count} писем",
                    f"**Активный получатель:** {active_target or 'не указан'}",
                    f"**Настроено доменов:** {len(self.smtp_handler.target_mapping)}",
                    "",
                    "**SMTP сервер:**",
                    f"• Порт: {self.smtp_handler.controller.port}",
                    f"• Обработано: {self.smtp_handler.message_counter}",
                    "",
                    "**Компоненты:**",
                    f"• Redis: {'🟢' if self.redis_storage.redis else '🔴'}",
                    f"• Telegram: {'🟢' if client.is_connected else '🔴'}",
                    f"• DNS валидатор: {'🟢' if self.smtp_handler.dns_validator else '🔴'}",
                    f"• Cloudflare: {'🟢' if hasattr(self.smtp_handler, 'cf_manager') else '🔴'}"
                ]
                
                await message.reply_text(
                    "\n".join(status_text),
                    parse_mode=ParseMode.MARKDOWN
                )
                
            except Exception as e:
                await message.reply_text(f"❌ Ошибка статуса: {e}")