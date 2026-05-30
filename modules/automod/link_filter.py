import re
import logging
import unicodedata
import urllib.parse
import typing

from aiocache import SimpleMemoryCache
import aiohttp
from cache import AsyncTTL
import discord
from rapidfuzz import fuzz

from classes.bot import LainBot
from modules.extract_message_content import extract_message_content

VARIATION_SELECTOR_RE = re.compile(r"[\uFE0F]")
ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200F\uFEFF\u2060]")
MARKDOWN_LINKS_RE = re.compile(r'\[([^\]]+)\]\(([^\)]+)\)')

SPACED_LINK_PATTERNS = [
    (re.compile(r't[\s\.\-_•]{0,3}\.[\s\.\-_•]{0,3}m[\s\.\-_•]{0,3}e[\s\.\-_•]{0,3}/[\s\.\-_•]{0,3}\w+'), "t.me"),
    (re.compile(r't[\s\.\-_•]{1,3}m[\s\.\-_•]{1,3}e[\s\.\-_•]{0,3}/[\s\.\-_•]{0,3}\w+'), "t.me"),
    
    (re.compile(r'd[\s\.\-_•]{0,2}i[\s\.\-_•]{0,2}s[\s\.\-_•]{0,2}c[\s\.\-_•]{0,2}o[\s\.\-_•]{0,2}r[\s\.\-_•]{0,2}d[\s\.\-_•]{0,3}\.[\s\.\-_•]{0,3}g[\s\.\-_•]{0,3}g'), "discord.gg"),
    (re.compile(r'd[\s\.\-_•]{0,2}i[\s\.\-_•]{0,2}s[\s\.\-_•]{0,2}c[\s\.\-_•]{0,2}[\s\.\-_•]{0,2}r[\s\.\-_•]{0,2}d[\s\.\-_•]{0,3}\.[\s\.\-_•]{0,3}g[\s\.\-_•]{0,3}g'), "discord.gg"),
    
    (re.compile(r'd[\s\.\-_•]{0,2}i[\s\.\-_•]{0,2}s[\s\.\-_•]{0,2}c[\s\.\-_•]{0,2}o[\s\.\-_•]{0,2}r[\s\.\-_•]{0,2}d[\s\.\-_•]{0,2}a[\s\.\-_•]{0,2}p[\s\.\-_•]{0,2}p'), "discordapp.com"),
    
    (re.compile(r't[\s\.\-_•]{0,2}e[\s\.\-_•]{0,2}l[\s\.\-_•]{0,2}e[\s\.\-_•]{0,2}g[\s\.\-_•]{0,2}r[\s\.\-_•]{0,2}a[\s\.\-_•]{0,2}m[\s\.\-_•]{0,3}\.[\s\.\-_•]{0,3}(me|org)'), "telegram"),
]

COLLAPSE_RE = re.compile(r"\s+")
COMPACT_RE = re.compile(r"[^a-z0-9]")

NATURAL_INDICATORS_PATTERNS = (
    re.compile(r'[а-яё]{3,}'),
    re.compile(r'[,;:!?]'),
    re.compile(r'\b(и|в|на|с|что|как|это|для|от|по|но|а|или)\b')
)

DOMAINS_WITH_DOT_RE = re.compile(r"([a-zA-Z0-9]+)\.([a-zA-Z]{2,6})\b")
GLUED_DOMAINS_RE = re.compile(r"([a-zA-Z0-9]{6,})(gg|com|app)\b")

EXPLICIT_URL_PATTERNS = [
    (re.compile(r'https?://discord\.gg/\w+', re.IGNORECASE), 'discord.gg (явная ссылка)'),
    (re.compile(r'https?://discord\.com/invite/\w+', re.IGNORECASE), 'discord.com/invite (явная ссылка)'),
    (re.compile(r'https?://discordapp\.com/invite/\w+', re.IGNORECASE), 'discordapp.com/invite (явная ссылка)'),
    (re.compile(r'https?://t\.me/\w+', re.IGNORECASE), 't.me (явная ссылка)'),
]

