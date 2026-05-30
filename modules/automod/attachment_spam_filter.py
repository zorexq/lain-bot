import asyncio
import logging
import traceback
import typing

from aiocache import SimpleMemoryCache
from collections import defaultdict
import discord

from modules.automod.handle_violation import delete_messages_safe
from modules.lock_manager import LockManagerWithIdleTTL

ATTACHMENTS_FROM_NEW_MEMBERS_CACHE = SimpleMemoryCache()
LOCK_MANAGER = LockManagerWithIdleTTL(idle_ttl=2400)

MAX_ATTACHMENT_COUNT_FOR_NEW_MEMBER = 3  # максимум вложений от одного пользователя
MAX_STORED_MESSAGES = 50                 # сколько последних сообщений хранить в кэше

async def get_cached_attachments_and_append(member: discord.Member, message: discord.Message = None) -> tuple:
    async with LOCK_MANAGER.lock(member.id):

        attachments_dict: dict = await ATTACHMENTS_FROM_NEW_MEMBERS_CACHE.get(member.id) or {}
        messages = attachments_dict.setdefault("messages", [])

        if message:

            messages = [m for m in messages if m.get("id") != message.id]

            if message.attachments:
                attachments_dict["attachment_count"] = attachments_dict.get("attachment_count", 0) + len(message.attachments)

            messages.append(
                {
                    "id": message.id,
                    "channel_id": message.channel.id
                }
            )

            if len(messages) > MAX_STORED_MESSAGES:
                messages = messages[-MAX_STORED_MESSAGES:]

            attachments_dict["messages"] = messages

            await ATTACHMENTS_FROM_NEW_MEMBERS_CACHE.set(member.id, attachments_dict, ttl=1200)

        attachments_copy = {k: v for k, v in attachments_dict.items() if k != "messages"}
        return attachments_copy, messages


async def detect_attachment_spam(member: discord.Member, message: discord.Message) -> typing.Tuple[bool, list]:
    attachments, messages = await get_cached_attachments_and_append(member, message)
    if not attachments:
        return False, messages

    max_count = attachments.get("attachment_count", 0)

    if max_count >= MAX_ATTACHMENT_COUNT_FOR_NEW_MEMBER:
        return True, messages

    return False, messages


async def check_attachment_spam(member: discord.Member, message: discord.Message) -> typing.Tuple[bool, str]:
    is_attachment_spam, messages = await detect_attachment_spam(member, message)

    attachment_content = message.content

    if is_attachment_spam:
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
                            reason="Подозрение на спам вложениями от нового участника"
                        )
                    )
                except Exception:
                    logging.error(traceback.format_exc())

        except Exception:
            logging.error(traceback.format_exc())

        if message.attachments:
            if attachment_content:
                attachment_content += "\n\n"
            attachment_content += "[Вложения:]\n\n"
            for attachment in message.attachments:
                attachment_content += (
                    f"Имя файла: {attachment.filename}\n"
                    f"Размер: {attachment.size} байт\n"
                    f"Тип: {attachment.content_type}\n\n"
                )

    return is_attachment_spam, attachment_content
