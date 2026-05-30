import asyncio
import logging
import traceback
import typing

from aiocache import SimpleMemoryCache
from collections import defaultdict
import discord

from modules.automod.handle_violation import delete_messages_safe
from modules.lock_manager import LockManagerWithIdleTTL

MENTIONS_FROM_NEW_MEMBERS_CACHE = SimpleMemoryCache()
LOCK_MANAGER = LockManagerWithIdleTTL(idle_ttl=2400)

MAX_SIMILLAR_MENTIONS = 10  # максимум упоминаний одного id
MAX_DIFFERENT_MENTIONS = 5  # максимум уникальных упоминаний
MAX_STORED_MESSAGES = 200   # сколько последних сообщений хранить в кэше

async def get_cached_mentions_and_append(member: discord.Member, message: discord.Message = None) -> tuple:
    async with LOCK_MANAGER.lock(member.id):

        mentions: dict = await MENTIONS_FROM_NEW_MEMBERS_CACHE.get(member.id) or {}
        messages = mentions.setdefault("messages", [])

        if message:

            messages = [m for m in messages if m.get("id") != message.id]

            if "@everyone" in message.content:
                mentions["@everyone"] = mentions.get("@everyone", 0) + 1

            if "@here" in message.content:
                mentions["@here"] = mentions.get("@here", 0) + 1

            replied_message_author_id = None
            if message.reference and message.reference.resolved:
                if isinstance(message.reference.resolved, discord.Message):
                    replied_message_author_id = message.reference.resolved.author.id
            
            for user in message.mentions:
                if user.id != replied_message_author_id and not user.bot:
                    mentions[user.id] = mentions.get(user.id, 0) + 1
            
            for role in message.role_mentions:
                mentions[role.id] = mentions.get(role.id, 0) + 1

            messages.append(
                {
                    "id": message.id,
                    "channel_id": message.channel.id
                }
            )

            # Ограничение кэша
            if len(messages) > MAX_STORED_MESSAGES:
                messages = messages[-MAX_STORED_MESSAGES:]

            mentions["messages"] = messages

            await MENTIONS_FROM_NEW_MEMBERS_CACHE.set(member.id, mentions, ttl=1800)

        mentions_copy = {k: v for k, v in mentions.items() if k != "messages"}

        return mentions_copy, messages


async def detect_mention_abuse(member: discord.Member, message: discord.Message) -> typing.Tuple[bool, list]:
    mentions, messages = await get_cached_mentions_and_append(member, message)

    if not mentions:
        return False, messages

    max_count = max(mentions.values()) if mentions else 0

    if max_count >= MAX_SIMILLAR_MENTIONS:
        return True, messages

    if len(mentions) >= MAX_DIFFERENT_MENTIONS:
        return True, messages

    return False, messages


async def check_mention_abuse(member: discord.Member, message: discord.Message) -> typing.Tuple[bool, str]:
    is_abuse, messages = await detect_mention_abuse(member, message)

    if is_abuse:
        try:
            messages_by_channel = defaultdict(set)

            for msg in messages:
                messages_by_channel[msg["channel_id"]].add(msg["id"])

            for channel_id, ids in messages_by_channel.items():
                try:
                    channel = member.guild.get_channel(channel_id) or await member.guild.fetch_channel(channel_id)
                    asyncio.create_task(
                        delete_messages_safe(
                            channel,
                            ids,
                            reason="Злоупотребление упоминаниями от нового участника"
                        )
                    )
                except Exception:
                    logging.error(traceback.format_exc())

        except Exception:
            logging.error(traceback.format_exc())

    return is_abuse, message.content