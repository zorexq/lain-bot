import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import logging
import time
import typing

import discord
from discord.ext import commands, tasks

from classes.bot import LainBot
from modules.automod.attachment_spam_filter import check_attachment_spam, ATTACHMENTS_FROM_NEW_MEMBERS_CACHE
from modules.automod.flood_filter import flood_and_messages_check, MESSAGES_FROM_NEW_MEMBERS_CACHE
from modules.automod.handle_violation import handle_automod_violation, handle_violation, safe_ban, safe_send_to_log, apply_invite_lockdown, DISCORD_AUTOMOD_CACHE, LOCK_MANAGER_FOR_DISCORD_AUTOMOD
from modules.automod.link_filter import detect_links, check_message_for_invite_codes
from modules.automod.mention_filter import check_mention_abuse, MENTIONS_FROM_NEW_MEMBERS_CACHE
from modules.automod.spam_filter import is_spam_block
from modules.automod.thread_filter import flood_and_threads_check, THREADS_FROM_NEW_MEMBERS_CACHE
from modules.configuration import CONFIG
from modules.extract_message_content import extract_message_content

class AutoModeration(commands.Cog):
    def __init__(self, bot: LainBot):
        self.bot = bot

        self._channel_activity = defaultdict(lambda: deque())
        self._channel_locks    = defaultdict(asyncio.Lock)

        self.SLOWMODE_LEVELS = [
            (40, 30, 600),
            (30, 15, 300),
            (15, 3, 120),
        ]
        self._slowmode_state = {}
        self.WINDOW          = 10

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._slowmode_task.is_running():
            self._slowmode_task.start()

    @tasks.loop(seconds=5)
    async def _slowmode_task(self):
        now = time.time()

        for channel_id, times in list(self._channel_activity.items()):
            channel = self.bot.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            async with self._channel_locks[channel_id]:
                while times and now - times[0] > self.WINDOW:
                    times.popleft()

                count = len(times)
                current_delay = channel.slowmode_delay

                target_delay = 0
                for limit, delay, _ in self.SLOWMODE_LEVELS:
                    if count >= limit:
                        target_delay = delay
                        break

                last_state = self._slowmode_state.get(channel_id)

                if target_delay > current_delay:
                    try:
                        await channel.edit(slowmode_delay=target_delay, reason="Ужесточение замедления в виду увеличения активности")
                        self._slowmode_state[channel_id] = (target_delay, now)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    continue

                if current_delay > target_delay and last_state:
                    last_delay, since = last_state

                    if last_delay != current_delay:
                        self._slowmode_state[channel_id] = (current_delay, now)
                        continue

                    hold_time = next(
                        (h for _, d, h in self.SLOWMODE_LEVELS if d == last_delay),
                        120
                    )

                    if now - since < hold_time:
                        continue

                    lower_levels = [
                        d for _, d, _ in self.SLOWMODE_LEVELS
                        if d < last_delay
                    ]

                    next_delay = max(
                        (d for d in lower_levels if d >= target_delay),
                        default=target_delay
                    )

                    try:
                        await channel.edit(slowmode_delay=next_delay, reason="Смягчение замедления в виду уменьшения активности")
                        if next_delay > 0:
                            self._slowmode_state[channel_id] = (next_delay, now)
                        else:
                            self._slowmode_state.pop(channel_id, None)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                if not times and channel.slowmode_delay == 0:
                    self._channel_activity.pop(channel_id, None)
                    self._channel_locks.pop(channel_id, None)
                    self._slowmode_state.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):

        if thread.guild.id != CONFIG.GUILD_ID:
            return
        if not thread.owner:
            return
        if thread.owner == self.bot.user:
            return
        if thread.owner.bot:
            return
        
        # расстановка приоритетов
        priority: int = 2

        if thread.owner.guild_permissions.manage_messages:
            priority = 0
        else:
            now = datetime.now(timezone.utc)

            if thread.owner.joined_at:
                difference_between_join_and_now = now - thread.owner.joined_at

                if difference_between_join_and_now > timedelta(weeks=2):
                    priority = 1
                elif difference_between_join_and_now < timedelta(days=2):
                    priority = 3

        if priority == 0:
            return
        
        # условия срабатывания
        if priority > 1:

            # модерация создания веток
            need_to_prune, matched, thread_name = await flood_and_threads_check(self.bot, thread.owner, thread)

            if need_to_prune:

                extra = f"Название ветки:\n```\n#{thread_name}```"

                if not matched:
                    await handle_violation(
                        self.bot,
                        detected_member=thread.owner,
                        detected_channel=thread,
                        detected_guild=thread.guild,
                        reason_title="Флуд ветками",
                        reason_text="флуд путём создания веток",
                        extra_info=extra,
                        timeout_reason="Флуд ветками",
                        force_ban=True
                    )

                else:

                    extra = f"Совпадение:\n```\n{matched}\n```\n{extra}"

                    await handle_violation(
                        self.bot,
                        detected_member=thread.owner,
                        detected_channel=thread,
                        detected_guild=thread.guild,
                        reason_title="Реклама в названии ветки",
                        reason_text="реклама путём создания веток",
                        extra_info=extra,
                        timeout_reason="Реклама в названии ветки",
                        force_ban=True
                    )

                await THREADS_FROM_NEW_MEMBERS_CACHE.delete(thread.owner.id)

                return


    @commands.Cog.listener()
    async def on_message_edit(self, message_before: discord.Message, message_after: discord.Message):
        if message_before.content == message_after.content:
            return

        await self.on_message(message_after)


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        if not message.guild:
            return
        if message.guild.id != CONFIG.GUILD_ID:
            return
        if message.author == self.bot.user:
            return
        if message.is_system() and message.type == discord.MessageType.auto_moderation_action:
            await asyncio.sleep(3)

            async with LOCK_MANAGER_FOR_DISCORD_AUTOMOD.lock(message.id):
                hits = await DISCORD_AUTOMOD_CACHE.get(message.id) or None
                if hits:
                    return
                await DISCORD_AUTOMOD_CACHE.set(message.id, 1, ttl=1200)
            
            await apply_invite_lockdown(self.bot, message.guild, "Подозрение на рейд сервера (уведомление от Discord)")
            return
        if message.author.bot:
            if not message.interaction_metadata:
                return
            if message.interaction_metadata.is_guild_integration():
                return
            message.author = message.guild.get_member(message.interaction_metadata.user.id)
            if not message.author:
                try:
                    message.author = await message.guild.fetch_member(message.interaction_metadata.user.id)
                except discord.NotFound:
                    return
        
        priority: int = 2                       # расстановка приоритетов
        difference_between_join_and_now = None  # время с момента присоединения

        if message.author.guild_permissions.manage_messages:
            priority = 0
        elif message.interaction_metadata:
            priority = 3
        elif message.channel.id in CONFIG.ADS_CHANNELS_IDS:
            priority = 0
        else:
            now = datetime.now(timezone.utc)

            if message.author.joined_at:
                difference_between_join_and_now = now - message.author.joined_at

                if difference_between_join_and_now > timedelta(weeks=2):
                    priority = 1
                elif next((role for role in message.author.roles if role.id in CONFIG.AUTOMOD_WHITELISTED_ROLES_IDS), None):
                    priority = 1
                elif difference_between_join_and_now < timedelta(days=2):
                    priority = 3

        if priority == 0:
            return

        if isinstance(message.channel, discord.TextChannel):
            now = time.time()
            channel_id = message.channel.id

            async with self._channel_locks[channel_id]:
                times = self._channel_activity[channel_id]
                times.append(now)

                while times and now - times[0] > self.WINDOW:
                    times.popleft()

        # модерация активности  
        if message.activity and priority > 0:

            if message.activity.get('type') == 3 and (not message.activity.get('icon_override') or 'spotify:' not in message.activity.get('icon_override')):

                activity_presence = None
                for presence in message.author.activities:
                    if isinstance(presence, discord.Spotify):
                        activity_presence = presence
                        break

                activity_info = (
                    f"Тип: {message.activity.get('type')}\n"
                    f"Party ID: {message.activity.get('party_id')}\n"
                    f"Трек: {activity_presence.title if hasattr(activity_presence, 'title') else 'Нет трека'}\n"
                    f"URL трека: {activity_presence.track_url if hasattr(activity_presence, 'track_url') else 'Нет ссылки'}\n"
                    f"Альбом: {activity_presence.album if hasattr(activity_presence, 'album') else 'Нет альбома'}\n"
                    f"Исполнитель: {activity_presence.artist if hasattr(activity_presence, 'artist') else 'Нет исполнителя'}\n"
                    f"Длительность трека: {str(activity_presence.duration) if hasattr(activity_presence, 'duration') else 'Неизвестна'}"
                )

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Реклама через активность",
                    reason_text="реклама через Discord Activity",
                    extra_info=f"Информация об активности:\n```\n{activity_info}```",
                    timeout_reason="Реклама через активность",
                    force_ban=True
                )

                return
            
        # модерация вложенных файлов
        if message.attachments and priority > 0:

            for attachment in message.attachments:

                if not attachment.content_type:
                    continue

                if attachment.content_type and any(ct in attachment.content_type for ct in [
                    "text/", "application/json", "application/xml", 
                    "application/x-yaml", "application/yaml"
                ]):

                    # ограничение по размеру
                    # if attachment.size > MAX_FILE_SIZE_BYTES:
                    #     continue

                    try:
                        file_bytes = await asyncio.wait_for(attachment.read(), timeout=30)
                    except (asyncio.TimeoutError, discord.HTTPException):
                        continue

                    if file_bytes.count(b"\x00") > 100:
                        continue

                    content = file_bytes[:1_000_000].decode(errors='ignore')

                    matched = await detect_links(self.bot, content)

                    if matched:

                        preview = content[:300].replace("`", "'")

                        file_info = (
                            f"Имя файла: {attachment.filename}\n"
                            f"Размер: {attachment.size} байт\n"
                            f"Тип: {attachment.content_type}\n"
                        )

                        extra = (
                            f"Совпадение:\n```\n{matched}\n```\n"
                            f"Информация о файле:\n```\n{file_info}```\n"
                            f"Содержание файла (первые 300 символов):\n```\n{preview}\n```"
                        )

                        await handle_violation(
                            self.bot,
                            detected_member=message.author,
                            detected_channel=message.channel,
                            detected_guild=message.guild,
                            detected_message=message,
                            reason_title="Реклама внутри файла",
                            reason_text="реклама в прикреплённом файле",
                            extra_info=extra,
                            timeout_reason="Реклама в файле",
                            force_ban=True
                        )

                        return
                    
        # модерация опросов
        if message.poll and priority > 0:

            poll_options = " | ".join([f'"{option.text}"' for option in message.poll.answers])
            poll_content = (
                "\n\n[Опрос:]"
                f'\nВопрос: "{message.poll.question}"'
                f"\nОпции: {poll_options}"
            )

            matched = await detect_links(self.bot, poll_content)

            if matched:

                extra = (
                    f"Совпадение:\n```\n{matched}\n```\n"
                    f"Информация об опросе:\n```\n{poll_content}```\n"
                )

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Реклама внутри опроса",
                    reason_text="реклама в прикреплённом опросе",
                    extra_info=extra,
                    timeout_reason="Реклама в опросе",
                    force_ban=True
                )

                return
            
        # детект злоупотребления упоминаниями
        if message.content and priority > 2:
            is_mention_abuse, mention_content = await check_mention_abuse(message.author, message)

            if is_mention_abuse:

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Злоупотребление упоминаниями",
                    reason_text="злоупотребление упоминаниями",
                    extra_info=f"Содержание сообщения (первые 300 символов):\n```\n{mention_content[:300].replace('`', '')}\n```",
                    timeout_reason="Злоупотребление упоминаниями от нового участника",
                    force_mute=True
                )

                await MENTIONS_FROM_NEW_MEMBERS_CACHE.delete(message.author.id)

                return
            
        # детект рекламы
        if priority > 1:
            matched = await detect_links(self.bot, message)

            if matched:

                preview = (await extract_message_content(self.bot, message))[:300].replace("`", "'")

                extra = (
                    f"Совпадение:\n```\n{matched}\n```\n"
                    f"Содержание сообщения (первые 300 символов):\n```\n{preview}\n```"
                )

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Реклама в сообщении",
                    reason_text="реклама в тексте сообщения",
                    extra_info=extra,
                    timeout_reason="Реклама в сообщении"
                )

                return
            
        # детект всех инвайт кодов
        if priority > 2:
            is_invite = await check_message_for_invite_codes(self.bot, message, message.guild.id)

            if is_invite.get("found_invite"):

                preview = (await extract_message_content(self.bot, message))[:300].replace("`", "'")

                extra = (
                    f"Информация по ссылке-приглашению:\n```\nКод: {is_invite['invite_code']}\nВедёт на сервер: {is_invite['guild_name']} (ID: {is_invite['guild_id']})\nКоличество участников: {is_invite['member_count']}\nИнформация извлечена из кэша: {'Да' if is_invite['from_cache'] else 'Нет'}\n```\n"
                    f"Содержание сообщения (первые 300 символов):\n```\n{preview}\n```"
                )

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Ссылка-приглашение в сообщении",
                    reason_text="Ссылка-приглашение в сообщении",
                    extra_info=extra,
                    timeout_reason="Ссылка-приглашение в сообщении"
                )

                return

        # детект флуда
        if priority > 2:
            is_flood, flood_content = await flood_and_messages_check(self.bot, message.author, message)

            if is_flood:

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Флуд",
                    reason_text="флуд",
                    extra_info=f"Содержание сообщения (первые 300 символов):\n```\n{flood_content[:300].replace('`', '')}\n```",
                    timeout_reason="Флуд от нового участника",
                    force_mute=True
                )

                await MESSAGES_FROM_NEW_MEMBERS_CACHE.delete(message.author.id)

                return
            
        # модерация сообщений
        if (message.content or message.embeds) and priority > 1:
        
            # защита от засирания чата

            message_content = message.content if message.content else ""
            for embed in message.embeds:
                if embed.title:
                    message_content += f"\nЗаголовок: {embed.title}"
                if embed.description:
                    message_content += f"\nОписание: {embed.description}"

            if await is_spam_block(message_content):

                await handle_violation(
                    self.bot,
                    detected_member=message.author,
                    detected_channel=message.channel,
                    detected_guild=message.guild,
                    detected_message=message,
                    reason_title="Спам / засорение чата",
                    reason_text="засорение чата (пустые строки / мусор / код-блоки)",
                    extra_info=f"Содержание сообщения (первые 300 символов):\n```\n{message_content[:300].replace('`', '')}\n```",
                    timeout_reason="Спам / засорение чата"
                )

                return
                
        # детект спама вложениями
        # if message.attachments and priority > 2 and difference_between_join_and_now and difference_between_join_and_now < timedelta(minutes=7):
            
        #     is_attachment_spam, attachment_content = await check_attachment_spam(message.author, message)

        #     if is_attachment_spam:

        #         await handle_violation(
        #             self.bot,
        #             detected_member=message.author,
        #             detected_channel=message.channel,
        #             detected_guild=message.guild,
        #             detected_message=message,
        #             reason_title="Подозрение на спам вложениями",
        #             reason_text="нечеловеческое поведение / подозрение на спам вложениями",
        #             extra_info=f"Содержание сообщения (первые 300 символов):\n```\n{attachment_content[:300].replace('`', '')}\n```",
        #             timeout_reason="Подозрение на спам вложениями от нового участника",
        #             force_mute=True
        #         )

        #         await ATTACHMENTS_FROM_NEW_MEMBERS_CACHE.delete(message.author.id)

        #         return

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        await handle_automod_violation(
            self.bot,
            execution
        )
                
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild

        if guild.id != CONFIG.GUILD_ID:
            return

        if channel.id not in CONFIG.PROTECTED_CHANNELS_IDS:
            return

        await asyncio.sleep(1)

        who_deleted: typing.List[typing.Union[discord.User, discord.Member]] = []

        try:
            async for entry in guild.audit_logs(limit=15, action=discord.AuditLogAction.channel_delete):
                if entry.target.id == channel.id:
                    if entry.user.id != self.bot.user.id:
                        who_deleted.append(entry.user)
                    break
        except:
            pass

        resolved: typing.List[typing.Union[discord.User, discord.Member]] = []

        for user in who_deleted:
            resolved.append(user)
            if user.bot:
                try:
                    async for entry in guild.audit_logs(
                        limit=10,
                        action=discord.AuditLogAction.bot_add,
                        after=datetime.now(timezone.utc) - timedelta(days=3)
                    ):
                        if entry.target.id == user.id:
                            resolved.append(entry.user)
                            break
                except:
                    pass

        if not resolved:
            embed = discord.Embed(
                title="Удаление защищённого канала",
                description=(
                    f"Защищённый канал `#{channel.name}` (`ID {channel.id}`) был удалён, но не удалось определить, кем именно\n"
                    f"Возможная причина удаления канала: попытка краша сервера"
                ),
                color=0xFF0000
            )
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
            embed.set_footer(text="Удаливший не найден")
            embed.add_field(name="Канал:", value=f"`#{channel.name}` (`ID {channel.id}`)")

            await safe_send_to_log(self.bot, embed=embed)
            return

        embeds = []

        for i, user in enumerate(resolved, 1):
            reason = f"Удаление защищённого канала #{channel.name} (ID {channel.id})"

            embed = discord.Embed(
                title="Удаление защищённого канала",
                description=(
                    f"Участник {user.mention} (`@{user}`) был забанен.\n"
                    f"Причина: удаление защищённого канала `#{channel.name}` (`ID {channel.id}`)\n"
                    f"Возможная причина удаления канала: попытка краша сервера"
                ),
                color=0xFF0000,
            )
            embed.set_footer(text=f"ID: {user.id}")
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="Канал:", value=f"`#{channel.name}` (`ID {channel.id}`)")

            if i == 1:
                embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)

            embeds.append(embed)

            await safe_ban(guild, user, reason=reason)

        await safe_send_to_log(self.bot, embeds=embeds)


    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if guild.id != CONFIG.GUILD_ID:
            return
        
        if isinstance(channel, discord.VoiceChannel):
            
            members = channel.members
            # детект рекламы
            matched = await detect_links(self.bot, channel.name)

            if matched:

                extra = (
                    f"Совпадение:\n```\n{matched}\n```\n"
                    f"Название голосового канала:\n```\n{channel.name}```"
                )

                if members:
                    for member in members:
                        await handle_violation(
                            self.bot,
                            detected_member=member,
                            detected_channel=channel,
                            detected_guild=channel.guild,
                            reason_title="Реклама в названии голосового канала",
                            reason_text="реклама путём создания голосового канала",
                            extra_info=extra,
                            timeout_reason="Реклама в названии голосового канала",
                            force_mute=True
                        )

                try:
                    await channel.delete(reason="Реклама в названии голосового канала")
                except:
                    pass

                return
            
            # детект всех инвайт кодов
            is_invite = await check_message_for_invite_codes(self.bot, channel.name, channel.guild.id)

            if is_invite.get("found_invite"):

                extra = (
                    f"Информация по ссылке-приглашению:\n```\nКод: {is_invite['invite_code']}\nВедёт на сервер: {is_invite['guild_name']} (ID: {is_invite['guild_id']})\nКоличество участников: {is_invite['member_count']}\nИнформация извлечена из кэша: {'Да' if is_invite['from_cache'] else 'Нет'}\n```"
                )

                if members:
                    for member in members:
                        await handle_violation(
                            self.bot,
                            detected_member=member,
                            detected_channel=channel,
                            detected_guild=channel.guild,
                            reason_title="Ссылка-приглашение в названии голосового канала",
                            reason_text="Ссылка-приглашение в названии голосового канала",
                            extra_info=extra,
                            timeout_reason="Ссылка-приглашение в названии голосового канала",
                            force_mute=True
                        )

                return
            

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        guild = after.guild
        if guild.id != CONFIG.GUILD_ID:
            return
        
        if isinstance(after, discord.VoiceChannel) and before.name != after.name:
            
            members = after.members
            # детект рекламы
            matched = await detect_links(self.bot, after.name)

            if matched:

                extra = (
                    f"Совпадение:\n```\n{matched}\n```\n"
                    f"Название голосового канала:\n```\n{after.name}```"
                )

                if members:
                    for member in members:
                        await handle_violation(
                            self.bot,
                            detected_member=member,
                            detected_channel=after,
                            detected_guild=after.guild,
                            reason_title="Реклама в названии голосового канала",
                            reason_text="реклама путём создания голосового канала",
                            extra_info=extra,
                            timeout_reason="Реклама в названии голосового канала",
                            force_mute=True
                        )

                try:
                    await after.delete(reason="Реклама в названии голосового канала")
                except:
                    pass

                return
            
            # детект всех инвайт кодов
            is_invite = await check_message_for_invite_codes(self.bot, after.name, after.guild.id)

            if is_invite.get("found_invite"):

                extra = (
                    f"Информация по ссылке-приглашению:\n```\nКод: {is_invite['invite_code']}\nВедёт на сервер: {is_invite['guild_name']} (ID: {is_invite['guild_id']})\nКоличество участников: {is_invite['member_count']}\nИнформация извлечена из кэша: {'Да' if is_invite['from_cache'] else 'Нет'}\n```"
                )

                if members:
                    for member in members:
                        await handle_violation(
                            self.bot,
                            detected_member=member,
                            detected_channel=after,
                            detected_guild=after.guild,
                            reason_title="Ссылка-приглашение в названии голосового канала",
                            reason_text="Ссылка-приглашение в названии голосового канала",
                            extra_info=extra,
                            timeout_reason="Ссылка-приглашение в названии голосового канала",
                            force_mute=True
                        )

                try:
                    await after.delete(reason="Реклама в названии голосового канала")
                except:
                    pass

                return

async def setup(bot: LainBot):
    await bot.add_cog(AutoModeration(bot))