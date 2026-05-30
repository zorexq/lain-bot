import asyncio
import logging
import traceback
import typing

from aiocache import SimpleMemoryCache
import discord

from classes.bot import LainBot
from modules.automod.link_filter import detect_links
from modules.lock_manager import LockManagerWithIdleTTL

THREADS_FROM_NEW_MEMBERS_CACHE = SimpleMemoryCache()

_DELETE_SEMAPHORE = asyncio.Semaphore(1)
LOCK_MANAGER = LockManagerWithIdleTTL(idle_ttl=2400)

MAX_THREADS = 7  # максимальное количество веток в кэше

async def get_cached_threads_and_append(member: discord.Member, append_thread: discord.Thread) -> list:
    async with LOCK_MANAGER.lock(member.id):
        threads = await THREADS_FROM_NEW_MEMBERS_CACHE.get(member.id) or []

        threads = threads[-MAX_THREADS:]

        if append_thread:
            threads.append({
                "id": append_thread.id
            })

            await THREADS_FROM_NEW_MEMBERS_CACHE.set(member.id, threads, ttl=1200)


    return threads

async def analyze_thread(bot: LainBot, member: discord.Member, thread: discord.Thread) -> typing.Tuple[bool, list, typing.Optional[str]]:

    threads_list = await get_cached_threads_and_append(member, append_thread=thread)

    if len(threads_list) >= MAX_THREADS:
        return True, threads_list, None
    
    matched = await detect_links(bot, thread.name)

    if matched:
        return True, threads_list, matched
    
    return False, threads_list, matched

async def delete_thread_safe(
    thread: discord.Thread,
    reason: str = "Автоматическая очистка"
):

    async with _DELETE_SEMAPHORE:

        if thread.starter_message:
            try:
                await thread.starter_message.delete()
            except (discord.HTTPException, discord.NotFound):
                pass

        elif thread.parent and not isinstance(thread.parent, discord.ForumChannel):
            try:
                await thread.parent.delete_messages(
                    [discord.Object(id=thread.id)],
                    reason=reason
                )
            except (discord.HTTPException, discord.NotFound):
                pass

        try:
            await thread.delete(
                reason=reason
            )
            return
        except discord.HTTPException:
            pass


async def flood_and_threads_check(bot: LainBot, member: discord.Member, thread: discord.Thread) -> typing.Tuple[bool, typing.Optional[str], str]:
    is_flood, threads, matched = await analyze_thread(bot, member, thread)

    if is_flood:
        
        for th_dict in threads:

            try:
                th = member.guild.get_thread(th_dict["id"]) or await member.guild.fetch_channel(th_dict["id"])
                if not isinstance(th, discord.Thread):
                    continue
                
                asyncio.create_task(delete_thread_safe(th, reason="Реклама в ветке" if matched else "Флуд ветками от нового участника"))

            except Exception:
                logging.error(traceback.format_exc())
                pass

    return is_flood, matched, thread.name