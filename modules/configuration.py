import logging
import logging.handlers
import sys
import typing
from itertools import cycle

import dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = dotenv.find_dotenv()

class Settings(BaseSettings):

    DISCORD_TOKEN: SecretStr

    LOGGING_LEVEL: typing.Literal["DEBUG", "INFO", "ERROR", "WARNING", "CRITICAL"] = "INFO"

    BOT_PREFIX: str = "."
    LAIN_COLOR: int = 0x5b00c1

    ACTIVITY_NAMES: cycle = cycle([
        {
            "name": "everyone is always connected 🤍",
            "streaming_url": ""
        },
        {
            "name": "Don't talk to me like I'm a machine,I'm not that.",
            "streaming_url": ""
        }
    ])

    BOT_LOGS_CHANNEL_ID:           int = 1380518098053894146
    GUILD_ID:                      int = 1380518097114497095
    AUTOMOD_LOGS_CHANNEL_ID:       int = 1415381171939967076
    NEWS_CHANNEL_ID:               int = 1435943738688929934

    AUTOMOD_WHITELISTED_ROLES_IDS: typing.List[int] = [
        1438936721449422930,
        1445780096181997750
    ]

    ADS_CHANNELS_IDS: typing.List[int] = [
        1434948088199516302, 
        1441759667217764462, 
        1434634895602225172
    ]

    PROTECTED_CHANNELS_IDS: typing.List[int] = [
        1415337442327789568,
        1380586615214178445,
        1421844403802341377,
        1438948881533505649,
        1434731894251065477,
        1445045932600197221,
        1436379767409737910,
        1415381171939967076,
        1380518098053894146,
        1415383643437928509,
        1415357514974887937,
        1415371944894926929,
        1434761574215848068,
        1435943738688929934,
        1415369367864082435,
        1475099422386950204,
        1415345171092213782,
        1449068959121936394,
        1454815375152648395,
        1439547094422650961
    ]

    model_config = SettingsConfigDict(
        env_file=ENV_PATH, enable_decoding="utf-8"
    )

CONFIG = Settings()

# Логирование
def setup_logging():
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(
        logging.Formatter('[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s')
    )

    backup_handler = logging.handlers.TimedRotatingFileHandler(
        filename='logs/tmp.log', 
        when='D', 
        interval=1, 
        backupCount=10, 
        encoding='utf-8', 
        delay=False
    )
    backup_handler.setFormatter(
        logging.Formatter('[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s')
    )
    
    root_logger.setLevel(CONFIG.LOGGING_LEVEL)
    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(backup_handler)
    
    logging.getLogger('discord').setLevel(logging.ERROR)
    logging.getLogger('discord.client').setLevel(logging.ERROR)
    logging.getLogger('discord.gateway').setLevel(logging.ERROR)
    # logging.getLogger('discord.http').setLevel(logging.ERROR)
    logging.getLogger('discord.webhook.async_').setLevel(logging.ERROR)

setup_logging()