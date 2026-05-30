import asyncio
import hashlib
import time
import traceback
import typing
import logging

from aiocache import SimpleMemoryCache
from datetime import timedelta
import discord

from classes.bot import LainBot
from modules.configuration import CONFIG
from modules.lock_manager import LockManagerWithIdleTTL

AUTOMOD_HIT_CACHE       = SimpleMemoryCache()
HIT_CACHE               = SimpleMemoryCache()
SENT_MESSAGES_CACHE     = SimpleMemoryCache()
VIOLATION_CACHE         = SimpleMemoryCache()
INVITE_LOCKDOWN_CACHE   = SimpleMemoryCache()

_PURGE_SEMAPHORE = asyncio.Semaphore(1)

LOCK_MANAGER_FOR_HITS     = LockManagerWithIdleTTL(idle_ttl=3600)
LOCK_MANAGER_FOR_MESSAGES = LockManagerWithIdleTTL(idle_ttl=2400)
LOCK_MANAGER_FOR_GUILD    = LockManagerWithIdleTTL(idle_ttl=7200)

LOCK_MANAGER_FOR_AUTOMOD_HITS  = LockManagerWithIdleTTL(idle_ttl=300)
LOCK_MANAGER_FOR_GUILD_AUTOMOD = LockManagerWithIdleTTL(idle_ttl=600)

LOCK_MANAGER_FOR_DISCORD_AUTOMOD = LockManagerWithIdleTTL(idle_ttl=1200)
DISCORD_AUTOMOD_CACHE            = SimpleMemoryCache()

INVITE_LOCKDOWN_DURATION = 2 * 60 * 60  # 2 часа
INVITE_LOCKDOWN_COOLDOWN = 45 * 60      # 45 минут
VIOLATION_WINDOW         = 5 * 60       # 5 минут
VIOLATION_LIMIT          = 10           # 10 нарушений в VILOATION_WINDOW минут

async def apply_invite_lockdown(bot: LainBot, guild: discord.Guild, reason: str):
    now = time.time()

    async with LOCK_MANAGER_FOR_GUILD.lock(guild.id):
        data = await INVITE_LOCKDOWN_CACHE.get(guild.id) or {}

        lockdown_until = data.get("lockdown_until", 0)
        cooldown_until = data.get("cooldown_until", 0)

        if now < cooldown_until:
            return

        if now < lockdown_until:
            lockdown_until += INVITE_LOCKDOWN_DURATION
        else:
            lockdown_until = now + INVITE_LOCKDOWN_DURATION

            log_desc = (
                "**Включён локдаун приглашений и личных сообщений** на 2 часа\n"
                f"Причина: {reason}\n\n"
            )
            log_embed = discord.Embed(
                title="Локдаун",
                description=log_desc,
                color=0xff0000
            )
            log_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)

            await safe_send_to_log(
                bot,
                embed=log_embed
            )

            try:
                news_channel: discord.PartialMessageable = bot.get_partial_messageable(CONFIG.NEWS_CHANNEL_ID)
                await safe_send_to_channel(
                    news_channel,
                    embed=log_embed
                )
            except Exception:
                return None

        disabled_until = discord.utils.utcnow() + timedelta(
            seconds=(lockdown_until - now)
        )

        await guild.edit(invites_disabled_until=disabled_until, dms_disabled_until=disabled_until)

        cooldown_until = now + INVITE_LOCKDOWN_COOLDOWN

        await INVITE_LOCKDOWN_CACHE.set(
            guild.id,
            {
                "lockdown_until": lockdown_until,
                "cooldown_until": cooldown_until
            },
            ttl=INVITE_LOCKDOWN_DURATION + INVITE_LOCKDOWN_COOLDOWN
        )

def generate_message_hash(message_content: str) -> str:
    return hashlib.md5(message_content.encode()).hexdigest()[:16]

