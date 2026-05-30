import logging

from cache import AsyncTTL
import discord
from discord.ext.commands import clean_content

from classes.bot import LainBot

@AsyncTTL(time_to_live=30)
async def activity_to_dict(activity: dict):
    if isinstance(activity, dict):
        return activity
    
    result = {}
    for attr in dir(activity):
        if not attr.startswith('_') and not callable(getattr(activity, attr, None)):
            try:
                value = getattr(activity, attr)
                if value is not None and not hasattr(value, '__dict__'):
                    result[attr] = value
            except Exception:
                continue
    return result

@AsyncTTL(time_to_live=30)
async def format_dict_fields(data, indent=0):
    lines = []
    prefix = "  " * indent
    
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(await format_dict_fields(value, indent + 1))
        elif isinstance(value, (list, tuple)):
            if value:
                lines.append(f"{prefix}{key}: {', '.join(str(v) for v in value)}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    
    return '\n'.join(lines)

async def clean_message_text(bot: LainBot, message: discord.Message):
    cleaner = clean_content(
        fix_channel_mentions=True,
        use_nicknames=True,
        escape_markdown=True
    )

    ctx = await bot.get_context(message)

    cleaned = await cleaner.convert(ctx, message.content)
    return cleaned

async def extract_message_content(bot: LainBot, message: discord.Message) -> str:

    message_content = ""

    if message.content:
        cleaned = await clean_message_text(bot, message)
        if cleaned:
            message_content += cleaned

    if message.stickers:
        message_content += "\n\n[Стикеры:]"
        for sticker in message.stickers:
            message_content += f"\n{sticker.name} ({sticker.id})\n"

    if message.attachments:
        message_content += "\n\n[Вложения:]"
        for attachment in message.attachments:
            message_content += f"\n{attachment.filename.replace('.', ' ')}\n"

    if message.embeds:
        message_content += "\n\n[Ембеды:]"
        for idx, embed in enumerate(message.embeds, 1):
            message_content += f"\n\n--- Ембед {idx} ---\n{await format_dict_fields(embed.to_dict())}"

    if message.activity:
        activity_dict = await activity_to_dict(message.activity)
        message_content += f"\n\n[Активность из сообщения:]\n{await format_dict_fields(activity_dict)}"

        if message.author.activities:
            message_content += "\n\n[Все активности пользователя:]"
            for idx, activity in enumerate(message.author.activities, 1):
                activity_dict = await activity_to_dict(activity)
                activity_type = type(activity).__name__
                message_content += f"\n\n--- Активность {idx} ({activity_type}) ---\n{await format_dict_fields(activity_dict)}"

    if message.poll:
        poll_options = " | ".join([f'"{option.text}"' for option in message.poll.answers])
        message_content += (
            "\n\n[Опрос:]"
            f'\nВопрос: "{message.poll.question}"'
            f"\nОпции: {poll_options}"
        )

    message_content = message_content.strip()

    return message_content