from cache import AsyncTTL
from collections import Counter
import re

ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200F\uFEFF\u2060]")
EMPTY_SPAM_LINE_RE = re.compile(r"^[\s\`\u200B-\u200F\uFEFF]{0,}$")

EXCESSIVE_EMOJI_RE = re.compile(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+")
SUSPICIOUS_LINKS_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
CAPS_WORDS_RE = re.compile(r"\b[A-ZА-ЯЁ]{3,}\b")

WORDS_RE = re.compile(r'\b\w{3,}\b')
CHAOTIC_WORDS_RE = re.compile(r'\b\w{2,}\b')
SPECIAL_CHARS_RE = re.compile(r'[^a-zA-Zа-яА-ЯёЁ0-9\s]')

LONG_REPEATS_RE = re.compile(r"(.)\1{50,}")
TWENTY_FIVE_REPEATS_RE = re.compile(r"(.)\1{{25,}}")
THIRTY_REPEATS_RE = re.compile(r"(.)\1{{30,}}")
NORMAL_WORDS_RE = re.compile(r"[a-zA-Z0-9а-яА-ЯёЁ\s]+")

TWENTY_QUOTATION_MARKS_RE = re.compile(r"`{20,}")
THIRTY_QUOTATION_MARKS_RE = re.compile(r"`{30,}")

ALTERNATING_SYMBOLS_PATTERN = re.compile(r"(.\|){10,}|(\|-){10,}")
SPECIAL_CHARS_SEQUENCE_SHORT_PATTERN = re.compile(r"[!@#$%^&*()_+=\[\]{}|\\:;\"'<>,.?/~`-]{15,}")
SPECIAL_CHARS_SEQUENCE_LONG_PATTERN = re.compile(r"[!@#$%^&*()_+=\[\]{}|\\:;\"'<>,.?/~`-]{25,}")
REPEATED_SEPARATORS_PATTERN = re.compile(r"[\.]{20,}|[-]{20,}|[_]{20,}|[=]{20,}|[+]{20,}")


@AsyncTTL(time_to_live=600, maxsize=20000)
async def is_spam_block(message: str) -> bool:
    msg_len = len(message)
    
    if msg_len < 3:
        return False
    
    if LONG_REPEATS_RE.search(message):
        return True
    
    repeat_pattern = TWENTY_FIVE_REPEATS_RE if msg_len < 100 else THIRTY_REPEATS_RE
    if repeat_pattern.search(message):
        repeats = repeat_pattern.findall(message)
        for match in repeats:
            if match[0] not in [' ', '\n', '\t']:
                return True
    
    if msg_len > 30:
        freq = Counter(message)
        freq_without_whitespace = {k: v for k, v in freq.items() if k not in [' ', '\n', '\t', '\r']}
        
        if freq_without_whitespace:
            dominant = max(freq_without_whitespace.values())
            
            if msg_len < 100:
                if dominant / msg_len >= 0.70:
                    return True
            elif msg_len < 500:
                if dominant / msg_len >= 0.75:
                    return True
            else:
                if dominant / msg_len >= 0.80:
                    return True
            
            if msg_len < 100 and dominant >= 50:
                return True
            elif dominant >= 300:
                return True
    
    tick_count = message.count("`")
    if msg_len < 100:
        if tick_count >= 60 or TWENTY_QUOTATION_MARKS_RE.search(message):
            return True
    else:
        if tick_count >= 150 or THIRTY_QUOTATION_MARKS_RE.search(message):
            return True
    
    lines = message.split("\n")
    line_count = len(lines)
    
    if line_count >= 10:
        empty_like = sum(1 for l in lines if EMPTY_SPAM_LINE_RE.match(l))
        if line_count < 30:
            if empty_like / line_count >= 0.70:
                return True
        else:
            if empty_like / line_count >= 0.75:
                return True
    
    if message.count("```") >= 2:
        parts = message.split("```")
        for i in range(1, len(parts), 2):
            code = parts[i].strip()
            if len(code) < 10:
                continue
            
            code_lines = code.split("\n")
            if len(code) > 2000 or len(code_lines) > 40:
                line_freq = Counter(code_lines)
                if line_freq:
                    most_common = line_freq.most_common(1)[0][1]
                    if most_common > 15:
                        return True
    
    if msg_len > 4000:
        compact = NORMAL_WORDS_RE.sub("", message)
        if len(compact) / msg_len >= 0.75:
            return True
    
    inv = re.findall(ZERO_WIDTH_RE, message)
    if msg_len < 100:
        if len(inv) > 30:
            return True
    else:
        if len(inv) > 100:
            return True
    
    if msg_len > 20: 
        emoji_matches = EXCESSIVE_EMOJI_RE.findall(message)
        emoji_count = sum(len(match) for match in emoji_matches)
        
        if msg_len < 100:
            if emoji_count > 40 or (emoji_count / msg_len > 0.65 and emoji_count > 15):
                return True
        else:
            if emoji_count > 80 or (emoji_count / msg_len > 0.6 and emoji_count > 30):
                return True
    
    links = SUSPICIOUS_LINKS_RE.findall(message)
    if msg_len < 100:
        if len(links) >= 5:
            return True
    else:
        if len(links) >= 10:
            return True
    
    if msg_len > 50:
        words = WORDS_RE.findall(message.lower())
        if len(words) > 10:
            word_freq = Counter(words)
            most_common_word, count = word_freq.most_common(1)[0]
            
            if msg_len < 100:
                if count > 12 and count / len(words) > 0.45:
                    return True
            else:
                if count > 30 and count / len(words) > 0.4:
                    return True
    
    if msg_len > 80:
        phrase_lengths = [15, 25, 35] if msg_len < 200 else [20, 30, 40]
        
        for phrase_len in phrase_lengths:
            if msg_len < phrase_len * 3:
                continue
            
            phrases = []
            step = max(phrase_len // 3, 5)
            for i in range(0, msg_len - phrase_len, step):
                phrases.append(message[i:i+phrase_len])
            
            if not phrases:
                continue
                
            phrase_freq = Counter(phrases)
            most_common_count = phrase_freq.most_common(1)[0][1]
            
            if msg_len < 200:
                if most_common_count >= 4:
                    return True
            else:
                if most_common_count >= 8:
                    return True
    
    if ALTERNATING_SYMBOLS_PATTERN.search(message):
        return True
    
    if msg_len < 100:
        if SPECIAL_CHARS_SEQUENCE_SHORT_PATTERN.search(message):
            return True
    else:
        if SPECIAL_CHARS_SEQUENCE_LONG_PATTERN.search(message):
            return True
    
    if REPEATED_SEPARATORS_PATTERN.search(message):
        return True
    
    if msg_len > 30:
        words_and_numbers = CHAOTIC_WORDS_RE.findall(message)
        if len(words_and_numbers) < 3 and msg_len > 50:
            special_chars = len(SPECIAL_CHARS_RE.findall(message))
            if special_chars / msg_len > 0.6:
                return True
    
    return False