async def check_message_sent_recently(user_id: int, message_hash: str) -> bool:
    async with LOCK_MANAGER_FOR_MESSAGES.lock(user_id):
        last_sent_cache: typing.List = await SENT_MESSAGES_CACHE.get(user_id)
        
        if last_sent_cache is None:
            last_sent_cache = []
        
        if message_hash in last_sent_cache:
            return True
        
        last_sent_cache.append(message_hash)
        if len(last_sent_cache) > 10:
            last_sent_cache = last_sent_cache[-10:]
        
        await SENT_MESSAGES_CACHE.set(user_id, last_sent_cache, ttl=60)
        return False

async def safe_ban(guild: discord.Guild, member: discord.abc.Snowflake, reason: str = None, delete_message_seconds: int = 0):
    try:
        await guild.ban(member, reason=reason, delete_message_seconds=delete_message_seconds)
    except Exception:
        pass

async def safe_send_to_channel(channel: discord.abc.Messageable, *args, user_id: int = None, message_content: str = None, **kwargs):
    if user_id and message_content and await check_message_sent_recently(user_id, generate_message_hash(message_content)):
        return None
    try:
        await channel.send(*args, **kwargs)
        return
    except Exception:
        return None

async def safe_send_to_log(bot: LainBot, *args, user_id: int = None, message_content: str = None, **kwargs):
    if user_id and message_content and await check_message_sent_recently(user_id, generate_message_hash(message_content)):
        return None
    try:
        channel: discord.PartialMessageable = bot.get_partial_messageable(CONFIG.AUTOMOD_LOGS_CHANNEL_ID)
        await channel.send(*args, **kwargs)
        return
    except Exception:
        return None

async def safe_delete(msg: discord.Message):
    try: 
        await msg.delete()
    except Exception:
        pass

async def delete_messages_safe(
    channel: typing.Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel],
    message_ids: set[int],
    reason: str = "Автоматическая очистка"
):

    if not message_ids:
        return

    async with _PURGE_SEMAPHORE:
        try:
            await channel.purge(
                check=lambda m: m.id in message_ids,
                bulk=True,
                limit=200,
                reason=reason
            )
            return
        except discord.HTTPException:
            pass

    for msg_id in message_ids:

        try:
            await channel.delete_messages(
                [
                    discord.Object(id=msg_id)
                ]
            )
        except discord.NotFound:
            continue
        except discord.HTTPException:
            await asyncio.sleep(2)
        finally:
            await asyncio.sleep(0.25)

async def safe_timeout(member: discord.Member, duration: timedelta, reason: str = None):
    try:
        await member.timeout(duration, reason=reason)
    except Exception:
        pass