TME_SPECIAL_PATTERNS = (
    re.compile(r't\.me/'),
    re.compile(r't\s*\.\s*me/'),
    re.compile(r'tme/'),
)

FUZZY_INVITE_RE = re.compile(r'invit|nvite|vite')
DISCORDGG_RE = re.compile(r'discordgg')

DISCORD_INVITE_PATTERNS = [
    re.compile(r"discord\.com/invite/", re.IGNORECASE),
    re.compile(r"discord\.gg/", re.IGNORECASE),
    re.compile(r"discordapp\.com/invite/", re.IGNORECASE),
]

URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)

EMOJI_ASCII_MAP = {
    "🅰️": "a", "🅱️": "b", "🅾️": "o", "🅿️": "p",
    "Ⓜ️": "m", "ℹ️": "i", "❌": "x", "⭕": "o",
}

REGIONAL_INDICATOR_MAP = {
    chr(code): chr(ord('a') + (code - 0x1F1E6))
    for code in range(0x1F1E6, 0x1F1FF + 1)
}

HOMOGLYPHS = {
    "а": "a", "А": "a",
    "е": "e", "Е": "e", "ё": "e", "Ё": "e",
    "о": "o", "О": "o",
    "р": "p", "Р": "p",
    "с": "c", "С": "c",
    "х": "x", "Х": "x",
    "у": "y", "У": "y",
    "к": "k", "К": "k",
    "м": "m", "М": "m",
    "т": "t", "Т": "t",
    "в": "b", "В": "b",
    "н": "h", "Н": "h",
    "д": "d", "Д": "d",
    "г": "g", "Г": "g",
    "б": "b", "Б": "b",
    "і": "i", "І": "i",

    "0": "o",
    "1": "l",
    "3": "e",
}

ENCLOSED_ALPHANUM_MAP = {
    "🄰": "a","🄱": "b","🄲": "c","🄳": "d","🄴": "e",
    "🄵": "f","🄶": "g","🄷": "h","🄸": "i","🄹": "j",
    "🄺": "k","🄻": "l","🄼": "m","🄽": "n","🄾": "o",
    "🄿": "p","🅀": "q","🅁": "r","🅂": "s","🅃": "t",
    "🅄": "u","🅅": "v","🅆": "w","🅇": "x","🅈": "y",
    "🅉": "z",
    "🅐": "a","🅑": "b","🅒": "c","🅓": "d","🅔": "e",
    "🅕": "f","🅖": "g","🅗": "h","🅘": "i","🅙": "j",
    "🅚": "k","🅛": "l","🅜": "m","🅝": "n","🅞": "o",
    "🅟": "p","🅠": "q","🅡": "r","🅢": "s","🅣": "t",
    "🅤": "u","🅥": "v","🅦": "w","🅧": "x","🅨": "y",
    "🅩": "z",
    "🆊": "j","🆋": "k","🆌": "l","🆍": "m","🆎": "ab",
    "🆏": "k","🆐": "p","🆑": "cl","🆒": "cool",
    "🆓": "free","🆔": "id","🆕": "new","🆖": "ng",
    "🆗": "ok","🆘": "sos","🆙": "up",
    "🆚": "vs","🆛": "b","🆜": "m","🆝": "n",
    "🆞": "o","🆟": "p","🆠": "q","🆡": "p",
    "🆢": "s","🆣": "t","🆤": "u","🆥": "v",
    "🆦": "w","🆧": "x","🆨": "h","🆩": "i",
    "🆪": "j","🆫": "k","🆬": "l","🆭": "m",
    "🆮": "n","🆯": "o",
}

