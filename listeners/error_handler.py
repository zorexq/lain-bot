import logging
import traceback

from discord.ext import commands

from classes.bot import LainBot

class ErrorHandler(commands.Cog):
    def __init__(self, bot: LainBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_error(self, error_event: str, *args, **kwargs):
        logging.error(f"Ошибка в событии {error_event}:\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        if ctx.command:
            if ctx.command.name == "run":
                return
        raise error


async def setup(bot: LainBot):
    await bot.add_cog(ErrorHandler(bot))
