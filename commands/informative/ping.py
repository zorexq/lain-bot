import logging
import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from classes.bot import LainBot

LOGGER = logging.getLogger(__name__)

class Ping(commands.Cog):
    def __init__(self, bot: LainBot):
        self.bot = bot

    @commands.command(name="пинг", description="Показывает задержку бота", aliases=["ping"])
    async def ping_usual(self, ctx: commands.Context):
        start_rest_latency = time.monotonic()
        msg = await ctx.reply("🏓 Считаю пинг...")
        end_rest_latency = time.monotonic()

        ws_latency = round(self.bot.latency * 1000)
        rest_latency = round((end_rest_latency - start_rest_latency) * 1000)

        status = "🟢 Отлично" if rest_latency < 300 else "🟠 Медленно"

        await msg.edit(content=f"🏓 Понг!\n\n**WebSocket задержка**: `{ws_latency}мс`\n**Реальная задержка** (время между командой и ответом): `{rest_latency}мс`\n\n**Состояние**: {status}")

    @ping_usual.error
    async def ping_usual_error(self, ctx: commands.Context, error):
        LOGGER.error(traceback.format_exc())
        await ctx.send(embed=discord.Embed(title="❌ Произошла ошибка!", description="Непредвиденная ошибка, прошу связаться с разработчиком. По всей видимости, что-то не так с ботом", color=0xff0000))

    @app_commands.command(name="пинг", description="Показывает задержку бота")
    async def ping_slash(self, interaction: discord.Interaction):
        start_rest_latency = time.monotonic()
        await interaction.response.send_message("🏓 Считаю пинг...")
        end_rest_latency = time.monotonic()

        ws_latency = round(self.bot.latency * 1000)
        rest_latency = round((end_rest_latency - start_rest_latency) * 1000)

        status = "🟢 Отлично" if rest_latency < 300 else "🟠 Медленно"

        await interaction.edit_original_response(content=f"🏓 Понг!\n\n**WebSocket задержка**: `{ws_latency}мс`\n**Реальная задержка** (время между командой и ответом): `{rest_latency}мс`\n\n**Состояние**: {status}")

    @ping_slash.error
    async def ping_slash_error(self, interaction: discord.Interaction, error):
        LOGGER.error(traceback.format_exc())
        if interaction.response.is_done():
            await interaction.followup.send(embed=discord.Embed(title="❌ Произошла ошибка!", description="Непредвиденная ошибка, прошу связаться с разработчиком. По всей видимости, что-то не так с ботом", color=0xff0000))
        else:
            await interaction.response.send_message(embed=discord.Embed(title="❌ Произошла ошибка!", description="Непредвиденная ошибка, прошу связаться с разработчиком. По всей видимости, что-то не так с ботом", color=0xff0000))

async def setup(bot: LainBot):
    await bot.add_cog(Ping(bot))