FANCY_MAP = {
    **{chr(i): chr(i - 0xFEE0).lower() for i in range(0xFF21, 0xFF3B)},
    **{chr(i): chr(i - 0xFEE0).lower() for i in range(0xFF41, 0xFF5B)},
    **{chr(0x1D400 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D41A + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D434 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D44E + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D468 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D482 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D49C + i): chr(ord('a') + i) for i in range(26) if i not in [1,4,7,11,12,17,18]},
    **{chr(0x1D4B6 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D4D0 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D4EA + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D504 + i): chr(ord('a') + i) for i in range(26) if i not in [1,4,18,23]},
    **{chr(0x1D51E + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D538 + i): chr(ord('a') + i) for i in range(26) if i not in [1,4,17]},
    **{chr(0x1D552 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D5A0 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D5BA + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D5D4 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D5EE + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D608 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D622 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D63C + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D656 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D670 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1D68A + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x24B6 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x24D0 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1F150 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1F130 + i): chr(ord('a') + i) for i in range(26)},
    **{chr(0x1F170 + i): chr(ord('a') + i) for i in range(26)},
}

_COMBINED_MAP = {}
_COMBINED_MAP.update(EMOJI_ASCII_MAP)
_COMBINED_MAP.update(REGIONAL_INDICATOR_MAP)
_COMBINED_MAP.update(ENCLOSED_ALPHANUM_MAP)
_COMBINED_MAP.update(HOMOGLYPHS)
_COMBINED_MAP.update(FANCY_MAP)

STRICT_INVITE_CODE_PATTERN = re.compile(
    r'\b(?=[a-zA-Z0-9]{6,20}\b)(?=\w*[a-z])(?=\w*[A-Z])(?=(?:\w*[A-Z]\w*[A-Z])|(?=\w*\d))[a-zA-Z0-9]{6,20}\b'
)

URL_PATTERN_FOR_EXTRACTING_WORDS = re.compile(
    r'https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&\/\/=]*)'
)

DATE_RE = re.compile(r'^\d{2,4}-\d{2}')
DISCORD_EMOJI_PATTERN = re.compile(r'<a?:[^:>]+:\d{17,20}>|:[^:\s]+:')

INVITE_CODE_CACHE = SimpleMemoryCache()
INVITE_CODE_CACHE_TTL = 1200

WHITELISTED_WORDS = ("spotify",)

def should_skip_potential_code(code: str) -> bool:
    
    if not any(c.isalpha() and c.isascii() for c in code):
        return True

    if DATE_RE.match(code):
        return True
    
    if any(w in code.lower() for w in WHITELISTED_WORDS):
        return True
    
    return False

async def check_potential_invite_code(bot: LainBot, code: str) -> dict:
    
    cache_key = f"invite_code:{code.lower()}"
    
    cached = await INVITE_CODE_CACHE.get(cache_key)
    if cached is not None:
        logging.debug(f"Инвайт-код {code} найден в кэше: {cached}")
        return {
            'is_invite': cached['is_valid'],
            'guild_id': cached.get('guild_id'),
            'guild_name': cached.get('guild_name'),
            'member_count': cached.get('member_count'),
            'from_cache': True
        }
    
    try:
        invite = await bot.fetch_invite(code, with_counts=True)
        
        guild_id = invite.guild.id if invite.guild else None
        guild_name = invite.guild.name if invite.guild else None
        member_count = getattr(invite, 'approximate_member_count', None)
        
        result = {
            'is_valid': True,
            'guild_id': guild_id,
            'guild_name': guild_name,
            'member_count': member_count
        }
        await INVITE_CODE_CACHE.set(cache_key, result, ttl=INVITE_CODE_CACHE_TTL)
        
        return {
            'is_invite': True,
            'guild_id': guild_id,
            'guild_name': guild_name,
            'member_count': member_count,
            'from_cache': False
        }
        
    except discord.NotFound:
        result = {
            'is_valid': False,
            'guild_id': None,
            'guild_name': None
        }
        await INVITE_CODE_CACHE.set(cache_key, result, ttl=INVITE_CODE_CACHE_TTL)
        
        logging.debug(f"Инвайт-код {code} проверен через API: невалидный (404)")
        
        return {
            'is_invite': False,
            'guild_id': None,
            'guild_name': None,
            'from_cache': False
        }
        
    except discord.HTTPException as e:
        logging.warning(f"Ошибка API при проверке кода {code}: {e}")
        
        return {
            'is_invite': False,
            'guild_id': None,
            'guild_name': None,
            'from_cache': False,
            'error': str(e)
        }
    
    except Exception as e:
        logging.error(f"Неожиданная ошибка при проверке кода {code}: {e}")
        
        return {
            'is_invite': False,
            'guild_id': None,
            'guild_name': None,
            'from_cache': False,
            'error': str(e)
        }