async def handle_automod_violation(
    bot: LainBot,
    execution: discord.AutoModAction
):

    async with LOCK_MANAGER_FOR_AUTOMOD_HITS.lock(execution.member.id):
        hits = await AUTOMOD_HIT_CACHE.get(execution.member.id) or 0
        hits += 1
        await AUTOMOD_HIT_CACHE.set(execution.member.id, hits, ttl=600)

    if execution.alert_system_message_id:
        async with LOCK_MANAGER_FOR_DISCORD_AUTOMOD.lock(execution.alert_system_message_id):
            await DISCORD_AUTOMOD_CACHE.set(execution.alert_system_message_id, 1, ttl=1200)

    is_soft = hits < 10

    # LOG EMBED
    if is_soft:
        return
    else:
        punishment = "Тебе выдан мут на 1 час"
        action_text = f"Участнику {execution.member.mention} (`@{execution.member}`) был выдан мут на 1 час"

    log_desc = (
        f"{action_text}\n"
        f"Причина: Множественные срабатывания автомода / попытки обойти автомод"
    )

    log_embed = discord.Embed(
        title="Попытки обойти автомод",
        description=log_desc,
        color=0xff0000
    )
    log_embed.set_author(name=execution.guild.name, icon_url=execution.guild.icon.url if execution.guild.icon else None)
    log_embed.set_footer(text=f"ID: {execution.member.id}")
    log_embed.set_thumbnail(url=execution.member.display_avatar.url)
    log_embed.add_field(
        name="Канал:",
        value=f"{execution.channel.mention} (`#{execution.channel.name}`)",
        inline=False
    )

    try:
        linked_msg = execution.channel.last_message

        if not linked_msg:
            linked_msgs = [message async for message in execution.channel.history(limit=2)]

            for msg in linked_msgs:
                linked_msg = msg
                break

        if linked_msg:
            log_embed.add_field(
                name="Контекст:",
                value=f"[Ссылка на контекст]({linked_msg.jump_url})",
                inline=False
            )
    except Exception as e:
        logging.warning(f"Ошибка при получении ссылки на сообщение: {e}\n{traceback.format_exc()}")
        pass

    asyncio.create_task(safe_send_to_log(bot, embed=log_embed, user_id=execution.member.id, message_content=log_desc))

    # MENTION EMBED
    mention_desc = (
        f"Причина срабатывания: множественные срабатывания автомода / попытки обойти автомод\n"
        f"{punishment}\n\n"
        f"-# Дополнительную информацию можно посмотреть в канале автомодерации"
    )

    mention_embed = discord.Embed(
        title="Попытки обойти автомод",
        description=mention_desc,
        color=0xff0000
    )
    mention_embed.set_author(name=execution.guild.name, icon_url=execution.guild.icon.url if execution.guild.icon else None)
    mention_embed.set_thumbnail(url=execution.member.display_avatar.url)
    mention_embed.set_footer(
        text=(
            "Если ты считаешь, что это ошибка, проигнорируй это сообщение" 
            if is_soft 
            else "Если ты считаешь, что это ошибка, обратись к модераторам"
        )
    )

    asyncio.create_task(
            safe_send_to_channel(
            execution.member,
            embed=mention_embed,
            user_id=execution.member.id,
            message_content=f"[ЛИЧНЫЕ СООБЩЕНИЯ]\n\n{mention_desc}"
        )
    )

    if not isinstance(execution.channel, discord.ForumChannel):
        asyncio.create_task(
            safe_send_to_channel(
                execution.channel,
                content=execution.member.mention,
                embed=mention_embed,
                user_id=execution.member.id,
                message_content=mention_desc
            )
        )

    if not is_soft:
        await safe_timeout(execution.member, timedelta(hours=1), "множественные срабатывания автомода / попытки обойти автомод")
        await AUTOMOD_HIT_CACHE.delete(execution.member.id)

        async with LOCK_MANAGER_FOR_GUILD.lock(execution.guild.id):
            violations = await VIOLATION_CACHE.get(execution.guild.id) or []

            now = time.time()
            violations = [t for t in violations if now - t <= VIOLATION_WINDOW]
            violations.extend([now, now, now])

            await VIOLATION_CACHE.set(
                execution.guild.id,
                violations,
                ttl=VIOLATION_WINDOW
            )

            if len(violations) >= VIOLATION_LIMIT:
                asyncio.create_task(apply_invite_lockdown(bot, execution.guild, "Подозрение на рейд сервера (массовые срабатывания автомодерации)"))


