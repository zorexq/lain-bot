import asyncio
from collections import defaultdict
import logging
import traceback
import typing

from aiocache import SimpleMemoryCache
from cache import AsyncTTL
import discord
from rapidfuzz import fuzz

from classes.bot import LainBot
from modules.automod.handle_violation import delete_messages_safe
from modules.extract_message_content import extract_message_content
from modules.lock_manager import LockManagerWithIdleTTL

MESSAGES_FROM_NEW_MEMBERS_CACHE = SimpleMemoryCache()
LOCK_MANAGER = LockManagerWithIdleTTL(idle_ttl=2400)

MAX_CACHE_MESSAGES = 60           # максимальное количество сообщений в кэше
GUARANTEED_WINDOW = 15            # количество сообщений для гарантированного флуда
ALTERNATING_WINDOW = 60           # окно анализа для чередования
FUZZY_THRESHOLD = 80              # порог нечёткого сравнения в процентах
MIN_CLUSTERS_FOR_ALTERNATING = 2  # количество кластеров для засчитывания флуда как чередование
MIN_CLUSTER_SIZE = 15             # количество сообщений в кластере для засчитывания флуда как чередование

@AsyncTTL(time_to_live=1600, maxsize=20000)
async def fuzzy_compare(str1: str, str2: str) -> int:
    try:
        score = fuzz.ratio(str1, str2)
    except Exception:
        score = 0
    return score

async def get_cached_messages_and_append(member: discord.Member, append_message_content: str = None, append_message: discord.Message = None) -> list:
    async with LOCK_MANAGER.lock(member.id):
        messages = await MESSAGES_FROM_NEW_MEMBERS_CACHE.get(member.id) or []

        messages = messages[-MAX_CACHE_MESSAGES:]  # ограничение кэша до MAX_CACHE_MESSAGES

        if append_message_content:

            # Удаляет предыдущее сообщение с таким же id
            messages = [m for m in messages if m.get("id") != append_message.id]

            messages.append({
                "content": append_message_content,
                "id": append_message.id,
                "channel_id": append_message.channel.id
            })

            await MESSAGES_FROM_NEW_MEMBERS_CACHE.set(member.id, messages, ttl=1200)


    return messages

async def append_cached_messages(bot: LainBot, member: discord.Member, message: discord.Message) -> str:

    message_content = await extract_message_content(bot, message)

    messages = await get_cached_messages_and_append(member, append_message_content=message_content, append_message=message)

    return message_content, messages

async def detect_flood(bot: LainBot, member: discord.Member, message: discord.Message) -> typing.Tuple[bool, list, str]:

    message_content, message_list = await append_cached_messages(bot, member, message)

    guaranteed_slice = message_list[-(GUARANTEED_WINDOW + 20):]
    alternating_slice = message_list[-ALTERNATING_WINDOW:]

    result = {
        "detected": False,
        "guaranteed_flood": False,
        "alternating_flood": False,
        "alternating_clusters_count": 0,
        "details": {
            "guaranteed_checked": len(guaranteed_slice),
            "alternating_checked": len(alternating_slice),
            "clusters": []
        }
    }

    if len(guaranteed_slice) >= GUARANTEED_WINDOW:

        guaranteed_clusters = []

        for i, msg in enumerate(guaranteed_slice):
            cur = (msg["content"] or "").strip()
            if not cur:
                continue

            placed = False

            for cl in guaranteed_clusters:
                proto = cl["prototype"]

                if cur == proto:
                    cl["count"] += 1
                    cl["indices"].append(i)
                    placed = True
                    break

                sim = await fuzzy_compare(cur, proto)
                if sim >= FUZZY_THRESHOLD:
                    cl["count"] += 1
                    cl["indices"].append(i)
                    placed = True
                    break

            if not placed:
                guaranteed_clusters.append({
                    "prototype": cur,
                    "count": 1,
                    "indices": [i]
                })

        for cl in guaranteed_clusters:
            if cl["count"] >= GUARANTEED_WINDOW:
                result["detected"] = True
                result["guaranteed_flood"] = True
                return True, message_list, message_content

    clusters = []

    for i, msg in enumerate(alternating_slice):
        cur = (msg["content"] or "").strip()
        if not cur:
            continue

        placed = False
        for cl in clusters:
            proto = cl["prototype"]
            if cur == proto:
                cl["indices"].append(i)
                cl["count"] += 1
                placed = True
                break
            else:
                try:
                    sim = await fuzzy_compare(cur, proto)
                except Exception:
                    sim = 0
                if sim >= FUZZY_THRESHOLD:
                    cl["indices"].append(i)
                    cl["count"] += 1
                    placed = True
                    break

        if not placed:
            clusters.append({
                "prototype": cur,
                "indices": [i],
                "count": 1
            })

    repeating_clusters = [c for c in clusters if c["count"] >= MIN_CLUSTER_SIZE]
    result["alternating_clusters_count"] = len(repeating_clusters)
    result["details"]["clusters"] = [
        {"prototype": c["prototype"][:200], "count": c["count"], "indices": c["indices"]} for c in clusters
    ]

    if len(repeating_clusters) >= MIN_CLUSTERS_FOR_ALTERNATING:
        result["alternating_flood"] = True
        result["detected"] = True

        return True, message_list, message_content
    
    return False, message_list, message_content

async def flood_and_messages_check(bot: LainBot, member: discord.Member, message: discord.Message) -> typing.Tuple[bool, str]:
    is_flood, messages, message_content = await detect_flood(bot, member, message)

    if is_flood:
        
        try:
            messages_by_channel = defaultdict(set)

            for msg in messages:
                messages_by_channel[msg["channel_id"]].add(msg["id"])

            for channel_id, ids in messages_by_channel.items():
                try:
                    channel = member.guild.get_channel(channel_id) or await member.guild.fetch_channel(channel_id)
                    asyncio.create_task(delete_messages_safe(channel, ids, reason="Флуд от нового участника"))

                except Exception:
                    logging.error(traceback.format_exc())
                    pass

        except Exception:
            logging.error(traceback.format_exc())
            pass

    return is_flood, message_content