async def extract_potential_invite_codes(bot: LainBot, message: typing.Union[discord.Message]) -> list:
    
    if isinstance(message, str):
        text = message
    else:
        text = await extract_message_content(bot, message)

    clean_text = DISCORD_EMOJI_PATTERN.sub(' ', text)
    
    clean_text = URL_PATTERN_FOR_EXTRACTING_WORDS.sub(' ', clean_text)
    
    matches = STRICT_INVITE_CODE_PATTERN.findall(clean_text)
    
    filtered_codes = [code for code in matches if not should_skip_potential_code(code)]
    
    seen = set()
    unique_codes = []
    for code in filtered_codes:
        code_lower = code.lower()
        if code_lower not in seen:
            seen.add(code_lower)
            unique_codes.append(code)
    
    return unique_codes[:5]

async def check_message_for_invite_codes(bot: LainBot, message: discord.Message, current_guild_id: int) -> dict:
    
    potential_codes = extract_potential_invite_codes(bot, message)
    
    if not potential_codes:
        return {'found_invite': False}
    
    logging.debug(f"Найдено {len(potential_codes)} потенциальных кодов для проверки: {potential_codes}")
    
    for code in potential_codes:
        result = await check_potential_invite_code(bot, code)
        
        if result['is_invite']:
            if result['guild_id'] == current_guild_id:
                logging.debug(f"Код {code} ведёт на свой сервер, пропускаем")
                continue
            
            return {
                'found_invite': True,
                'invite_code': code,
                'guild_id': result['guild_id'],
                'guild_name': result['guild_name'],
                'from_cache': result.get('from_cache', False),
                'member_count': result.get('member_count')
            }
    
    return {'found_invite': False}

def is_discord_invite_url(url: str) -> bool:
    for pattern in DISCORD_INVITE_PATTERNS:
        if pattern.search(url):
            return True
    return False

@AsyncTTL(time_to_live=300, maxsize=1000)
async def check_url_redirect(url: str, max_redirects: int = 5) -> str:
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                allow_redirects=True,
                max_redirects=max_redirects,
                headers={'User-Agent': 'Mozilla/5.0'}
            ) as response:
                return str(response.url)
    except Exception as e:
        return url


def extract_urls_from_text(text: str) -> list:
    urls = URL_PATTERN.findall(text)
    return urls


async def check_urls_for_discord_invites(text: str) -> str:
    urls = extract_urls_from_text(text)
    
    if not urls:
        return None
    
    for url in urls:
        if is_discord_invite_url(url):
            return "discord.gg/invite (прямая ссылка через URL)"
    
    suspicious_urls = urls[:3]
    
    for url in suspicious_urls:
        try:
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            final_url = await check_url_redirect(url)
            
            if is_discord_invite_url(final_url):
                return f"discord.gg/invite (через переходник {url})"
        except Exception as e:
            logging.debug(f"Error checking URL {url}: {e}")
            continue
    
    return None

async def _char_to_ascii(ch: str) -> str:
    if VARIATION_SELECTOR_RE.match(ch):
        return ""
    if ZERO_WIDTH_RE.match(ch):
        return ""
    if ch in _COMBINED_MAP:
        return _COMBINED_MAP[ch]

    code = ord(ch)
    if 0x1F1E6 <= code <= 0x1F1FF:
        return chr(ord("a") + (code - 0x1F1E6))

    decomp = unicodedata.normalize("NFKD", ch)
    if decomp:
        base = decomp[0]
        if ('A' <= base <= 'Z') or ('a' <= base <= 'z'):
            return base.lower()

    if ch.isdigit():
        return ch

    if ch in " \t\r\n./\\|_•·-:":
        return " "

    try:
        name = unicodedata.name(ch)
    except ValueError:
        name = ""

    if name:
        nm = name.upper().split()
        for token in nm:
            if len(token) == 1 and 'A' <= token <= 'Z':
                return token.lower()

    return " "

