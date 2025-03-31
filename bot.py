import asyncio
import logging
from core.xmpp import Bot
from core.config import load_config

def setup_logging(config):
    logging_config = config.get("logging", {})
    enabled = logging_config.get("enabled", True)
    level = logging_config.get("level", "INFO").upper()
    log_to_file = logging_config.get("log_to_file", True)
    log_file_path = logging_config.get("log_file_path", "bot.log")

    if not enabled:
        logging.disable(logging.CRITICAL)
        return

    log_level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    log_level = log_level_map.get(level, logging.INFO)

    handlers = [logging.StreamHandler()]
    if log_to_file:
        handlers.append(logging.FileHandler(log_file_path))

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

    logger = logging.getLogger(__name__)
    logger.info("Logging configured successfully.")

def main():
    config = load_config()
    if not config:
        logger = logging.getLogger(__name__)
        logger.error("Failed to load configuration.")
        return

    setup_logging(config)
    bot = Bot(config)
    bot.connect()
    bot.process()

if __name__ == "__main__":
    main()