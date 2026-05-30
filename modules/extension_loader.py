import logging
import os
import traceback

from classes.bot import LainBot

LOGGER = logging.getLogger(__name__)

async def load_all_extensions(bot: LainBot, base_folder="commands"):
    for root, dirs, files in os.walk(base_folder):
        for file in files:
            if file.endswith(".py") and not file.startswith("_"):
                relative_path = os.path.join(root, file).replace("\\", "/")
                module = relative_path.removesuffix(".py").replace("/", ".")

                try:
                    await bot.load_extension(module)
                    LOGGER.info(f"☑️ Загружено расширение: {module}")
                except Exception as e:
                    LOGGER.error(f"❌ Ошибка при загрузке {module}: {type(e).__name__}: {e}")
                    LOGGER.error(traceback.format_exc())