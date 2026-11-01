import sys

from loguru import logger

import config


def setup_logger():
    """Настроить Loguru: консоль и файл ошибок, без дублирования обработчиков."""
    logger.remove()
    options = {
        "format": config.LOG_FORMAT,
        "filter": lambda record: record["level"].no >= logger.level(config.LOG_LEVEL).no,
        "backtrace": config.LOG_BACKTRACE,
        "diagnose": config.LOG_DIAGNOSE,
    }
    logger.add(sys.stdout, level=config.CONSOLE_LOG_LEVEL, **options)
    logger.add(
        config.LOG_FILE,
        level=config.FILE_LOG_LEVEL,
        encoding=config.LOG_FILE_ENCODING,
        **options,
    )
    return logger