async def normalize_and_compact(raw_text: str) -> str:

    text = unicodedata.normalize("NFKC", raw_text)

    out = []
    for ch in text:
        out.append(await _char_to_ascii(ch))

    collapsed = "".join(out)
    collapsed = COLLAPSE_RE.sub(" ", collapsed).strip()
    compact = COMPACT_RE.sub("", collapsed.lower())
    return compact

async def looks_like_discord(word: str, threshold: int = 85):
    if len(word) < 6:
        return False
    score = fuzz.partial_ratio("discord", word)
    return score >= threshold

def extract_markdown_links(text: str):
    return re.findall(MARKDOWN_LINKS_RE, text)

def is_natural_word_context(text: str, match_pos: int, match_len: int) -> bool:
    start = max(0, match_pos - 20)
    end = min(len(text), match_pos + match_len + 20)
    context = text[start:end].lower()
    
    for pattern in NATURAL_INDICATORS_PATTERNS:
        if pattern.search(context):
            return True
    
    return False

def extract_spaced_patterns(text: str, compact: str):
    findings = []
    
    text_lower = text.lower()
    
    for pattern, label in SPACED_LINK_PATTERNS:
        matches = pattern.finditer(text_lower)
        for match in matches:
            if not is_natural_word_context(text, match.start(), len(match.group())):
                findings.append((label, match.group()))
    
    return findings

def extract_possible_domains(text: str):
    text_no_spaces = text.replace(" ", "")
    candidates = []

    dom1 = DOMAINS_WITH_DOT_RE.findall(text_no_spaces)
    for a, b in dom1:
        candidates.append(a + "." + b)

    dom2 = GLUED_DOMAINS_RE.findall(text_no_spaces)
    for a, b in dom2:
        candidates.append(a + b)

    return candidates


@AsyncTTL(time_to_live=600, maxsize=20000)
async def detect_links(bot: LainBot, message: typing.Union[discord.Message, str]):

    if isinstance(message, discord.Message):
        raw_text = await extract_message_content(bot, message)
    else:
        raw_text = message

    redirect_result = await check_urls_for_discord_invites(raw_text)
    if redirect_result:
        return redirect_result
    
    decoded_text = raw_text
    for _ in range(5):
        try:
            new_decoded = urllib.parse.unquote(decoded_text)
            if new_decoded == decoded_text:
                break
            decoded_text = new_decoded
        except Exception:
            break
    
    compact = await normalize_and_compact(decoded_text)
    
    for pattern, label in EXPLICIT_URL_PATTERNS:
        if pattern.search(decoded_text):
            return label
    
    if "discord" in compact:
        if FUZZY_INVITE_RE.search(compact):
            return "discord.com/invite (замаскированная через encoding)"
    
    if DISCORDGG_RE.search(compact):
        return "discord.gg (замаскированная)"
    
    if "discordapp" in compact and FUZZY_INVITE_RE.search(compact):
        return "discordapp.com/invite (замаскированная через encoding)"
    
    if TME_SPECIAL_PATTERNS[2].search(compact):
        return "t.me (замаскированная)"
    
    if len(raw_text) < 8:
        return None
    
    spaced_findings = extract_spaced_patterns(decoded_text, compact)
    if spaced_findings:
        label, matched = spaced_findings[0]
        return f"{label} (замаскированная ссылка: {matched})"
    
    markdown_links = extract_markdown_links(decoded_text)
    all_urls_to_check = [decoded_text]
    
    for link_text, url in markdown_links:
        all_urls_to_check.append(url)
        all_urls_to_check.append(link_text)
    
    for text_fragment in all_urls_to_check:
        result = await _check_single_fragment(text_fragment, decoded_text, compact)
        if result:
            return result
    
    return None


