import discord
from discord.ext import commands

from classes.bot import LainBot
from modules.configuration import CONFIG

class SyncSlashCommands(commands.Cog):
    def __init__(self, bot: LainBot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, guild_id: int=None):
        if guild_id:
            await self.bot.tree.sync(guild=discord.Object(id=guild_id))
        else:
            await self.bot.tree.sync(guild=None)
        await ctx.send(embed=discord.Embed(description="☑️ Синхронизировано!", color=CONFIG.LAIN_COLOR))

async def setup(bot: LainBot):
    await bot.add_cog(SyncSlashCommands(bot))