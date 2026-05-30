import asyncio
import logging
import signal
import sys

import discord
from discord.ext import commands

from modules.configuration import CONFIG

LOGGER = logging.getLogger(__name__)

class LainBot(commands.AutoShardedBot):

    def __init__(self):
        discord_intents = discord.Intents(
            message_content=True,
            members=True,
            presences=True,
            guilds=True,
            guild_messages=True,
            voice_states=True,
            auto_moderation_execution=True,
        )

        next_status = next(CONFIG.ACTIVITY_NAMES)

        super().__init__(
            command_prefix=commands.when_mentioned_or(CONFIG.BOT_PREFIX),
            case_insensitive=True,
            help_command=None,
            intents=discord_intents,
            status=discord.Status.idle,
            activity=discord.Streaming(name=next_status.get("name"), url=next_status.get("streaming_url")) if next_status.get("streaming_url") else discord.CustomActivity(name=next_status.get("name"))
        )

    async def setup_hook(self):
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda _sig=sig: asyncio.create_task(self.close()))

        from modules.status_update      import change_status_periodically
        from modules.extension_loader   import load_all_extensions

        change_status_periodically.start(self)

        await load_all_extensions(self, "commands")
        await load_all_extensions(self, "listeners")

        LOGGER.info("Модули запущены")


bot = LainBot()