async def _check_single_fragment(text_fragment: str, original_text: str, compact: str):
    
    if not compact:
        compact = await normalize_and_compact(text_fragment)

    if "tme" in compact and ("t.me" in text_fragment.lower() or "tme/" in text_fragment.lower()):
        return "t.me"
    
    text_lower = text_fragment.replace(" ", "").lower()
    
    if len(compact) < 5:
        return None
    
    if "discord" in compact:
        invite_parts = ['invit', 'nvite', 'vite']
        if any(part in compact for part in invite_parts):
            match_pos = text_fragment.lower().find("discord")
            if match_pos != -1:
                if is_natural_word_context(text_fragment, match_pos, 7):
                    return None
            return "discord.com/invite"
        
        if compact.endswith("gg") or "discordgg" in compact:
            match_pos = text_fragment.lower().find("discord")
            if match_pos != -1:
                if is_natural_word_context(text_fragment, match_pos, 7):
                    return None
            return "discord.gg"
    
    if "discordgg" in compact:
        match_pos = text_fragment.lower().find("discord")
        if match_pos != -1:
            if is_natural_word_context(text_fragment, match_pos, 7):
                return None
        return "discord.gg"
    
    if "discordcom" in compact:
        if "/channels/" not in text_lower:
            match_pos = text_fragment.lower().find("discord")
            if match_pos != -1:
                if is_natural_word_context(text_fragment, match_pos, 7):
                    return None
            return "discord.com"
    
    if "discordappcom" in compact:
        if not any(x in original_text for x in ["https://cdn.discordapp.com", "https://media.discordapp.net", "https://images-ext-1.discordapp.net"]):
            return "discordapp.com"
        elif any(part in compact for part in ['invit', 'nvite']):
            return "discordapp.com/invite"
    
    if "telegramme" in compact or "telegramorg" in compact:
        return "telegram.me" if "telegramme" in compact else "telegram.org"
    
    if "tme" in compact:
        for pattern in TME_SPECIAL_PATTERNS:
            if pattern.search(text_lower):
                match = pattern.search(text_lower)
                if match and not is_natural_word_context(text_fragment, match.start(), len(match.group())):
                    return "t.me"
    
    candidates = extract_possible_domains(compact)
    
    for cand in candidates:
        if len(cand) < 8:
            continue
            
        left = cand.split(".")[0].replace("gg","").replace("com","").replace("app","")
        
        if await looks_like_discord(left):
            if left == "discord":
                continue
            
            if any(x in cand for x in ["imagesext1discordapp", "mediadiscordapp", "cdndiscordapp"]):
                if not any(part in compact for part in ['invit', 'nvite']):
                    continue
            
            if "/channels/" in text_lower:
                continue
            
            match_pos = text_fragment.lower().find(left)
            if match_pos != -1:
                if is_natural_word_context(text_fragment, match_pos, len(left)):
                    continue
            
            return f"Похоже на ссылку приглашения в Discord сервер ({cand})"
    
    return None

async def check_message_for_invite_codes(bot: LainBot, message: typing.Union[str, discord.Message], current_guild_id: int) -> dict:
    
    potential_codes = await extract_potential_invite_codes(bot, message)
    
    if not potential_codes:
        return {'found_invite': False}
    
    logging.debug(f"Найдено {len(potential_codes)} потенциальных кодов: {potential_codes}")
    
    for code in potential_codes:
        result = await check_potential_invite_code(bot, code)
        
        if result['is_invite']:
            if result['guild_id'] == current_guild_id:
                logging.debug(f"Код {code} ведёт на свой сервер, пропускаем")
                continue
            
            return {
                'found_invite': True,
                'invite_code': code,
                'guild_id': result['guild_id'],
                'guild_name': result['guild_name'],
                'from_cache': result['from_cache'],
                'member_count': result.get('member_count')
            }
    
    return {'found_invite': False}