import discord
from discord.ext import tasks

from classes.bot import LainBot
from modules.configuration import CONFIG

@tasks.loop(hours=1)
async def change_status_periodically(bot: LainBot):
    next_status = next(CONFIG.ACTIVITY_NAMES)
    await bot.change_presence(
        status=discord.Status.idle,
        activity=discord.Streaming(name=next_status.get("name"), url=next_status.get("streaming_url")) if next_status.get("streaming_url") else discord.CustomActivity(name=next_status.get("name"))
    )