async def handle_violation(
    bot: LainBot,
    detected_member: discord.Member,
    detected_channel: typing.Union[discord.abc.GuildChannel, discord.Thread],
    detected_guild: discord.Guild,
    reason_title: str,
    reason_text: str,
    extra_info: str = "",
    detected_message: discord.Message = None,
    timeout_reason: str = None,
    force_mute: bool = False,
    force_ban: bool = False,
):

    now = time.time()

    async with LOCK_MANAGER_FOR_GUILD.lock(detected_guild.id):
        violations = await VIOLATION_CACHE.get(detected_guild.id) or []

        violations = [t for t in violations if now - t <= VIOLATION_WINDOW]
        violations.append(now)

        await VIOLATION_CACHE.set(
            detected_guild.id,
            violations,
            ttl=VIOLATION_WINDOW
        )

        if len(violations) >= VIOLATION_LIMIT:
            asyncio.create_task(apply_invite_lockdown(bot, detected_guild, "Подозрение на рейд сервера (массовые срабатывания автомодерации)"))

    # system message ignore
    if detected_message and detected_message.is_system():
        await safe_delete(detected_message)
        return

    # hit-cache
    async with LOCK_MANAGER_FOR_HITS.lock(detected_member.id):
        hits = await HIT_CACHE.get(detected_member.id) or 0
        hits += 1
        await HIT_CACHE.set(detected_member.id, hits, ttl=3600)

    is_soft = hits < 3 and not force_mute and not force_ban

    # LOG EMBED
    if force_ban:
        punishment = "Тебе выдан бан"
        action_text = f"Участнику {detected_member.mention} (`@{detected_member}`) был выдан бан"
    elif is_soft:
        punishment = "Наказание не применяется, за исключением удаления сообщения"
        action_text = f"Удалено сообщение от {detected_member.mention} (`@{detected_member}`)"
    else:
        punishment = "Тебе выдан мут на 1 час"
        action_text = f"Участнику {detected_member.mention} (`@{detected_member}`) был выдан мут на 1 час"

    log_desc = (
        f"{action_text}\n"
        f"Причина: {reason_text}\n\n"
        f"{extra_info}"
    )

    log_embed = discord.Embed(
        title=reason_title,
        description=log_desc,
        color=0xff0000
    )
    log_embed.set_author(name=detected_guild.name, icon_url=detected_guild.icon.url if detected_guild.icon else None)
    log_embed.set_footer(text=f"ID: {detected_member.id}")
    log_embed.set_thumbnail(url=detected_member.display_avatar.url)
    log_embed.add_field(
        name="Канал:",
        value=f"{detected_channel.mention} (`#{detected_channel.name}`)",
        inline=False
    )

    if detected_message:
        log_embed.add_field(
            name="Контекст:",
            value=f"[Ссылка на контекст]({detected_message.jump_url})",
            inline=False
        )

    asyncio.create_task(safe_send_to_log(bot, embed=log_embed, user_id=detected_member.id, message_content=log_desc))

    # MENTION EMBED
    mention_desc = (
        f"Причина срабатывания: {reason_text}\n"
        f"{punishment}\n\n"
        f"-# Дополнительную информацию можно посмотреть в канале автомодерации"
    )

    mention_embed = discord.Embed(
        title=reason_title,
        description=mention_desc,
        color=0xff0000
    )
    mention_embed.set_author(name=detected_guild.name, icon_url=detected_guild.icon.url if detected_guild.icon else None)
    mention_embed.set_thumbnail(url=detected_member.display_avatar.url)
    mention_embed.set_footer(
        text=(
            "Если ты считаешь, что это ошибка, проигнорируй это сообщение" 
            if is_soft and not force_ban 
            else "Если ты считаешь, что это ошибка, обратись к модераторам"
        )
    )

    asyncio.create_task(
            safe_send_to_channel(
            detected_member,
            embed=mention_embed,
            user_id=detected_member.id,
            message_content=f"[ЛИЧНЫЕ СООБЩЕНИЯ]\n\n{mention_desc}"
        )
    )

    if not isinstance(detected_channel, discord.ForumChannel):
        asyncio.create_task(
            safe_send_to_channel(
                detected_channel,
                content=detected_member.mention,
                embed=mention_embed,
                user_id=detected_member.id,
                message_content=mention_desc
            )
        )

    if detected_message and not force_ban:
        asyncio.create_task(safe_delete(detected_message))

    # выдаёт бан
    if force_ban:
        await safe_ban(detected_guild, detected_member, timeout_reason, delete_message_seconds=216000)
        await HIT_CACHE.delete(detected_member.id)

    # выдаёт мут
    elif not is_soft:
        await safe_timeout(detected_member, timedelta(hours=1), timeout_reason)
        await HIT_CACHE.delete(detected_member.id)