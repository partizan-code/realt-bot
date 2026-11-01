import logging
import sys

import config


def setup_logger():
    """Настройка основного логгера"""
    logger = logging.getLogger()
    logger.setLevel(config.LOG_LEVEL)  # общий уровень логирования

    # Форматтер
    formatter = logging.Formatter(config.LOG_FORMAT)

    # Handler для консоли — все уровни
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(config.CONSOLE_LOG_LEVEL)

    # Handler для файла — только ERROR
    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(config.FILE_LOG_LEVEL)

    # Добавляем handlers к логгеру
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
